"""Угрозы по нашим матчам (п. 6.3 ТЗ, решение автора 29.09.2026). Роли Valve не используются.

Для каждого врага из статистики сыгранных матчей:
- фарм-приоритет — среднее место по нетворсу в своей команде (1 — керри, 5 — пятёрка);
- сила в лейте — винрейт в играх длиннее 40 минут минус в играх короче 30 минут;
- матчап — винрейт героя пользователя против него;
- общий винрейт.
Оценка — взвешенная сумма, веса в data/threats.json → threat_score. Причина — человеческим
текстом: «Керри, силён в долгих играх (56% после 40 мин)», «Ваш герой проигрывает ему (45%)».
"""
from dataclasses import dataclass, field

from app.threats.stats import Stats


@dataclass(frozen=True)
class ThreatScoreConfig:
    w_farm: float  # за каждое место выше среднего (3.0)
    w_late: float  # за каждый п.п. прибавки в долгих играх
    w_matchup: float  # за каждый п.п. проигрыша героя пользователя
    w_winrate: float  # за каждый п.п. винрейта выше 50%
    min_length_games: int  # сколько игр нужно в коротких и в долгих, чтобы судить о лейте
    min_pair_games: int  # сколько игр пары, чтобы судить о матчапе
    carry_rank: float  # среднее место не больше этого — «керри»
    core_rank: float  # не больше этого — «кор»
    late_notable: float  # прибавка в лейте, которую стоит упомянуть, п.п.
    matchup_notable: float  # проигрыш героя пользователя, который стоит упомянуть, п.п. ниже 50%


@dataclass(frozen=True)
class ThreatFacts:
    hero_id: int
    farm_rank: float | None
    late_delta: float | None
    late_winrate: float | None
    my_winrate: float | None  # винрейт героя пользователя против него
    winrate: float | None
    score: float
    reasons: tuple[str, ...] = field(default=())  # ключи причин: carry/core/support, late, matchup, winrate


def _wr(cell: tuple[int, int]) -> float | None:
    return cell[1] / cell[0] * 100 if cell[0] else None


def facts(stats: Stats, bucket: str, enemy_id: int, my_hero_id: int | None, cfg: ThreatScoreConfig) -> ThreatFacts:
    farm = stats.farm_rank(bucket, enemy_id)
    short, long = stats.length(bucket, enemy_id, "short"), stats.length(bucket, enemy_id, "long")
    late_delta = late_wr = None
    if short[0] >= cfg.min_length_games and long[0] >= cfg.min_length_games:
        late_wr = _wr(long)
        late_delta = late_wr - _wr(short)
    my_wr = None
    if my_hero_id is not None:
        pair = stats.pair(bucket, my_hero_id, enemy_id)
        if pair[0] >= cfg.min_pair_games:
            my_wr = _wr(pair)
    winrate = _wr(stats.hero(bucket, enemy_id))
    score = 0.0
    if farm is not None:
        score += cfg.w_farm * (3.0 - farm)
    if late_delta is not None:
        score += cfg.w_late * late_delta
    if my_wr is not None:
        score += cfg.w_matchup * (50 - my_wr)
    if winrate is not None:
        score += cfg.w_winrate * (winrate - 50)
    reasons = []
    if farm is not None:
        reasons.append("carry" if farm <= cfg.carry_rank else ("core" if farm <= cfg.core_rank else "support"))
    if late_delta is not None and late_delta >= cfg.late_notable:
        reasons.append("late")
    if my_wr is not None and my_wr <= 50 - cfg.matchup_notable:
        reasons.append("matchup")
    return ThreatFacts(enemy_id, farm, late_delta, late_wr, my_wr, winrate, round(score, 2), tuple(reasons))


def rank_threats(stats: Stats, bucket: str, enemy_ids: list[int], my_hero_id: int | None,
                 cfg: ThreatScoreConfig) -> list[ThreatFacts]:
    """Враги по убыванию опасности."""
    return sorted((facts(stats, bucket, e, my_hero_id, cfg) for e in enemy_ids), key=lambda f: -f.score)


def threat_score_config(raw: dict) -> ThreatScoreConfig:
    t = raw["threat_score"]
    return ThreatScoreConfig(t["w_farm"], t["w_late"], t["w_matchup"], t["w_winrate"], t["min_length_games"],
                             t["min_pair_games"], t["carry_rank"], t["core_rank"], t["late_notable"],
                             t["matchup_notable"])
