"""Тайминг — реальная первая покупка, отдельные режимы/роли, без выдуманного запасного времени."""
import copy
import json
import pytest

from app.threats.item_timing import TimingTable, validate
from tools.collect_item_timings import aggregate, sample_match
from tests.test_collect_raw import match

CFG = {"min_samples": 3, "max_age_days": 14}
NOW = 1_790_000_000


def game(mid=1, mode=22, clock=1519, patch=58):
    value = match(mode=mode, start=NOW - 100)
    value.update(match_id=mid, version=22, patch=patch)
    for i, p in enumerate(value["players"]):
        p["net_worth"] = 10000 + i
        p["purchase_log"] = [{"key": "mithril_hammer", "time": clock - 60},
                             {"key": "black_king_bar", "time": clock},
                             {"key": "black_king_bar", "time": clock + 40}]
    return value


def test_ready_item_first_purchase_and_no_accounts():
    s = sample_match(game(), {"black_king_bar"})
    assert len(s["rows"]) == 10
    assert all(row[2:] == ["black_king_bar", 1519] for row in s["rows"])
    assert "account_id" not in json.dumps(s) and "players" not in s
    assert {row[1] for row in s["rows"]} == {"core", "offlane", "support"}


def test_late_purchase_is_not_cut_at_thirty_minutes():
    g = game(clock=3000)
    g["duration"] = 4000
    s = sample_match(g, {"black_king_bar"})
    assert s and s["rows"][0][-1] == 3000


def test_unparsed_practice_short_and_abandoned_are_not_used():
    for field, value in [("version", None), ("game_mode", 2), ("duration", 100), ("lobby_type", 1)]:
        g = game()
        g[field] = value
        assert sample_match(g, {"black_king_bar"}) is None
    g = game()
    g["players"][0]["leaver_status"] = 2
    assert sample_match(g, {"black_king_bar"}) is None


def test_median_mode_role_patch_and_expiry():
    samples = [sample_match(game(mid=i, clock=t), {"black_king_bar"}) for i, t in enumerate([1200, 1500, 1800])]
    # Другой режим не должен сдвигать обычный тайминг.
    samples += [sample_match(game(mid=10+i, mode=23, clock=600), {"black_king_bar"}) for i in range(3)]
    # Старый патч не участвует в расчёте нового.
    samples += [sample_match(game(mid=20+i, clock=300, patch=57), {"black_king_bar"}) for i in range(3)]
    raw = aggregate(samples, CFG, now=NOW)
    table = TimingTable(raw, now=NOW)
    assert table.get(1, "normal", "support", "black_king_bar").minute == 25
    assert table.get(1, "turbo", "support", "black_king_bar").minute == 10
    assert table.get(1, "normal", "core", "black_king_bar") is None
    assert table.get(99, "normal", "support", "black_king_bar") is None
    assert TimingTable(raw, now=NOW+15*86400).get(1, "normal", "support", "black_king_bar") is None


def test_no_timing_until_enough_samples():
    sample = sample_match(game(), {"black_king_bar"})
    raw = aggregate([sample], CFG, now=NOW)
    assert TimingTable(raw, now=NOW).get(1, "normal", "support", "black_king_bar") is None
    assert raw["heroes"] == {}


@pytest.mark.parametrize("bad", [None, [], {"version": 1, "heroes": []},
                                  {"version": 1, "heroes": {}, "generated_at": float("nan")}])
def test_invalid_snapshot_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        validate(bad)
