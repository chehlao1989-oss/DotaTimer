"""Тесты связки угроз: карточка, подсказки, напоминание при смерти (без экрана и сети)."""
from app.threats.controller import ThreatSettings, ThreatsController
from app.threats.scoring import load_threat_config
from app.vision.inventory import InventorySnapshot
from app.vision.matcher import Match
from app.vision.topbar import TopbarResult
from tests.test_threats import COUNTERS, DATA


def make(role_mode="auto"):
    shown = []
    ctrl = ThreatsController(DATA, COUNTERS, load_threat_config(), ThreatSettings(role_mode=role_mode, rank_tier=54),
                             lambda text, important, voice: shown.append((text, voice)), lambda: False)
    return ctrl, shown


def topbar(radiant, dire):
    return TopbarResult(tuple(Match(h, 0.8, 0.3) for h in radiant), tuple(Match(h, 0.8, 0.3) for h in dire))


def test_topbar_gives_enemies_and_card():
    ctrl, shown = make()
    assert ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True) is True  # пора снять панель
    heroes = ctrl.on_topbar(topbar(["sven", "sven", "sven", "sven", "sven"], ["alchemist", "medusa", "lion", "lion", "lion"]))
    assert len(heroes) == 10
    assert ctrl.enemies == ["alchemist", "medusa", "lion", "lion", "lion"]
    text, voice = shown[-1]
    assert voice == "threat" and "Главные угрозы" in text and "Alchemist" in text and "Medusa" in text


def test_enemy_side_from_own_hero_when_team_unknown():
    ctrl, _ = make()
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", None, True)
    ctrl.on_topbar(topbar(["alchemist", "medusa", "lion", "lion", "lion"], ["sven", "sven", "sven", "sven", "sven"]))
    assert ctrl.enemies[0] == "alchemist"


def test_item_hint_only_for_threats_and_role():
    ctrl, shown = make(role_mode="support")
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True)
    ctrl.set_enemies(["alchemist", "medusa", "lion"])
    shown.clear()
    ctrl.on_inventory(InventorySnapshot("alchemist", ("heart",), (None, None, None), None), 1200, set())
    assert shown and shown[-1][1] == "item_hint"
    assert "Heart of Tarrasque" in shown[-1][0] and "spirit_vessel" in shown[-1][0]
    shown.clear()
    ctrl.on_inventory(InventorySnapshot("sven", ("heart",), (None, None, None), None), 1200, set())  # союзник
    assert shown == []


def test_dead_reminder_once_per_death():
    ctrl, shown = make()
    ctrl.set_enemies(["alchemist"], show_card=False)
    ctrl.on_game_state(100, True, "npc_dota_hero_sven", "radiant", True)
    ctrl.on_game_state(101, True, "npc_dota_hero_sven", "radiant", False)
    ctrl.on_game_state(102, True, "npc_dota_hero_sven", "radiant", False)
    reminders = [t for t, _ in shown if "Кликни" in t]
    assert len(reminders) == 1


def test_manual_item():
    ctrl, shown = make(role_mode="core")
    ctrl.set_enemies(["medusa"], show_card=False)
    ctrl.manual_item("medusa", "black_king_bar", 900, set())
    assert "Black King Bar" in shown[-1][0] and "nullifier" in shown[-1][0]
