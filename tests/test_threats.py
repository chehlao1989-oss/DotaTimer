"""Тесты угроз: оценка, выбор совета (механики + статистика), память о предметах врагов."""
import pytest

from app.threats.advisor import TIER_BASE, TIER_HERO, TIER_MECHANIC, TIER_TRAIT, Advisor
from app.threats.data import GameData, Hero, Item
from app.threats.mechanics import MechanicsTagger, load_mechanics_config
from app.threats.scoring import load_threat_config, rank_bracket, score_enemies, winrate
from app.threats.stats import Stats
from app.threats.tracker import KIND_BUILDING, KIND_ITEM, ItemTracker

RAW_ITEMS = {
    "spirit_vessel": {"id": 1, "cost": 2725, "abilities": [{"type": "active", "description": "reduces Health Restoration by 70%."}]},
    "skadi": {"id": 2, "cost": 5900, "abilities": [{"type": "passive", "description": "Attacks reduce Health Restoration by 50%."}]},
    "orb_of_corrosion": {"id": 3, "cost": 1050, "abilities": [{"type": "passive", "description": "reduces Health Restoration by 18%."}]},
    "heart": {"id": 114, "cost": 5100, "abilities": [{"type": "passive", "description": "Your health regeneration is increased."}]},
    "reaver": {"id": 311, "cost": 2800, "abilities": []},
    "ring_of_tarrasque": {"id": 5, "cost": 1800, "abilities": []},
    "tango": {"id": 44, "cost": 90, "abilities": [{"type": "active", "description": "Heals you over time."}]},
}
ABILITIES = {"alchemist_rage": {"behavior": "No Target", "desc": "Increases health regeneration."}}
HERO_ABILITIES = {"npc_dota_hero_alchemist": {"abilities": ["alchemist_rage"]}}

DATA = GameData(
    heroes={
        1: Hero(1, "alchemist", "Alchemist", ("Carry",)),
        2: Hero(2, "medusa", "Medusa", ("Carry",)),
        3: Hero(3, "lion", "Lion", ("Support", "Disabler")),
        4: Hero(4, "sven", "Sven", ("Carry",)),
    },
    items={
        "heart": Item("heart", 114, "Heart of Tarrasque", 5100, "epic", ("reaver", "ring_of_tarrasque")),
        "reaver": Item("reaver", 311, "Reaver", 2800, "secret_shop"),
        "ring_of_tarrasque": Item("ring_of_tarrasque", 5, "Ring of Tarrasque", 1800, "component"),
        "spirit_vessel": Item("spirit_vessel", 1, "Spirit Vessel", 2725, "rare"),
        "skadi": Item("skadi", 2, "Eye of Skadi", 5900, "artifact"),
        "orb_of_corrosion": Item("orb_of_corrosion", 3, "Orb of Corrosion", 1050, "common"),
        "tango": Item("tango", 44, "Tango", 90, "consumable"),
    },
    hero_stats={
        1: {"5_pick": 1000, "5_win": 560, "pub_pick": 1000, "pub_win": 560},
        2: {"5_pick": 1000, "5_win": 500, "pub_pick": 1000, "pub_win": 500},
        3: {"5_pick": 1000, "5_win": 550, "pub_pick": 1000, "pub_win": 550},
        4: {"5_pick": 1000, "5_win": 480},
    },
    matchups={4: [{"hero_id": 1, "games_played": 100, "wins": 30}]},
)

# статистика для героя пользователя Sven (id 4) в обычных играх
STATS = Stats({"patch": "7.41", "base": {"normal": {"4": {
    "skadi": [400, 200], "spirit_vessel": [100, 48], "orb_of_corrosion": [5, 3]}}},
    "vs_hero": {"normal": {"4": {"1": {"skadi": [60, 36], "spirit_vessel": [10, 9]}}}},
    "vs_trait": {"normal": {"4": {"heal": {"spirit_vessel": [50, 26], "skadi": [120, 62]}}}},
    "vs_item": {"normal": {"4": {"heart": {"spirit_vessel": [40, 24]}}}}})


@pytest.fixture
def config():
    return load_threat_config()


def tagger():
    return MechanicsTagger(load_mechanics_config(), RAW_ITEMS, ABILITIES, HERO_ABILITIES)


def advisor(stats=STATS, config=None):
    config = config or load_threat_config()
    buyable = {k for k, v in RAW_ITEMS.items() if v["cost"] > 0}
    return Advisor(tagger(), stats, buyable, {k: v["cost"] for k, v in RAW_ITEMS.items()},
                   config.stats_min_games, config.stats_min_base_games)


def heal_rules():
    t = tagger()
    return [r for r in t.rules if r.id == "heal"]


def test_rank_bracket_and_winrate():
    assert rank_bracket(54) == 5 and rank_bracket(None) is None and rank_bracket(0) is None
    assert winrate({"5_pick": 1000, "5_win": 560}, 5, False, 200) == pytest.approx(56.0)
    assert winrate({"5_pick": 100, "5_win": 60}, 5, False, 200) is None
    assert winrate({"pub_pick": 1000, "pub_win": 500}, None, False, 200) == pytest.approx(50.0)  # ранга нет


def test_score_enemies(config):
    threats = score_enemies([DATA.heroes[i] for i in (1, 2, 3)], DATA, config, None, False, my_hero_id=4)
    assert [t.hero.name for t in threats][:2] == ["alchemist", "medusa"]  # саппорт Lion ниже, хоть винрейт выше
    assert {t.hero.name: t.reason for t in threats}["alchemist"] == "matchup"  # Sven проигрывает ему 70%


def test_advisor_prefers_vs_hero_stats():
    suggestions = advisor().suggest(heal_rules(), 4, "normal", enemy_hero_id=1)
    best = suggestions[0]
    # Skadi против Alchemist: 60% (36/60) против 50% в целом → +10, выборка 60 ≥ 30
    assert (best.item, best.tier, best.delta) == ("skadi", TIER_HERO, 10.0)
    # Spirit Vessel против Alchemist — всего 10 игр, берётся ступенька «против механики»: 52% − 48% = +4
    vessel = next(s for s in suggestions if s.item == "spirit_vessel")
    assert (vessel.tier, vessel.delta) == (TIER_TRAIT, 4.0)
    # Orb of Corrosion на Свене почти не собирают (5 игр) — не советуем
    assert all(s.item != "orb_of_corrosion" for s in suggestions)


def test_advisor_vs_enemy_item_and_owned():
    suggestions = advisor().suggest(heal_rules(), 4, "normal", enemy_item="heart", owned={"skadi"})
    assert [(s.item, s.tier, s.delta) for s in suggestions] == [("spirit_vessel", TIER_HERO, 12.0)]


def test_advisor_without_stats_gives_cheap_mechanics_first():
    suggestions = advisor(Stats(None)).suggest(heal_rules(), 4, "normal", enemy_hero_id=1, limit=3)
    assert [s.item for s in suggestions] == ["orb_of_corrosion", "spirit_vessel", "skadi"]
    assert all(s.tier == TIER_MECHANIC for s in suggestions)


def test_advisor_base_tier():
    stats = Stats({"base": {"normal": {"4": {"skadi": [400, 200]}}}})
    [s] = advisor(stats).suggest(heal_rules(), 4, "normal", enemy_hero_id=1)
    assert (s.item, s.tier) == ("skadi", TIER_BASE)


def make_tracker(config):
    return ItemTracker(DATA, tagger(), config.min_component_cost)


def test_new_important_item_gives_hint_once(config):
    tracker = make_tracker(config)
    hints = tracker.update("alchemist", {"heart", "tango"}, 1200, {"alchemist"})
    assert [(h.kind, h.item) for h in hints] == [(KIND_ITEM, "heart")]
    assert [r.id for r in hints[0].rules] == ["heal"]
    assert tracker.update("alchemist", {"heart"}, 1300, {"alchemist"}) == []
    assert tracker.enemies["alchemist"].items == {"heart": 1200}  # расходник не запомнен


def test_component_gives_building_hint_then_item(config):
    tracker = make_tracker(config)
    hints = tracker.update("alchemist", {"reaver"}, 900, {"alchemist"})
    assert [(h.kind, h.item, h.component) for h in hints] == [(KIND_BUILDING, "heart", "reaver")]
    assert tracker.update("alchemist", {"ring_of_tarrasque", "reaver"}, 1000, {"alchemist"}) == []
    hints = tracker.update("alchemist", {"heart"}, 1100, {"alchemist"})
    assert [(h.kind, h.item) for h in hints] == [(KIND_ITEM, "heart")]


def test_non_threats_are_remembered_but_silent(config):
    tracker = make_tracker(config)
    assert tracker.update("medusa", {"heart"}, 800, {"alchemist"}) == []
    assert "heart" in tracker.enemies["medusa"].items


def test_new_match_resets(config):
    tracker = make_tracker(config)
    tracker.update("alchemist", {"heart"}, 1200, {"alchemist"})
    tracker.reset()
    assert tracker.update("alchemist", {"heart"}, 1200, {"alchemist"}) != []
