"""Запись падений программы (BUGLOG №57).

17.10 и 18.10 программа дважды тихо умерла посреди катки: в логе ни ошибки, ни «Выход». Журнал Windows: оба раза
обращение к чужой памяти (0xc0000005) внутри pyside6.abi3.dll — Python в такой момент сам ничего не пишет.
- аварийное падение (как это): стек всех потоков Python — в logs/crash.log (faulthandler);
- необработанная ошибка Python в основном потоке или в фоновом потоке — в dotatimer.log;
- предупреждения и ошибки самого Qt — в dotatimer.log.
"""
import datetime
import faulthandler
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger("dotatimer.crash")

CRASH_FILE = "crash.log"
_crash_stream = None  # файл должен оставаться открытым всё время работы: в него пишет faulthandler


def install(logs_dir: Path) -> None:
    global _crash_stream
    _crash_stream = open(logs_dir / CRASH_FILE, "a", encoding="utf-8")
    _crash_stream.write(f"\n=== запуск {datetime.datetime.now():%Y-%m-%d %H:%M:%S}, процесс {os.getpid()} ===\n")
    _crash_stream.flush()
    faulthandler.enable(_crash_stream, all_threads=True)

    def excepthook(exc_type, exc, tb):
        log.critical("Необработанная ошибка", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        log.critical("Необработанная ошибка в потоке %s", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook  # PySide передаёт сюда и ошибки из обработчиков сигналов и таймеров
    threading.excepthook = thread_excepthook
    _install_qt_handler()


def _install_qt_handler() -> None:
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    levels = {QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}
    qt_log = logging.getLogger("qt")

    def handler(mode, context, message):
        if mode in levels:
            qt_log.log(levels[mode], "%s", message)

    qInstallMessageHandler(handler)


def crash_tail(logs_dir: Path, max_lines: int = 40) -> str:
    """Последний блок crash.log (после строки «=== запуск»), если в нём есть стек падения."""
    path = logs_dir / CRASH_FILE
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    block = text.rsplit("=== запуск", 1)[-1]
    lines = [line for line in block.splitlines()[1:] if line.strip()]
    return "\n".join(lines[-max_lines:])
