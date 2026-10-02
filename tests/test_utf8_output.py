"""Скрипты tools/ печатают в UTF-8: на Windows вывод в файл иначе идёт в cp1251 и падает на «≤», «→» (BUGLOG №39)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# сборщик работает только на GitHub (Linux, вывод и так UTF-8); правка сборщика — только через ветку и пробный запуск
ONLY_ON_GITHUB = {"collect_raw.py", "collect_stats.py"}


def test_every_tool_script_prints_utf8():
    missing = [f.name for f in sorted((ROOT / "tools").glob("*.py"))
               if "__main__" in f.read_text(encoding="utf-8") and f.name not in ONLY_ON_GITHUB
               and 'reconfigure(encoding="utf-8")' not in f.read_text(encoding="utf-8")]
    assert not missing, missing
