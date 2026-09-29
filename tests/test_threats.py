"""Тесты таблицы контров, оценки угроз и подсказок по предметам (синтетические данные)."""
import pytest

from app.threats.counters import parse_counters
from app.threats.data import GameData, Hero, Item
from app.threats.scoring import load_threat_config, rank_bracket, score_enemies, winrate
from app.threats.tracker import KIND_BUILDING, KIND_ITEM, ItemTracker

COUNTERS = parse_counters({
    "rules": [
        {"id": "heal", "title_ru": "Лечение", "priority": 2,
         "triggers": {"items": ["heart"], "heroes": ["alchemist"]},
         "counters": [{"item": "spirit_vessel", "roles": ["support"], "why_ru": "режет лечение"},
                      {"item": "skadi", "roles": ["core"], "why_ru": "режет лечение"},
                      {"item": "shivas_guard", "roles": ["any"], "why_ru": "аура"}]},
        {"id": "magic_immune", "title_ru": "BKB", "priority": 3,
         "triggers": {"items": ["black_king_bar"], "heroes": []},
         "counters": [{"item": "nullifier", "roles": ["core"], "why_ru": "развеивает"}]},
    ],
    "heroes": {"medusa": {"scaling": 3}},
})

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
        "ring_of_tarrasque": Item("ring_of_tarrasque", 1, "Ring of Tarrasque", 1800, "component"),
        "black_king_bar": Item("black_king_bar", 116, "Black King Bar", 4050, "epic", ("mithril_hammer", "ogre_axe")),
        "mithril_hammer": Item("mithril_hammer", 2, "Mithril Hammer", 1600, "component"),
        "ogre_axe": Item("ogre_axe", 3, "Ogre Axe", 1000, "component"),
        "tango": Item("tango", 44, "Tango", 90, "consumable"),
        "boots": Item("boots", 29, "Boots of Speed", 500, "component"),
    },
    hero_stats={
        1: {"5_pick": 1000, "5_win": 560, "turbo_picks": 1000, "turbo_wins": 480},
        2: {"5_pick": 1000, "5_win": 500},
        3: {"5_pick": 1000, "5_win": 580},
        4: {"5_pick": 1000, "5_win": 480},
    },
    matchups={4: [{"hero_id": 1, "games_played": 100, "wins": 30}, {"hero_id": 2, "games_played": 5, "wins": 0}]},
)


@pytest.fixture
def config():
    return load_threat_config()


def test_pick_counters_by_role():
    rules = COUNTERS.rules_for_item("heart")
    assert [c.item for c in COUNTERS.pick_counters(rules, "core")] == ["skadi", "shivas_guard"]
    assert [c.item for c in COUNTERS.pick_counters(rules, "support")] == ["spirit_vessel", "shivas_guard"]
    assert [c.item for c in COUNTERS.pick_counters(rules, "core", owned={"skadi"})] == ["shivas_guard"]


def test_rank_bracket_and_winrate():
    assert rank_bracket(54) == 5 and rank_bracket(None) is None and rank_bracket(0) is None
    assert winrate({"5_pick": 1000, "5_win": 560}, 5, False, 200) == pytest.approx(56.0)
    assert winrate({"5_pick": 100, "5_win": 60}, 5, False, 200) is None  # мало игр
    assert winrate({"turbo_picks": 1000, "turbo_wins": 480}, 5, True, 200) == pytest.approx(48.0)


def test_score_enemies(config):
    enemies = [DATA.heroes[i] for i in (1, 2, 3)]
    threats = score_enemies(enemies, DATA, COUNTERS, config, rank_tier=54, turbo=False, my_hero_id=4)
    order = [t.hero.name for t in threats]
    assert order[:2] == ["alchemist", "medusa"]  # саппорт Lion ниже, несмотря на винрейт 58%
    reasons = {t.hero.name: t.reason for t in threats}
    assert reasons["medusa"] == "scaling"
    assert reasons["alchemist"] in ("winrate", "matchup")


def make_tracker(config):
    return ItemTracker(DATA, COUNTERS, config.min_component_cost, config.counters_per_hint)


def test_new_important_item_gives_hint_once(config):
    tracker = make_tracker(config)
    hints = tracker.update("alchemist", {"heart", "tango"}, 1200, {"alchemist"}, "core", set())
    assert [(h.kind, h.item) for h in hints] == [(KIND_ITEM, "heart")]
    assert [c.item for c in hints[0].counters] == ["skadi", "shivas_guard"]
    assert tracker.update("alchemist", {"heart"}, 1300, {"alchemist"}, "core", set()) == []
    assert tracker.enemies["alchemist"].items == {"heart": 1200}  # расходник не запомнен


def test_component_gives_building_hint_then_item(config):
    tracker = make_tracker(config)
    hints = tracker.update("alchemist", {"reaver", "boots"}, 900, {"alchemist"}, "support", set())
    assert [(h.kind, h.item, h.component) for h in hints] == [(KIND_BUILDING, "heart", "reaver")]
    hints = tracker.update("alchemist", {"ring_of_tarrasque", "reaver"}, 1000, {"alchemist"}, "support", set())
    assert hints == []  # «собирает Heart» уже сказали
    hints = tracker.update("alchemist", {"heart"}, 1100, {"alchemist"}, "support", set())
    assert [(h.kind, h.item) for h in hints] == [(KIND_ITEM, "heart")]


def test_cheap_components_and_non_threats_are_ignored(config):
    tracker = make_tracker(config)
    assert tracker.update("medusa", {"black_king_bar"}, 800, {"alchemist"}, "core", set()) == []  # не угроза
    assert "black_king_bar" in tracker.enemies["medusa"].items  # но предмет запомнен
    # Ogre Axe стоит 1000 — проходит порог; Tango — расходник
    hints = tracker.update("alchemist", {"ogre_axe", "tango"}, 800, {"alchemist"}, "core", set())
    assert [(h.kind, h.item) for h in hints] == [(KIND_BUILDING, "black_king_bar")]


def test_new_match_resets(config):
    tracker = make_tracker(config)
    tracker.update("alchemist", {"heart"}, 1200, {"alchemist"}, "core", set())
    tracker.reset()
    assert tracker.update("alchemist", {"heart"}, 1200, {"alchemist"}, "core", set()) != []
