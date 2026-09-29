"""Память о предметах врагов и подсказки только по факту (п. 6.4 ТЗ).

- у угрозы появился новый важный предмет (есть в counters.json) → «У X появился Y → контры»;
- у врага появился компонент крупного важного предмета → «X собирает Y (есть <компонент>)»;
- расходники, варды, TP и предметы, которых нет в counters.json, игнорируются;
- одна и та же подсказка про одного врага не повторяется.
"""
import logging
from dataclasses import dataclass, field

from app.threats.counters import Counter, CounterTable
from app.threats.data import GameData

log = logging.getLogger(__name__)

IGNORED_QUALITIES = {"consumable"}
KIND_ITEM = "item"
KIND_BUILDING = "building"


@dataclass(frozen=True)
class ItemHint:
    kind: str  # item — предмет уже есть; building — собирает (есть компонент)
    hero: str  # ключ героя
    item: str  # важный предмет
    component: str | None
    counters: tuple[Counter, ...]


@dataclass
class EnemyItems:
    items: dict[str, int] = field(default_factory=dict)  # предмет → игровое время, когда впервые увидели
    last_seen: int | None = None


class ItemTracker:
    def __init__(self, data: GameData, counters: CounterTable, min_component_cost: int, counters_per_hint: int):
        self.data = data
        self.counters = counters
        self.counters_per_hint = counters_per_hint
        self.enemies: dict[str, EnemyItems] = {}
        self._said: set[tuple[str, str, str]] = set()  # (вид, герой, предмет) — уже подсказано
        # компонент → важные предметы, в которые он входит (с учётом компонентов компонентов)
        self._parents: dict[str, set[str]] = {}
        for big in counters.trigger_items:
            for component in self._components(big):
                item = data.items.get(component)
                if item and item.cost >= min_component_cost:
                    self._parents.setdefault(component, set()).add(big)

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

    def update(self, hero: str, items: set[str], clock: int, threats: set[str], user_role: str,
               owned: set[str]) -> list[ItemHint]:
        """Новый снимок инвентаря врага. Возвращает подсказки (только для угроз)."""
        enemy = self.enemies.setdefault(hero, EnemyItems())
        enemy.last_seen = clock
        new = [i for i in items if i not in enemy.items and not self._ignored(i)]
        for item in new:
            enemy.items[item] = clock
        if new:
            log.info("У %s новые предметы: %s", hero, new)
        if hero not in threats:
            return []
        hints = []
        have = set(enemy.items)
        for item in new:
            rules = self.counters.rules_for_item(item)
            if rules:
                hint = self._hint(KIND_ITEM, hero, item, None, rules, user_role, owned)
                if hint:
                    hints.append(hint)
                continue
            # компонент может входить в несколько важных предметов — подсказываем про самый важный
            candidates = [b for b in self._parents.get(item, ()) if b not in have]
            if not candidates:
                continue
            big = max(sorted(candidates), key=lambda b: self.counters.rules_for_item(b)[0].priority)
            hint = self._hint(KIND_BUILDING, hero, big, item, self.counters.rules_for_item(big), user_role, owned)
            if hint:
                hints.append(hint)
        return hints

    def _hint(self, kind, hero, item, component, rules, user_role, owned) -> ItemHint | None:
        key = (kind, hero, item)
        # если уже сказали «собирает», то о готовом предмете скажем ещё раз, а наоборот — нет
        if key in self._said or (kind == KIND_BUILDING and (KIND_ITEM, hero, item) in self._said):
            return None
        self._said.add(key)
        picked = self.counters.pick_counters(rules, user_role, owned, self.counters_per_hint)
        return ItemHint(kind, hero, item, component, tuple(picked))
