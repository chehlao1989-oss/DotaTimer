"""Тёмная тема в стиле Доты и иконка программы (рисуется кодом, без файлов)."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap

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


def app_icon() -> QIcon:
    """Иконка: тёмный круг с золотым ободком и буквой D."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#7a2418"))
    painter.setPen(QPen(QColor("#e3b341"), 5))
    painter.drawEllipse(4, 4, size - 8, size - 8)
    painter.setPen(QColor("#f2f2f2"))
    font = QFont("Segoe UI", 26)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "D")
    painter.end()
    return QIcon(pixmap)
