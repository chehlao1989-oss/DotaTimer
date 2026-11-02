"""Замена ровно N уникальных матчей, целостность партий и безопасная публикация окна."""
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tests.test_collect_raw import PATCHES, match
from tools.collect_raw import MATCH_SCHEMA, match_rows
from tools import raw_window as window


def write(directory, ids, name="matches-part.parquet", times=None):
    directory.mkdir(exist_ok=True)
    rows = []
    for i in ids:
        game = match(start=1774310500 + (times or {}).get(i, i))
        game["match_id"] = i
        rows.extend(match_rows(game, PATCHES))
    pq.write_table(pa.Table.from_pylist(rows, schema=MATCH_SCHEMA), directory / name)


def ids(directory):
    return {i for p in directory.glob("matches-*.parquet")
            for i in pq.read_table(p, columns=["match_id"]).column("match_id").to_pylist()}


def test_replaces_exact_unique_count_and_all_players(tmp_path, monkeypatch):
    old, new, out = [tmp_path / n for n in ("old", "new", "out")]
    write(old, [1, 2, 3, 4, 5], times={1: 50, 2: 10, 3: 20, 4: 30, 5: 40})
    write(new, [4, 6, 7, 7])  # повторы в окне и в партии ничего не вытесняют
    monkeypatch.setattr(window, "CHUNK_MATCHES", 2)
    result = window.rotate(old, new, out, "2026-11-02-1200")
    assert result["added"] == result["removed"] == 2
    assert result["matches"] == result["capacity"] == 5
    assert ids(out) == {1, 4, 5, 6, 7}  # старые определяются временем, а не номером
    assert sum(pq.read_metadata(p).num_rows for p in out.glob("matches-*.parquet")) == 50
    assert len(list(out.glob("matches-*.parquet"))) == 3


def test_duplicates_delete_nothing_and_capacity_survives_restart(tmp_path):
    old, new, out, next_out = [tmp_path / n for n in ("old", "new", "out", "next")]
    write(old, [1, 2, 3])
    write(new, [2, 3])
    result = window.rotate(old, new, out, "a")
    assert result["removed"] == 0 and ids(out) == {1, 2, 3}
    write(new, [4])
    result = window.rotate(out, new, next_out, "b", capacity=result["capacity"])
    assert result["removed"] == 1 and ids(next_out) == {2, 3, 4}


def test_incomplete_match_does_not_produce_snapshot(tmp_path):
    old, new, out = [tmp_path / n for n in ("old", "new", "out")]
    write(old, [1, 2])
    write(new, [3])
    p = new / "matches-part.parquet"
    pq.write_table(pq.read_table(p).slice(0, 9), p)
    with pytest.raises(ValueError, match="Неполные"):
        window.rotate(old, new, out, "a")
    assert not list(out.glob("matches-*.parquet"))


def test_ranks_expire_with_removed_matches(tmp_path):
    old, new, out = [tmp_path / n for n in ("old", "new", "out")]
    write(old, [1, 2, 3])
    write(new, [4])
    pq.write_table(pa.table({"match_id": [1, 2, 3], "avg_rank_tier": [30, 40, 50],
                            "num_rank_tier": [8, 8, 8]}), old / "ranks-old.parquet")
    pq.write_table(pa.table({"match_id": [4], "avg_rank_tier": [60], "num_rank_tier": [8]}),
                   new / "ranks-new.parquet")
    window.rotate(old, new, out, "a")
    assert set(pq.read_table(out / "ranks-a.parquet")["match_id"].to_pylist()) == {2, 3, 4}


def test_resolver_ignores_unfinished_release(tmp_path, monkeypatch):
    monkeypatch.delenv("RAW_SNAPSHOT", raising=False)
    monkeypatch.setattr(window, "releases", lambda _: [
        {"tag_name": "data-window-12", "assets": []},
        {"tag_name": "data-window-11", "assets": [{"name": "window.json"}]},
    ])
    assert window.resolve("repo") == "data-window-11"
    assert window.resolve("repo", "data-raw-trial") == "data-raw-trial"


def test_cleanup_never_deletes_current_newer_or_unrelated_release(monkeypatch):
    monkeypatch.setattr(window, "manifest", lambda *_: {
        "matches": 10, "capacity": 10, "added": 2, "removed": 2, "generation": 12, "stage": "data-stage-12"})
    names = ["data-raw", "data-window-11", "data-window-12", "data-window-13",
             "data-stage-12", "data-stage-13", "recs", "v0.2.6", "data-raw-trial"]
    monkeypatch.setattr(window, "releases", lambda _: [{"tag_name": n} for n in names])
    deleted = []
    monkeypatch.setattr(window, "gh", lambda *args, **_: deleted.append(args[2]))
    window.cleanup("repo", "data-window-12")
    assert deleted == ["data-raw", "data-window-11", "data-stage-12"]


def test_failed_upload_never_publishes_ready_marker(tmp_path, monkeypatch):
    raw, work = tmp_path / "raw", tmp_path / "work"
    write(raw, [4])
    (raw / "run-2026-11-02-1200.json").write_text(json.dumps(
        {"tag": "2026-11-02-1200", "all_saved": True}), encoding="utf-8")
    (raw / "next_seq.txt").write_text("100", encoding="utf-8")
    monkeypatch.setattr(window, "resolve", lambda *_: "data-raw")
    def download(*args):
        args[2].mkdir(parents=True)
        write(args[2], [1, 2, 3])
    monkeypatch.setattr(window, "download", download)
    monkeypatch.setattr(window, "create_release", lambda *_: None)
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("upload failed")
    monkeypatch.setattr(window, "gh", fail)
    with pytest.raises(RuntimeError, match="upload failed"):
        window.publish("repo", "data-stage-12", raw, work)
    assert all("delete" not in args for args in calls)
    assert all(not any("window.json" in a for a in args) for args in calls)
