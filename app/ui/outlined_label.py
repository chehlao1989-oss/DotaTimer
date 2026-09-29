"""Надпись с тёмной обводкой: читается на любом фоне игры.

Обводка рисуется вручную (QPainterPath), без QGraphicsEffect: вложенные графические эффекты
(тень внутри плавно гаснущего сообщения) Qt рисует ненадёжно.
Переносит строки по словам под ширину wrap_width.
"""
from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

OUTLINE_COLOR = QColor(0, 0, 0, 235)
TEXT_COLOR = QColor("#ffffff")


class OutlinedLabel(QWidget):
    def __init__(self, text: str, font: QFont, wrap_width: int | None = None, color: QColor = TEXT_COLOR,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self._font = font
        self._wrap = wrap_width
        self._color = color
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._relayout()

    def text(self) -> str:
        return self._text

    def set_font(self, font: QFont, wrap_width: int | None = None) -> None:
        self._font = font
        if wrap_width is not None:
            self._wrap = wrap_width
        self._relayout()

    def _lines(self) -> list[str]:
        metrics = QFontMetrics(self._font)
        lines = []
        for paragraph in self._text.split("\n"):
            if not self._wrap:
                lines.append(paragraph)
                continue
            current = ""
            for word in paragraph.split(" "):
                candidate = f"{current} {word}" if current else word
                if current and metrics.horizontalAdvance(candidate) > self._wrap:
                    lines.append(current)
                    current = "   " + word if paragraph.startswith("   ") else word
                else:
                    current = candidate
            lines.append(current)
        return lines

    def _outline_width(self) -> float:
        return max(2.0, self._font.pixelSize() / 7)

    def _relayout(self) -> None:
        metrics = QFontMetrics(self._font)
        pad = round(self._outline_width())
        lines = self._lines()
        width = max((metrics.horizontalAdvance(line) for line in lines), default=0) + pad * 2
        height = metrics.lineSpacing() * len(lines) + pad * 2
        self._size = QSize(width, height)
        self.setFixedSize(self._size)
        self.update()

    def sizeHint(self) -> QSize:
        return self._size

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        metrics = QFontMetrics(self._font)
        pad = self._outline_width()
        path = QPainterPath()
        for index, line in enumerate(self._lines()):
            y = pad + metrics.ascent() + index * metrics.lineSpacing()
            path.addText(QPointF(pad, y), self._font, line)
        painter.setPen(QPen(OUTLINE_COLOR, self._outline_width() * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawPath(path)
        painter.end()
