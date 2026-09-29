"""Пути к рабочим папкам программы в %APPDATA%\\DotaTimer.

Настройки, кеш, логи и записи матчей хранятся там, а не рядом с программой.
"""
import os
from pathlib import Path

APP_NAME = "DotaTimer"


def app_data_dir() -> Path:
    """Главная папка программы в %APPDATA%. Создаётся, если её нет."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _subdir(name: str) -> Path:
    path = app_data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def recordings_dir() -> Path:
    """Папка с записями GSI-пакетов матчей."""
    return _subdir("recordings")


def logs_dir() -> Path:
    """Папка с лог-файлами."""
    return _subdir("logs")


def cache_dir() -> Path:
    """Папка с кешем статистики."""
    return _subdir("cache")
