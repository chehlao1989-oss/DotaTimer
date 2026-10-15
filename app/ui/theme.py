"""Тёмная тема в стиле Доты и иконка программы (рисуется кодом, без файлов)."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

DARK_QSS = """
QWidget { background: #15181d; color: #e6e6e6; font-size: 13px; font-family: "Segoe UI"; }
QTabWidget::pane { border: 1px solid #2c3139; border-radius: 6px; }
QTabBar::tab { background: #1d2127; padding: 7px 14px; border: 1px solid #2c3139;
               border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { background: #2a1f1c; color: #f0c674; }
QGroupBox { border: 1px solid #2c3139; border-radius: 6px; margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #c9a45c; }
QPushButton { background: #7a2418; border: 1px solid #a3342a; border-radius: 5px; padding: 6px 12px; }
QPushButton:hover { background: #93301f; }
QComboBox, QSpinBox { background: #1d2127; border: 1px solid #3a404a; border-radius: 4px; padding: 3px 6px; }
QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; border: 1px solid #6b7380;
                                              background: #1d2127; }
QCheckBox::indicator { border-radius: 3px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: #c9a45c; border-color: #e3b341; }
QLabel#status { font-size: 14px; font-weight: 600; padding: 4px 2px; }
QLabel#hint { color: #8a93a0; }
"""


ICON_SIZE = 256  # рисуется в 256 px, Windows сама уменьшает для ярлыка, трея и панели задач


def _draw_stopwatch(painter: QPainter) -> None:
    """Секундомер (вариант А, выбор автора 15.10): золотой корпус на тёмно-красном фоне, стрелка на 6 и красная
    дуга до неё — намёк на первую активку на 6:00. Координаты — в квадрате 256×256."""
    s = ICON_SIZE
    frame = QRectF(8, 8, s - 16, s - 16)
    background = QLinearGradient(0, 8, 0, s - 8)
    background.setColorAt(0, QColor("#3a0f0c"))
    background.setColorAt(1, QColor("#120606"))
    path = QPainterPath()
    path.addRoundedRect(frame, 52, 52)
    painter.fillPath(path, QBrush(background))
    painter.setPen(QPen(QColor("#c9962f"), 6))
    painter.drawPath(path)
    gold = QLinearGradient(0, 40, 0, 230)
    gold.setColorAt(0, QColor("#ffe08a"))
    gold.setColorAt(1, QColor("#b8862b"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(gold))
    painter.drawRoundedRect(QRectF(112, 30, 32, 22), 6, 6)  # кнопка
    painter.drawRect(QRectF(122, 50, 12, 14))  # ушко
    center = QPointF(128, 140)
    painter.setBrush(QColor("#1b0907"))
    painter.setPen(QPen(QBrush(gold), 14))
    painter.drawEllipse(center, 78, 78)
    painter.setPen(QPen(QColor("#e0452f"), 12, Qt.SolidLine, Qt.RoundCap))
    painter.drawArc(QRectF(center.x() - 58, center.y() - 58, 116, 116), 90 * 16, -180 * 16)
    painter.setPen(QPen(QColor("#f3d27a"), 5, Qt.SolidLine, Qt.RoundCap))
    for k in range(12):
        angle = math.radians(k * 30)
        inner = 44 if k % 3 else 38
        painter.drawLine(QPointF(center.x() + inner * math.sin(angle), center.y() - inner * math.cos(angle)),
                         QPointF(center.x() + 56 * math.sin(angle), center.y() - 56 * math.cos(angle)))
    painter.setPen(QPen(QColor("#ffffff"), 9, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(center, QPointF(center.x(), center.y() + 40))  # стрелка на 6
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#ffe08a"))
    painter.drawEllipse(center, 9, 9)


def app_icon() -> QIcon:
    """Значок программы (окно, трей, ярлык, exe — через tools/make_icon.py): секундомер."""
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    _draw_stopwatch(painter)
    painter.end()
    return QIcon(pixmap)
