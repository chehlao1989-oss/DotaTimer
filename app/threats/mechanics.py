"""Слой 1: автоматическая разметка механик по официальным описаниям Valve (dotaconstants).

Метки врага — что есть у героя (по способностям) или у его предмета: лечение, уклонение и т.д.
Метки ответа — что умеет предмет: режет лечение, игнорирует уклонение и т.д.
Правила «механика врага → механика ответа» и шаблоны поиска лежат в data/mechanics.json.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "mechanics.json"
HERO_PREFIX = "npc_dota_hero_"


@dataclass(frozen=True)
class MechanicRule:
    id: str
    title_ru: str
    enemy: str  # метка врага
    answers: tuple[str, ...]  # метки ответа


def _compile(patterns):
    return [re.compile(p) for p in patterns or []]


class _TagSpec:
    def __init__(self, raw: dict):
        self.desc = _compile(raw.get("desc"))
        self.exclude = _compile(raw.get("exclude"))
        self.attrib = set(raw.get("attrib") or [])
        self.behavior = raw.get("behavior")
        self.min_count = raw.get("min_count", 1)
        self.max_cost = raw.get("max_cost")
        self.active_only = raw.get("active_only", False)

    def text_hits(self, texts: list[str]) -> int:
        """Сколько описаний подходит (с учётом исключений)."""
        hits = 0
        for text in texts:
            if any(p.search(text) for p in self.desc) and not any(p.search(text) for p in self.exclude):
                hits += 1
        return hits


def item_texts(raw_item: dict, active_only: bool = False) -> list[str]:
    return [(a.get("description") or "").lower() for a in raw_item.get("abilities") or []
            if not active_only or a.get("type") == "active"]


def item_attribs(raw_item: dict) -> set[str]:
    return {a.get("key") for a in raw_item.get("attrib") or [] if a.get("key")}


class MechanicsTagger:
    def __init__(self, config: dict, items: dict, abilities: dict, hero_abilities: dict,
                 hero_roles: dict[str, tuple[str, ...]] | None = None):
        """hero_roles: короткое имя героя → официальные роли Valve (Carry, Nuker, Escape...)."""
        self.hero_roles = hero_roles or {}
        self.role_tags = config.get("role_tags", {})
        self.rules = [MechanicRule(r["id"], r["title_ru"], r["enemy"], tuple(r["answers"])) for r in config["rules"]]
        self.enemy_specs = {k: _TagSpec(v) for k, v in config["enemy_tags"].items()}
        self.answer_specs = {k: _TagSpec(v) for k, v in config["answer_tags"].items()}
        self.items = items
        self.abilities = abilities
        self.hero_abilities = hero_abilities
        self._item_traits: dict[str, frozenset[str]] = {}
        self._item_answers: dict[str, frozenset[str]] = {}
        self._hero_traits: dict[str, frozenset[str]] = {}

    # --- враг ---
    def item_traits(self, key: str) -> frozenset[str]:
        """Метки врага у предмета: что даёт этот предмет своему владельцу."""
        if key not in self._item_traits:
            raw = self.items.get(key) or {}
            texts, attribs = item_texts(raw), item_attribs(raw)
            tags = set()
            for tag, spec in self.enemy_specs.items():
                if spec.behavior:
                    continue  # пассивки героя — только по способностям
                if spec.text_hits(texts) >= 1 or (spec.attrib & attribs):
                    tags.add(tag)
            self._item_traits[key] = frozenset(tags)
        return self._item_traits[key]

    def hero_traits(self, hero: str) -> frozenset[str]:
        """Метки врага у героя по его способностям."""
        hero = hero.removeprefix(HERO_PREFIX)
        if hero not in self._hero_traits:
            names = _flatten((self.hero_abilities.get(HERO_PREFIX + hero) or {}).get("abilities") or [])
            abilities = [self.abilities.get(n) or {} for n in names if n and not n.startswith("generic_hidden")]
            texts = [(a.get("desc") or "").lower() for a in abilities]
            tags = set()
            for tag, spec in self.enemy_specs.items():
                if spec.behavior:
                    count = sum(1 for a in abilities if spec.behavior in _as_list(a.get("behavior")))
                else:
                    count = spec.text_hits(texts)
                if count >= spec.min_count:
                    tags.add(tag)
            for role in self.hero_roles.get(hero, ()):
                if role in self.role_tags:
                    tags.add(self.role_tags[role])
            self._hero_traits[hero] = frozenset(tags)
        return self._hero_traits[hero]

    # --- ответ ---
    def item_answers(self, key: str) -> frozenset[str]:
        """Метки ответа у предмета: против чего он работает."""
        if key not in self._item_answers:
            raw = self.items.get(key) or {}
            cost = raw.get("cost") or 0
            tags = set()
            for tag, spec in self.answer_specs.items():
                if spec.max_cost is not None and (not cost or cost > spec.max_cost):
                    continue
                if spec.text_hits(item_texts(raw, spec.active_only)) >= 1:
                    tags.add(tag)
            self._item_answers[key] = frozenset(tags)
        return self._item_answers[key]

    # --- правила ---
    def rules_for_traits(self, traits: frozenset[str]) -> list[MechanicRule]:
        return [r for r in self.rules if r.enemy in traits]

    def candidate_items(self, rule: MechanicRule, buyable: set[str]) -> set[str]:
        """Предметы, механически подходящие под правило (только те, что можно купить)."""
        return {k for k in buyable if self.item_answers(k) & set(rule.answers)}


def _flatten(names) -> list[str]:
    """Имена способностей: в данных OpenDota некоторые вложены списком (варианты способности)."""
    out = []
    for name in names:
        if isinstance(name, list):
            out.extend(_flatten(name))
        elif isinstance(name, str):
            out.append(name)
    return out


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def load_mechanics_config(path: Path = DEFAULT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
