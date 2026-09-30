"""Оверлей: прозрачное окно поверх игры с короткими сообщениями.

- клики проходят сквозь окно, фокус у Доты не отбирается;
- сообщение держится display_sec секунд (или своё время) и плавно гаснет;
- шрифт жирный с кириллицей (Segoe UI Semibold), размер от высоты экрана: 20 px при 1080p,
  плюс масштаб пользователя 80–150%; у текста тень и тёмная подложка — читается на любом фоне;
- режим «Настроить расположение»: окно можно тащить мышью, колесо меняет размер,
  Ctrl+колесо меняет прозрачность, правый клик завершает настройку.
"""
from PySide6.QtCore import QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QVBoxLayout, QWidget

from app.config import OverlaySettings
from app.i18n import ru
from app.ui.outlined_label import OutlinedLabel

BASE_WIDTH = 560  # карточка угроз с причинами механик в 440 переносилась посреди фраз (предпросмотр 30.09)
BASE_FONT_PX = 20  # при высоте экрана 1080
FONT_FAMILY = "Segoe UI"  # с полужирным начертанием Windows подставляет Segoe UI Semibold
SCALE_MIN, SCALE_MAX = 0.8, 1.5
MAX_MESSAGES = 4
FADE_MS = 800
MARGIN_RIGHT = 40
RAISE_INTERVAL_MS = 2000

FRAME_STYLE = "QFrame#msg {{ background: rgba(12, 14, 18, 215); border-radius: 8px; border-left: 4px solid {accent}; }}"
FRAME_PAD_X, FRAME_PAD_Y = 12, 8
ACCENT_NORMAL = "#8a9bb0"
ACCENT_IMPORTANT = "#e3b341"
ACCENT_LAYOUT = "#6fa8ff"
LAYOUT_FRAME = "background: rgba(40, 90, 160, 90); border: 2px dashed #6fa8ff; border-radius: 8px;"

BASE_FLAGS = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
CLICK_THROUGH = Qt.WindowTransparentForInput


def screen_factor() -> float:
    """Поправка на разрешение: 1.0 при высоте 1080, 1.33 при 1440 и т.д."""
    screen = QGuiApplication.primaryScreen()
    return screen.geometry().height() / 1080 if screen else 1.0


def overlay_font(px: int) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPixelSize(px)
    font.setWeight(QFont.DemiBold)
    return font


class DraggableOverlay(QWidget):
    """Основа для окон поверх игры: клики насквозь, режим настройки положения/размера/прозрачности."""

    layout_finished = Signal()

    def __init__(self, settings):
        super().__init__(None, BASE_FLAGS | CLICK_THROUGH)
        self.settings = settings  # должны быть поля x, y, scale, opacity
        self._layout_mode = False
        self._drag_offset: QPoint | None = None
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowOpacity(settings.opacity)
        self.settings.scale = min(SCALE_MAX, max(SCALE_MIN, settings.scale))
        # Дота может перекрыть окно при переключении, поэтому периодически поднимаем его наверх
        self._raise_timer = QTimer(self, interval=RAISE_INTERVAL_MS, timeout=self.raise_)
        self._raise_timer.start()

    def factor(self) -> float:
        return screen_factor() * self.settings.scale

    def place(self, width: int, height: int, default_pos) -> None:
        self.resize(width, height)
        if self.settings.x is None or self.settings.y is None:
            self.move(*default_pos(QGuiApplication.primaryScreen().availableGeometry(), width, height))
        else:
            self.move(self.settings.x, self.settings.y)

    def relayout(self) -> None:
        """Пересчитать размеры после смены масштаба (переопределяется)."""

    def set_layout_mode(self, on: bool) -> None:
        self._layout_mode = on
        self.setWindowFlags(BASE_FLAGS if on else BASE_FLAGS | CLICK_THROUGH)
        self.setStyleSheet(f"{type(self).__name__} {{ {LAYOUT_FRAME} }}" if on else "")
        self.setAttribute(Qt.WA_StyledBackground, on)
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
            self.settings.scale = round(min(SCALE_MAX, max(SCALE_MIN, self.settings.scale + 0.1 * step)), 2)
            self.settings.x, self.settings.y = self.x(), self.y()
            self.relayout()


class Overlay(DraggableOverlay):
    def __init__(self, settings: OverlaySettings):
        super().__init__(settings)
        self._hint: QFrame | None = None
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(6, 6, 6, 6)
        self._box.setSpacing(6)
        self._box.addStretch(1)
        self.relayout()

    # --- размеры и положение ---
    def _font_px(self) -> int:
        return round(BASE_FONT_PX * self.factor())

    def relayout(self) -> None:
        width = round(BASE_WIDTH * self.factor())
        height = round((self._font_px() * 2.6 + 6) * MAX_MESSAGES + 12)
        self.place(width, height, lambda s, w, h: (s.right() - w - MARGIN_RIGHT, s.center().y() - h // 2))
        for frame in self._frames():
            self._style(frame)
        self.relayout_hint()

    # --- сообщения ---
    def _text_width(self) -> int:
        return self.width() - 12 - FRAME_PAD_X * 2 - 8

    def _make_frame(self, text: str, accent: str, font_delta: int = 0) -> QFrame:
        frame = QFrame(self, objectName="msg")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(FRAME_PAD_X, FRAME_PAD_Y, FRAME_PAD_X, FRAME_PAD_Y)
        font = overlay_font(max(12, self._font_px() + font_delta))
        layout.addWidget(OutlinedLabel(text, font, self._text_width(), parent=frame))
        frame.setProperty("accent", accent)
        frame.setProperty("font_delta", font_delta)
        self._style(frame)
        return frame

    def _style(self, frame: QFrame) -> None:
        frame.setStyleSheet(FRAME_STYLE.format(accent=frame.property("accent")))
        label = frame.findChild(OutlinedLabel)
        font = overlay_font(max(12, self._font_px() + (frame.property("font_delta") or 0)))
        label.set_font(font, self._text_width())

    def show_message(self, text: str, important: bool = False, duration_sec: float | None = None) -> None:
        frame = self._make_frame(text, ACCENT_IMPORTANT if important else ACCENT_NORMAL)
        effect = QGraphicsOpacityEffect(frame)
        effect.setOpacity(1.0)
        frame.setGraphicsEffect(effect)
        # новые сообщения внизу, лишние старые убираем
        self._box.addWidget(frame)
        for old in self._frames()[:-MAX_MESSAGES]:
            old.deleteLater()
        seconds = duration_sec if duration_sec is not None else self.settings.display_sec
        QTimer.singleShot(round(seconds * 1000), lambda: self._fade_out(frame, effect))
        if not self.isVisible():
            self.show()

    def _frames(self) -> list[QFrame]:
        return [self._box.itemAt(i).widget() for i in range(self._box.count())
                if self._box.itemAt(i).widget() is not None and self._box.itemAt(i).widget() is not self._hint]

    def _fade_out(self, frame: QFrame, effect: QGraphicsOpacityEffect) -> None:
        try:
            animation = QPropertyAnimation(effect, b"opacity", frame)
        except RuntimeError:
            return  # сообщение уже удалено
        animation.setDuration(FADE_MS)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.finished.connect(frame.deleteLater)
        animation.start()

    # --- режим «Настроить расположение» ---
    def set_layout_mode(self, on: bool) -> None:
        if on and self._hint is None:
            self._hint = self._make_frame(ru.OVERLAY_LAYOUT_HINT, ACCENT_LAYOUT, font_delta=-4)
            self._box.insertWidget(0, self._hint)
        elif not on and self._hint is not None:
            self._hint.deleteLater()
            self._hint = None
        super().set_layout_mode(on)

    def relayout_hint(self) -> None:
        if self._hint is not None:
            self._style(self._hint)
