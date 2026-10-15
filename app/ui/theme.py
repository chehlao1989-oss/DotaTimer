"""Тема в стиле Доты (вариант Б автора 15.10: панели поверх фона) и иконка программы (рисуется кодом)."""
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF)

from app import paths

# Вариант Б (выбор автора 15.10): полупрозрачные тёмные панели с золотой рамкой поверх фона главного окна
# (#bg — app/ui/background.py). Остальные окна (выбор героев, диалоги) — на сплошном тёмном фоне.
_QSS = """
QWidget { background: #14110f; color: #f2efe9; font-size: 13px; font-family: "Segoe UI"; }
#bg, #bg QTabWidget, #bg QStackedWidget, #bg QStackedWidget > QWidget, #bg QLabel, #bg QCheckBox,
#bg QRadioButton, #bg QScrollArea, #bg QScrollArea > QWidget > QWidget { background: transparent; }
QLabel#status { font-size: 16px; font-weight: 700; color: #ffd27a; padding: 6px 2px; }
QFrame#header { background: rgba(10,8,8,175); border: 1px solid rgba(227,179,65,70); border-radius: 8px; }
QLabel#hint { color: #c4bdb2; }
QTabWidget::pane { border: 1px solid rgba(227,179,65,90); border-radius: 10px; background: rgba(12,10,10,120); top: -1px; }
QTabBar::tab { background: rgba(12,10,10,150); color: #d2cbc0; padding: 9px 14px; border: 1px solid rgba(227,179,65,50);
               border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px;
               font-weight: 600; }
QTabBar::tab:selected { background: rgba(110,31,21,225); color: #ffd27a; border-color: rgba(227,179,65,160); }
QTabBar::tab:hover { color: #ffffff; }
QGroupBox { background: rgba(20,16,15,110); border: 1px solid rgba(227,179,65,70); border-radius: 8px; margin-top: 16px;
            padding: 10px 8px 6px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #ffd27a; font-weight: 700; }
QCheckBox, QRadioButton { background: transparent; spacing: 8px; padding: 2px 0; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; border: 1px solid #8a7a60;
                                              background: rgba(0,0,0,130); }
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: #e3b341; border-color: #ffd27a; }
QPushButton { background: rgba(18,12,10,235); color: #ffd27a; border: 1px solid rgba(227,179,65,150);
              border-radius: 6px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background: rgba(110,31,21,240); color: #ffffff; border-color: #ffd27a; }
QPushButton:pressed { background: rgba(155,46,31,245); }
QComboBox, QKeySequenceEdit, QLineEdit { background: rgba(0,0,0,150); border: 1px solid rgba(227,179,65,90);
                                         border-radius: 4px; padding: 3px 8px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url(ARROW_DOWN); width: 10px; height: 6px; }
QComboBox QAbstractItemView { background: #1d1714; selection-background-color: #6e1f15; }
QSpinBox { padding: 3px 26px 3px 8px; min-width: 92px; background: rgba(0,0,0,150); border: 1px solid rgba(227,179,65,90);
           border-radius: 4px; }
QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: border; width: 20px; background: rgba(60,30,20,210);
                                             border-left: 1px solid rgba(227,179,65,90); }
QSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 4px; }
QSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 4px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: rgba(110,31,21,235); }
QSpinBox::up-arrow { image: url(ARROW_UP); width: 10px; height: 6px; }
QSpinBox::down-arrow { image: url(ARROW_DOWN); width: 10px; height: 6px; }
QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,40); border-radius: 2px; }
QSlider::handle:horizontal { background: #e3b341; width: 14px; margin: -6px 0; border-radius: 7px; }
QMenu { background: #1d1714; border: 1px solid rgba(227,179,65,90); }
QMenu::item:selected { background: #6e1f15; }
"""
ARROW_COLOR = "#ffd27a"


def _arrow(path: Path, up: bool) -> str:
    """Стрелка для кнопок поля с числом: стандартная стрелка Windows под нашим стилем наезжала на «сек» и не
    нажималась (BUGLOG №45) — рисуем свою. Кладётся в %APPDATA%\\DotaTimer\\ui."""
    if not path.exists():
        image = QPixmap(10, 6)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(ARROW_COLOR))
        points = [QPointF(0, 6), QPointF(10, 6), QPointF(5, 0)] if up else [QPointF(0, 0), QPointF(10, 0), QPointF(5, 6)]
        painter.drawPolygon(QPolygonF(points))
        painter.end()
        image.save(str(path))
    return path.as_posix()


def app_stylesheet(body_font: str = "Segoe UI", head_font: str = "Segoe UI") -> str:
    """Стиль программы (нужен уже созданный QApplication: стрелки рисуются Qt). Шрифты — app/ui/fonts.py:
    текст — body_font (Radiance из Доты), статус, заголовки блоков и вкладки — head_font (Reaver из Доты)."""
    folder = paths.app_data_dir() / "ui"
    folder.mkdir(parents=True, exist_ok=True)
    heads = (f'QLabel#status {{ font-family: "{head_font}"; font-size: 20px; letter-spacing: 1px; '
             f'text-transform: uppercase; }}\n'
             f'QGroupBox::title {{ font-family: "{head_font}"; font-size: 15px; }}\n'
             f'QTabBar::tab {{ font-family: "{head_font}"; font-size: 14px; }}\n')
    return (_QSS.replace("ARROW_UP", _arrow(folder / "arrow_up.png", True))
            .replace("ARROW_DOWN", _arrow(folder / "arrow_down.png", False))
            .replace('font-family: "Segoe UI"', f'font-family: "{body_font}"') + heads)


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
