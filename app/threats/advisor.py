"""Выбор контр-предмета: слой 1 (механики) даёт кандидатов, слой 2 (статистика) выбирает лучший.

Ступеньки (п. 6.6 ТЗ), по числу игр в выборке:
1. прибавка против этого героя врага (или против его предмета);
2. иначе прибавка против врагов с той же механикой;
3. иначе лучший кандидат по винрейту на герое пользователя в целом.
Метрика прибавки: винрейт предмета на герое пользователя в таких играх минус винрейт этого
же предмета на этом герое в целом. Кандидаты, которые на этом герое почти не покупают, отсеиваются.
"""
from dataclasses import dataclass

from app.threats.mechanics import MechanicRule, MechanicsTagger
from app.threats.stats import Stats

TIER_HERO, TIER_TRAIT, TIER_BASE, TIER_MECHANIC = "hero", "trait", "base", "mechanic"


@dataclass(frozen=True)
class Suggestion:
    item: str
    rule: MechanicRule
    tier: str  # hero / trait / base / mechanic (без статистики)
    delta: float | None  # прибавка винрейта в процентных пунктах (для hero и trait)
    games: int
    winrate: float | None


def _wr(cell: tuple[int, int]) -> float | None:
    games, wins = cell
    return wins / games * 100 if games else None


class Advisor:
    def __init__(self, tagger: MechanicsTagger, stats: Stats, buyable: set[str], item_costs: dict[str, int],
                 min_games: int, min_base_games: int):
        self.tagger = tagger
        self.stats = stats
        self.buyable = buyable
        self.item_costs = item_costs
        self.min_games = min_games
        self.min_base_games = min_base_games

    def suggest(self, rules: list[MechanicRule], my_hero_id: int | None, bucket: str,
                enemy_hero_id: int | None = None, enemy_item: str | None = None,
                owned: set[str] = frozenset(), limit: int = 2) -> list[Suggestion]:
        """Лучшие ответы на механики врага для героя пользователя."""
        found: dict[str, Suggestion] = {}
        for rule in rules:
            for item in self.tagger.candidate_items(rule, self.buyable) - set(owned):
                suggestion = self._score(item, rule, my_hero_id, bucket, enemy_hero_id, enemy_item)
                if suggestion and (item not in found or _rank(suggestion) > _rank(found[item])):
                    found[item] = suggestion
        # дешёвые первыми, когда статистики нет
        return sorted(found.values(), key=lambda x: (_rank(x), -self.cost(x.item)), reverse=True)[:limit]

    def _score(self, item, rule, my_hero_id, bucket, enemy_hero_id, enemy_item) -> Suggestion | None:
        if my_hero_id is None or not self.stats.available:
            # статистики нет: только механически подходящие предметы, дешёвые первыми
            return Suggestion(item, rule, TIER_MECHANIC, None, 0, None)
        base = self.stats.base(bucket, my_hero_id, item)
        if base[0] < self.min_base_games:
            return None  # на этом герое предмет почти не собирают
        base_wr = _wr(base)
        levels = []
        if enemy_item:
            levels.append((TIER_HERO, self.stats.vs_item(bucket, my_hero_id, enemy_item, item)))
        if enemy_hero_id is not None:
            levels.append((TIER_HERO, self.stats.vs_hero(bucket, my_hero_id, enemy_hero_id, item)))
        levels.append((TIER_TRAIT, self.stats.vs_trait(bucket, my_hero_id, rule.enemy, item)))
        for tier, cell in levels:
            if cell[0] >= self.min_games:
                return Suggestion(item, rule, tier, round(_wr(cell) - base_wr, 1), cell[0], _wr(cell))
        return Suggestion(item, rule, TIER_BASE, None, base[0], base_wr)

    def cost(self, item: str) -> int:
        return self.item_costs.get(item, 0)


_TIER_ORDER = {TIER_HERO: 3, TIER_TRAIT: 2, TIER_BASE: 1, TIER_MECHANIC: 0}


def _rank(s: Suggestion):
    """Сначала предметы без минуса, потом более точная ступенька, внутри — большая прибавка (или винрейт)."""
    value = s.delta if s.delta is not None else (s.winrate or 0) / 100
    return (s.delta is None or s.delta >= 0), _TIER_ORDER[s.tier], value
