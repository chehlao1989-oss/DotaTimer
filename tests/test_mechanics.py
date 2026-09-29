"""Тесты слоя 1: разметка механик по описаниям Valve (на маленьких примерах из dotaconstants)."""
from app.threats.mechanics import MechanicsTagger, load_mechanics_config

ITEMS = {
    "spirit_vessel": {"cost": 2725, "abilities": [{"type": "active", "description":
        "When used against enemies, it reduces health by 4% of current health per second, and reduces Health Restoration by 70%."}]},
    "monkey_king_bar": {"cost": 5000, "abilities": [{"type": "passive", "description":
        "Grants each attack a 80% chance to pierce through evasion and deal 70 bonus magical damage."}]},
    "butterfly": {"cost": 5450, "abilities": [], "attrib": [{"key": "bonus_evasion", "value": "35"}]},
    "heart": {"cost": 5100, "abilities": [{"type": "passive", "description":
        "Your health regeneration is increased by 1.5% of your missing health."}]},
    "quelling_blade": {"cost": 100, "abilities": [{"type": "active", "description": "Destroy a target tree."}]},
    "rod_of_atos": {"cost": 2250, "abilities": [{"type": "active", "description": "Roots the target for 2 seconds."}]},
    "mask_of_madness": {"cost": 1900, "abilities": [{"type": "active", "description":
        "Gives 100 attack speed, but reduces your armor by 7 and silences you."}]},
}
ABILITIES = {
    "phantom_assassin_blur": {"behavior": "Passive", "desc": "Grants evasion and blurs her body."},
    "phantom_assassin_coup_de_grace": {"behavior": "Passive", "desc": "Chance to deal a critical strike."},
    "alchemist_chemical_rage": {"behavior": "No Target", "desc": "Increases movement speed and health regeneration."},
}
HERO_ABILITIES = {
    "npc_dota_hero_phantom_assassin": {"abilities": ["phantom_assassin_blur", "phantom_assassin_coup_de_grace"]},
    "npc_dota_hero_alchemist": {"abilities": ["alchemist_chemical_rage"]},
}


def make():
    return MechanicsTagger(load_mechanics_config(), ITEMS, ABILITIES, HERO_ABILITIES,
                           {"alchemist": ("Carry", "Nuker")})


def test_rules_are_the_approved_fifteen():
    assert len(make().rules) == 15


def test_item_answers():
    t = make()
    assert "heal_reduction" in t.item_answers("spirit_vessel")
    assert "true_strike" in t.item_answers("monkey_king_bar")
    assert "cheap_target" in t.item_answers("rod_of_atos")
    assert "cheap_target" not in t.item_answers("quelling_blade")  # «цель — дерево» не считается
    assert "silence" not in t.item_answers("mask_of_madness")  # молчит сам владелец
    assert "armor_reduction" not in t.item_answers("mask_of_madness")


def test_enemy_traits():
    t = make()
    assert "evasion" in t.item_traits("butterfly")  # уклонение только в характеристиках
    assert "heal" in t.item_traits("heart")
    assert {"evasion", "passives"} <= t.hero_traits("phantom_assassin")
    assert {"heal", "magic_damage"} <= t.hero_traits("npc_dota_hero_alchemist")  # магия — по роли Nuker


def test_candidates_for_rule():
    t = make()
    heal = next(r for r in t.rules if r.id == "heal")
    assert t.candidate_items(heal, set(ITEMS)) == {"spirit_vessel"}
