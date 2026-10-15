"""Фон главного окна (вариант Б, выбор автора 15.10).

В катке — рендер героя игрока справа на тёмно-красном фоне (картинка с сервера Valve, app/ui/backdrops.py);
вне катки — своя картинка игрока (кнопка «Выбрать фон…»), а без неё — панорама карты. Пока картинки нет —
тёмный градиент. Своя картинка и панорама заполняют окно с обрезкой краёв (без искажения) и затемняются,
чтобы текст на полупрозрачных панелях читался. Арт Valve в программу не кладём — это их авторская собственность.
"""
import logging
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from app.ui.backdrops import KIND_HERO

log = logging.getLogger(__name__)

TOP_SHADE, BOTTOM_SHADE = 60, 150  # затемнение картинки сверху и снизу (0–255): текст читается на любом фоне
HERO_HEIGHT = 1.05  # рендер героя — чуть выше окна (ноги уходят за низ), прижат вправо


def _load(image: str | None) -> QPixmap | None:
    pixmap = QPixmap(image) if image and Path(image).is_file() else None
    return pixmap if pixmap is not None and not pixmap.isNull() else None


class BackgroundWidget(QWidget):
    def __init__(self, image: str | None = None, parent: QWidget | None = None):
        super().__init__(parent, objectName="bg")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._custom: QPixmap | None = None
        self._auto: QPixmap | None = None
        self._auto_kind: str | None = None
        self.set_image(image)

    def set_image(self, image: str | None) -> bool:
        """Своя картинка игрока (None — убрать, тогда фон с сервера Valve). False — файл не читается."""
        pixmap = _load(image)
        if image and pixmap is None:
            log.warning("Фон не загрузился: %s", image)
            return False
        self._custom = pixmap
        self.update()
        return True

    def set_auto(self, kind: str, image: str) -> None:
        """Картинка с сервера Valve: панорама карты или рендер героя (KIND_HERO)."""
        pixmap = _load(image)
        if pixmap is None:
            log.warning("Фон Valve не загрузился: %s", image)
            return
        self._auto, self._auto_kind = pixmap, kind
        self.update()

    @property
    def custom(self) -> bool:
        return self._custom is not None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.rect()
        # в катке — всегда герой игрока; вне катки — своя картинка, иначе карта (автор 15.10)
        if self._auto is not None and self._auto_kind == KIND_HERO:
            self._hero(painter, self._auto)
        elif self._custom is not None:
            self._cover(painter, self._custom)
        elif self._auto is not None:
            self._cover(painter, self._auto)
        else:
            self._plain(painter)
        painter.end()

    def _cover(self, painter: QPainter, pixmap: QPixmap) -> None:
        rect = self.rect()
        scaled = pixmap.scaled(rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (scaled.width() - rect.width()) // 2
        y = (scaled.height() - rect.height()) // 2
        painter.drawPixmap(rect, scaled, rect.translated(x, y))
        shade = QLinearGradient(0, 0, 0, rect.height())
        shade.setColorAt(0, QColor(10, 8, 8, TOP_SHADE))
        shade.setColorAt(1, QColor(10, 6, 6, BOTTOM_SHADE))
        painter.fillRect(rect, shade)

    def _plain(self, painter: QPainter) -> None:
        rect = self.rect()
        plain = QLinearGradient(0, 0, 0, rect.height())
        plain.setColorAt(0, QColor("#2a0f0b"))
        plain.setColorAt(1, QColor("#0d0807"))
        painter.fillRect(QRectF(rect), plain)

    def _hero(self, painter: QPainter, pixmap: QPixmap) -> None:
        """Рендер героя (прозрачный фон) справа на тёмно-красном градиенте с тёплой подсветкой."""
        rect = self.rect()
        base = QLinearGradient(0, 0, rect.width(), rect.height())
        base.setColorAt(0, QColor("#1a0907"))
        base.setColorAt(1, QColor("#3b120c"))
        painter.fillRect(rect, base)
        glow = QRadialGradient(rect.width() * 0.72, rect.height() * 0.45, rect.height() * 0.6)
        glow.setColorAt(0, QColor(227, 120, 60, 110))
        glow.setColorAt(1, QColor(227, 120, 60, 0))
        painter.fillRect(rect, glow)
        hero = pixmap.scaledToHeight(round(rect.height() * HERO_HEIGHT), Qt.SmoothTransformation)
        painter.drawPixmap(rect.width() - hero.width() + hero.width() // 8, rect.height() - hero.height() + 10, hero)
        shade = QLinearGradient(0, 0, rect.width(), 0)  # слева темнее — там текст
        shade.setColorAt(0, QColor(10, 6, 6, 120))
        shade.setColorAt(0.6, QColor(10, 6, 6, 40))
        shade.setColorAt(1, QColor(10, 6, 6, 0))
        painter.fillRect(rect, shade)
