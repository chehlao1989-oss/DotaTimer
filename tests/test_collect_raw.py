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


def test_part_saver_moves_next_seq_only_after_upload(tmp_path):
    """next_seq двигается только когда все части до него выложены; невыложенная часть уходит со следующей."""
    from tools.collect_raw import PartSaver
    ok = {"value": False}
    uploaded = []

    def upload(_release, paths):
        if ok["value"]:
            uploaded.extend(p.name for p in paths)
        return ok["value"]

    saver = PartSaver(tmp_path, "t", "data-raw", upload=upload)
    rows = match_rows(match(), PATCHES)
    assert not saver.save(rows, 100)  # выгрузка упала
    assert saver.saved_seq is None and not (tmp_path / "next_seq.txt").exists()
    ok["value"] = True
    assert saver.save(rows, 200)
    assert uploaded == ["matches-t-p00.parquet", "matches-t-p01.parquet", "next_seq.txt"]
    assert (tmp_path / "next_seq.txt").read_text() == "200" and saver.saved_seq == 200


def test_retry_after_header():
    from tools.collect_raw import RETRY_AFTER_DEFAULT_SEC, retry_after

    class Err:
        def __init__(self, headers):
            self.headers = headers

    assert retry_after(Err({"Retry-After": "7"})) == 7.0
    assert retry_after(Err({})) == RETRY_AFTER_DEFAULT_SEC
    assert retry_after(ValueError()) == RETRY_AFTER_DEFAULT_SEC


def test_ranks_stop_by_daily_limit_header(tmp_path, monkeypatch):
    """Число запросов OpenDota — по заголовку остатка лимита; при остатке 100 сбор останавливается."""
    import tools.collect_raw as cr
    write_parquet([{**r, "match_id": 1000 + k} for k in range(1000) for r in match_rows(match(), PATCHES)[:1]],
                  MATCH_SCHEMA, tmp_path / "matches-t-p00.parquet")
    left = {"n": 103}
    calls = []

    def fake(url, timeout=60):
        calls.append(url)
        left["n"] -= 1
        cursor = int(url.rsplit("=", 1)[1])
        return [{"match_id": cursor - 1, "avg_rank_tier": 45, "num_rank_tier": 3}], {"X-Rate-Limit-Remaining-Day": str(left["n"])}

    monkeypatch.setattr(cr, "http_json_headers", fake)
    monkeypatch.setattr(cr.time, "sleep", lambda _s: None)
    stats = cr.collect_ranks(20, tmp_path, "t", cr.match_files(str(tmp_path / "matches-*.parquet")))
    assert stats["opendota_requests"] == 3 and stats["opendota_left"] == 100
    assert stats["ranked_ours"] >= 1


def test_run_report_lines():
    from tools.run_report import report
    run = {"tag": "t", "run_number": 6, "matches": 150000, "matches_per_hour": 31000, "steam_requests": 1900,
           "minutes": 290, "final_pause_sec": 3.2, "steam_429": 80, "opendota_requests": 750, "opendota_left": 2100,
           "ranked_ours": 90000, "ours": 150000, "matches_bytes": 2 * 1024 * 1024, "ranks_bytes": 0}
    lines = report([run], [{"name": "matches-x.parquet", "size": 3 * 1024 * 1024}], None, legacy=64725)
    assert len(lines) == 5
    assert "150 000" in lines[0] and "214 725" in lines[0] and "№6" in lines[0]
    assert "(60%)" in lines[3] and "2.0 МБ" in lines[4] and "3.0 МБ" in lines[4]
