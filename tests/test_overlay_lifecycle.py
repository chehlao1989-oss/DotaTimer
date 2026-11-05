"""Уведомления: просроченные callbacks, массовая замена и уничтожение владельца (№67)."""
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFrame
from app.ui.outlined_label import OutlinedLabel
from app.config import OverlaySettings
from app.ui.overlay import Overlay, MAX_MESSAGES


def test_discarded_message_has_no_delayed_callback(qapp, monkeypatch):
    import shiboken6
    overlay = Overlay(OverlaySettings())
    expired = []
    original = overlay._fade_out
    def fade(frame, effect):
        expired.append(shiboken6.isValid(frame) and frame in overlay._frames())
        original(frame, effect)
    monkeypatch.setattr(overlay, "_fade_out", fade)
    overlay.show_message("discarded", duration_sec=.001)
    for i in range(MAX_MESSAGES):
        overlay.show_message(str(i), duration_sec=30)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QTest.qWait(25)
    # Удалённое/вытесненное сообщение не должно возвращаться через старый таймер.
    assert expired == []
    overlay.close()


def test_closing_overlay_cancels_pending_messages(qapp, monkeypatch):
    overlay = Overlay(OverlaySettings())
    expired = []
    monkeypatch.setattr(overlay, "_fade_out", lambda *args: expired.append(True))
    overlay.show_message("short", duration_sec=.001)
    overlay.close()
    QTest.qWait(25)
    assert not expired
    assert not overlay._frames()


def test_burst_and_layout_mode_have_bounded_qt_objects(qapp):
    overlay = Overlay(OverlaySettings())
    for i in range(250):
        overlay.show_message(f"Message {i}", duration_sec=30)
        overlay.set_layout_mode(True)
        overlay.set_layout_mode(False)
    # Без ожидания DeferredDelete ресурсы уже ограничены, а текст последнего сообщения актуален.
    assert len(overlay.findChildren(QFrame, "msg")) <= MAX_MESSAGES + 1
    assert overlay._frames()[-1].findChild(OutlinedLabel).text() == "Message 249"
    overlay.close()


def test_reused_block_keeps_new_deadline(qapp, monkeypatch):
    import app.ui.overlay as module
    monkeypatch.setattr(module, "FADE_MS", 1)
    overlay = Overlay(OverlaySettings())
    overlay.show_message("old", duration_sec=.001)
    for i in range(MAX_MESSAGES):
        overlay.show_message(f"new {i}", duration_sec=30)
    QTest.qWait(30)
    assert overlay._frames()[-1].findChild(OutlinedLabel).text() == f"new {MAX_MESSAGES-1}"
    assert overlay._frames()[-1].graphicsEffect().opacity() == 1.0
    overlay.close()
