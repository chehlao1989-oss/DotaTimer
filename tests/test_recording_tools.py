"""Тесты рекордера, разбора и проигрывания записи."""
import json

import pytest

from app.gsi import installer
from app.gsi.server import ExclusiveHTTPServer, GsiServer
from tools import analyze_recording, gsi_recorder, gsi_replay


def _packet(clock, state="DOTA_GAMERULES_STATE_GAME_IN_PROGRESS", paused=False, daytime=True, events=None):
    return {
        "auth": {"token": installer.GSI_TOKEN},
        "map": {"matchid": "42", "clock_time": clock, "game_state": state, "paused": paused, "daytime": daytime},
        "items": {"slot0": {"name": "item_tango"}, "slot1": {"name": "empty"}},
        "events": events or [],
    }


@pytest.fixture
def recorder_server(tmp_path):
    """Настоящий рекордер на свободном порту."""
    recorder = gsi_recorder.Recorder(tmp_path / "rec.jsonl")
    server = GsiServer(recorder.write, port=0)
    server.start()
    yield recorder, server, f"http://127.0.0.1:{server.port}/"
    server.stop()
    recorder.close()


def test_recorder_writes_packets_and_rejects_foreign_token(recorder_server):
    recorder, server, url = recorder_server
    gsi_replay.send(url, _packet(10))
    with pytest.raises(Exception):
        gsi_replay.send(url, {"auth": {"token": "чужой"}, "map": {}})
    assert recorder.packets == 1
    assert server.rejected == 1
    lines = recorder.out_path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["data"]["map"]["clock_time"] == 10


def test_replay_sends_whole_recording(recorder_server, tmp_path):
    recorder, _, url = recorder_server
    source = tmp_path / "source.jsonl"
    source.write_text("\n".join(json.dumps({"t": i, "data": _packet(i)}) for i in range(5)), encoding="utf-8")
    assert gsi_replay.replay(source, url, speed=0) == 5
    assert recorder.packets == 5


def test_load_records_skips_broken_line(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text('{"t": 1, "data": {}}\n{"t": 2, "da', encoding="utf-8")
    assert len(analyze_recording.load_records(path)) == 1


def test_flatten_normalizes_numbered_keys():
    pairs = dict(analyze_recording.flatten({"items": {"slot0": {"name": "a"}, "slot5": {"name": "b"}}}))
    assert list(pairs) == ["items.slot#.name"]


def test_summarize_tracks_state_pause_daytime_and_events():
    records = [
        {"t": 0, "data": _packet(-60, state="DOTA_GAMERULES_STATE_PRE_GAME")},
        {"t": 1, "data": _packet(0)},
        {"t": 2, "data": _packet(100, paused=True)},
        {"t": 3, "data": _packet(100, paused=False, daytime=False,
                                 events=[{"event_type": "roshan_killed", "game_time": 100}])},
    ]
    summary = analyze_recording.summarize(records)
    assert [s for _, s in summary["state_changes"]] == [
        "DOTA_GAMERULES_STATE_PRE_GAME", "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"]
    assert [p for _, p in summary["pause_changes"]] == [False, True, False]
    assert summary["daytime_changes"] == [(-60, True), (100, False)]
    assert len(summary["events"]) == 1
    assert summary["matches"]["42"]["packets"] == 4
    assert "auth.token" not in summary["fields"]
    report = analyze_recording.render_markdown(summary, "test")
    assert "roshan_killed" in report


def test_second_recorder_cannot_take_busy_port():
    first = ExclusiveHTTPServer(("127.0.0.1", 0), None)
    try:
        with pytest.raises(OSError):
            ExclusiveHTTPServer(("127.0.0.1", first.server_address[1]), None)
    finally:
        first.server_close()


def test_format_clock():
    assert gsi_recorder.format_clock(332) == "5:32"
    assert gsi_recorder.format_clock(-75) == "-1:15"
    assert gsi_recorder.format_clock(None) == "—"
