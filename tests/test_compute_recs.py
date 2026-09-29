"""Расчёт советов на искусственных матчах, где правильный ответ известен заранее (спека, раздел 9.2).

Устройство данных:
- Skadi помогает ТОЛЬКО против Heart: +15% к победе, если у врага Heart, иначе 0 → B должен это найти;
- BKB и Heart оба чаще в долгих играх, но между собой не связаны → A для BKB должен быть ≈ 0
  (без страт по длительности «против Heart часто BKB» выглядело бы как сильная адаптация);
- Vessel игроки докупают, увидев Heart (60% против 20%), на победу не влияет → A высокий, B ≈ 0.
"""
import random

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tools.collect_raw import MATCH_SCHEMA
from tools.compute_recs import compute, load_config

HERO, ENEMY = 1, 10
HEART, SKADI, BKB, VESSEL = 114, 160, 116, 267
ITEMS = {
    "heart": {"id": HEART, "cost": 5100, "created": True, "qual": "epic", "components": ["reaver"],
              "abilities": [{"type": "passive", "description": "Your health regeneration is increased."}]},
    "reaver": {"id": 311, "cost": 2800, "qual": "secret_shop"},
    "skadi": {"id": SKADI, "cost": 5900, "created": True, "qual": "artifact", "components": ["ultimate_orb"],
              "abilities": [{"type": "passive", "description": "Attacks reduce Health Restoration by 50%."}]},
    "ultimate_orb": {"id": 24, "cost": 2800, "qual": "secret_shop"},
    "black_king_bar": {"id": BKB, "cost": 4050, "created": True, "qual": "epic", "components": ["ogre_axe"],
                       "abilities": [{"type": "active", "description": "Applies a basic dispel. Grants magic resistance."}]},
    "ogre_axe": {"id": 21, "cost": 1000, "qual": "component"},
    "spirit_vessel": {"id": VESSEL, "cost": 2725, "created": True, "qual": "rare", "components": ["urn_of_shadows"],
                      "abilities": [{"type": "active", "description": "Reduces Health Restoration by 70%."}]},
    "urn_of_shadows": {"id": 92, "cost": 840, "created": True, "qual": "rare", "components": []},
}
HEROES = {str(h): {"id": h, "name": f"npc_dota_hero_h{h}", "roles": []} for h in (1, 2, 3, 4, 5, 10, 11, 12, 13, 14)}


def make_matches(path, n=6000, seed=7):
    rng = random.Random(seed)
    rows = []
    for m in range(n):
        long = rng.random() < 0.5
        duration = (50 if long else 20) * 60
        x = rng.random() < (0.7 if long else 0.2)
        has = {
            BKB: rng.random() < (0.7 if long else 0.2),  # зависит только от длительности
            VESSEL: rng.random() < (0.6 if x else 0.2),  # адаптация к Heart
            SKADI: rng.random() < 0.3,
        }
        win = rng.random() < 0.5 + (0.15 if (has[SKADI] and x) else 0.0)
        for slot in range(10):
            radiant = slot < 5
            hero = [1, 2, 3, 4, 5][slot] if radiant else [10, 11, 12, 13, 14][slot - 5]
            items = []
            if hero == HERO:
                items = [i for i, on in has.items() if on]
            if hero == ENEMY and x:
                items = [HEART]
            rows.append({"match_id": m, "start_time": 1780000000, "duration": duration, "game_mode": 22,
                         "lobby_type": 7, "radiant_win": win, "patch": "7.41", "is_radiant": radiant,
                         "hero_id": hero, "items": items, "item_neutral": 0,
                         "net_worth": 30000 if hero == HERO else 10000 - slot, "gold_per_min": 500, "xp_per_min": 500,
                         "kills": 0, "deaths": 0, "assists": 0, "last_hits": 0})
    pq.write_table(pa.Table.from_pylist(rows, schema=MATCH_SCHEMA), path)


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    folder = tmp_path_factory.mktemp("raw")
    make_matches(folder / "matches-test.parquet")
    return compute(str(folder / "matches-*.parquet"), None, ITEMS, {}, {}, HEROES, load_config(), log=lambda *_: None)


def answers(result, level, ctx):
    table = result[level]["normal"]["all"]["core"][str(HERO)]
    return {row[0]: row for row in table[ctx]}


def test_effect_finds_skadi_only_against_heart(result):
    rows = answers(result, "item", str(HEART))
    assert rows["skadi"][3] > 8  # B*, п.п.: истинная разность разностей +15 (сглаживание её уменьшает)
    assert abs(rows["spirit_vessel"][3]) < 5  # Vessel на победу не влияет
    assert max(rows, key=lambda y: rows[y][1]) in ("skadi", "spirit_vessel")


def test_adaptation_not_fooled_by_game_length(result):
    rows = answers(result, "item", str(HEART))
    assert rows["spirit_vessel"][2] > 25  # A*, п.п.: истинно +40
    assert abs(rows["black_king_bar"][2]) < 8  # BKB чаще в долгих играх, но не «против Heart»


def test_hero_level_and_meta(result):
    rows = answers(result, "item_hero", f"{ENEMY}:{HEART}")
    assert "skadi" in rows
    assert result["meta"]["matches"] == 6000
    assert result["meta"]["ranked_share"] == 0


def test_answer_rows_have_source_flag(result):
    for row in answers(result, "item", str(HEART)).values():
        assert len(row) == 7 and isinstance(row[6], str)  # правило механики или «exp» — по опыту игроков


def test_class_fallback_and_threat(result):
    cls, role = result["meta"]["hero_class"][str(HERO)]
    assert role == "core" and cls == "all-core"  # в тестовых героях нет атрибута → «all»
    assert str(HEART) in result["item_class"]["normal"]["all"]["core"][cls]
    threat = result["threat"]["normal"]["all"][str(HERO)]
    assert threat["roles"] == {"core": 1.0}  # у тестового героя всегда самый большой нетворс
    assert str(ENEMY) in result["threat"]["normal"]["all"] and "vs" in result["threat"]["normal"]["all"][str(ENEMY)]


def test_recency_weight_halves_every_half_life(tmp_path):
    import duckdb
    from tools.compute_recs import ItemMeta, prepare
    cfg = load_config()
    rows = [{"match_id": m, "start_time": 1780000000 - m * int(cfg.half_life_days * 86400), "duration": 1800,
             "game_mode": 22, "lobby_type": 7, "radiant_win": True, "patch": "7.41", "is_radiant": slot < 5,
             "hero_id": slot + 1, "items": [], "item_neutral": 0, "net_worth": 1000 - slot, "gold_per_min": 0,
             "xp_per_min": 0, "kills": 0, "deaths": 0, "assists": 0, "last_hits": 0}
            for m in range(3) for slot in range(10)]
    pq.write_table(pa.Table.from_pylist(rows, schema=MATCH_SCHEMA), tmp_path / "matches-a.parquet")
    con = duckdb.connect()
    prepare(con, str(tmp_path / "matches-*.parquet"), None, ItemMeta(set(), set(), [], {}, {}, []), HEROES, cfg)
    weights = [round(w, 3) for (w,) in con.execute("SELECT DISTINCT wt FROM pg ORDER BY wt DESC").fetchall()]
    assert weights == [1.0, 0.5, 0.25]
