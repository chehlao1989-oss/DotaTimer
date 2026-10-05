"""Самопроверка программы (--selftest): ею проверяется собранный exe на GitHub (фаза 3)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_selftest_passes(tmp_path):
    report = tmp_path / "selftest.txt"
    run = subprocess.run([sys.executable, "-m", "app.main", "--selftest", str(report)], cwd=ROOT, timeout=120)
    text = report.read_text(encoding="utf-8")
    assert run.returncode == 0, text
    assert "ОШИБКА" not in text and "приём пакета GSI" in text
