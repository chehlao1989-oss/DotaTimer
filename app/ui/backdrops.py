"""Картинки фона с официального сервера Valve (выбор автора 15.10: «Б в катке, А вне катки»).

Вне катки — панорама карты Доты, в катке — рендер героя игрока. Картинки скачиваются у игрока с CDN Valve
(как иконки героев и предметов) и лежат в кеше программы — в репозиторий и сборку они не входят.
Скачивание — в отдельном потоке (CLAUDE.md: сеть не в потоке интерфейса); готовую картинку сообщает сигнал ready.
"""
import logging
import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from app import paths

log = logging.getLogger(__name__)

CDN = "https://cdn.cloudflare.steamstatic.com/apps/dota2"
MAP_URL = f"{CDN}/images/dota_react/home/radiant_dire5.jpg"  # панорама карты, 4000×1680 (проверено 15.10)
# рендер героя по внутреннему имени (npc_dota_hero_<имя>): проверено 15.10 на 17 героях, в том числе nevermore, zuus
HERO_URL = CDN + "/videos/dota_react/heroes/renders/{hero}.png"
KIND_MAP, KIND_HERO = "map", "hero"


def cache_dir() -> Path:
    folder = paths.cache_dir() / "backgrounds"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def hero_key(hero_name: str | None) -> str | None:
    """npc_dota_hero_drow_ranger → drow_ranger."""
    return hero_name.removeprefix("npc_dota_hero_") if hero_name else None


def source(kind: str, hero: str | None = None) -> tuple[str, Path]:
    """(адрес на сервере Valve, файл в кеше)."""
    if kind == KIND_HERO:
        return HERO_URL.format(hero=hero), cache_dir() / f"hero_{hero}.png"
    return MAP_URL, cache_dir() / "map.jpg"


class Backdrops(QObject):
    """Достаёт картинку из кеша или скачивает её; ready(вид, путь) — когда файл готов."""

    ready = Signal(str, str)

    def __init__(self, fetch=None):
        super().__init__()
        self._fetch = fetch or self._download
        self._busy: set[str] = set()

    def request(self, kind: str, hero: str | None = None) -> None:
        url, path = source(kind, hero)
        if path.is_file():
            self.ready.emit(kind, str(path))
            return
        if url in self._busy:
            return
        self._busy.add(url)

        def work():
            try:
                self._fetch(url, path)
                self.ready.emit(kind, str(path))
            except Exception:  # без фона программа работает: останется прежний фон
                log.warning("Фон не скачался: %s", url, exc_info=True)
            finally:
                self._busy.discard(url)

        threading.Thread(target=work, name="backdrop", daemon=True).start()

    @staticmethod
    def _download(url: str, path: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "DotaTimer"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)


class BackgroundSwitcher:
    """В катке — рендер героя игрока, вне катки — карта (выбор автора 15.10). Каждый пакет GSI — on_state."""

    def __init__(self, widget, backdrops: Backdrops):
        self.widget, self.backdrops = widget, backdrops
        self.wanted: tuple[str, str | None] = (KIND_MAP, None)
        backdrops.ready.connect(self._on_ready)
        backdrops.request(KIND_MAP)

    def on_state(self, state) -> None:
        hero = hero_key(state.hero_name) if state.in_match else None
        wanted = (KIND_HERO, hero) if hero else (KIND_MAP, None)
        if wanted != self.wanted:
            self.wanted = wanted
            self.backdrops.request(*wanted)

    def _on_ready(self, kind: str, path: str) -> None:
        # докачалась уже не нужная картинка (катка кончилась, пока качался герой) — не ставим
        if source(*self.wanted)[1] == Path(path):
            self.widget.set_auto(kind, path)
