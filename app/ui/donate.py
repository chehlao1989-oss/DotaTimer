"""Донат (ТЗ п. 9): ссылка на Boosty автора из data/donate.json.

Кнопка «Поддержать автора» — в шапке окна, во вкладке «О программе» и в меню трея. Во время игры поверх Доты
ничего не показывается и не напоминается. Пустой url — кнопки скрыты.
"""
import json
import logging
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

log = logging.getLogger(__name__)

DONATE_PATH = Path(__file__).resolve().parents[2] / "data" / "donate.json"


def donate_url(path: Path = DONATE_PATH) -> str | None:
    """Ссылка для доната; None — файла нет, он битый или ссылка пустая (только https)."""
    try:
        url = json.loads(path.read_text(encoding="utf-8")).get("url", "").strip()
    except (OSError, ValueError, AttributeError):
        return None
    return url if url.startswith("https://") else None


def open_donate(url: str) -> None:
    log.info("Открываю страницу доната")
    QDesktopServices.openUrl(QUrl(url))
