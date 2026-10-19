"""Сторож и запись падений (BUGLOG №57): программа умерла посреди катки — перезапуск и стек в логе."""
import logging
import threading

import app.supervisor as sup
from app import crash


def run_codes(codes, monkeypatch, tmp_path):
    monkeypatch.setattr(sup.paths, "logs_dir", lambda: tmp_path)
    calls, clock = [], [0.0]

    def run(command, cwd=None):
        calls.append(list(command))
        return codes.pop(0)

    def sleep(sec):
        clock[0] += sec

    return sup.supervise([], run=run, sleep=sleep, now=lambda: clock[0]), calls


def test_normal_exit_stops(monkeypatch, tmp_path):
    code, calls = run_codes([0], monkeypatch, tmp_path)
    assert code == 0 and len(calls) == 1 and calls[0][-1] == sup.CHILD_FLAG


def test_crash_restarts_minimized(monkeypatch, tmp_path):
    """Падение (0xC0000005, как 17–18.10) — перезапуск в трей; затем «Выход» — сторож выходит."""
    code, calls = run_codes([0xC0000005, 0], monkeypatch, tmp_path)
    assert code == 0 and len(calls) == 2
    assert "--minimized" not in calls[0] and "--minimized" in calls[1]


def test_gives_up_when_crashing_too_often(monkeypatch, tmp_path):
    code, calls = run_codes([1] * (sup.MAX_RESTARTS + 5), monkeypatch, tmp_path)
    assert code == 1 and len(calls) == sup.MAX_RESTARTS + 1


def test_crash_tail_reads_last_block(tmp_path):
    (tmp_path / crash.CRASH_FILE).write_text(
        "=== запуск 1 ===\nстарое\n=== запуск 2 ===\nWindows fatal exception: access violation\n  File \"x.py\"\n",
        encoding="utf-8")
    tail = crash.crash_tail(tmp_path)
    assert "access violation" in tail and "старое" not in tail


def test_thread_errors_go_to_log(qapp, tmp_path, caplog):
    """Ошибка в фоновом потоке не пропадает молча: CRITICAL в лог с именем потока."""
    import faulthandler
    import sys
    saved = sys.excepthook, threading.excepthook
    try:
        crash.install(tmp_path)
        with caplog.at_level(logging.CRITICAL):
            t = threading.Thread(target=lambda: 1 / 0, name="проверка")
            t.start()
            t.join()
        assert any("проверка" in r.getMessage() for r in caplog.records)
    finally:
        sys.excepthook, threading.excepthook = saved
        faulthandler.disable()
        crash._crash_stream.close()
