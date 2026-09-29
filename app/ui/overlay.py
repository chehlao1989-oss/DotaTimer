"""Оверлей: прозрачное окно поверх игры с короткими сообщениями.

- клики проходят сквозь окно, фокус у Доты не отбирается;
- сообщение держится display_sec секунд и плавно гаснет;
- режим «Настроить расположение»: окно можно тащить мышью, колесо меняет размер,
  Ctrl+колесо меняет прозрачность, правый клик завершает настройку.
"""
from PySide6.QtCore import QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QVBoxLayout, QWidget

from app.config import OverlaySettings
from app.i18n import ru

BASE_WIDTH = 420
BASE_FONT_PX = 20
MAX_MESSAGES = 4
FADE_MS = 800
MARGIN_RIGHT = 40
RAISE_INTERVAL_MS = 2000

STYLE = (
    "QLabel {{ color: #f2f2f2; background: rgba(18, 20, 24, 200); border-radius: 8px;"
    " border-left: 4px solid {accent}; padding: 8px 12px; font-size: {font}px; font-weight: 600; }}"
)
ACCENT_NORMAL = "#8a9bb0"
ACCENT_IMPORTANT = "#e3b341"
ACCENT_LAYOUT = "#6fa8ff"
LAYOUT_FRAME = "background: rgba(40, 90, 160, 90); border: 2px dashed #6fa8ff; border-radius: 8px;"

_BASE_FLAGS = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
_CLICK_THROUGH = Qt.WindowTransparentForInput


class Overlay(QWidget):
    layout_finished = Signal()

    def __init__(self, settings: OverlaySettings):
        super().__init__(None, _BASE_FLAGS | _CLICK_THROUGH)
        self.settings = settings
        self._layout_mode = False
        self._drag_offset: QPoint | None = None
        self._hint: QLabel | None = None
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(6, 6, 6, 6)
        self._box.setSpacing(6)
        self._box.addStretch(1)
        self._apply_geometry()
        self.setWindowOpacity(settings.opacity)
        # Дота может перекрыть окно при переключении, поэтому периодически поднимаем его наверх
        self._raise_timer = QTimer(self, interval=RAISE_INTERVAL_MS, timeout=self.raise_)
        self._raise_timer.start()

    # --- размеры и положение ---
    def _font_px(self) -> int:
        return round(BASE_FONT_PX * self.settings.scale)

    def _apply_geometry(self) -> None:
        width = round(BASE_WIDTH * self.settings.scale)
        height = round((self._font_px() * 2.6 + 6) * MAX_MESSAGES + 12)
        self.resize(width, height)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if self.settings.x is None or self.settings.y is None:
            self.move(screen.right() - width - MARGIN_RIGHT, screen.center().y() - height // 2)
        else:
            self.move(self.settings.x, self.settings.y)

    # --- сообщения ---
    def show_message(self, text: str, important: bool = False) -> None:
        label = QLabel(text, self)
        label.setWordWrap(True)
        self._style(label, ACCENT_IMPORTANT if important else ACCENT_NORMAL)
        effect = QGraphicsOpacityEffect(label)
        effect.setOpacity(1.0)
        label.setGraphicsEffect(effect)
        # новые сообщения внизу, лишние старые убираем
        self._box.addWidget(label)
        labels = self._message_labels()
        for old in labels[:-MAX_MESSAGES]:
            old.deleteLater()
        QTimer.singleShot(round(self.settings.display_sec * 1000), lambda: self._fade_out(label, effect))
        if not self.isVisible():
            self.show()

    def _style(self, label: QLabel, accent: str, font_delta: int = 0) -> None:
        """Оформление сообщения; accent и font_delta запоминаются для смены размера."""
        label.setProperty("accent", accent)
        label.setProperty("font_delta", font_delta)
        label.setStyleSheet(STYLE.format(accent=accent, font=max(12, self._font_px() + font_delta)))

    def _message_labels(self) -> list[QLabel]:
        return [self._box.itemAt(i).widget() for i in range(self._box.count())
                if self._box.itemAt(i).widget() is not None and self._box.itemAt(i).widget() is not self._hint]

    def _fade_out(self, label: QLabel, effect: QGraphicsOpacityEffect) -> None:
        try:
            animation = QPropertyAnimation(effect, b"opacity", label)
        except RuntimeError:
            return  # сообщение уже удалено
        animation.setDuration(FADE_MS)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.finished.connect(label.deleteLater)
        animation.start()

    # --- режим «Настроить расположение» ---
    def set_layout_mode(self, on: bool) -> None:
        self._layout_mode = on
        flags = _BASE_FLAGS if on else _BASE_FLAGS | _CLICK_THROUGH
        self.setWindowFlags(flags)
        self.setStyleSheet(f"Overlay {{ {LAYOUT_FRAME} }}" if on else "")
        self.setAttribute(Qt.WA_StyledBackground, on)
        if on:
            self._hint = QLabel(ru.OVERLAY_LAYOUT_HINT, self)
            self._hint.setWordWrap(True)
            self._style(self._hint, ACCENT_LAYOUT, font_delta=-4)
            self._box.insertWidget(0, self._hint)
        elif self._hint is not None:
            self._hint.deleteLater()
            self._hint = None
        self.show()

    def mousePressEvent(self, event):
        if not self._layout_mode:
            return
        if event.button() == Qt.RightButton:
            self.set_layout_mode(False)
            self.layout_finished.emit()
        elif event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._layout_mode and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self.settings.x, self.settings.y = self.x(), self.y()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

    def wheelEvent(self, event):
        if not self._layout_mode:
            return
        step = 1 if event.angleDelta().y() > 0 else -1
        if event.modifiers() & Qt.ControlModifier:
            self.settings.opacity = round(min(1.0, max(0.3, self.settings.opacity + 0.05 * step)), 2)
            self.setWindowOpacity(self.settings.opacity)
        else:
            self.settings.scale = round(min(2.0, max(0.6, self.settings.scale + 0.1 * step)), 2)
            self.settings.x, self.settings.y = self.x(), self.y()
            self._apply_geometry()
            for label in self._message_labels() + ([self._hint] if self._hint else []):
                self._style(label, label.property("accent"), label.property("font_delta"))
