"""Тесты связки угроз: карточка, подсказки, напоминание при смерти (без экрана и сети)."""
from app.threats.controller import ThreatSettings, ThreatsController
from app.threats.scoring import load_threat_config
from app.vision.inventory import InventorySnapshot
from app.vision.matcher import Match
from app.vision.topbar import TopbarResult
from tests.test_threats import DATA, advisor, tagger


def make():
    shown = []
    ctrl = ThreatsController(DATA, tagger(), advisor(), load_threat_config(), ThreatSettings(),
                             lambda text, important, voice: shown.append((text, voice)), lambda: False)
    return ctrl, shown


def topbar(radiant, dire):
    return TopbarResult(tuple(Match(h, 0.8, 0.3) for h in radiant), tuple(Match(h, 0.8, 0.3) for h in dire))


def test_topbar_gives_enemies_and_card():
    ctrl, shown = make()
    assert ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True) is True  # пора снять панель
    heroes = ctrl.on_topbar(topbar(["sven"] * 5, ["alchemist", "medusa", "lion", "lion", "lion"]))
    assert len(heroes) == 10
    assert ctrl.enemies == ["alchemist", "medusa", "lion", "lion", "lion"]
    text, voice = shown[-1]
    assert voice == "threat" and "Главные угрозы" in text and "Alchemist" in text
    assert "Eye of Skadi (+10% против Alchemist)" in text  # совет из статистики


def test_enemy_side_from_own_hero_when_team_unknown():
    ctrl, _ = make()
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", None, True)
    ctrl.on_topbar(topbar(["alchemist", "medusa", "lion", "lion", "lion"], ["sven"] * 5))
    assert ctrl.enemies[0] == "alchemist"


def test_item_hint_uses_stats_and_skips_owned():
    ctrl, shown = make()
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True, own_items={"skadi"})
    ctrl.set_enemies(["alchemist", "medusa", "lion"])
    shown.clear()
    ctrl.on_inventory(InventorySnapshot("alchemist", ("heart",), (None, None, None), None), 1200)
    text, voice = shown[-1]
    assert voice == "item_hint"
    assert "У Alchemist появился Heart of Tarrasque" in text
    assert "Spirit Vessel (+12% против Heart of Tarrasque)" in text and "Skadi" not in text
    shown.clear()
    ctrl.on_inventory(InventorySnapshot("sven", ("heart",), (None, None, None), None), 1200)  # союзник
    assert shown == []


def test_dead_reminder_once_per_death():
    ctrl, shown = make()
    ctrl.set_enemies(["alchemist"], show_card=False)
    ctrl.on_game_state(100, True, "npc_dota_hero_sven", "radiant", True)
    ctrl.on_game_state(101, True, "npc_dota_hero_sven", "radiant", False)
    ctrl.on_game_state(102, True, "npc_dota_hero_sven", "radiant", False)
    assert len([t for t, _ in shown if "Кликни" in t]) == 1


def test_manual_item():
    ctrl, shown = make()
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True)
    ctrl.set_enemies(["medusa"], show_card=False)
    ctrl.manual_item("medusa", "heart", 900)
    assert "У Medusa появился Heart of Tarrasque" in shown[-1][0]
