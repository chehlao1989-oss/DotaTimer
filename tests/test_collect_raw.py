"""Тесты сборщика сырых матчей (без сети)."""
import pyarrow.parquet as pq

from tools.collect_raw import MATCH_SCHEMA, id_range, match_rows, patch_for, write_parquet

PATCHES = [{"name": "7.40", "date": "2025-12-16T00:00:00Z"}, {"name": "7.41", "date": "2026-03-24T00:00:00Z"}]
MARCH_2026 = 1774310400  # 24.03.2026 00:00 UTC


def match(mode=22, duration=2000, leaver=0, start=MARCH_2026 + 100):
    players = [{"hero_id": 1 + slot, "player_slot": slot if slot < 5 else 123 + slot, "item_0": 116, "item_1": 0,
                "backpack_0": 29, "item_neutral": 1808, "net_worth": 10000 + slot, "gold_per_min": 500,
                "xp_per_min": 600, "kills": 3, "deaths": 2, "assists": 7, "last_hits": 150,
                "leaver_status": leaver if slot == 0 else 0, "account_id": 123456}
               for slot in range(10)]
    return {"match_id": 9000000001, "start_time": start, "duration": duration, "game_mode": mode, "lobby_type": 7,
            "radiant_win": True, "players": players}


def test_filters_from_spec():
    assert len(match_rows(match(), PATCHES)) == 10
    assert match_rows(match(mode=4), PATCHES) == []  # Single Draft — не берём
    assert match_rows(match(duration=14 * 60), PATCHES) == []  # обычный режим короче 15 мин
    assert len(match_rows(match(mode=23, duration=11 * 60), PATCHES)) == 10  # Турбо: от 10 мин
    assert match_rows(match(leaver=2), PATCHES) == []


def test_row_content_and_no_accounts():
    rows = match_rows(match(), PATCHES)
    first = rows[0]
    assert first["items"] == [116, 29] and first["item_neutral"] == 1808 and first["is_radiant"]
    assert not rows[5]["is_radiant"]
    assert "account_id" not in first
    assert first["patch"] == "7.41"
    assert patch_for(MARCH_2026 - 1000, PATCHES) == "7.40"


def test_parquet_roundtrip(tmp_path):
    path = tmp_path / "m.parquet"
    write_parquet(match_rows(match(), PATCHES), MATCH_SCHEMA, path)
    table = pq.read_table(path)
    assert table.num_rows == 10 and table.schema.field("items").type.value_type.bit_width == 16
    assert id_range(path) == (9000000001, 9000000001)


def test_resume_from_previous_run(tmp_path):
    from tools.collect_raw import resume_seq_num
    assert resume_seq_num(None) is None
    assert resume_seq_num(tmp_path) is None  # прошлого запуска нет — старт от якоря
    (tmp_path / "next_seq.txt").write_text("7577293702\n")
    assert resume_seq_num(tmp_path) == 7577293702
