"""Хук перед коммитом: сборщик и .github в main — только слиянием ветки (BUGLOG №31)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "pre_commit", Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "pre_commit.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def test_main_commit_with_collector_is_blocked():
    files = {"tools/collect_raw.py", "docs/BUGLOG.md"}
    assert hook.protected_violation(files, "main", merging=False) == ["tools/collect_raw.py"]
    assert hook.protected_violation({".github/workflows/stats.yml"}, "main", merging=False)


def test_branch_and_merge_are_allowed():
    files = {"tools/collect_raw.py", ".github/workflows/stats.yml"}
    assert hook.protected_violation(files, "collector-freshness", merging=False) == []
    assert hook.protected_violation(files, "main", merging=True) == []
    assert hook.protected_violation({"tools/compute_recs.py", "docs/BUGLOG.md"}, "main", merging=False) == []
