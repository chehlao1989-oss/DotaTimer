"""Оценка угроз: кто из врагов может затащить игру на ранге и в режиме пользователя (п. 6.3 ТЗ).

Оценка = винрейт выше среднего + вес кора + масштабирование в лейт + матчап против героя
пользователя. Саппорты получают понижающий множитель. Веса — в data/threats.json.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from app.threats.counters import ROLE_CORE, ROLE_SUPPORT, CounterTable
from app.threats.data import GameData, Hero

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "threats.json"

REASON_WINRATE = "winrate"
REASON_SCALING = "scaling"
REASON_MATCHUP = "matchup"
REASON_CORE = "core"


@dataclass(frozen=True)
class ThreatConfig:
    weights: dict[str, float]
    support_multiplier: float
    matchup_min_games: int
    stats_min_picks: int
    default_threat_count: int
    counters_per_hint: int
    min_component_cost: int
    reason_winrate_min: float


def load_threat_config(path: Path = DEFAULT_PATH) -> ThreatConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ThreatConfig(raw["weights"], raw["support_multiplier"], raw["matchup_min_games"],
                        raw["stats_min_picks"], raw["default_threat_count"], raw["counters_per_hint"],
                        raw["min_component_cost"], raw["reason_winrate_min"])


def hero_role(hero: Hero) -> str:
    """Роль героя по OpenDota: есть «Carry» — кор, иначе есть «Support» — саппорт, иначе кор."""
    if "Carry" in hero.roles:
        return ROLE_CORE
    if "Support" in hero.roles:
        return ROLE_SUPPORT
    return ROLE_CORE


def rank_bracket(rank_tier: int | None) -> int | None:
    """rank_tier 54 → категория 5 (Легенда). None — ранг неизвестен."""
    if not rank_tier:
        return None
    bracket = rank_tier // 10
    return bracket if 1 <= bracket <= 8 else None


def winrate(stats: dict, bracket: int | None, turbo: bool, min_picks: int) -> float | None:
    """Винрейт героя в процентах в нужной категории. None, если игр мало или данных нет."""
    if turbo:
        picks, wins = stats.get("turbo_picks"), stats.get("turbo_wins")
    elif bracket:
        picks, wins = stats.get(f"{bracket}_pick"), stats.get(f"{bracket}_win")
    else:
        picks, wins = stats.get("pub_pick"), stats.get("pub_win")
    if not picks or picks < min_picks or wins is None:
        return None
    return wins / picks * 100


def my_winrate_against(data: GameData, my_hero_id: int | None, enemy_id: int, min_games: int) -> float | None:
    """Винрейт героя пользователя против врага (матчапы OpenDota). None при малой выборке."""
    if my_hero_id is None:
        return None
    for row in data.matchups.get(my_hero_id, []):
        if row.get("hero_id") == enemy_id:
            games = row.get("games_played", 0)
            return row["wins"] / games * 100 if games >= min_games else None
    return None


@dataclass(frozen=True)
class Threat:
    hero: Hero
    score: float
    reason: str  # главная причина: winrate / scaling / matchup / core
    winrate: float | None


def score_enemies(enemies: list[Hero], data: GameData, counters: CounterTable, config: ThreatConfig,
                  rank_tier: int | None, turbo: bool, my_hero_id: int | None) -> list[Threat]:
    """Враги по убыванию опасности."""
    bracket = rank_bracket(rank_tier)
    w = config.weights
    threats = []
    for hero in enemies:
        wr = winrate(data.hero_stats.get(hero.id, {}), bracket, turbo, config.stats_min_picks)
        mine = my_winrate_against(data, my_hero_id, hero.id, config.matchup_min_games)
        parts = {
            REASON_WINRATE: w["winrate"] * ((wr - 50) if wr is not None else 0),
            REASON_CORE: w["core"] if hero_role(hero) == ROLE_CORE else 0,
            REASON_SCALING: w["scaling"] * counters.scaling(hero.name),
            REASON_MATCHUP: w["matchup"] * ((50 - mine) if mine is not None else 0),
        }
        score = sum(parts.values())
        if hero_role(hero) == ROLE_SUPPORT:
            score *= config.support_multiplier
        # причина для карточки: самая весомая из понятных пользователю
        explainable = {k: v for k, v in parts.items() if k != REASON_CORE}
        reason = max(explainable, key=explainable.get)
        if explainable[reason] <= 0:
            reason = REASON_CORE
        if reason == REASON_WINRATE and (wr is None or wr < config.reason_winrate_min):
            reason = REASON_SCALING if parts[REASON_SCALING] > 0 else REASON_CORE
        threats.append(Threat(hero, round(score, 2), reason, wr))
    return sorted(threats, key=lambda t: -t.score)
