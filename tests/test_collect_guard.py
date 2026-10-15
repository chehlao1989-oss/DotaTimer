"""Сторож сбора (BUGLOG №32): расписание каждый час, настоящий сбор — если с прошлого успешного прошло ≥ 11 ч."""
from datetime import datetime, timezone

from tools.collect_guard import last_success_age_hours, should_collect

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ASSETS = [{"name": "matches-2026-10-01-0212-p00.parquet", "updated_at": "2026-10-01T11:30:00Z"},
          {"name": "run-2026-09-30-1333.json", "updated_at": "2026-09-30T18:51:00Z"},
          {"name": "run-2026-09-30-0153.json", "updated_at": "2026-09-30T06:44:00Z"}]


def test_age_from_newest_run_file():
    # от НАЧАЛА свежайшего успешного сбора (13:33 30.09 → 22,45 ч), а не от выкладки run-файла (18:51 → 17,15 ч):
    # иначе цикл = 11 ч + 5 ч сбора ≈ 1,4 сбора в сутки (решение автора 15.10: 2 в сутки); части без итогов не в счёт
    assert round(last_success_age_hours(ASSETS, NOW), 2) == 22.45
    assert last_success_age_hours([], NOW) is None


def test_collect_only_after_eleven_hours():
    assert should_collect(17.15) and should_collect(11.0) and should_collect(None)
    assert not should_collect(10.9) and not should_collect(0.5)


def test_two_collections_a_day():
    """Сбор начался в 00:47, идёт 5 ч: следующий по сторожу — в 11:47 того же дня (11 ч от начала), а не в 16:47."""
    from tools.collect_guard import MIN_HOURS
    run = [{"name": "run-2026-10-14-0047.json", "updated_at": "2026-10-14T05:57:00Z"}]
    assert not should_collect(last_success_age_hours(run, datetime(2026, 10, 14, 10, 47, tzinfo=timezone.utc)), MIN_HOURS)
    assert should_collect(last_success_age_hours(run, datetime(2026, 10, 14, 11, 47, tzinfo=timezone.utc)), MIN_HOURS)
