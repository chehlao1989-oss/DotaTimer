"""Сторож сбора (BUGLOG №32): расписание каждый час, настоящий сбор — если с прошлого успешного прошло ≥ 11 ч."""
from datetime import datetime, timezone

from tools.collect_guard import last_success_age_hours, should_collect

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ASSETS = [{"name": "matches-2026-10-01-0212-p00.parquet", "updated_at": "2026-10-01T11:30:00Z"},
          {"name": "run-2026-09-30-1333.json", "updated_at": "2026-09-30T18:51:00Z"},
          {"name": "run-2026-09-30-0153.json", "updated_at": "2026-09-30T06:44:00Z"}]


def test_age_from_newest_run_file():
    assert round(last_success_age_hours(ASSETS, NOW), 2) == 17.15  # свежайший run-файл, части без итогов не в счёт
    assert last_success_age_hours([], NOW) is None


def test_collect_only_after_eleven_hours():
    assert should_collect(17.15) and should_collect(11.0) and should_collect(None)
    assert not should_collect(10.9) and not should_collect(0.5)
