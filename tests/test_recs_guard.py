"""Сторож расчёта советов: считать только при новых данных (BUGLOG №38)."""
from pathlib import Path

from tools.recs_guard import newest_data, recs_time, should_compute

ROOT = Path(__file__).resolve().parent.parent
DATA = [{"name": "matches-2026-10-02-0247-p19.parquet", "updated_at": "2026-10-02T07:50:00Z"},
        {"name": "ranks-2026-10-02-0247.parquet", "updated_at": "2026-10-02T07:58:00Z"},
        {"name": "next_seq.txt", "updated_at": "2026-10-02T09:00:00Z"},
        {"name": "run-2026-10-02-0247.json", "updated_at": "2026-10-02T09:00:00Z"}]


def test_compute_after_collection():
    """Сбор выложил части после прошлого recs.zip — считать."""
    recs = recs_time([{"name": "recs.zip", "updated_at": "2026-10-02T02:02:10Z"}])
    assert should_compute(newest_data(DATA), recs)


def test_skip_when_no_new_data():
    """Будильник вышел по сторожу, новых частей нет (next_seq/run-файлы не считаются данными) — не считать."""
    recs = recs_time([{"name": "recs.zip", "updated_at": "2026-10-02T08:15:00Z"}])
    assert not should_compute(newest_data(DATA), recs)


def test_compute_without_recs_or_data():
    assert should_compute(newest_data(DATA), None) and should_compute(None, None)


def test_workflow_uses_guard_and_does_not_cancel():
    """В recs.yml: сторож только для запуска после сбора, шаги расчёта зависят от него; идущий расчёт не отменяется."""
    workflow = (ROOT / ".github" / "workflows" / "recs.yml").read_text(encoding="utf-8")
    assert "cancel-in-progress: false" in workflow
    assert "if: github.event_name == 'workflow_run'\n        run: python tools/recs_guard.py" in workflow
    assert workflow.count("if: steps.guard.outputs.run != 'false'") == 6
