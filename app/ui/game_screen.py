"""Монитор, на котором идёт Дота (BUGLOG №51).

Окна поверх игры, их масштаб и «зрение» раньше всегда брали основной монитор — игра на втором мониторе
оставалась без полоски и подсказок (игра автора 15.10). Теперь раз в несколько секунд спрашиваем у Windows,
на каком мониторе окно «Dota 2»: это положение окна, как его видит панель задач, — в игру и её память не лезем.
Окно не найдено (Дота закрыта или свёрнута) — остаётся прошлый монитор, при запуске — основной.
"""
import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QScreen

log = logging.getLogger(__name__)

# замер 15.10 на компьютере автора: заголовок «Dota 2», класс окна SDL_app (EnumWindows)
DOTA_WINDOW_TITLE = "Dota 2"
DOTA_WINDOW_CLASS = "SDL_app"
CHECK_INTERVAL_MS = 3000

Rect = tuple[int, int, int, int]  # left, top, ширина, высота — в пикселях Windows (как у mss)

_tracker: "GameScreenTracker | None" = None


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def find_game_monitor() -> Rect | None:
    """Монитор с окном Доты; None — окна нет, оно свёрнуто или это не Windows."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR  # без этого 64-битный номер монитора обрезается
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    hwnd = user32.FindWindowW(DOTA_WINDOW_CLASS, DOTA_WINDOW_TITLE)
    if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return None
    monitor = user32.MonitorFromWindow(hwnd, 0)  # 0 — MONITOR_DEFAULTTONULL
    info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    r = info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def screen_for(rect: Rect | None) -> QScreen:
    """Экран Qt для монитора Windows: по левому верхнему углу (у Qt он в пикселях Windows), иначе основной."""
    if rect is not None:
        for screen in QGuiApplication.screens():
            corner = screen.geometry().topLeft()
            if (corner.x(), corner.y()) == rect[:2]:
                return screen
    return QGuiApplication.primaryScreen()


class GameScreenTracker(QObject):
    """Следит, на каком мониторе Дота. changed — монитор сменился (окна переставить)."""

    changed = Signal()

    def __init__(self, finder=find_game_monitor):
        super().__init__()
        self._finder = finder
        self.rect: Rect | None = None  # None — монитор игры не известен, берётся основной
        self._timer = QTimer(self, interval=CHECK_INTERVAL_MS, timeout=self.check)

    def start(self) -> None:
        global _tracker
        _tracker = self
        self.check()
        self._timer.start()

    def check(self) -> None:
        rect = self._finder()
        if rect is None or rect == self.rect:
            return
        self.rect = rect
        log.info("Дота на мониторе %s, %dx%d", f"{rect[0]},{rect[1]}", rect[2], rect[3])
        self.changed.emit()

    def screen(self) -> QScreen:
        return screen_for(self.rect)


def current_screen() -> QScreen:
    """Экран, на котором идёт игра (основной, пока слежение не запущено или Доту не нашли)."""
    return _tracker.screen() if _tracker is not None else QGuiApplication.primaryScreen()


def current_monitor() -> Rect | None:
    """Монитор игры в пикселях Windows — для снимков экрана в потоке зрения; None — основной."""
    return _tracker.rect if _tracker is not None else None
