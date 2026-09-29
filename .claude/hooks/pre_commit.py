"""Хук Claude Code перед командой `git commit` (docs/PROCESS.md, разделы 5–6).

1. Запускает pytest. Если тесты падают — коммит блокируется (код выхода 2, причина уходит агенту).
2. Если в коммит попадает код сборщика/расчёта или данные, а журналы (BUGLOG, DECISIONS, DATA_SOURCES)
   не изменены — предупреждение (коммит не блокируется): знания должны попадать в журналы в том же коммите.

Вход — JSON вызова инструмента на stdin (tool_input.command).
"""
import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
WATCHED = ("tools/collect_raw.py", "tools/compute_recs.py", ".github/workflows/", "data/")
JOURNALS = ("docs/BUGLOG.md", "docs/DECISIONS.md", "docs/DATA_SOURCES.md")


def is_commit(command: str) -> bool:
    return any(part.strip().startswith("git commit") or " git commit" in part
               for part in command.replace("&&", ";").replace("||", ";").split(";"))


def git_lines(*args: str) -> list[str]:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def files_in_commit(command: str) -> set[str]:
    """Что попадёт в коммит: уже добавленное + файлы из `git add` в этой же команде (+ всё изменённое при -a)."""
    files = set(git_lines("diff", "--cached", "--name-only"))
    for part in command.replace("&&", ";").split(";"):
        part = part.strip()
        try:
            words = shlex.split(part)
        except ValueError:
            words = part.split()
        if words[:2] == ["git", "add"]:
            added = [w for w in words[2:] if not w.startswith("-")]
            if any(w in (".", "-A", "--all") for w in words[2:]):
                files |= set(git_lines("diff", "--name-only")) | set(git_lines("ls-files", "--others", "--exclude-standard"))
            files |= {w.replace("\\", "/") for w in added}
        if words[:2] == ["git", "commit"] and any(w.startswith("-") and "a" in w.lstrip("-") and not w.startswith("--")
                                                  for w in words[2:]):
            files |= set(git_lines("diff", "--name-only"))
    return files


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not is_commit(command):
        return
    python = str(PYTHON) if PYTHON.exists() else sys.executable
    tests = subprocess.run([python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if tests.returncode != 0:
        tail = "\n".join((tests.stdout + tests.stderr).strip().splitlines()[-25:])
        print(f"Коммит заблокирован: тесты не проходят.\n{tail}", file=sys.stderr)
        sys.exit(2)
    files = files_in_commit(command)
    watched = sorted(f for f in files if f.startswith(WATCHED))
    if watched and not any(f in JOURNALS for f in files):
        message = ("Предупреждение: изменены сборщик/расчёт/данные (" + ", ".join(watched) + "), а журналы "
                   "(docs/BUGLOG.md, docs/DECISIONS.md, docs/DATA_SOURCES.md) — нет. Если выяснилось что-то новое "
                   "или изменено решение, запиши это в журнал в этом же коммите (PROCESS.md, раздел 3).")
        print(json.dumps({"systemMessage": message,
                          "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": message}},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
