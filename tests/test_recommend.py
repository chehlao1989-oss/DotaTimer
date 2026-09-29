"""Тесты модуля рекомендаций: частота покупки, порог выборки, фильтр по винрейту, отсев,
несколько угроз, гистерезис, «уже куплено», «мало данных»."""
from app.threats.mechanics import MechanicsTagger, load_mechanics_config
from app.threats.recommend import (
    TIER_HERO, TIER_ITEM, TIER_MECHANIC, RecommendConfig, Recommender, ThreatInput, share_uplift,
    winrate_harmful,
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
ABILITIES = {"heal_ab": {"behavior": "No Target", "desc": "Increases health regeneration."}}
HERO_ABILITIES = {"npc_dota_hero_alchemist": {"abilities": ["heal_ab"]},
                  "npc_dota_hero_huskar": {"abilities": ["heal_ab"]}}
CONFIG = RecommendConfig(min_pair_games=150, min_base_games=20, min_buy_share=0.03, z=1.64, wr_filter_games=500,
                         multi_threat_bonus=1.0, hysteresis=1.5, phase_base_cost=2500, phase_cost_per_min=200,
                         per_threat=2)
ME = 18  # Sven, 10000 игр
ALCH = ThreatInput("alchemist", 73)
HUSKAR = ThreatInput("huskar", 59)
LATE = 40 * 60  # на 40-й минуте фаза разрешает всё

BASE = {"skadi": [2000, 1000], "spirit_vessel": [1000, 480], "orb_of_corrosion": [100, 55],
        "crellas_crozier": [400, 200], "monkey_king_bar": [3000, 1500]}


def stats(vs_alch=None, vs_huskar=None, pair_alch=400, pair_huskar=400, extra=None):
    raw = {"version": 2, "hero": {"normal": {"18": [10000, 5000]}},
           "base": {"normal": {"18": BASE}},
           "pair": {"normal": {"18": {"73": [pair_alch, pair_alch // 2], "59": [pair_huskar, pair_huskar // 2]}}},
           # Skadi против Alchemist: 30% игр при 20% в целом → +10 п.п.; Vessel: 12.5% при 10% → +2.5
           "vs_hero": {"normal": {"18": {"73": vs_alch or {"skadi": [120, 60], "spirit_vessel": [50, 25]},
                                         "59": vs_huskar or {"crellas_crozier": [48, 24]}}}},
           "vs_trait": {}, "vs_item": {}, "trait_games": {}, "item_games": {}}
    raw.update(extra or {})
    return Stats(raw)


def make(s=None):
    tagger = MechanicsTagger(load_mechanics_config(), RAW_ITEMS, ABILITIES, HERO_ABILITIES)
    return Recommender(tagger, s or stats(), {k for k in RAW_ITEMS if k not in ("heart", "butterfly")},
                       {k: v["cost"] for k, v in RAW_ITEMS.items()}, CONFIG)


def names(rec):
    return [i.item for i in rec.items]


def test_threshold_math():
    # +5 п.п. к доле 20%: на 150 играх нижняя граница ниже нуля (шум), на 300 — выше (сигнал)
    delta, low = share_uplift(round(150 * 0.25), 150, 0.20, 1.64)
    assert 4.5 < delta < 5.5 and low < 0
    delta, low = share_uplift(round(300 * 0.25), 300, 0.20, 1.64)
    assert low > 0


def test_frequency_uplift_ranks_first_and_low_buy_share_filtered():
    [rec] = make().recommend(ME, [ALCH], set(), LATE)
    top = rec.items[0]
    assert (top.item, top.tier, round(top.delta)) == ("skadi", TIER_HERO, 10)
    assert top.reason == "heal_reduction"
    # Vessel: прибавка +2.5 не значима → совет по механике без процентов
    vessel = next(i for i in rec.items if i.item == "spirit_vessel")
    assert vessel.tier == TIER_MECHANIC and vessel.delta is None
    # Orb of Corrosion собирают в 1% игр Свена — не советуем
    assert "orb_of_corrosion" not in names(rec)


def test_small_pair_sample_means_mechanic_only():
    [rec] = make(stats(pair_alch=100, vs_alch={"skadi": [40, 20]})).recommend(ME, [ALCH], set(), LATE)
    assert all(i.tier == TIER_MECHANIC for i in rec.items)  # 100 игр пары < 150 — «мало данных»


def test_winrate_filter_removes_harmful_item():
    s = stats(vs_alch={"skadi": [600, 240], "spirit_vessel": [50, 25]}, pair_alch=2000)  # Skadi: 40% при 50%
    [rec] = make(s).recommend(ME, [ALCH], set(), LATE)
    assert "skadi" not in names(rec)
    assert winrate_harmful((600, 240), (2000, 1000), 1.64, 500)
    assert not winrate_harmful((100, 30), (2000, 1000), 1.64, 500)  # мало игр — не судим


def test_already_bought_is_checked_and_next_offered():
    [rec] = make().recommend(ME, [ALCH], {"skadi"}, LATE)
    assert rec.items[0].item == "skadi" and rec.items[0].bought and rec.items[0].reason == "heal_reduction"
    assert rec.items[1].item != "skadi" and not rec.items[1].bought


def test_phase_filter():
    [early] = make().recommend(ME, [ALCH], set(), 5 * 60)  # 5 мин: до 3500 золота
    assert names(early) == ["spirit_vessel"]


def test_enemy_item_tier():
    extra = {"item_games": {"normal": {"18": {"butterfly": [300, 150]}}},
             "vs_item": {"normal": {"18": {"butterfly": {"monkey_king_bar": [150, 75]}}}}}  # 50% при 30% → +20
    [rec] = make(stats(extra=extra)).recommend(ME, [ThreatInput("alchemist", 73, frozenset({"butterfly"}))],
                                                set(), LATE)
    mkb = [i for i in rec.items if i.item == "monkey_king_bar"]
    assert mkb and mkb[0].tier == TIER_ITEM and mkb[0].reason == "true_strike"


def test_bonus_for_item_against_several_threats():
    # против Huskar: Crozier +8 (12% при 4%), Skadi +8.75 (28.75% при 20%), но у Skadi шире погрешность;
    # Skadi значимо докупают и против Alchemist → бонус выводит его вперёд
    s = stats(vs_huskar={"crellas_crozier": [48, 24], "skadi": [115, 57]})
    recs = make(s).recommend(ME, [ALCH, HUSKAR], set(), LATE)
    huskar = next(r for r in recs if r.hero == "huskar")
    assert huskar.items[0].item == "skadi" and huskar.items[0].shared == 2


def test_hysteresis_keeps_advice_unless_clearly_better():
    r = make()
    [first] = r.recommend(ME, [ALCH], set(), LATE)
    assert first.items[0].item == "skadi" and not first.items[0].changed  # первый показ — не «смена»
    # Vessel стал почти так же хорош — совет не прыгает
    r.stats = stats(vs_alch={"skadi": [120, 60], "spirit_vessel": [80, 40]})  # 20% при 10% → +10
    [second] = r.recommend(ME, [ALCH], set(), LATE)
    assert second.items[0].item == "skadi" and not second.items[0].changed
    # стал заметно лучше — меняем и подсвечиваем
    r.stats = stats(vs_alch={"skadi": [120, 60], "spirit_vessel": [140, 70]})  # 35% при 10% → +25
    [third] = r.recommend(ME, [ALCH], set(), LATE)
    assert third.items[0].item == "spirit_vessel" and third.items[0].changed


def test_bought_advice_moves_to_next_and_signals_change():
    r = make()
    r.recommend(ME, [ALCH], set(), LATE)
    [after] = r.recommend(ME, [ALCH], {"skadi"}, LATE)
    assert after.items[0].bought and after.items[1].changed


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
