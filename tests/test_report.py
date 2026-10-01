"""Отчёт о сборе: оборванный запуск не выдаётся за идущий и входит в «всего» (BUGLOG №36)."""
from datetime import datetime

from tools.report import broken_runs, live, report


def part(tag: str, n: int, uploaded: str) -> dict:
    return {"name": f"matches-{tag}-p{n:02d}.parquet", "size": 1024, "updated_at": uploaded,
            "browser_download_url": f"x/{tag}/{n}"}


RUN = {"tag": "2026-10-01-0942", "run_number": 11, "matches": 186106, "matches_per_hour": 38481}
ASSETS = ([part("2026-10-01-0212", 0, "2026-10-01T02:30:00Z"), part("2026-10-01-0212", 1, "2026-10-01T04:43:00Z")]
          + [part("2026-10-01-0942", 0, "2026-10-01T10:00:00Z")])
COUNT = {"2026-10-01-0212": 92248}


def fake_count(parts):
    return COUNT[parts[0]["name"][8:23]]


def test_stale_parts_are_broken_not_running():
    """Запуск №9 оборвался в 04:43 UTC; в 16:00 он не «идёт», а оборван."""
    now = datetime(2026, 10, 1, 16, 0)
    assert broken_runs(ASSETS, [RUN], now, fake_count) == {"2026-10-01-0212": 92248}
    lines = live(ASSETS, [RUN], now, fake_count)
    assert "идущего запуска нет" in lines[0] and "2026-10-01-0212" in lines[0]


def test_fresh_parts_are_running():
    now = datetime(2026, 10, 1, 4, 50)
    lines = live(ASSETS, [RUN], now, fake_count)
    assert lines[0].startswith("идёт запуск 2026-10-01-0212")


def test_total_includes_broken_runs():
    lines = report([RUN], ASSETS, None, legacy=64725, broken={"2026-10-01-0212": 92248})
    assert "всего: 343 079" in lines[0] and "оборванные" in lines[0]
