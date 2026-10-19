"""Сторож: запускает программу отдельным процессом и перезапускает, если она умерла не через «Выход» (BUGLOG №57).

Через него запускаются автозапуск (run_dotatimer.pyw) и собранная программа (packaging/launcher.py); запуск из
PyCharm (python -m app.main) идёт без сторожа. Сам сторож окон не открывает и Qt не загружает — падать в нём нечему.
Падает слишком часто (MAX_RESTARTS за RESTART_WINDOW_SEC) — больше не перезапускает, чтобы не крутиться впустую.
"""
import logging
import subprocess
import sys
import time
from pathlib import Path

from app import paths
from app.crash import crash_tail

log = logging.getLogger("dotatimer.supervisor")

CHILD_FLAG = "--child"
RESTART_DELAY_SEC = 3
MAX_RESTARTS = 5  # за окно ниже; больше — значит падает сразу после запуска, перезапуск не поможет
RESTART_WINDOW_SEC = 600


def child_command(args: list[str]) -> list[str]:
    """Команда запуска самой программы: собранный exe — он же, иначе тот же Python с python -m app.main."""
    base = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "app.main"]
    return base + [a for a in args if a != CHILD_FLAG] + [CHILD_FLAG]


def supervise(args: list[str], run=subprocess.call, sleep=time.sleep, now=time.monotonic) -> int:
    """Запускать программу, пока она не завершится сама (код 0). Возвращает последний код."""
    logs = paths.logs_dir()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(logs / "dotatimer.log", encoding="utf-8")])
    cwd = None if getattr(sys, "frozen", False) else str(Path(__file__).resolve().parents[1])
    restarts: list[float] = []
    command = child_command(args)
    while True:
        code = run(command, cwd=cwd)
        if code == 0:
            return 0
        tail = crash_tail(logs)
        log.error("Программа умерла (код %s = 0x%08X), без «Выход»%s", code, code & 0xFFFFFFFF,
                  f"; стек падения из crash.log:\n{tail}" if tail else "")
        restarts = [t for t in restarts if now() - t < RESTART_WINDOW_SEC] + [now()]
        if len(restarts) > MAX_RESTARTS:
            log.error("Падает слишком часто (%d раз за %d мин) — больше не перезапускаю", len(restarts),
                      RESTART_WINDOW_SEC // 60)
            return code
        sleep(RESTART_DELAY_SEC)
        log.info("Перезапуск после падения")
        if "--minimized" not in command:
            command = command[:-1] + ["--minimized", CHILD_FLAG]  # после падения — тихо в трей, без окна
