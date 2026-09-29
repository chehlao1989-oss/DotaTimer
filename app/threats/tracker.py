"""Память о предметах врагов и подсказки только по факту (п. 6.4 ТЗ).

- у угрозы появился новый предмет с важной механикой (слой 1) → «У X появился Y → ответ»;
- у врага появился компонент крупного предмета с важной механикой → «X собирает Y (есть <компонент>)»;
- расходники, варды, TP и предметы без важной механики игнорируются;
- одна и та же подсказка про одного врага не повторяется.
Какой предмет советовать в ответ, решает Advisor (слой 2).
"""
import logging
from dataclasses import dataclass, field

from app.threats.data import GameData
from app.threats.mechanics import MechanicRule, MechanicsTagger

log = logging.getLogger(__name__)

IGNORED_QUALITIES = {"consumable"}
KIND_ITEM = "item"
KIND_BUILDING = "building"


@dataclass(frozen=True)
class ItemHint:
    kind: str  # item — предмет уже есть; building — собирает (есть компонент)
    hero: str  # ключ героя
    item: str  # предмет с важной механикой
    component: str | None
    rules: tuple[MechanicRule, ...]  # какие механики он даёт врагу


@dataclass
class EnemyItems:
    items: dict[str, int] = field(default_factory=dict)  # предмет → игровое время, когда впервые увидели
    last_seen: int | None = None


class ItemTracker:
    def __init__(self, data: GameData, tagger: MechanicsTagger, min_component_cost: int):
        self.data = data
        self.tagger = tagger
        self.enemies: dict[str, EnemyItems] = {}
        self._said: set[tuple[str, str, str]] = set()  # (вид, герой, предмет) — уже подсказано
        self._important = {k for k, item in data.items.items() if item.cost > 0 and item.qual not in IGNORED_QUALITIES
                           and not k.startswith("recipe") and self.rules_for(k)}
        # компонент → важные предметы, в которые он входит (с учётом компонентов компонентов)
        self._parents: dict[str, set[str]] = {}
        for big in self._important:
            for component in self._components(big):
                item = data.items.get(component)
                if item and item.cost >= min_component_cost and component not in self._important:
                    self._parents.setdefault(component, set()).add(big)

    def rules_for(self, item: str) -> list[MechanicRule]:
        return self.tagger.rules_for_traits(self.tagger.item_traits(item))

    def _components(self, key: str, depth: int = 2) -> set[str]:
        item = self.data.items.get(key)
        if item is None or depth == 0:
            return set()
        found = set(item.components)
        for component in item.components:
            found |= self._components(component, depth - 1)
        return found

    def reset(self) -> None:
        """Новый матч."""
        self.enemies.clear()
        self._said.clear()

    def _ignored(self, key: str) -> bool:
        item = self.data.items.get(key)
        return item is None or item.qual in IGNORED_QUALITIES

    def update(self, hero: str, items: set[str], clock: int, threats: set[str]) -> list[ItemHint]:
        """Новый снимок инвентаря врага. Возвращает подсказки (только для угроз)."""
        enemy = self.enemies.setdefault(hero, EnemyItems())
        enemy.last_seen = clock
        new = [i for i in sorted(items) if i not in enemy.items and not self._ignored(i)]
        for item in new:
            enemy.items[item] = clock
        if new:
            log.info("У %s новые предметы: %s", hero, new)
        if hero not in threats:
            return []
        hints = []
        have = set(enemy.items)
        for item in new:
            if item in self._important:
                hint = self._hint(KIND_ITEM, hero, item, None)
            else:
                candidates = [b for b in self._parents.get(item, ()) if b not in have]
                if not candidates:
                    continue
                # компонент может входить в несколько важных предметов — берём самый дорогой
                big = max(sorted(candidates), key=lambda b: self.data.items[b].cost)
                hint = self._hint(KIND_BUILDING, hero, big, item)
            if hint:
                hints.append(hint)
        return hints

    def _hint(self, kind, hero, item, component) -> ItemHint | None:
        key = (kind, hero, item)
        # если уже сказали «собирает», то о готовом предмете скажем ещё раз, а наоборот — нет
        if key in self._said or (kind == KIND_BUILDING and (KIND_ITEM, hero, item) in self._said):
            return None
        self._said.add(key)
        return ItemHint(kind, hero, item, component, tuple(self.rules_for(item)))
