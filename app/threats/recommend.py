"""Модуль рекомендаций: что собрать против главных угроз (п. 6.4, 6.6 ТЗ).

recommend(my_hero, threats, known_enemy_items, my_items, game_time, my_gold):
1. кандидаты — предметы, механически отвечающие на механики угрозы (слой 1: её способности
   и её известные предметы);
2. отсев: уже куплено (показывается галочкой, дальше следующий); на моём герое предмет
   покупают реже min_buy_share; не по фазе игры (слишком дорогой для текущей минуты);
3. главный сигнал — прибавка частоты покупки (решение автора 29.09.2026): доля игр с предметом
   против этого врага минус доля игр с предметом в целом на моём герое. Игроки сами докупают
   ответ, когда он нужен, — это видно по частоте, а винрейт на малых выборках шумит.
   Ранжирование по нижней границе прибавки (прибавка − z·ошибка). Нужно не меньше
   min_pair_games игр пары, и нижняя граница должна быть больше нуля;
4. винрейт — только фильтр: предмет выкидывается, если при wr_filter_games+ играх его прибавка
   винрейта значимо меньше нуля;
5. мало данных — совет по механике без процентов (tier="mechanic"), по доле покупок на герое;
6. бонус за предмет против нескольких угроз; гистерезис — совет меняется, только если новый
   заметно лучше; тогда changed=True (подсветка и голос «Совет по предметам»).
"""
import math
from dataclasses import dataclass, field

from app.threats.mechanics import MechanicsTagger
from app.threats.stats import Stats

TIER_HERO, TIER_ITEM, TIER_TRAIT, TIER_MECHANIC = "hero", "item", "trait", "mechanic"
STATS_TIERS = (TIER_HERO, TIER_ITEM, TIER_TRAIT)


@dataclass(frozen=True)
class RecommendConfig:
    min_pair_games: int  # игр пары «мой герой против врага», чтобы судить о частоте
    min_base_games: int  # сколько раз предмет вообще собирали на моём герое
    min_buy_share: float  # доля игр моего героя с этим предметом
    z: float
    wr_filter_games: int  # с этим числом игр предмета против врага проверяем, не вредит ли он
    multi_threat_bonus: float  # п.п. прибавки за каждую дополнительную угрозу
    hysteresis: float  # п.п.
    phase_base_cost: int
    phase_cost_per_min: int
    per_threat: int


@dataclass(frozen=True)
class ThreatInput:
    hero: str  # ключ героя, например "alchemist"
    hero_id: int
    items: frozenset[str] = frozenset()  # что у него видели


@dataclass(frozen=True)
class RecItem:
    item: str
    score: float  # по чему ранжировали
    tier: str  # hero / item / trait — из статистики; mechanic — мало данных; bought — уже есть
    delta: float | None  # прибавка частоты покупки, п.п. (только для tier из статистики)
    games: int  # игр пары (или против механики)
    reason: str | None = None  # механика ответа: heal_reduction, true_strike, ...
    bought: bool = False
    changed: bool = False  # совет сменился — подсветить и сказать
    shared: int = 1  # против скольких угроз работает
    affordable: bool = False  # хватает золота купить прямо сейчас


@dataclass(frozen=True)
class ThreatRecommendation:
    hero: str
    items: tuple[RecItem, ...]  # сначала купленные (галочка), потом советы


def format_delta(delta: float, zero: str, template: str) -> str:
    """+6% / −3% / ±0% (без «−0%»)."""
    return zero if abs(delta) < 0.5 else template.format(delta=delta)


def share_uplift(with_item: int, games: int, base_share: float, z: float) -> tuple[float, float]:
    """(прибавка доли, нижняя граница) в п.п.: доля игр с предметом среди `games` минус base_share."""
    share = with_item / games
    p = max(share, base_share, 0.01)  # дисперсию берём по большей доле — осторожнее
    se = math.sqrt(p * (1 - p) / games) * 100
    delta = (share - base_share) * 100
    return delta, delta - z * se


def winrate_harmful(cell: tuple[int, int], base: tuple[int, int], z: float, min_games: int) -> bool:
    """Значимо ли предмет снижает винрейт (только на больших выборках)."""
    n, w = cell
    nb, wb = base
    if n < min_games or not nb:
        return False
    p, pb = w / n, wb / nb
    se = math.sqrt(p * (1 - p) / n + pb * (1 - pb) / nb)
    return (p - pb) + z * se < 0


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

    def _base_share(self, my_hero_id: int, item: str) -> float | None:
        hero_games = self.stats.hero(self.bucket, my_hero_id)[0]
        return self.stats.base(self.bucket, my_hero_id, item)[0] / hero_games if hero_games else None

    def _buy_share_ok(self, my_hero_id: int, item: str) -> bool:
        if not self.stats.available:
            return True
        share = self._base_share(my_hero_id, item)
        base_games = self.stats.base(self.bucket, my_hero_id, item)[0]
        return share is not None and share >= self.config.min_buy_share and base_games >= self.config.min_base_games

    def _reason(self, item: str, rules) -> str | None:
        answers = self.tagger.item_answers(item)
        for rule in rules:
            for tag in rule.answers:
                if tag in answers:
                    return tag
        return None

    # --- оценка ---
    def _score(self, my_hero_id: int, threat: ThreatInput, item: str, rules):
        """(оценка, ступенька, прибавка, игр) предмета против одной угрозы; None — выкинуть."""
        cfg = self.config
        if not self.stats.available:
            return -self.costs.get(item, 0) / 100_000, TIER_MECHANIC, None, 0  # без статистики — дешёвые первыми
        base = self.stats.base(self.bucket, my_hero_id, item)
        base_share = self._base_share(my_hero_id, item) or 0.0
        cells = [(TIER_HERO, self.stats.vs_hero(self.bucket, my_hero_id, threat.hero_id, item),
                  self.stats.pair(self.bucket, my_hero_id, threat.hero_id))]
        cells += [(TIER_ITEM, self.stats.vs_item(self.bucket, my_hero_id, j, item),
                   self.stats.item_games(self.bucket, my_hero_id, j))
                  for j in sorted(threat.items) if self.tagger.item_traits(j)]
        answered = {r.enemy for r in rules if self.tagger.item_answers(item) & set(r.answers)}
        cells += [(TIER_TRAIT, self.stats.vs_trait(self.bucket, my_hero_id, t, item),
                   self.stats.trait_games(self.bucket, my_hero_id, t)) for t in sorted(answered)]
        best = None
        for tier, cell, games in cells:
            if winrate_harmful(cell, base, cfg.z, cfg.wr_filter_games):
                return None  # против этого врага предмет достоверно вредит
            if games[0] < cfg.min_pair_games:
                continue
            delta, low = share_uplift(cell[0], games[0], base_share, cfg.z)
            if low > 0 and (best is None or low > best[0]):
                best = (low, tier, delta, games[0])
        if best:
            return best
        # мало данных или нет значимой прибавки: совет по механике, чаще покупаемые на герое — первыми
        return base_share - 1.0, TIER_MECHANIC, None, 0

    # --- главное ---
    def recommend(self, my_hero_id: int | None, threats: list[ThreatInput], my_items: set[str],
                  game_time: int, my_gold: int | None = None) -> list[ThreatRecommendation]:
        if my_hero_id is None:
            return []
        per_threat: dict[str, dict[str, tuple]] = {}
        owned: dict[str, list[str]] = {}
        reasons: dict[tuple[str, str], str | None] = {}
        for threat in threats:
            rules = self._threat_rules(threat)
            candidates = set()
            for rule in rules:
                candidates |= self.tagger.candidate_items(rule, self.buyable)
            owned[threat.hero] = sorted(candidates & set(my_items))
            scored = {}
            for item in candidates - set(my_items):
                if not self._phase_ok(item, game_time) or not self._buy_share_ok(my_hero_id, item):
                    continue
                result = self._score(my_hero_id, threat, item, rules)
                if result is not None:
                    scored[item] = result
                    reasons[(threat.hero, item)] = self._reason(item, rules)
            for item in owned[threat.hero]:
                reasons[(threat.hero, item)] = self._reason(item, rules)
            per_threat[threat.hero] = scored
        # бонус за предмет против нескольких угроз: только там, где прибавка из статистики
        shared = {}
        for scored in per_threat.values():
            for item, (_score, tier, *_rest) in scored.items():
                if tier in STATS_TIERS:
                    shared[item] = shared.get(item, 0) + 1
        result = []
        for threat in threats:
            scored = per_threat[threat.hero]
            ranked = sorted(scored, key=lambda i: (self._key(i, scored, shared), -self.costs.get(i, 0)), reverse=True)
            ranked = self._apply_hysteresis(threat.hero, ranked, scored, shared)
            items = [RecItem(i, 0.0, "bought", None, 0, reasons.get((threat.hero, i)), bought=True)
                     for i in owned[threat.hero]]
            for index, item in enumerate(ranked[:self.config.per_threat]):
                _score, tier, delta, games = scored[item]
                changed = index == 0 and self._changed(threat.hero, item)
                items.append(RecItem(item, round(self._key(item, scored, shared), 2), tier, delta, games,
                                     reasons.get((threat.hero, item)), changed=changed,
                                     shared=shared.get(item, 1),
                                     affordable=my_gold is not None and my_gold >= self.costs.get(item, 0)))
            if ranked:
                self._previous[threat.hero] = ranked[0]
            result.append(ThreatRecommendation(threat.hero, tuple(items)))
        return result

    def _key(self, item, scored, shared) -> float:
        score, tier, *_rest = scored[item]
        bonus = self.config.multi_threat_bonus * (max(1, shared.get(item, 1)) - 1) if tier in STATS_TIERS else 0
        return score + bonus

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


def recommend_config(raw: dict) -> RecommendConfig:
    r = raw["recommend"]
    return RecommendConfig(r["min_pair_games"], r["min_base_games"], r["min_buy_share"], r["z"],
                           r["wr_filter_games"], r["multi_threat_bonus"], r["hysteresis"], r["phase_base_cost"],
                           r["phase_cost_per_min"], r["per_threat"])
