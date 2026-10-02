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


def metrics_config():
    """Настройки без порога «настоящего контр-предмета»: тесты формул A и B смотрят и на слабые советы (DECISIONS №22)."""
    import dataclasses
    return dataclasses.replace(load_config(), counter_min_a_pp=None)


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    folder = tmp_path_factory.mktemp("raw")
    make_matches(folder / "matches-test.parquet")
    make_matches(folder / "matches-test-copy.parquet", n=100)  # повтор первых 100 матчей — не должен считаться дважды
    return compute(str(folder / "matches-*.parquet"), None, ITEMS, {}, {}, HEROES, metrics_config(), log=lambda *_: None)


def answers(result, level, ctx):
    """Строки ответов по ключу предмета; номер предмета, уверенность и источник раскодированы по meta."""
    names = {v["id"]: k for k, v in ITEMS.items()}
    meta = result["meta"]
    table = result[level]["normal"]["all"]["core"][str(HERO)]
    return {names[row[0]]: [names[row[0]], row[1], row[2], row[3], meta["conf_codes"][row[4]], row[5],
                            meta["flags"][row[6]]] for row in table[ctx]}


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
    raw = result["item"]["normal"]["all"]["core"][str(HERO)][str(HEART)]
    assert all(len(row) == 7 and isinstance(row[0], int) for row in raw)  # компактная запись: номер предмета
    assert result["meta"]["row_format"][0] == "item_id" and result["meta"]["flags"][0] == "exp"
    for row in answers(result, "item", str(HEART)).values():
        assert isinstance(row[6], str)  # правило механики или «exp» — по опыту игроков


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


def test_components_and_boots_filtered(tmp_path):
    """DECISIONS №14: сапоги не ответ (кроме гривов); промежуточный — только если с ним заканчивают игру."""
    import duckdb
    from app.threats.mechanics import MechanicsTagger, load_mechanics_config
    from tools.compute_recs import build_item_meta, prepare
    items = {
        "big": {"id": 501, "cost": 3000, "created": True, "components": ["comp_a"]},
        "comp_a": {"id": 502, "cost": 1500, "created": True, "components": []},  # обычно доделывают → убрать
        "big_b": {"id": 503, "cost": 3000, "created": True, "components": ["comp_b"]},
        "comp_b": {"id": 504, "cost": 1500, "created": True, "components": []},  # обычно так и заканчивают → оставить
        "boots": {"id": 29, "cost": 500},
        "power_treads": {"id": 63, "cost": 1400, "created": True, "components": ["boots"]},
        "arcane_boots": {"id": 180, "cost": 1400, "created": True, "components": ["boots"]},
        "guardian_greaves": {"id": 231, "cost": 5000, "created": True, "components": ["arcane_boots"]},
    }
    inventories = [[501]] * 10 + [[502]] * 2 + [[504]] * 8 + [[503]] * 2 + [[63, 231]] * 3
    rows = []
    for m, inv in enumerate(inventories):
        for slot in range(10):
            rows.append({"match_id": m, "start_time": 1780000000, "duration": 1800, "game_mode": 22, "lobby_type": 7,
                         "radiant_win": True, "patch": "7.41", "is_radiant": slot < 5, "hero_id": slot + 1,
                         "items": inv if slot == 0 else [], "item_neutral": 0, "net_worth": 1000 - slot,
                         "gold_per_min": 0, "xp_per_min": 0, "kills": 0, "deaths": 0, "assists": 0, "last_hits": 0})
    pq.write_table(pa.Table.from_pylist(rows, schema=MATCH_SCHEMA), tmp_path / "matches-a.parquet")
    cfg = load_config()
    tagger = MechanicsTagger(load_mechanics_config(), items, {}, {}, {})
    meta = build_item_meta(items, tagger, HEROES, cfg)
    con = duckdb.connect()
    prepare(con, str(tmp_path / "matches-*.parquet"), None, meta, HEROES, cfg)
    answers = {y for (y,) in con.execute("SELECT DISTINCT y FROM expand").fetchall()}
    assert answers == {501, 503, 504, 231}  # big, big_b, comp_b, guardian_greaves


def test_empirical_bayes_finds_true_spread():
    """BUGLOG №21: разброс τ² и k = σ²/τ² находятся и при ячейках с огромной дисперсией (доля игры)."""
    import duckdb
    from tools.compute_recs import eb_spread
    rng = random.Random(3)
    rows = []
    for _ in range(3000):  # истинный разброс τ = 0.05, дисперсия на игру σ² = 0.25 → k = 100
        n = rng.choice([50, 200, 1000, 3000])
        rows.append((rng.gauss(0, 0.05) + rng.gauss(0, (0.25 / n) ** 0.5), 0.25 / n, n))
    for _ in range(500):  # ячейки с долей игры: шум огромный
        rows.append((rng.gauss(0, 0.9), 0.8, 0.3))
    con = duckdb.connect()
    con.execute("CREATE TABLE cells(A DOUBLE, vA DOUBLE, n DOUBLE)")
    con.executemany("INSERT INTO cells VALUES (?, ?, ?)", rows)
    found = eb_spread(con, "cells", "A", 999.0, min_games=100)
    assert found["estimated"] and 0.0018 < found["tau2"] < 0.0035 and 60 < found["k"] < 170  # истинно τ² = 0.0025, k = 100
    assert not 60 < eb_spread(con, "cells", "A", 999.0)["k"] < 170  # без отбора ячеек оценка смещена (замер теста: 270)


def test_variances_are_sane_with_fractional_weights(tmp_path):
    """BUGLOG №21: дробные веса по давности не дают дисперсиям уйти в миллионы или в минус."""
    import duckdb
    from app.threats.mechanics import MechanicsTagger, load_mechanics_config
    from tools.compute_recs import build_item_meta, level_metrics, prepare
    rng = random.Random(11)
    rows = []
    for m in range(3000):
        start = 1780000000 - rng.randrange(0, 40 * 86400)  # веса по давности — дробные
        items_hero = [SKADI] if rng.random() < 0.4 else []
        win = rng.random() < 0.5
        for slot in range(10):
            radiant = slot < 5
            hero = [1, 2, 3, 4, 5][slot] if radiant else [10, 11, 12, 13, 14][slot - 5]
            rows.append({"match_id": m, "start_time": start, "duration": 1800, "game_mode": 22, "lobby_type": 7,
                         "radiant_win": win, "patch": "7.41", "is_radiant": radiant, "hero_id": hero,
                         "items": items_hero if hero == HERO else ([HEART] if hero == ENEMY else []),  # X есть всегда
                         "item_neutral": 0, "net_worth": 30000 if hero == HERO else 10000 - slot, "gold_per_min": 0,
                         "xp_per_min": 0, "kills": 0, "deaths": 0, "assists": 0, "last_hits": 0})
    pq.write_table(pa.Table.from_pylist(rows, schema=MATCH_SCHEMA), tmp_path / "matches-a.parquet")
    cfg = load_config()
    meta = build_item_meta(ITEMS, MechanicsTagger(load_mechanics_config(), ITEMS, {}, {}, {}), HEROES, cfg)
    con = duckdb.connect()
    prepare(con, str(tmp_path / "matches-*.parquet"), None, meta, HEROES, cfg)
    for level in ("L2", "hero"):
        level_metrics(con, level, "true", "met")
        worst = con.execute("SELECT max(vA), min(vA), max(vB), min(vB) FROM met").fetchone()
        assert all(v is None or 0 <= v < 1 for v in worst), (level, worst)


def test_recs_zip_by_hero(result, tmp_path):
    """DECISIONS №16–17: архив по героям, программа читает только своего героя; редкие роли не пишутся."""
    from app.threats.recs_file import load_all, load_hero, load_meta, write_recs_zip
    path = tmp_path / "recs.zip"
    write_recs_zip(result, path, min_role_share=0.1)
    mine = load_hero(path, HERO)
    assert str(HEART) in mine["item"]["normal"]["all"]["core"]
    assert load_hero(path, 999) is None
    assert load_meta(path)["row_format"][0] == "item_id"
    back = load_all(path)
    assert back["item"]["normal"]["all"]["core"][str(HERO)] == result["item"]["normal"]["all"]["core"][str(HERO)]
    assert "support" not in mine["item"]["normal"]["all"]  # тестовый герой всегда кор — роль саппорта не пишется


def test_buys_list_for_program_filter(result, tmp_path):
    """Спека 6.4: в файле советов — что герой покупает ≥ 3% игр в роли; попадает в кусок героя в архиве."""
    from app.threats.recs_file import load_hero, write_recs_zip
    buys = result["buys"]["normal"]["core"][str(HERO)]
    assert {SKADI, BKB, VESSEL} <= set(buys)
    path = tmp_path / "recs.zip"
    write_recs_zip(result, path)
    assert set(load_hero(path, HERO)["buys"]["normal"]["core"]) == set(buys)


def test_mechanic_answers_first(tmp_path):
    """DECISIONS №18: сначала ответы по механике, потом «по опыту»; внутри группы — по S; сам S не меняется."""
    import dataclasses
    folder = tmp_path / "raw"
    folder.mkdir()
    make_matches(folder / "matches-test.parquet", n=2000)
    base = metrics_config()
    args = (str(folder / "matches-*.parquet"), None, ITEMS, {}, {}, HEROES)
    first = compute(*args, base, log=lambda *_: None)
    plain = compute(*args, dataclasses.replace(base, mechanic_first=False), log=lambda *_: None)
    flags = first["meta"]["flags"]
    rows = first["item"]["normal"]["all"]["core"][str(HERO)][str(HEART)]
    # первыми — ответы по механике, которые против этого врага берут чаще (A > 0, mechanic_first_min_a = 0)
    is_exp = [not (flags[r[6]] != "exp" and r[2] > base.mechanic_first_min_a) for r in rows]
    assert is_exp == sorted(is_exp)
    for group in (False, True):
        s_values = [r[1] for r, e in zip(rows, is_exp) if e == group]
        assert s_values == sorted(s_values, reverse=True)
    plain_rows = plain["item"]["normal"]["all"]["core"][str(HERO)][str(HEART)]
    assert sorted(r[1] for r in rows) == sorted(r[1] for r in plain_rows)  # S тот же, меняется только порядок


def test_compute_is_reproducible(result, tmp_path):
    """Те же данные — тот же результат до последней цифры (BUGLOG №40: ничьи по нетворсу и в сортировке советов
    решались случайно, два расчёта на одних данных расходились в 20 468 значениях из 225 608)."""
    folder = tmp_path / "raw"
    folder.mkdir()
    make_matches(folder / "matches-test.parquet")
    make_matches(folder / "matches-test-copy.parquet", n=100)
    again = compute(str(folder / "matches-*.parquet"), None, ITEMS, {}, {}, HEROES, metrics_config(), log=lambda *_: None)
    strip = lambda r: {**r, "meta": {k: v for k, v in r["meta"].items() if k not in ("generated_at", "seconds")}}  # noqa: E731
    assert strip(again) == strip(result)


def test_counter_threshold_keeps_only_real_counters(tmp_path):
    """DECISIONS №22: совет — только если A значим и ≥ порога. Vessel против Heart (A ≈ +40 п.п.) остаётся;
    BKB (берут в долгих играх, не против Heart) и Skadi (помогает выиграть, но берут не чаще — только B) уходят."""
    folder = tmp_path / "raw"
    folder.mkdir()
    make_matches(folder / "matches-test.parquet")
    res = compute(str(folder / "matches-*.parquet"), None, ITEMS, {}, {}, HEROES, load_config(), log=lambda *_: None)
    rows = answers(res, "item", str(HEART))
    assert "spirit_vessel" in rows and "black_king_bar" not in rows and "skadi" not in rows
