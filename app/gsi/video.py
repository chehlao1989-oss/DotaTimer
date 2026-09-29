"""Режим экрана Доты из video.txt (только чтение файла настроек, не игры).

Файл: Steam\\userdata\\<id аккаунта>\\570\\local\\cfg\\video.txt
Ключи: setting.fullscreen (1 = полный экран), setting.nowindowborder (1 = без рамки).
Поверх эксклюзивного полноэкранного режима оверлей не виден.
"""
import logging
import re
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)

DOTA_APP_ID = "570"


class ScreenMode(Enum):
    FULLSCREEN = "fullscreen"
    BORDERLESS = "borderless"
    WINDOWED = "windowed"
    UNKNOWN = "unknown"


def find_video_file(steam_path: Path) -> Path | None:
    """Самый свежий video.txt среди аккаунтов Steam на компьютере."""
    candidates = list((steam_path / "userdata").glob(f"*/{DOTA_APP_ID}/local/cfg/video.txt"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def parse_video(text: str) -> dict[str, str]:
    return dict(re.findall(r'"(setting\.[^"]+)"\s+"([^"]*)"', text))


def screen_mode(settings: dict[str, str]) -> ScreenMode:
    fullscreen = settings.get("setting.fullscreen")
    if fullscreen is None:
        return ScreenMode.UNKNOWN
    if fullscreen == "1":
        return ScreenMode.FULLSCREEN
    if settings.get("setting.nowindowborder") == "1":
        return ScreenMode.BORDERLESS
    return ScreenMode.WINDOWED


def read_screen_mode(steam_path: Path | None) -> ScreenMode:
    if steam_path is None:
        return ScreenMode.UNKNOWN
    path = find_video_file(steam_path)
    if path is None:
        log.info("video.txt не найден")
        return ScreenMode.UNKNOWN
    try:
        values = parse_video(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        log.exception("Не удалось прочитать %s", path)
        return ScreenMode.UNKNOWN
    mode = screen_mode(values)
    log.info("Режим экрана Доты: %s (%s)", mode.value, path)
    return mode
