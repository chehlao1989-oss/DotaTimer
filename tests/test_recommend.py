"""Тесты модуля рекомендаций: отсев, доверительный интервал, несколько угроз, гистерезис, «уже куплено»."""
from app.threats.mechanics import MechanicsTagger, load_mechanics_config
from app.threats.recommend import (
    TIER_HERO, TIER_ITEM, TIER_MECHANIC, RecommendConfig, Recommender, ThreatInput, lower_bound_delta,
)
from app.threats.stats import Stats

HEAL = "reduces Health Restoration by 50%."
RAW_ITEMS = {
    "spirit_vessel": {"cost": 2725, "abilities": [{"type": "active", "description": HEAL}]},
    "skadi": {"cost": 5900, "abilities": [{"type": "passive", "description": HEAL}]},
    "orb_of_corrosion": {"cost": 1050, "abilities": [{"type": "passive", "description": HEAL}]},
    "crellas_crozier": {"cost": 4800, "abilities": [{"type": "passive", "description": HEAL}]},
    "monkey_king_bar": {"cost": 5000, "abilities": [{"type": "passive", "description": "pierce through evasion"}]},
    "heart": {"cost": 5100, "abilities": [{"type": "passive", "description": "health regeneration is increased"}]},
    "butterfly": {"cost": 5450, "abilities": [], "attrib": [{"key": "bonus_evasion"}]},
}
ABILITIES = {"heal_ab": {"behavior": "No Target", "desc": "Increases health regeneration."},
             "evasion_ab": {"behavior": "No Target", "desc": "Grants evasion."}}
HERO_ABILITIES = {"npc_dota_hero_alchemist": {"abilities": ["heal_ab"]},
                  "npc_dota_hero_huskar": {"abilities": ["heal_ab"]},
                  "npc_dota_hero_phantom_assassin": {"abilities": ["evasion_ab"]}}
CONFIG = RecommendConfig(min_games=30, min_base_games=20, min_buy_share=0.03, z=1.0, multi_threat_bonus=1.0,
                         hysteresis=1.5, phase_base_cost=2500, phase_cost_per_min=200, per_threat=2,
                         base_tier_scale=0.1)
ME = 18  # Sven
ALCH = ThreatInput("alchemist", 73)
HUSKAR = ThreatInput("huskar", 59)
LATE = 40 * 60  # на 40-й минуте фаза разрешает всё


def stats(extra=None):
    raw = {"hero": {"normal": {"18": [10000, 5000]}},
           "base": {"normal": {"18": {"skadi": [2000, 1000], "spirit_vessel": [1000, 480],
                                      "orb_of_corrosion": [100, 55], "crellas_crozier": [400, 200],
                                      "monkey_king_bar": [3000, 1500]}}},
           "vs_hero": {"normal": {"18": {"73": {"skadi": [400, 240], "spirit_vessel": [35, 21]},
                                         "59": {"skadi": [300, 165], "crellas_crozier": [300, 168]}}}},
           "vs_trait": {}, "vs_item": {}}
    raw.update(extra or {})
    return Stats(raw)


def make(s=None):
    tagger = MechanicsTagger(load_mechanics_config(), RAW_ITEMS, ABILITIES, HERO_ABILITIES)
    return Recommender(tagger, s or stats(), {k for k in RAW_ITEMS if k not in ("heart", "butterfly")},
                       {k: v["cost"] for k, v in RAW_ITEMS.items()}, CONFIG)


def names(rec):
    return [i.item for i in rec.items]


def test_lower_bound_penalizes_small_samples():
    big = lower_bound_delta((400, 240), (2000, 1000), 1.0)  # +10, много игр
    small = lower_bound_delta((35, 21), (1000, 480), 1.0)  # +12, но всего 35 игр
    assert round(big[0], 1) == 10.0 and big[1] > 6
    assert small[0] > big[0] and small[1] < big[1]  # прибавка больше, но доверяем меньше


def test_best_item_and_low_buy_share_filtered():
    [rec] = make().recommend(ME, [ALCH], set(), LATE)
    assert rec.items[0].item == "skadi" and rec.items[0].tier == TIER_HERO
    # Orb of Corrosion собирают в 1% игр Свена (100 из 10000) — не советуем
    assert "orb_of_corrosion" not in names(rec)


def test_already_bought_is_checked_and_next_offered():
    [rec] = make().recommend(ME, [ALCH], {"skadi"}, LATE)
    assert rec.items[0].item == "skadi" and rec.items[0].bought
    assert rec.items[1].item == "spirit_vessel" and not rec.items[1].bought


def test_phase_filter():
    [early] = make().recommend(ME, [ALCH], set(), 5 * 60)  # 5 мин: до 3500 золота
    assert names(early) == ["spirit_vessel"]
    [late] = make().recommend(ME, [ALCH], set(), LATE)
    assert "skadi" in names(late)


def test_bonus_for_item_against_several_threats():
    # против одного Huskar Crozier чуть лучше Skadi, но Skadi работает и против Alchemist → бонус
    recs = make().recommend(ME, [ALCH, HUSKAR], set(), LATE)
    huskar = next(r for r in recs if r.hero == "huskar")
    assert huskar.items[0].item == "skadi" and huskar.items[0].shared == 2


def test_enemy_item_adds_mechanic_and_uses_item_stats():
    s = stats({"vs_item": {"normal": {"18": {"butterfly": {"monkey_king_bar": [200, 130]}}}}})
    [rec] = make(s).recommend(ME, [ThreatInput("alchemist", 73, frozenset({"butterfly"}))], set(), LATE)
    mkb = [i for i in rec.items if i.item == "monkey_king_bar"]
    assert mkb and mkb[0].tier == TIER_ITEM


def test_hysteresis_keeps_advice_unless_clearly_better():
    r = make()
    [first] = r.recommend(ME, [ALCH], set(), LATE)
    assert first.items[0].item == "skadi" and not first.items[0].changed  # первый показ — не «смена»
    # Spirit Vessel стал чуть лучше, но не на 1.5 п.п. — совет не прыгает
    r.stats = stats({"vs_hero": {"normal": {"18": {"73": {"skadi": [400, 240], "spirit_vessel": [400, 238]}}}}})
    [second] = r.recommend(ME, [ALCH], set(), LATE)
    assert second.items[0].item == "skadi" and not second.items[0].changed
    # стал заметно лучше — меняем и подсвечиваем
    r.stats = stats({"vs_hero": {"normal": {"18": {"73": {"skadi": [400, 240], "spirit_vessel": [400, 300]}}}}})
    [third] = r.recommend(ME, [ALCH], set(), LATE)
    assert third.items[0].item == "spirit_vessel" and third.items[0].changed


def test_bought_advice_moves_to_next_and_signals_change():
    r = make()
    r.recommend(ME, [ALCH], set(), LATE)
    [after] = r.recommend(ME, [ALCH], {"skadi"}, LATE)
    assert after.items[0].bought and after.items[1].item == "spirit_vessel" and after.items[1].changed


def test_without_stats_cheap_mechanics_first_and_gold():
    [rec] = make(Stats(None)).recommend(ME, [ALCH], set(), LATE, my_gold=3000)
    assert names(rec) == ["orb_of_corrosion", "spirit_vessel"]
    assert all(i.tier == TIER_MECHANIC for i in rec.items)
    assert rec.items[0].affordable and rec.items[1].affordable


def test_new_match_reset_and_no_hero():
    r = make()
    r.recommend(ME, [ALCH], set(), LATE)
    r.reset()
    [rec] = r.recommend(ME, [ALCH], set(), LATE)
    assert not rec.items[0].changed
    assert r.recommend(None, [ALCH], set(), LATE) == []
