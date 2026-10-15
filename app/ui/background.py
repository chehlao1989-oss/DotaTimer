"""Фон главного окна (вариант Б, выбор автора 15.10): картинка игрока или тёмный градиент.

Картинку выбирает сам игрок (кнопка «Выбрать фон…» во вкладке «Общие»): арт Valve в программу не кладём —
это их авторская собственность. Картинка заполняет окно с обрезкой краёв (без искажения пропорций) и затемняется,
чтобы текст на полупрозрачных панелях читался.
"""
import logging
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

TOP_SHADE, BOTTOM_SHADE = 60, 150  # затемнение картинки сверху и снизу (0–255): текст читается на любом фоне


class BackgroundWidget(QWidget):
    def __init__(self, image: str | None = None, parent: QWidget | None = None):
        super().__init__(parent, objectName="bg")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._pixmap: QPixmap | None = None
        self.set_image(image)

    def set_image(self, image: str | None) -> bool:
        """Поставить картинку (None — тёмный градиент). False — файл не читается (фон остаётся градиентом)."""
        pixmap = QPixmap(image) if image and Path(image).is_file() else None
        if image and (pixmap is None or pixmap.isNull()):
            log.warning("Фон не загрузился: %s", image)
            pixmap = None
        self._pixmap = pixmap
        self.update()
        return pixmap is not None or not image

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.rect()
        if self._pixmap is not None:
            scaled = self._pixmap.scaled(rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (scaled.width() - rect.width()) // 2
            y = (scaled.height() - rect.height()) // 2
            painter.drawPixmap(rect, scaled, rect.translated(x, y))
            shade = QLinearGradient(0, 0, 0, rect.height())
            shade.setColorAt(0, QColor(10, 8, 8, TOP_SHADE))
            shade.setColorAt(1, QColor(10, 6, 6, BOTTOM_SHADE))
            painter.fillRect(rect, shade)
        else:
            plain = QLinearGradient(0, 0, 0, rect.height())
            plain.setColorAt(0, QColor("#2a0f0b"))
            plain.setColorAt(1, QColor("#0d0807"))
            painter.fillRect(QRectF(rect), plain)
        painter.end()
