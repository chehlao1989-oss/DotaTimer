"""Игровые данные из OpenDota с кешем на диске (%APPDATA%\\DotaTimer\\cache).

Без интернета всё работает из кеша. Обновление раз в сутки, в фоновом потоке.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from app import paths
from app.threats.opendota import OpenDotaClient, OpenDotaError

log = logging.getLogger(__name__)

MAX_AGE_SEC = 24 * 3600
HERO_PREFIX = "npc_dota_hero_"


@dataclass(frozen=True)
class Hero:
    id: int
    name: str  # короткое имя: "bloodseeker"
    localized: str  # как в игре: "Bloodseeker"
    roles: tuple[str, ...] = ()
    attr: str = ""
    img: str = ""


@dataclass(frozen=True)
class Item:
    key: str  # "heart"
    id: int
    dname: str  # "Heart of Tarrasque"
    cost: int = 0
    qual: str = ""
    components: tuple[str, ...] = ()
    img: str = ""
    tier: int | None = None  # тир нейтрального предмета; None — обычный предмет


class DataCache:
    """JSON-файлы и картинки с отметкой времени загрузки."""

    def __init__(self, root: Path | None = None):
        self.root = root or paths.cache_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def load(self, name: str):
        try:
            raw = json.loads(self._path(name).read_text(encoding="utf-8"))
            return raw["data"], raw["fetched_at"]
        except (OSError, ValueError, KeyError):
            return None, None

    def save(self, name: str, data) -> None:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # запись целиком или никак: кеш не бьётся при выключении

    def is_fresh(self, name: str, max_age: float = MAX_AGE_SEC) -> bool:
        _, fetched_at = self.load(name)
        return fetched_at is not None and time.time() - fetched_at < max_age

    def image_path(self, kind: str, key: str) -> Path:
        folder = self.root / "images" / kind
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{key}.png"


def parse_heroes(raw: dict) -> dict[int, Hero]:
    heroes = {}
    for value in raw.values():
        name = value["name"].removeprefix(HERO_PREFIX)
        heroes[value["id"]] = Hero(value["id"], name, value["localized_name"], tuple(value.get("roles") or ()),
                                   value.get("primary_attr", ""), value.get("img", ""))
    return heroes


def parse_items(raw: dict) -> dict[str, Item]:
    items = {}
    for key, value in raw.items():
        if not isinstance(value, dict) or "id" not in value:
            continue
        items[key] = Item(key, value["id"], value.get("dname") or key, value.get("cost") or 0,
                          value.get("qual") or "", tuple(value.get("components") or ()), value.get("img") or "",
                          value.get("tier"))
    return items


@dataclass
class GameData:
    heroes: dict[int, Hero] = field(default_factory=dict)
    items: dict[str, Item] = field(default_factory=dict)
    hero_stats: dict[int, dict] = field(default_factory=dict)
    matchups: dict[int, list] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return bool(self.heroes and self.items)

    def hero_by_name(self, name: str) -> Hero | None:
        name = name.removeprefix(HERO_PREFIX)
        return next((h for h in self.heroes.values() if h.name == name), None)

    @classmethod
    def from_cache(cls, cache: DataCache) -> "GameData":
        data = cls()
        raw_heroes, _ = cache.load("heroes")
        raw_items, _ = cache.load("items")
        raw_stats, _ = cache.load("hero_stats")
        if raw_heroes:
            data.heroes = parse_heroes(raw_heroes)
        if raw_items:
            data.items = parse_items(raw_items)
        if raw_stats:
            data.hero_stats = {row["id"]: row for row in raw_stats}
        for hero_id in data.heroes:
            rows, _ = cache.load(f"matchups/{hero_id}")
            if rows is not None:
                data.matchups[hero_id] = rows
        return data


class DataUpdater:
    """Обновляет кеш из OpenDota. Запускается в фоновом потоке, ошибки сети не страшны."""

    def __init__(self, cache: DataCache, client: OpenDotaClient | None = None):
        self.cache = cache
        self.client = client or OpenDotaClient()

    def _refresh(self, name: str, fetch) -> bool:
        if self.cache.is_fresh(name):
            return False
        try:
            self.cache.save(name, fetch())
            log.info("Обновлено из OpenDota: %s", name)
            return True
        except OpenDotaError:
            log.warning("Не удалось обновить %s, работаю из кеша", name, exc_info=True)
            return False

    def refresh_core(self) -> None:
        """Герои, предметы, описания способностей, винрейты: 5 запросов."""
        self._refresh("heroes", self.client.heroes)
        self._refresh("items", self.client.items)
        self._refresh("abilities", lambda: self.client.get_json("/constants/abilities"))
        self._refresh("hero_abilities", lambda: self.client.get_json("/constants/hero_abilities"))
        self._refresh("hero_stats", self.client.hero_stats)

    def refresh_images(self, data: GameData) -> int:
        """Докачать недостающие иконки героев и предметов."""
        todo = [("heroes", h.name, h.img) for h in data.heroes.values() if h.img]
        todo += [("items", i.key, i.img) for i in data.items.values() if i.img]
        downloaded = 0
        for kind, key, url in todo:
            path = self.cache.image_path(kind, key)
            if path.exists():
                continue
            try:
                path.write_bytes(self.client.download(url))
                downloaded += 1
            except OpenDotaError:
                log.warning("Не скачалась иконка %s/%s", kind, key)
        if downloaded:
            log.info("Скачано иконок: %d", downloaded)
        return downloaded

    def refresh_matchups(self, data: GameData, stop=lambda: False) -> None:
        """Матчапы всех героев (~130 запросов, ~2.5 мин). stop() прерывает, например, когда начался матч."""
        for hero_id in data.heroes:
            if stop():
                return
            self._refresh(f"matchups/{hero_id}", lambda h=hero_id: self.client.matchups(h))

    def fetch_rank(self, account_id: str) -> int | None:
        """rank_tier игрока (например, 54 = Легенда 4). None, если профиль скрыт или нет сети."""
        try:
            return self.client.player(account_id).get("rank_tier")
        except OpenDotaError:
            log.warning("Не удалось узнать ранг", exc_info=True)
            return None
