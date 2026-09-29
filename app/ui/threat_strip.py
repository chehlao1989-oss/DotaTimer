"""Полоска угроз: постоянный компактный блок поверх игры (п. 6.4 ТЗ).

Для каждой из 1–3 угроз: портрет героя, под ним 1–2 иконки контр-предметов.
- купленный совет — с зелёной галочкой, рядом следующий;
- если хватает золота — золотая рамка;
- совет только что сменился — яркая жёлтая рамка на 5 секунд;
- под иконкой прибавка винрейта («+6%»), если совет из статистики.
Клики проходят насквозь. Место, размер (80–150%) и прозрачность настраиваются,
Ctrl+Alt+H скрывает и показывает.
"""
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.config import StripSettings
from app.i18n import ru
from app.threats.recommend import ThreatRecommendation, format_delta
from app.ui.outlined_label import OutlinedLabel
from app.ui.overlay import DraggableOverlay, overlay_font

PORTRAIT_W, PORTRAIT_H = 96, 54  # при 1080p и масштабе 100%
ICON_W, ICON_H = 46, 34
CAPTION_PX = 13
HIGHLIGHT_MS = 5000
MARGIN = 20
BOTTOM_OFFSET = 300  # над миникартой слева
COLOR_BOUGHT = QColor("#3fb950")
COLOR_AFFORDABLE = QColor("#c9a45c")
COLOR_HIGHLIGHT = QColor("#ffd33d")
CAPTION_COLOR = QColor("#dfe6ee")
PANEL_STYLE = "QWidget#panel { background: rgba(12, 14, 18, 200); border-radius: 8px; }"


def _pixmap(path: Path, w: int, h: int) -> QPixmap:
    pixmap = QPixmap(str(path)) if path.is_file() else QPixmap()
    if pixmap.isNull():
        pixmap = QPixmap(w, h)
        pixmap.fill(QColor(40, 44, 52))
    return pixmap.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def item_icon(path: Path, w: int, h: int, bought: bool, affordable: bool, highlighted: bool) -> QPixmap:
    """Иконка предмета с рамкой и галочкой."""
    base = _pixmap(path, w, h)
    painter = QPainter(base)
    painter.setRenderHint(QPainter.Antialiasing)
    if bought:
        painter.fillRect(base.rect(), QColor(0, 0, 0, 110))
        pen = QPen(COLOR_BOUGHT, max(3, w // 10))
        painter.setPen(pen)
        painter.drawPolyline([QPointF(w * 0.22, h * 0.55), QPointF(w * 0.42, h * 0.78), QPointF(w * 0.8, h * 0.25)])
    border = COLOR_HIGHLIGHT if highlighted else (COLOR_AFFORDABLE if affordable and not bought else None)
    if border is not None:
        width = 4 if highlighted else 2
        painter.setPen(QPen(border, width))
        painter.drawRect(QRectF(width / 2, width / 2, w - width, h - width))
    painter.end()
    return base


class ThreatStrip(DraggableOverlay):
    def __init__(self, settings: StripSettings, images_dir: Path):
        super().__init__(settings)
        self.images_dir = images_dir
        self._recs: list[ThreatRecommendation] = []
        self._titles: dict[str, str] = {}
        self._highlight: set[tuple[str, str]] = set()  # (угроза, предмет), подсвеченные сейчас
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._panel = QWidget(self, objectName="panel")
        self._panel.setStyleSheet(PANEL_STYLE)
        outer.addWidget(self._panel)
        self._row = QHBoxLayout(self._panel)
        self.relayout()
        if not settings.visible:
            self.hide()

    def relayout(self) -> None:
        self._render()

    def update_recommendations(self, recs: list[ThreatRecommendation], titles: dict[str, str]) -> None:
        """Новые советы. Сменившиеся подсвечиваются на 5 секунд."""
        self._recs, self._titles = recs, titles
        for rec in recs:
            for item in rec.items:
                if item.changed:
                    key = (rec.hero, item.item)
                    self._highlight.add(key)
                    QTimer.singleShot(HIGHLIGHT_MS, lambda k=key: self._unhighlight(k))
        self._render()
        if recs and self.settings.visible and not self.isVisible():
            self.show()

    def _unhighlight(self, key) -> None:
        self._highlight.discard(key)
        self._render()

    def toggle(self) -> None:
        self.settings.visible = not self.settings.visible
        self.setVisible(self.settings.visible and (bool(self._recs) or self._layout_mode))

    def _clear(self) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render(self) -> None:
        f = self.factor()
        pw, ph = round(PORTRAIT_W * f), round(PORTRAIT_H * f)
        iw, ih = round(ICON_W * f), round(ICON_H * f)
        spacing = round(8 * f)
        self._clear()
        self._row.setContentsMargins(spacing, spacing, spacing, spacing)
        self._row.setSpacing(spacing * 2)
        recs = self._recs or ([] if not self._layout_mode else [ThreatRecommendation("", ())])
        for rec in recs:
            column = QVBoxLayout()
            column.setSpacing(round(4 * f))
            portrait = QLabel()
            portrait.setPixmap(_pixmap(self.images_dir / "heroes" / f"{rec.hero}.png", pw, ph))
            column.addWidget(portrait, alignment=Qt.AlignHCenter)
            title = self._titles.get(rec.hero, ru.STRIP_LAYOUT if self._layout_mode else rec.hero)
            name = OutlinedLabel(title, overlay_font(round(CAPTION_PX * f)))
            column.addWidget(name, alignment=Qt.AlignHCenter)
            icons = QHBoxLayout()
            icons.setSpacing(round(4 * f))
            for item in rec.items:
                cell = QVBoxLayout()
                cell.setSpacing(0)
                icon = QLabel()
                icon.setPixmap(item_icon(self.images_dir / "items" / f"{item.item}.png", iw, ih, item.bought,
                                         item.affordable, (rec.hero, item.item) in self._highlight))
                cell.addWidget(icon, alignment=Qt.AlignHCenter)
                caption = OutlinedLabel(self._caption(item), overlay_font(round((CAPTION_PX - 1) * f)),
                                        color=CAPTION_COLOR)
                cell.addWidget(caption, alignment=Qt.AlignHCenter)
                icons.addLayout(cell)
            column.addLayout(icons)
            self._row.addLayout(column)
        self._panel.adjustSize()
        self.adjustSize()
        width, height = self.sizeHint().width(), self.sizeHint().height()
        self.place(width, height, lambda s, w, h: (s.left() + MARGIN, s.bottom() - round(BOTTOM_OFFSET * f) - h))

    @staticmethod
    def _caption(item) -> str:
        if item.bought:
            return ru.STRIP_BOUGHT
        if item.delta is not None:
            return format_delta(item.delta, ru.DELTA_ZERO, ru.STRIP_DELTA)
        return ""

    def set_layout_mode(self, on: bool) -> None:
        self._layout_mode = on
        self._render()
        super().set_layout_mode(on)
        if not on and (not self._recs or not self.settings.visible):
            self.hide()
