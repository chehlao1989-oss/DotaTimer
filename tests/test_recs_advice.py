"""Угрозы и советы в игре из recs.zip (app/threats/recs_advice.py)."""
from app.threats.data import Hero, Item
from app.threats.recommend import ThreatInput
from app.threats.recs_advice import AdviceConfig, RecsRecommender, RecsSource, rank_threats
from app.threats.recs_file import write_recs_zip
from app.threats.threat_score import ThreatScoreConfig

ME, AXE, PA = 8, 2, 44
ITEMS = {
    "orchid": Item("orchid", 98, "Orchid Malevolence", 3275),
    "bloodthorn": Item("bloodthorn", 250, "Bloodthorn", 6625, components=("orchid",)),
    "monkey_king_bar": Item("monkey_king_bar", 135, "Monkey King Bar", 4700),
    "black_king_bar": Item("black_king_bar", 116, "Black King Bar", 4050),
    "blade_mail": Item("blade_mail", 127, "Blade Mail", 2100),
    "butterfly": Item("butterfly", 139, "Butterfly", 5450),
}
META = {"row_format": ["item_id", "S", "A_pp", "B_pp", "conf", "games", "source"], "conf_codes": ["low", "mid", "high"],
        "flags": ["exp", "evasion", "disables"], "hero_class": {str(ME): ["agi-core", "core"]}, "matches": 100}


def row(item, s, flag=0):
    return [ITEMS[item].id, s, 5.0, 1.0, 2, 1000, flag]


RESULT = {
    "meta": META,
    "threat": {"normal": {"all": {
        str(AXE): {"roles": {"offlane": 0.9, "support": 0.1}, "late": [-2.0, 900, 900], "vs": {str(ME): [-1.0, 900]}},
        str(PA): {"roles": {"core": 1.0}, "late": [6.0, 900, 900], "vs": {str(ME): [-5.0, 900]}},
    }}},
    "hero": {"normal": {"all": {"core": {str(ME): {
        str(PA): [row("monkey_king_bar", 3.0, 1), row("black_king_bar", 2.0), row("orchid", 1.0)],
        str(AXE): [row("black_king_bar", 2.5, 2), row("blade_mail", 1.5)],
    }}}}},
    "item": {"normal": {"all": {"core": {str(ME): {
        str(ITEMS["butterfly"].id): [row("monkey_king_bar", 4.0, 1), row("bloodthorn", 2.0, 1)],
    }}}}},
    "item_hero": {}, "item_class": {}, "hero_class": {},
}
CFG = AdviceConfig(phase_base_cost=2500, phase_cost_per_min=200, multi_threat_bonus=0.5, hysteresis=0.5, top_for_bonus=3)
SCORE = ThreatScoreConfig(3.0, 0.5, 0.5, 0.5, 200, 150, 1.8, 2.8, 3.0, 3.0)


def source(tmp_path):
    path = tmp_path / "recs.zip"
    write_recs_zip(RESULT, path)
    return RecsSource(path)


def test_threats_from_recs_file(tmp_path):
    heroes = [Hero(AXE, "axe", "Axe"), Hero(PA, "phantom_assassin", "Phantom Assassin")]
    threats = rank_threats(source(tmp_path), heroes, ME, "normal", SCORE, describe=lambda r: str(sorted(r.items())))
    assert [t.hero.name for t in threats] == ["phantom_assassin", "axe"]  # керри, сильна в лейте, мой герой ей проигрывает
    assert "late" in threats[0].note and "matchup" in threats[0].note and "'carry'" in threats[0].note
    assert "late" not in threats[1].note


def test_item_answers_first_then_hero_and_slots(tmp_path):
    rec = RecsRecommender(source(tmp_path), ITEMS, CFG)
    pa = ThreatInput("phantom_assassin", PA, frozenset({"butterfly"}))
    result = rec.recommend(ME, [pa], set(), game_time=20 * 60, my_gold=5000)[0]
    assert [i.item for i in result.items] == ["monkey_king_bar", "bloodthorn"]  # сначала ответы на Butterfly
    assert result.items[0].affordable  # 5000 золота ≥ 4700


def test_now_slot_respects_game_phase(tmp_path):
    rec = RecsRecommender(source(tmp_path), ITEMS, CFG)
    pa = ThreatInput("phantom_assassin", PA, frozenset())
    # 5-я минута: бюджет фазы 2500 + 200·5 = 3500 → «сейчас» — Orchid (3275), «потом» — лучший вообще (MKB)
    result = rec.recommend(ME, [pa], set(), game_time=5 * 60)[0]
    assert [i.item for i in result.items] == ["orchid", "monkey_king_bar"]


def test_bought_counts_upgrades(tmp_path):
    rec = RecsRecommender(source(tmp_path), ITEMS, CFG)
    pa = ThreatInput("phantom_assassin", PA, frozenset())
    result = rec.recommend(ME, [pa], {"bloodthorn"}, game_time=30 * 60)[0]
    assert result.items[0].bought and result.items[0].item == "orchid"  # Orchid засчитан: собран Bloodthorn
    assert "orchid" not in [i.item for i in result.items[1:]]


def test_hysteresis_keeps_previous_advice(tmp_path):
    rec = RecsRecommender(source(tmp_path), ITEMS, CFG)
    pa = ThreatInput("phantom_assassin", PA, frozenset())
    first = rec.recommend(ME, [pa], set(), game_time=30 * 60)[0].items[0].item
    assert first == "monkey_king_bar"
    # враг собрал Butterfly: MKB и так первый — совет не «мигает», changed = False
    again = rec.recommend(ME, [ThreatInput("phantom_assassin", PA, frozenset({"butterfly"}))], set(), 30 * 60)[0]
    assert again.items[0].item == "monkey_king_bar" and not again.items[0].changed


def test_multi_threat_bonus_and_counters(tmp_path):
    rec = RecsRecommender(source(tmp_path), ITEMS, CFG)
    threats = [ThreatInput("phantom_assassin", PA, frozenset()), ThreatInput("axe", AXE, frozenset())]
    result = rec.recommend(ME, threats, set(), game_time=30 * 60)
    bkb_vs_axe = next(i for i in result[1].items if i.item == "black_king_bar")
    assert bkb_vs_axe.shared == 2 and bkb_vs_axe.score == 3.0  # 2.5 + бонус 0.5: BKB в топ-3 против обеих угроз
    assert rec.counters(ME, PA, "butterfly") == ["monkey_king_bar", "bloodthorn"]
    assert rec.recommend(999, threats, set(), 60) == []  # героя нет в файле


def test_new_enemy_item_switches_advice_at_once(tmp_path):
    """Гистерезис не держит старый совет, если у врага появился новый предмет (проверка связки 30.09)."""
    rec = RecsRecommender(source(tmp_path), ITEMS, CFG)
    rec.recommend(ME, [ThreatInput("axe", AXE, frozenset())], set(), 30 * 60)  # против Axe: BKB (S 2.5)
    after = rec.recommend(ME, [ThreatInput("axe", AXE, frozenset({"butterfly"}))], set(), 30 * 60)[0]
    assert after.items[0].item == "monkey_king_bar" and after.items[0].changed  # ответ на Butterfly — сразу


def test_threat_role_is_most_played_role(tmp_path):
    heroes = [Hero(AXE, "axe", "Axe")]
    threats = rank_threats(source(tmp_path), heroes, ME, "normal", SCORE, describe=lambda r: r.get("role"))
    assert threats[0].note == "offlane"  # Axe в тройке 90% игр
