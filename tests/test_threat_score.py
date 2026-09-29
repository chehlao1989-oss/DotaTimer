"""Тесты угроз по нашим матчам: фарм-приоритет, сила в лейте, матчап, причины."""
from app.threats.stats import Stats
from app.threats.threat_score import ThreatScoreConfig, facts, rank_threats

CFG = ThreatScoreConfig(w_farm=3.0, w_late=0.5, w_matchup=0.5, w_winrate=0.5, min_length_games=200,
                        min_pair_games=150, carry_rank=1.8, core_rank=2.8, late_notable=3.0, matchup_notable=3.0)
MY = 18
STATS = Stats({"version": 2,
               "hero": {"normal": {"1": [5000, 2600], "2": [5000, 2500], "3": [5000, 2550]}},
               # 1 — керри (место 1.3), 2 — хардлайнер-кор (2.5), 3 — саппорт (4.6)
               "farm": {"normal": {"1": [5000, 6500], "2": [5000, 12500], "3": [5000, 23000]}},
               "length": {"normal": {"1": {"short": [1000, 470], "long": [1000, 560]},
                                     "2": {"short": [1000, 520], "long": [1000, 480]},
                                     "3": {"short": [100, 50], "long": [100, 60]}}},
               "pair": {"normal": {"18": {"1": [400, 200], "2": [400, 180], "3": [100, 30]}}}})


def test_carry_late_game():
    f = facts(STATS, "normal", 1, MY, CFG)
    assert round(f.farm_rank, 1) == 1.3
    assert round(f.late_delta) == 9 and round(f.late_winrate) == 56
    assert f.reasons == ("carry", "late")


def test_matchup_and_core():
    f = facts(STATS, "normal", 2, MY, CFG)
    assert f.my_winrate == 45.0
    assert f.reasons == ("core", "matchup")  # в лейте слабее — не упоминаем


def test_support_small_samples_ignored():
    f = facts(STATS, "normal", 3, MY, CFG)
    assert f.reasons == ("support",)  # 100 игр пары и 100 долгих игр — мало, не судим
    assert f.my_winrate is None and f.late_delta is None


def test_ranking():
    order = [f.hero_id for f in rank_threats(STATS, "normal", [3, 2, 1], MY, CFG)]
    assert order == [1, 2, 3]


def test_no_my_hero():
    assert facts(STATS, "normal", 2, None, CFG).my_winrate is None
