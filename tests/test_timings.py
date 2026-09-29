"""Тесты загрузки таймингов."""
import pytest

from app.timers import timings
from app.timers.timings import TimingsError, parse_timings

META = {"source": "test", "checked_patch": "7.41"}

SAMPLE = {
    "events": {
        "rune": {"kind": "periodic", "first": 100, "interval": 50, "warn_before": [10], **META},
        "water": {"kind": "at_times", "times": [20, 40], "warn_before": [5], **META},
        "boss": {"kind": "after_trigger", "trigger": "boss_killed", "delays": [300, 400],
                 "warn_before": [0], "respawn_note": "x", **META},
    },
    "turbo": {"boss": {"delays": [150, 200]}},
}


def test_parse_normal():
    specs = parse_timings(SAMPLE)
    assert specs["rune"].first == 100 and specs["rune"].interval == 50
    assert specs["water"].times == (20, 40)
    assert specs["boss"].delays == (300, 400)
    assert specs["boss"].extra == {"respawn_note": "x"}


def test_turbo_overrides_only_listed_fields():
    specs = parse_timings(SAMPLE, timings.MODE_TURBO)
    assert specs["boss"].delays == (150, 200)
    assert specs["boss"].trigger == "boss_killed"
    assert specs["rune"].first == 100


def test_turbo_does_not_change_original_data():
    parse_timings(SAMPLE, timings.MODE_TURBO)
    assert SAMPLE["events"]["boss"]["delays"] == [300, 400]


@pytest.mark.parametrize("broken", [
    {"kind": "weird", **META},
    {"kind": "periodic", "first": 1, **META},
    {"kind": "at_times", "times": [], **META},
    {"kind": "after_trigger", "delays": [1], **META},
    {"kind": "at_times", "times": [1], "source": "x"},
])
def test_broken_event_rejected(broken):
    with pytest.raises(TimingsError):
        parse_timings({"events": {"e": broken}})


@pytest.mark.parametrize("mode", [timings.MODE_NORMAL, timings.MODE_TURBO])
def test_real_timings_file_loads(mode):
    specs = timings.load_timings(mode)
    assert "power_rune" in specs and "tormentor_respawn" in specs


def test_turbo_unknown_event_rejected():
    with pytest.raises(TimingsError):
        parse_timings({"events": SAMPLE["events"], "turbo": {"nope": {}}}, timings.MODE_TURBO)
