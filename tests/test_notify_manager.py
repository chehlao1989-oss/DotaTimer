"""Тесты менеджера уведомлений: пресеты, склейка, приоритеты, лимит голоса, тишина."""
from pathlib import Path

import pytest

from app.gsi.models import parse_packet
from app.notify.manager import NotificationManager, NotifySettings, load_presets
from app.timers import timings
from app.timers.engine import MatchTimers, TimerEngine
from tools.analyze_recording import load_records

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def config():
    return load_presets()


def simulate(settings, config, start, end, mode=timings.MODE_NORMAL, silence_at=None, triggers=()):
    """Прогон по секундам игровых часов; время компьютера = игровые часы."""
    specs = timings.load_timings(mode)
    engine = TimerEngine(specs)
    manager = NotificationManager(specs, config, settings)
    log = []
    for clock in range(start, end + 1):
        for name, at in triggers:
            if at == clock:
                engine.trigger(name, clock)
        if silence_at == clock:
            manager.silence(clock)
        manager.push(engine.update(clock + config.merge_window_sec))
        result = manager.tick(clock, float(clock))
        if result.texts or result.voices:
            log.append((clock, tuple(m.text for m in result.texts), result.voices))
    return log


def at(log, clock):
    return next(((t, v) for c, t, v in log if c == clock), ((), ()))


def test_bounty_and_water_merge_into_one_message(config):
    log = simulate(NotifySettings("standard"), config, 200, 240)
    texts, voices = at(log, 230)
    assert len(texts) == 1
    assert "Баунти" in texts[0] and "Водные руны" in texts[0]
    assert len(voices) == 1


def test_night_merges_with_siege_creeps(config):
    log = simulate(NotifySettings("all"), config, 270, 300)
    texts, voices = at(log, 285)
    assert texts == ("Ночь через 15 сек\nОсадные крипы через 10 сек",)
    assert voices == ("night",)


def test_first_power_rune_texts_and_voices(config):
    log = simulate(NotifySettings("minimum"), config, 300, 360)
    assert at(log, 330) == (("Руна силы на 6:00, подтягивайтесь к реке",), ("power_rune_30",))
    assert at(log, 350) == (("Руна силы через 10 сек",), ("power_rune",))


def test_minimum_preset_has_no_bounty(config):
    log = simulate(NotifySettings("minimum"), config, -20, 300)
    assert all("Баунти" not in text for _, texts, _ in log for text in texts)


def test_first_power_rune_works_even_if_power_runes_disabled(config):
    settings = NotifySettings("minimum", enabled_overrides={"power_rune": False})
    log = simulate(settings, config, 300, 500)
    assert [c for c, _, _ in log] == [330, 350]


def test_first_power_rune_can_be_disabled(config):
    settings = NotifySettings("minimum", first_power_rune=False)
    log = simulate(settings, config, 300, 400)
    assert log == []


def test_voice_limit_delays_less_important_phrase(config):
    # берём лимит 30 сек, чтобы проверить его на реальных событиях первых минут
    settings = NotifySettings("standard", voice_min_interval_sec=30)
    log = simulate(settings, config, 100, 250)
    voices = [(c, v) for c, _, v in log if v]
    # 1:50 вода звучит; 3:50 баунти+вода звучит (прошло больше 30 сек)
    assert voices == [(110, ("water",)), (230, ("bounty",))]


def test_stale_voice_is_dropped(config):
    settings = NotifySettings("standard", voice_min_interval_sec=200)
    log = simulate(settings, config, 100, 250)
    voices = [(c, v) for c, _, v in log if v]
    # баунти на 3:50 не успевает из-за лимита и выкидывается, когда событие прошло
    assert voices == [(110, ("water",))]
    assert any("Баунти" in t for _, texts, _ in log for t in texts)  # надпись всё равно показана


def test_top_priority_bypasses_voice_limit(config):
    settings = NotifySettings("standard", voice_min_interval_sec=100)
    # день/ночь на 4:45 занимает голос, первая активка на 5:30 всё равно звучит
    log = simulate(settings, config, 280, 360)
    voices = [(c, v) for c, _, v in log if v]
    assert voices == [(285, ("night",)), (330, ("power_rune_30",)), (350, ("power_rune",))]


def test_silence_mutes_voice_but_keeps_text(config):
    log = simulate(NotifySettings("standard"), config, 300, 360, silence_at=300)
    assert all(not v for _, _, v in log)
    assert at(log, 330)[0]


def test_tormentor_respawn_text(config):
    log = simulate(NotifySettings("minimum"), config, 2000, 2700, triggers=[("tormentor_killed", 2000)])
    assert at(log, 2600) == (("Терзатель появился у Света (внизу справа)",), ("tormentor",))


def test_tormentor_side_by_daytime(config):
    log = simulate(NotifySettings("minimum"), config, 1190, 1200)
    assert at(log, 1200)[0] == ("Терзатель появился у Света (внизу справа)",)


def test_warn_overrides_from_settings():
    specs = timings.load_timings(timings.MODE_NORMAL)
    assert NotifySettings().warn_overrides(specs) == {}
    assert NotifySettings(every_power_rune_30=True).warn_overrides(specs) == {"power_rune": (30, 10)}
    settings = NotifySettings(every_power_rune_30=True, warn_seconds={"power_rune": 15, "bounty_rune": 20})
    assert settings.warn_overrides(specs) == {"power_rune": (30, 15), "bounty_rune": (20,)}


def test_full_allpick_match_voice_rate(config):
    specs = timings.load_timings(timings.MODE_NORMAL)
    manager = NotificationManager(specs, config, NotifySettings("all"))
    timers = MatchTimers(TimerEngine(specs), lookahead=config.merge_window_sec, on_new_match=manager.reset)
    voices = []
    for record in load_records(FIXTURES / "allpick.jsonl"):
        state = parse_packet(record["data"])
        manager.push(timers.on_state(state))
        if state.clock_time is not None and state.in_match and not state.paused:
            result = manager.tick(state.clock_time, record["t"])
            voices += [(record["t"], v) for v in result.voices]
    assert "power_rune_30" in [v for _, v in voices]
    # лимит: между обычными фразами не меньше 8 сек
    top = {"power_rune_30"}  # первая активка обходит лимит
    times = [t for t, v in voices if v not in top]
    assert all(b - a >= config.voice_min_interval_sec - 0.01 for a, b in zip(times, times[1:]))
