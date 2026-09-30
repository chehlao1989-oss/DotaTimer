"""Проверки советов (спека, раздел 8) на маленьком ручном наборе."""
from tools.validate_recs import check_benchmark, report_markdown

RECS = {
    "meta": {"matches": 10, "patch": "7.41", "ranked_share": 0.5, "hero_class": {"1": ["agi-core", "core"]}},
    "hero": {"normal": {"all": {"core": {"1": {
        "2": [["monkey_king_bar", 3, 5, 1, "high", 2000, "exp"], ["bfury", 2, 1, 0, "high", 2000, "exp"]],
        "3": [["bfury", 3, 5, 1, "low", 50, "exp"]],
    }}}}},
    "item": {},
}
BENCH = {"heroes": {
    "pa": {"hero_id": 2, "counter_items": ["monkey_king_bar", "tango"]},
    "axe": {"hero_id": 3, "counter_items": ["blade_mail"]},
    "nobody": {"hero_id": 4, "counter_items": ["tango"]},  # только расходник — пара не считается
}}


def test_benchmark_top3_share():
    res = check_benchmark(RECS, BENCH, {"monkey_king_bar", "bfury", "blade_mail"})
    assert (res["pairs"], res["hits"]) == (2, 1)
    assert (res["pairs_confident"], res["hits_confident"]) == (1, 1)
    assert res["misses"] == [(1, 3, ["bfury"])]


def test_report_has_table():
    heroes = {"1": {"id": 1, "name": "npc_dota_hero_juggernaut", "localized_name": "Juggernaut"},
              "2": {"id": 2, "name": "npc_dota_hero_phantom_assassin", "localized_name": "Phantom Assassin"}}
    text = report_markdown(RECS, heroes)
    assert "## Juggernaut (core)" in text and "**monkey_king_bar**" in text


def test_benchmark_reachable_pairs():
    """Достижимые пары: герой покупает хотя бы один вики-предмет (иначе программа его не покажет, спека 6.4)."""
    recs = {**RECS, "buys": {"normal": {"core": {"1": ["monkey_king_bar"]}}}}
    res = check_benchmark(recs, BENCH, {"monkey_king_bar", "bfury", "blade_mail"})
    assert (res["pairs_reachable"], res["hits_reachable"]) == (1, 1)  # vs PA достижима и попала; vs Axe (blade_mail) — нет
