"""Общие настройки тестов."""
import os

import pytest

# окна Qt в тестах не показываются на экране
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")


@pytest.fixture(scope="session")
def qapp():
    """Приложение Qt: нужно для отрисовки имён героев."""
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
