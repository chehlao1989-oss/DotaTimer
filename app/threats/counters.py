"""Таблица контров data/counters.json и выбор контр-предметов под роль пользователя.

Формат правила:
{
  "id": "heal",
  "title_ru": "Сильное лечение",
  "triggers": {"items": ["heart", ...], "heroes": ["alchemist", ...]},
  "counters": [{"item": "spirit_vessel", "roles": ["support"], "why_ru": "..."}],
  "priority": 2,
  "checked_patch": "7.41f",
  "verified": true
}
Роли контр-предмета: "core", "support" или "any".
В разделе "heroes" — пометки героев для оценки угроз, например {"medusa": {"scaling": 3}}.
"""
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "counters.json"
ROLE_CORE, ROLE_SUPPORT, ROLE_ANY = "core", "support", "any"


@dataclass(frozen=True)
class Counter:
    item: str
    roles: tuple[str, ...]
    why_ru: str


@dataclass(frozen=True)
class Rule:
    id: str
    title_ru: str
    trigger_items: frozenset[str]
    trigger_heroes: frozenset[str]
    counters: tuple[Counter, ...]
    priority: int = 1
    checked_patch: str = ""
    verified: bool = True


@dataclass
class CounterTable:
    rules: list[Rule]
    heroes: dict[str, dict]

    @property
    def trigger_items(self) -> set[str]:
        return {item for rule in self.rules for item in rule.trigger_items}

    def rules_for_item(self, item: str) -> list[Rule]:
        return sorted((r for r in self.rules if item in r.trigger_items), key=lambda r: -r.priority)

    def rules_for_hero(self, hero: str) -> list[Rule]:
        return sorted((r for r in self.rules if hero in r.trigger_heroes), key=lambda r: -r.priority)

    def scaling(self, hero: str) -> int:
        return int(self.heroes.get(hero, {}).get("scaling", 0))

    def pick_counters(self, rules: list[Rule], user_role: str, owned: set[str] = frozenset(),
                      limit: int = 2) -> list[Counter]:
        """До limit контр-предметов: сначала подходящие роли пользователя, без уже купленных, без повторов."""
        picked: list[Counter] = []
        for suitable in (lambda c: user_role in c.roles, lambda c: ROLE_ANY in c.roles):
            for rule in rules:
                for counter in rule.counters:
                    if len(picked) >= limit:
                        return picked
                    if counter.item in owned or any(p.item == counter.item for p in picked):
                        continue
                    if suitable(counter):
                        picked.append(counter)
        return picked


def parse_counters(raw: dict) -> CounterTable:
    rules = []
    for r in raw.get("rules", []):
        rules.append(Rule(
            id=r["id"],
            title_ru=r["title_ru"],
            trigger_items=frozenset(r.get("triggers", {}).get("items", [])),
            trigger_heroes=frozenset(r.get("triggers", {}).get("heroes", [])),
            counters=tuple(Counter(c["item"], tuple(c.get("roles", [ROLE_ANY])), c.get("why_ru", ""))
                           for c in r.get("counters", [])),
            priority=r.get("priority", 1),
            checked_patch=r.get("checked_patch", ""),
            verified=r.get("verified", True),
        ))
    return CounterTable(rules, dict(raw.get("heroes", {})))


def load_counters(path: Path = DEFAULT_PATH) -> CounterTable:
    with open(path, encoding="utf-8") as f:
        return parse_counters(json.load(f))
