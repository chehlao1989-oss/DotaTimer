"""Оверлей: прозрачное окно поверх игры с короткими сообщениями.

- клики проходят сквозь окно, фокус у Доты не отбирается;
- сообщение держится display_sec секунд (или своё время) и плавно гаснет;
- шрифт жирный с кириллицей (Segoe UI Semibold), размер от высоты экрана: 20 px при 1080p,
  плюс масштаб пользователя 80–150%; у текста обводка и тёмная подложка — читается на любом фоне;
- вид как у оповещений Доты о покупке (выбор автора 01.10, вариант 2): полоса, тающая влево, текст прижат
  вправо, время и предмет — золотом, у подсказок справа портрет врага и иконка предмета;
- режим «Настроить расположение»: окно можно тащить мышью, колесо меняет размер,
  Ctrl+колесо меняет прозрачность, правый клик завершает настройку.
"""
import re
from pathlib import Path

from PySide6.QtCore import QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.config import OverlaySettings
from app.i18n import ru
from app.ui.outlined_label import OutlinedLabel

# 440 → 560: карточка угроз переносилась посреди фраз (предпросмотр 30.09); 560 → 700: в подсказке с иконками
# «… Silver Edge» переносилось в 4 строки (предпросмотр 01.10, BUGLOG №35)
BASE_WIDTH = 700
BASE_FONT_PX = 20  # при высоте экрана 1080
FONT_FAMILY = "Segoe UI"  # с полужирным начертанием Windows подставляет Segoe UI Semibold
SCALE_MIN, SCALE_MAX = 0.8, 1.5
MAX_MESSAGES = 4
FADE_MS = 800
MARGIN_RIGHT = 0  # полоса прижата к правому краю, как оповещения Доты о покупке
# Место по умолчанию (при высоте 1080): под оповещениями Доты о покупке и не ниже миникарты справа.
# Оповещение о покупке — y ≈ 445–500 (оценка по скриншоту автора 01.10: снимок обрезан, ±15 px); стопка из
# нескольких покупок не замерена (STATUS → допущения). Миникарта справа — с 798 (замер, MINIMAP_REF в предпросмотре).
MESSAGES_TOP = 540
MESSAGES_BOTTOM = 790
# Сохранённое место окна сбрасывается один раз при смене схемы расположения (01.10: окна переехали направо,
# у автора окно стояло на y = 304 и наслаивалось на оповещение Доты о покупке, BUGLOG №35).
LAYOUT_VERSION = 1
ICON_HEIGHT_EM = 1.8  # высота иконок в подсказке, в размерах шрифта
RAISE_INTERVAL_MS = 2000

# полоса тает влево (цвета подобраны на глаз по оповещению Доты о покупке на скриншоте 01.10)
FRAME_STYLE = ("QFrame#msg { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(8, 10, 14, 0), "
               "stop:0.22 rgba(8, 10, 14, 190), stop:1 rgba(8, 10, 14, 225)); }")
HINT_FRAME_STYLE = "QFrame#msg {{ background: rgba(12, 14, 18, 215); border-radius: 8px; border-left: 4px solid {accent}; }}"
FRAME_PAD_X, FRAME_PAD_Y = 12, 8
FRAME_PAD_LEFT = 48  # слева место под «таяние» полосы
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


def clamp_to_screen(x: int, y: int, width: int, height: int, screen) -> tuple[int, int]:
    """Окно целиком на экране: сохранённое место + новая ширина не должны вылезать за край
    (окно сообщений на x = 2121 при ширине 560 обрезалось справа на 2560 — игра 01.10, BUGLOG №34)."""
    x = max(screen.left(), min(x, screen.right() + 1 - width))
    y = max(screen.top(), min(y, screen.bottom() + 1 - height))
    return x, y


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
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if self.settings.x is None or self.settings.y is None:
            x, y = default_pos(screen, width, height)
        else:
            x, y = self.settings.x, self.settings.y
        self.move(*clamp_to_screen(x, y, width, height, screen))

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
    def __init__(self, settings: OverlaySettings, images_dir: Path | None = None):
        if settings.layout_version < LAYOUT_VERSION:
            settings.x = settings.y = None
            settings.layout_version = LAYOUT_VERSION
        super().__init__(settings)
        self.images_dir = images_dir  # иконки героев и предметов (cache/images); задаётся после загрузки данных
        self._hint: QFrame | None = None
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(6, 6, 6, 6)
        self._box.setSpacing(6)
        self._box.addStretch(1)  # растяжка внизу: сообщения копятся сверху вниз, верх окна — под оповещениями Доты
        self.relayout()

    # --- размеры и положение ---
    def _font_px(self) -> int:
        return round(BASE_FONT_PX * self.factor())

    def relayout(self) -> None:
        width = round(BASE_WIDTH * self.factor())
        height = min(round((self._font_px() * 2.6 + 6) * MAX_MESSAGES + 12),
                     round((MESSAGES_BOTTOM - MESSAGES_TOP) * screen_factor()))
        self.place(width, height, lambda s, w, h: (s.right() + 1 - w - MARGIN_RIGHT,
                                                   s.top() + round(MESSAGES_TOP * screen_factor())))
        for frame in self._frames():
            self._style(frame)
        self.relayout_hint()

    # --- сообщения ---
    def _icon_size(self, pixmap: QPixmap) -> tuple[int, int]:
        h = round(self._font_px() * ICON_HEIGHT_EM)
        return round(pixmap.width() * h / max(1, pixmap.height())), h

    def _text_width(self, frame: QFrame | None = None) -> int:
        icons = sum(self._icon_size(pix)[0] + 6 for _, pix in getattr(frame, "icons", []))
        return self.width() - 12 - FRAME_PAD_LEFT - FRAME_PAD_X - 8 - icons

    def _load_icons(self, icons) -> list[QPixmap]:
        if self.images_dir is None:
            return []
        pixmaps = [QPixmap(str(Path(self.images_dir) / kind / f"{key}.png")) for kind, key in icons]
        return [p for p in pixmaps if not p.isNull()]

    def _make_frame(self, text: str, accent: str, font_delta: int = 0, icons=(), highlights=()) -> QFrame:
        frame = QFrame(self, objectName="msg")
        layout = QHBoxLayout(frame)
        layout.setSpacing(6)
        frame.setProperty("accent", accent)
        frame.setProperty("font_delta", font_delta)
        is_hint = accent == ACCENT_LAYOUT
        left = FRAME_PAD_X if is_hint else FRAME_PAD_LEFT
        layout.setContentsMargins(left, FRAME_PAD_Y, FRAME_PAD_X, FRAME_PAD_Y)
        words = [re.escape(w) for w in highlights if w]
        pattern = None if is_hint else re.compile("|".join([ru.HIGHLIGHT_PATTERN] + words))
        frame.icons = [(QLabel(frame), pixmap) for pixmap in self._load_icons(icons)]
        font = overlay_font(max(12, self._font_px() + font_delta))
        # короткие сообщения прижаты вправо, как в Доте; длинная карточка угроз — влево, иначе «лесенка»
        label = OutlinedLabel(text, font, self._text_width(frame), parent=frame, highlight=pattern,
                              align_right=not is_hint and text.count("\n") <= 1)
        layout.addWidget(label, 0, Qt.AlignVCenter)
        for icon, _ in frame.icons:
            layout.addWidget(icon, 0, Qt.AlignVCenter)
        self._style(frame)
        return frame

    def _style(self, frame: QFrame) -> None:
        accent = frame.property("accent")
        frame.setStyleSheet(HINT_FRAME_STYLE.format(accent=accent) if accent == ACCENT_LAYOUT else FRAME_STYLE)
        for icon, pixmap in frame.icons:
            w, h = self._icon_size(pixmap)
            icon.setPixmap(pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            icon.setFixedSize(w, h)
        label = frame.findChild(OutlinedLabel)
        font = overlay_font(max(12, self._font_px() + (frame.property("font_delta") or 0)))
        label.set_font(font, self._text_width(frame))

    def show_message(self, text: str, important: bool = False, duration_sec: float | None = None,
                     icons=(), highlights=()) -> None:
        """icons — [(папка, ключ)] из cache/images, например ("heroes", "axe"); highlights — слова золотом."""
        frame = self._make_frame(text, ACCENT_IMPORTANT if important else ACCENT_NORMAL,
                                 icons=icons, highlights=highlights)
        effect = QGraphicsOpacityEffect(frame)
        effect.setOpacity(1.0)
        frame.setGraphicsEffect(effect)
        # новые сообщения внизу, лишние старые убираем
        self._box.insertWidget(self._box.count() - 1, frame, 0, Qt.AlignRight)  # перед растяжкой внизу
        # лишние старые убираем: больше MAX_MESSAGES или не влезают по высоте (окно не заходит на миникарту справа)
        frames = self._frames()
        room = self.height() - 12 - (self._hint.sizeHint().height() + 6 if self._hint else 0)
        while len(frames) > 1 and (len(frames) > MAX_MESSAGES
                                   or sum(f.sizeHint().height() + 6 for f in frames) > room):
            old = frames.pop(0)
            old.hide()
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
            self._box.insertWidget(0, self._hint, 0, Qt.AlignRight)
        elif not on and self._hint is not None:
            self._hint.deleteLater()
            self._hint = None
        super().set_layout_mode(on)

    def relayout_hint(self) -> None:
        if self._hint is not None:
            self._style(self._hint)
