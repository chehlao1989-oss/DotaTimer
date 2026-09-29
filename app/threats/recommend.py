"""Модуль рекомендаций: что собрать против главных угроз прямо сейчас (п. 6.4, 6.6 ТЗ).

recommend(my_hero, threats, known_enemy_items, my_items, game_time, my_gold):
1. кандидаты — предметы, механически отвечающие на механики угрозы (слой 1: её способности
   и её известные предметы);
2. отсев: уже куплено (показывается галочкой, дальше следующий), на моём герое предмет
   покупают реже min_buy_share, не по фазе игры (слишком дорогой для текущей минуты);
3. ранжирование по прибавке винрейта (слой 2) с поправкой на надёжность выборки:
   берётся нижняя граница «прибавка − z·ошибка», плюс бонус, если предмет работает
   против нескольких угроз сразу;
4. гистерезис: совет меняется, только если новый лучше старого больше чем на hysteresis;
   тогда changed=True — оверлей подсвечивает иконку и говорит «Совет по предметам».
"""
import math
from dataclasses import dataclass, field

from app.threats.mechanics import MechanicsTagger
from app.threats.stats import Stats

TIER_HERO, TIER_ITEM, TIER_TRAIT, TIER_BASE, TIER_MECHANIC = "hero", "item", "trait", "base", "mechanic"


@dataclass(frozen=True)
class RecommendConfig:
    min_games: int  # сколько игр нужно в выборке «против», чтобы ей доверять
    min_base_games: int
    min_buy_share: float
    z: float
    multi_threat_bonus: float
    hysteresis: float
    phase_base_cost: int
    phase_cost_per_min: int
    per_threat: int
    base_tier_scale: float


@dataclass(frozen=True)
class ThreatInput:
    hero: str  # ключ героя, например "alchemist"
    hero_id: int
    items: frozenset[str] = frozenset()  # что у него видели


@dataclass(frozen=True)
class RecItem:
    item: str
    score: float  # по чему ранжировали (нижняя граница прибавки + бонус)
    tier: str
    delta: float | None  # прибавка винрейта, п.п.
    games: int
    bought: bool = False
    changed: bool = False  # совет сменился — подсветить и сказать
    shared: int = 1  # против скольких угроз работает
    affordable: bool = False  # хватает золота купить прямо сейчас


@dataclass(frozen=True)
class ThreatRecommendation:
    hero: str
    items: tuple[RecItem, ...]  # сначала купленные (галочка), потом советы


def lower_bound_delta(cell: tuple[int, int], base: tuple[int, int], z: float) -> tuple[float, float]:
    """(прибавка, нижняя граница прибавки) в процентных пунктах."""
    n, w = cell
    nb, wb = base
    p, pb = w / n, wb / nb
    se = math.sqrt(p * (1 - p) / n + pb * (1 - pb) / nb) * 100
    delta = (p - pb) * 100
    return delta, delta - z * se


@dataclass
class Recommender:
    tagger: MechanicsTagger
    stats: Stats
    buyable: set[str]
    costs: dict[str, int]
    config: RecommendConfig
    bucket: str = "normal"
    _previous: dict[str, str] = field(default_factory=dict)  # угроза → текущий совет (для гистерезиса)

    def reset(self) -> None:
        """Новый матч."""
        self._previous.clear()

    # --- кандидаты ---
    def _threat_rules(self, threat: ThreatInput):
        traits = set(self.tagger.hero_traits(threat.hero))
        for item in threat.items:
            traits |= self.tagger.item_traits(item)
        return self.tagger.rules_for_traits(frozenset(traits))

    def _phase_ok(self, item: str, game_time: int) -> bool:
        minutes = max(0, game_time) / 60
        return self.costs.get(item, 0) <= self.config.phase_base_cost + self.config.phase_cost_per_min * minutes

    def _buy_share_ok(self, my_hero_id: int, item: str) -> bool:
        if not self.stats.available:
            return True
        hero_games = self.stats.hero(self.bucket, my_hero_id)[0]
        base_games = self.stats.base(self.bucket, my_hero_id, item)[0]
        if not hero_games:
            return base_games >= self.config.min_base_games
        return base_games / hero_games >= self.config.min_buy_share and base_games >= self.config.min_base_games

    # --- оценка ---
    def _score(self, my_hero_id: int, threat: ThreatInput, item: str, rules) -> tuple[float, str, float | None, int]:
        """(оценка, ступенька, прибавка, игр) предмета против одной угрозы."""
        if not self.stats.available:
            return -self.costs.get(item, 0) / 100_000, TIER_MECHANIC, None, 0  # без статистики — дешёвые первыми
        base = self.stats.base(self.bucket, my_hero_id, item)
        cells = [(TIER_HERO, self.stats.vs_hero(self.bucket, my_hero_id, threat.hero_id, item))]
        cells += [(TIER_ITEM, self.stats.vs_item(self.bucket, my_hero_id, j, item)) for j in sorted(threat.items)
                  if self.tagger.item_traits(j)]
        answered = {r.enemy for r in rules if self.tagger.item_answers(item) & set(r.answers)}
        cells += [(TIER_TRAIT, self.stats.vs_trait(self.bucket, my_hero_id, t, item)) for t in sorted(answered)]
        best = None
        for tier, cell in cells:
            if cell[0] >= self.config.min_games and base[0]:
                delta, low = lower_bound_delta(cell, base, self.config.z)
                if best is None or low > best[0]:
                    best = (low, tier, delta, cell[0])
        if best:
            return best
        wr = base[1] / base[0] * 100 if base[0] else 50
        return self.config.base_tier_scale * (wr - 50), TIER_BASE, None, base[0]

    # --- главное ---
    def recommend(self, my_hero_id: int | None, threats: list[ThreatInput], my_items: set[str],
                  game_time: int, my_gold: int | None = None) -> list[ThreatRecommendation]:
        if my_hero_id is None:
            return []
        per_threat: dict[str, dict[str, tuple]] = {}
        owned_candidates: dict[str, list[str]] = {}
        for threat in threats:
            rules = self._threat_rules(threat)
            candidates = set()
            for rule in rules:
                candidates |= self.tagger.candidate_items(rule, self.buyable)
            owned_candidates[threat.hero] = sorted(candidates & set(my_items))
            scored = {}
            for item in candidates - set(my_items):
                if not self._phase_ok(item, game_time) or not self._buy_share_ok(my_hero_id, item):
                    continue
                scored[item] = self._score(my_hero_id, threat, item, rules)
            per_threat[threat.hero] = scored
        # бонус за предмет против нескольких угроз: считаем только угрозы, против которых
        # есть статистика (не «в целом») и нижняя граница прибавки не отрицательная
        shared = {}
        for scored in per_threat.values():
            for item, (score, tier, *_rest) in scored.items():
                if score >= 0 and tier in (TIER_HERO, TIER_ITEM, TIER_TRAIT):
                    shared[item] = shared.get(item, 0) + 1
        result = []
        for threat in threats:
            scored = per_threat[threat.hero]
            ranked = sorted(scored, key=lambda i: (self._key(i, scored, shared), -self.costs.get(i, 0)), reverse=True)
            ranked = self._apply_hysteresis(threat.hero, ranked, scored, shared)
            items = [RecItem(i, 0.0, "bought", None, 0, bought=True) for i in owned_candidates[threat.hero]]
            for index, item in enumerate(ranked[:self.config.per_threat]):
                score, tier, delta, games = scored[item]
                changed = index == 0 and self._changed(threat.hero, item)
                items.append(RecItem(item, round(self._key(item, scored, shared), 2), tier, delta, games,
                                     changed=changed, shared=shared.get(item, 1),
                                     affordable=my_gold is not None and my_gold >= self.costs.get(item, 0)))
            if ranked:
                self._previous[threat.hero] = ranked[0]
            result.append(ThreatRecommendation(threat.hero, tuple(items)))
        return result

    def _key(self, item, scored, shared) -> float:
        return scored[item][0] + self.config.multi_threat_bonus * (max(1, shared.get(item, 1)) - 1)

    def _apply_hysteresis(self, hero, ranked, scored, shared) -> list[str]:
        previous = self._previous.get(hero)
        if not ranked or previous is None or previous not in scored or ranked[0] == previous:
            return ranked
        if self._key(ranked[0], scored, shared) < self._key(previous, scored, shared) + self.config.hysteresis:
            return [previous] + [i for i in ranked if i != previous]
        return ranked

    def _changed(self, hero: str, item: str) -> bool:
        """Совет сменился (не первый показ). Если прошлый совет купили — это тоже смена."""
        previous = self._previous.get(hero)
        return previous is not None and previous != item


def recommend_config(raw: dict, min_games: int, min_base_games: int) -> RecommendConfig:
    r = raw["recommend"]
    return RecommendConfig(min_games, min_base_games, r["min_buy_share"], r["z"], r["multi_threat_bonus"],
                           r["hysteresis"], r["phase_base_cost"], r["phase_cost_per_min"], r["per_threat"],
                           r["base_tier_scale"])
