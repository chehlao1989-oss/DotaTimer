"""Тесты движка таймеров: синтетика и проигрывание реальных матчей."""
from pathlib import Path

import pytest

from app.gsi import models
from app.gsi.models import GameState, parse_packet
from app.timers import timings
from app.timers.engine import MAX_LATE_SEC, MatchTimers, TimerEngine
from tools.analyze_recording import load_records

FIXTURES = Path(__file__).parent / "fixtures"
IN_PROGRESS = "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"


def run_seconds(engine, start, end):
    alerts = []
    for clock in range(start, end + 1):
        alerts += engine.update(clock)
    return alerts


def fired(alerts, event_id):
    return [(a.fire_time, a.warn_before) for a in alerts if a.event_id == event_id]


@pytest.fixture
def normal():
    return timings.load_timings(timings.MODE_NORMAL)


def test_first_power_rune_warns_at_30_and_10(normal):
    alerts = run_seconds(TimerEngine(normal), -90, 500)
    assert fired(alerts, "power_rune") == [(330, 30), (350, 10), (470, 10)]


def test_warn_override_for_every_power_rune(normal):
    engine = TimerEngine(normal, warn_overrides={"power_rune": (30, 10)})
    alerts = run_seconds(engine, 300, 500)
    assert fired(alerts, "power_rune") == [(330, 30), (350, 10), (450, 30), (470, 10)]


def test_bounty_before_horn(normal):
    alerts = run_seconds(TimerEngine(normal), -90, 250)
    assert fired(alerts, "bounty_rune") == [(-10, 10), (230, 10)]


def test_same_clock_twice_gives_no_duplicates(normal):
    engine = TimerEngine(normal)
    engine.update(349)
    assert fired(engine.update(350), "power_rune") == [(350, 10)]
    assert engine.update(350) == []


def test_skipped_seconds_are_not_lost(normal):
    engine = TimerEngine(normal)
    engine.update(348)
    assert fired(engine.update(351), "power_rune") == [(350, 10)]


def test_jump_forward_skips_stale_alerts(normal):
    engine = TimerEngine(normal)
    engine.update(100)
    alerts = engine.update(1000)
    assert all(1000 - MAX_LATE_SEC <= a.fire_time <= 1000 for a in alerts)


def test_tormentor_respawn_10_minutes_after_kill(normal):
    engine = TimerEngine(normal)
    engine.update(1999)
    engine.trigger("tormentor_killed", 2000)
    engine.trigger("tormentor_killed", 2000)  # повтор не удваивает таймер
    alerts = run_seconds(engine, 2000, 2700)
    assert fired(alerts, "tormentor_respawn") == [(2600, 0)]


def test_double_hotkey_press_does_not_double_timer(normal):
    engine = TimerEngine(normal)
    engine.update(1999)
    engine.trigger("tormentor_killed", 2000)
    engine.trigger("tormentor_killed", 2004)  # нажали ещё раз через 4 сек
    alerts = run_seconds(engine, 2000, 2700)
    assert fired(alerts, "tormentor_respawn") == [(2600, 0)]


def test_second_tormentor_kill_is_separate(normal):
    engine = TimerEngine(normal)
    engine.update(1999)
    engine.trigger("tormentor_killed", 2000)
    engine.trigger("tormentor_killed", 2700)
    alerts = run_seconds(engine, 2000, 3400)
    assert fired(alerts, "tormentor_respawn") == [(2600, 0), (3300, 0)]


def test_turbo_tormentor():
    engine = TimerEngine(timings.load_timings(timings.MODE_TURBO))
    engine.update(0)
    engine.trigger("tormentor_killed", 650)
    alerts = run_seconds(engine, 1, 1000)
    assert fired(alerts, "tormentor_spawn") == [(600, 0)]
    assert fired(alerts, "tormentor_respawn") == [(950, 0)]


def _state(clock, paused=False, match="1", events=()):
    return GameState(match, clock, IN_PROGRESS, paused, True, None, None, None, tuple(events))


def test_pause_stops_alerts(normal):
    timers = MatchTimers(TimerEngine(normal))
    timers.on_state(_state(345))
    assert timers.on_state(_state(345, paused=True)) == []
    assert timers.on_state(_state(345, paused=True)) == []
    assert fired(timers.on_state(_state(350)), "power_rune") == [(350, 10)]


def test_new_match_resets_triggers(normal):
    timers = MatchTimers(TimerEngine(normal))
    timers.on_state(_state(100))
    timers.manual_trigger("tormentor_killed", 100)
    timers.on_state(_state(10, match="2"))
    alerts = []
    for clock in range(11, 800):
        alerts += timers.on_state(_state(clock, match="2"))
    assert fired(alerts, "tormentor_respawn") == []


def replay_fixture(name, mode):
    timers = MatchTimers(TimerEngine(timings.load_timings(mode)))
    alerts = []
    for record in load_records(FIXTURES / name):
        alerts += timers.on_state(parse_packet(record["data"]))
    return alerts


def test_replay_allpick_match():
    alerts = replay_fixture("allpick.jsonl", timings.MODE_NORMAL)
    assert fired(alerts, "power_rune")[:3] == [(330, 30), (350, 10), (470, 10)]
    assert fired(alerts, "tormentor_spawn") == [(1200, 0)]
    # Рошан убран: хотя в записи есть его смерть, таймера нет
    assert not any(a.event_id.startswith(("roshan", "aegis")) for a in alerts)
    # каждое предупреждение ровно один раз
    keys = [(a.event_id, a.event_time, a.warn_before) for a in alerts]
    assert len(keys) == len(set(keys))


def test_replay_turbo_match():
    alerts = replay_fixture("turbo.jsonl", timings.MODE_TURBO)
    assert fired(alerts, "tormentor_spawn") == [(600, 0)]
    assert fired(alerts, "shrine_of_wisdom")[0] == (405, 15)  # в Турбо как в обычной: 7:00


def test_replay_pause_before_horn_gives_no_alerts():
    # запись с ботами: только стадия до горна (около -1:30) с двумя паузами
    assert replay_fixture("bots_pause.jsonl", timings.MODE_NORMAL) == []


def test_repeated_event_with_shifted_offset_triggers_once(monkeypatch):
    # сейчас ни одно событие GSI не запускает таймеры; проверяем механизм на подставном
    monkeypatch.setitem(models.GSI_TRIGGERS, "roshan_killed", "tormentor_killed")

    def packet(clock, game_time):
        return {"map": {"matchid": "5", "clock_time": clock, "game_time": game_time,
                        "game_state": IN_PROGRESS, "paused": False, "daytime": True},
                "events": [{"event_type": "roshan_killed", "game_time": 2098}]}

    engine = TimerEngine(timings.load_timings(timings.MODE_NORMAL))
    timers = MatchTimers(engine)
    timers.on_state(parse_packet(packet(1872, 2098)))
    timers.on_state(parse_packet(packet(1873, 2100)))  # смещение другое на 1 сек
    alerts = []
    for clock in range(1874, 2600):
        alerts += engine.update(clock)
    assert len(fired(alerts, "tormentor_respawn")) == 1


def test_parse_packet_converts_event_time_to_clock():
    state = parse_packet({
        "map": {"matchid": "5", "clock_time": 1872, "game_time": 2098,
                "game_state": IN_PROGRESS, "paused": False, "daytime": True},
        "events": [{"event_type": "roshan_killed", "game_time": 2098, "killed_by_team": "radiant"}],
    })
    assert state.events[0].clock_time == 1872
    assert state.events[0].data == {"killed_by_team": "radiant"}


def test_parse_own_items_and_alive():
    state = parse_packet({
        "hero": {"name": "npc_dota_hero_zuus", "alive": False},
        "items": {"slot0": {"name": "item_tango"}, "slot1": {"name": "empty"}, "stash0": {"name": "item_boots"},
                  "neutral0": {"name": "item_trusty_shovel"}, "teleport0": {"name": "item_tpscroll"}},
    })
    assert state.hero_alive is False
    assert state.own_items == {"tango", "boots", "trusty_shovel"}
