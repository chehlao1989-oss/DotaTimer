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


def test_nonfinite_purchase_does_not_poison_snapshot():
    g = game()
    g["players"][0]["purchase_log"] = [{"key": "black_king_bar", "time": float("nan")}]
    result = sample_match(g, {"black_king_bar"})
    assert len(result["rows"]) == 9


def test_newer_bundled_table_beats_old_cache(tmp_path, monkeypatch):
    import app.threats.item_timing as timing
    raw = aggregate([sample_match(game(i), {"black_king_bar"}) for i in range(3)], CFG, now=NOW)
    cache = tmp_path / "cache.json"
    old = copy.deepcopy(raw)
    old["generated_at"] -= 15 * 86400
    cache.write_text(json.dumps(old), encoding="utf-8")
    (tmp_path / "item_timings.json").write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(timing, "DATA_DIR", tmp_path)
    assert TimingTable.load(cache).raw["generated_at"] == NOW


def test_failed_refresh_keeps_previous_cache(tmp_path, monkeypatch):
    import io
    import app.threats.item_timing as timing
    cache = tmp_path / "cache.json"
    cache.write_bytes(b"previous working copy")
    monkeypatch.setattr(timing.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b'{"version":0}'))
    assert not timing.download_timings(cache, max_age=0)
    assert cache.read_bytes() == b"previous working copy"


def test_timing_is_annotation_for_same_choices():
    from types import SimpleNamespace
    from app.threats.controller import ThreatsController
    from app.threats.item_hints import Choice
    c = SimpleNamespace(item_title=lambda key: "Black King Bar", purchase_minute=None)
    choices = [Choice("black_king_bar", "dispel", 10.0, prop="slow")]
    original = ThreatsController.format_choices(c, choices)
    c.purchase_minute = lambda key: 25
    assert ThreatsController.format_choices(c, choices) == original + " · ≈25 мин"
    c.purchase_minute = lambda key: None
    assert ThreatsController.format_choices(c, choices) == original


def test_service_uses_own_hero_mode_and_role():
    from types import SimpleNamespace
    from app.threats.service import ThreatsService
    raw = aggregate([sample_match(game(i), {"black_king_bar"}) for i in range(3)], CFG, now=NOW)
    s = SimpleNamespace(controller=SimpleNamespace(my_hero_id=lambda: 1),
                        settings=SimpleNamespace(role_mode="support"),
                        recs=SimpleNamespace(main_role=lambda hero: "support"),
                        turbo=lambda: False, timings=TimingTable(raw, now=NOW))
    assert ThreatsService._purchase_minute(s, "black_king_bar") == 25
    s.turbo = lambda: True
    assert ThreatsService._purchase_minute(s, "black_king_bar") is None
    s.turbo = lambda: False
    s.settings.role_mode = "core"
    assert ThreatsService._purchase_minute(s, "black_king_bar") is None
    s.settings.role_mode = "auto"
    assert ThreatsService._purchase_minute(s, "black_king_bar") == 25
