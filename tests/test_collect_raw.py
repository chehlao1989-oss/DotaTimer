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
    from tools.report import report
    run = {"tag": "t", "run_number": 6, "matches": 150000, "matches_per_hour": 31000, "steam_requests": 1900,
           "minutes": 290, "final_pause_sec": 3.2, "steam_429": 80, "opendota_requests": 750, "opendota_left": 2100,
           "ranked_ours": 90000, "ours": 150000, "matches_bytes": 2 * 1024 * 1024, "ranks_bytes": 0}
    lines = report([run], [{"name": "matches-x.parquet", "size": 3 * 1024 * 1024}], None, legacy=64725)
    assert len(lines) == 5
    assert "150 000" in lines[0] and "214 725" in lines[0] and "№6" in lines[0]
    assert "(60%)" in lines[3] and "2.0 МБ" in lines[4] and "3.0 МБ" in lines[4]


def test_ranks_continue_after_minute_429(tmp_path, monkeypatch):
    """429 при живом суточном лимите не останавливает сбор рангов (BUGLOG №18); 429 с остатком ≤ 100 — стоп."""
    import urllib.error
    import tools.collect_raw as cr
    write_parquet([{**r, "match_id": 1000 + k} for k in range(1000) for r in match_rows(match(), PATCHES)[:1]],
                  MATCH_SCHEMA, tmp_path / "matches-t-p00.parquet")
    files = cr.match_files(str(tmp_path / "matches-*.parquet"))
    monkeypatch.setattr(cr.time, "sleep", lambda _s: None)

    def run(day_left_on_429):
        state = {"n": 0}

        def fake(url, timeout=60):
            state["n"] += 1
            if state["n"] <= 2:
                raise urllib.error.HTTPError(url, 429, "Too Many", {"X-Rate-Limit-Remaining-Day": day_left_on_429}, None)
            cursor = int(url.rsplit("=", 1)[1])
            return [{"match_id": cursor - 1, "avg_rank_tier": 45, "num_rank_tier": 3}], {"X-Rate-Limit-Remaining-Day": "2500"}

        monkeypatch.setattr(cr, "http_json_headers", fake)
        return cr.collect_ranks(20, tmp_path, "t", files)

    stats = run("2500")  # минутный лимит: ждём и продолжаем
    assert stats["opendota_429"] == 2 and stats["ranks_stop"] == "дошли до конца диапазона" and stats["ranked_ours"] > 100
    stats = run("50")  # суточный лимит кончился: стоп сразу
    assert stats["opendota_requests"] == 1 and stats["ranks_stop"].startswith("суточный лимит")


def test_report_total_skips_running_parts():
    """Части идущего запуска (-pNN без run-файла) не попадают в «всего» (BUGLOG №19)."""
    from tools.report import legacy_matches
    assets = [{"name": "matches-2026-09-30-0153-p00.parquet", "browser_download_url": "http://нет-сети"}]
    assert legacy_matches(assets, []) == 0


def test_windows_of_neighbour_runs_never_overlap():
    """Окна — на сетке часов UTC; соседние запуски берут разную чётность часов, поэтому не пересекаются при любом
    времени старта (BUGLOG №42: сборы №88 и №108 через 16 ч 46 мин совпали окнами — 22% повторов). Каждый запуск —
    сутки (12 окон через 2 ч), самое свежее окно — не ближе часа к настоящему (BUGLOG №24)."""
    import calendar
    from tools.collect_raw import window_targets
    first = calendar.timegm((2026, 10, 4, 4, 47, 0))
    for gap_minutes in (11 * 60, 12 * 60 + 13, 16 * 60 + 46, 23 * 60 + 59):
        second = first + gap_minutes * 60
        a, b = window_targets(first, 12, 0), window_targets(second, 12, 1)
        assert not set(a) & set(b), gap_minutes
        assert all(t % 3600 == 0 for t in a + b)  # ровно на часах UTC
        assert max(b) <= second - 3600 and max(b) > second - 3 * 3600  # свежее окно: от 1 до 3 ч назад
        assert max(b) - min(b) == 22 * 3600  # 12 окон через 2 ч


def test_next_parity_alternates(monkeypatch):
    """Чётность — противоположная прошлому запуску из его run-*.json; без прошлого — по часу старта."""
    import io
    import json
    import tools.collect_guard as guard
    import tools.collect_raw as cr
    monkeypatch.setattr(guard, "release_assets", lambda repo, release: [
        {"name": "run-2026-10-04-0447.json", "browser_download_url": "https://x/u1"},
        {"name": "run-2026-10-04-2133.json", "browser_download_url": "https://x/u2"}])
    seen = []
    monkeypatch.setattr(cr.urllib.request, "urlopen",
                        lambda req, timeout=60: seen.append(req.full_url) or io.BytesIO(json.dumps({"window_parity": 0}).encode()))
    assert cr.next_window_parity("data-raw", 1780000000) == 1 and seen == ["https://x/u2"]  # по свежайшему run-файлу
    assert cr.next_window_parity(None, 3600 * 7) == 1  # без релиза — по часу старта


def test_seq_for_time_finds_match_near_target(monkeypatch):
    """Поиск номера матча по времени: номера растут равномерно — попадаем в 10 минут за несколько запросов."""
    import tools.collect_raw as cr
    t0, id0, rate = 1_790_000_000, 9_000_000_000, 20.0  # 20 номеров матчей в секунду
    calls = []

    def page(below):
        return [{"match_id": below - k, "start_time": t0 + (below - k - id0) / rate, "match_seq_num": (below - k) // 2}
                for k in range(1, 101)]

    def fake(url, timeout=60):
        calls.append(url)
        if "less_than_match_id=" in url:
            return page(int(url.rsplit("=", 1)[1]))
        return page(id0 + int(rate * 86400 * 2))  # «свежие» матчи — через двое суток

    monkeypatch.setattr(cr, "http_json", fake)
    monkeypatch.setattr(cr.time, "sleep", lambda _s: None)
    target = t0 + 86400
    seq = cr.seq_for_time(target)
    found_id = seq * 2
    assert abs((found_id - id0) / rate + t0 - target) <= cr.SEEK_TOLERANCE_SEC + 10
    assert len(calls) <= 6


def test_collect_jumps_between_windows(tmp_path, monkeypatch):
    """В режиме окон сбор прыгает к следующему окну, когда вышло его время."""
    import tools.collect_raw as cr
    clock = {"t": 0.0}
    monkeypatch.setattr(cr.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(cr.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    jumps = []
    monkeypatch.setattr(cr, "seq_for_time", lambda target: jumps.append(target) or 1000 * len(jumps))

    def fake(url, timeout=60):
        if "constants/patch" in url:
            return [{"name": "7.41", "date": "2026-03-24T00:00:00Z"}]
        clock["t"] += 30  # каждый запрос к Steam — 30 с
        return {"result": {"matches": [{**match(), "match_seq_num": 5}]}}

    monkeypatch.setattr(cr, "http_json", fake)
    stats = cr.collect_matches("key", 10_000, max_minutes=60, out=tmp_path, tag="t", windows=4)
    assert len(jumps) == 4 and stats["windows"] == 4 and stats["window_jumps"] == 3


def test_unexpected_network_error_does_not_stop_collection(tmp_path, monkeypatch):
    """Обрыв ответа Steam (http.client.IncompleteRead — не OSError) не роняет сбор (запуск №9, BUGLOG №33)."""
    import http.client
    import tools.collect_raw as cr
    calls = {"n": 0}

    def fake(url, timeout=60):
        if "constants/patch" in url:
            return [{"name": "7.41", "date": "2026-03-24T00:00:00Z"}]
        calls["n"] += 1
        if calls["n"] == 1:
            raise http.client.IncompleteRead(b"partial")
        return {"result": {"matches": []}}

    monkeypatch.setattr(cr, "http_json", fake)
    monkeypatch.setattr(cr, "anchor_seq_num", lambda: 1)
    monkeypatch.setattr(cr.time, "sleep", lambda _s: None)
    stats = cr.collect_matches("key", 10, 60, tmp_path, "t")
    assert stats["steam_requests"] == 2  # после ошибки сбор продолжился
