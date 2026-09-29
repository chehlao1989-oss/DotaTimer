"""Тесты автоопределения режима по пассивному золоту (числа из записей автора 28.09.2026)."""
from app.timers.mode import ModeDetector


def test_all_pick_and_turbo_from_real_numbers():
    assert ModeDetector().update(60, 91) == "normal"  # олпик к 1:00
    assert ModeDetector().update(60, 182) == "turbo"  # турбо к 1:00
    assert ModeDetector().update(120, 181) == "normal"
    assert ModeDetector().update(90, 272) == "turbo"


def test_waits_for_enough_time_and_answers_once():
    d = ModeDetector()
    assert d.update(-30, 0) is None  # до горна
    assert d.update(10, 30) is None  # слишком рано
    assert d.update(40, 120) == "turbo"
    assert d.update(60, 91) is None  # уже определено
    d.reset()
    assert d.update(60, 91) == "normal"


def test_missing_data():
    assert ModeDetector().update(None, 100) is None
    assert ModeDetector().update(60, None) is None
