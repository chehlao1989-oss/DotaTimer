"""Подсказки v2 (docs/TASK_ITEM_HINTS_V2.md, ок автора 16.10): что брать против предмета врага и против героя.

Слой «механика» решает, ЧТО может быть ответом:
- предмет врага → его свойства (data/item_counters.json, каждая строка подтверждена страницей Dota 2 Wiki —
  data/item_counters_checked.json) → тип ответа по утверждённым правилам (data/mechanics.json) → контр-предметы;
- герой-угроза → контр-предметы со страницы «<Герой>/Counters» Dota 2 Wiki (data/benchmark_counters.json,
  решение автора 16.10).
Слой «твой герой» решает только, КАКИЕ 1–2 из них показать: те, что реально собирают на твоём герое в роли
(обычная доля покупки из recs.zip, не A). Ни один не берут хотя бы min_own_share — показывается самый
универсальный с пометкой «редко на твоём герое».
Статистика A/B здесь не участвует: по финальным инвентарям она путала «у врага дорогой предмет» с «игра длинная»
(BUGLOG №54).
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
WIKI_HERO = "wiki_hero"  # тип ответа «со страницы Counters героя»


@dataclass(frozen=True)
class Counter:
    item: str  # контр-предмет
    answer: str  # тип ответа (heal_reduction…) или WIKI_HERO
    prop: str | None  # свойство врага, на которое он отвечает (для героя — None)


@dataclass(frozen=True)
class Choice:
    item: str
    answer: str
    share: float | None  # доля покупки на моём герое, % игр; None — неизвестно
    rare: bool = False  # ни один ответ не берут на моём герое — показан самый универсальный
    prop: str | None = None  # на какое свойство врага отвечает (для причины словами)


class CounterTable:
    def __init__(self, table: dict, checked: dict, rules: dict[str, tuple[str, ...]], hero_counters: dict,
                 consumables: set[str] = frozenset()):
        """rules: свойство врага → типы ответа (из правил mechanics.json). hero_counters: герой → контр-предметы.
        consumables: расходники — со страниц Counters не советуем (Town Portal Scroll у всех и так; по доле покупки
        ~100% он встал бы первым), кроме тех, что прямо стоят в таблице ответов (Dust, Sentry Ward, Gem)."""
        self.rules = rules
        verified = checked.get("threat_items", {})
        self._props = {item: tuple(p for p in spec["props"] if verified.get(item, {}).get(p, {}).get("verified"))
                       for item, spec in table.get("threat_items", {}).items()}
        self._props = {k: v for k, v in self._props.items() if v}
        ok = checked.get("counters", {})
        self._counters = {answer: tuple(k for k in keys if ok.get(answer, {}).get(k, {}).get("verified"))
                          for answer, keys in table.get("counters", {}).items()}
        self._sources = {(answer, k): row.get("source") for answer, rows in ok.items() for k, row in rows.items()}
        allowed = {k for keys in self._counters.values() for k in keys}
        skip = set(consumables) - allowed
        self.hero_counters = {h: tuple(k for k in keys if k not in skip) for h, keys in hero_counters.items()}

    @classmethod
    def load(cls, mechanics_config: dict, data_dir: Path = DATA_DIR,
             consumables: set[str] = frozenset()) -> "CounterTable | None":
        try:
            table = json.loads((data_dir / "item_counters.json").read_text(encoding="utf-8"))
            checked = json.loads((data_dir / "item_counters_checked.json").read_text(encoding="utf-8"))
            wiki = json.loads((data_dir / "benchmark_counters.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("Таблица подсказок v2 не прочитана — подсказки по старой схеме", exc_info=True)
            return None
        rules = {}
        for rule in mechanics_config.get("rules", []):
            rules.setdefault(rule["enemy"], ())
            rules[rule["enemy"]] += tuple(rule["answers"])
        heroes = {h: tuple(v.get("counter_items", ())) for h, v in wiki.get("heroes", {}).items()}
        return cls(table, checked, rules, heroes, consumables)

    def is_threat(self, item: str) -> bool:
        """Есть ли подсказка на этот предмет врага (только подтверждённые свойства)."""
        return item in self._props

    def threat_items(self) -> set[str]:
        return set(self._props)

    def props(self, item: str) -> tuple[str, ...]:
        return self._props.get(item, ())

    def vs_item(self, item: str) -> list[Counter]:
        """Контр-предметы на предмет врага: по его свойствам, в порядке таблицы."""
        out = []
        for prop in self.props(item):
            for answer in self.rules.get(prop, ()):
                out += [Counter(k, answer, prop) for k in self._counters.get(answer, ())]
        return out

    def vs_hero(self, hero: str) -> list[Counter]:
        """Контр-предметы на героя-угрозу: страница Counters вики, в порядке страницы."""
        return [Counter(k, WIKI_HERO, None) for k in self.hero_counters.get(hero, ())]

    def source(self, counter: Counter) -> str | None:
        return self._sources.get((counter.answer, counter.item))


def pick_parent(shares: dict[str, float], candidates: list[str]) -> str | None:
    """Во что враг соберёт деталь: тот из предметов, который этот герой покупает чаще (доля покупки);
    ни один не покупает — None: лучше промолчать, чем угадать (BUGLOG №54)."""
    known = [c for c in candidates if shares.get(c)]
    return max(known, key=lambda c: (shares[c], -candidates.index(c))) if known else None


def choose(counters: list[Counter], share: Callable[[str], float | None], owned: Callable[[str], bool],
           min_share: float, limit: int = 2) -> list[Choice]:
    """1–2 ответа, которые реально собирают на моём герое: по доле покупки, от min_share (% игр).

    Уже купленные пропускаются. Ни один не набирает min_share — самый универсальный (закрывает больше свойств
    врага, при равенстве — раньше в таблице) с пометкой rare."""
    first: dict[str, Counter] = {}
    covers: dict[str, set] = {}
    for c in counters:
        if owned(c.item):
            continue
        first.setdefault(c.item, c)
        covers.setdefault(c.item, set()).add(c.prop or c.answer)
    if not first:
        return []
    order = list(first)
    shares = {k: share(k) for k in order}
    good = [k for k in order if (shares[k] or 0.0) >= min_share]
    if good:
        good.sort(key=lambda k: (-(shares[k] or 0.0), order.index(k)))
        return [Choice(k, first[k].answer, shares[k], prop=first[k].prop) for k in good[:limit]]
    best = max(order, key=lambda k: (len(covers[k]), -order.index(k)))
    return [Choice(best, first[best].answer, shares[best], rare=True, prop=first[best].prop)]
