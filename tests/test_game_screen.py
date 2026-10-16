"""Игра на втором мониторе: окна поверх игры и «зрение» — на мониторе Доты (BUGLOG №51)."""
from PySide6.QtCore import QRect

from app.config import OverlaySettings, StripSettings
from app.threats.recommend import RecItem, ThreatRecommendation

SECOND = QRect(2560, 0, 1920, 1080)  # мониторы автора 15.10: основной 2560×1080, второй 1920×1080 справа


class FakeScreen:
    def __init__(self, rect: QRect):
        self.rect = rect

    def geometry(self):
        return self.rect

    def availableGeometry(self):
        return self.rect


def on_second_monitor(monkeypatch):
    import app.ui.overlay as overlay_mod
    monkeypatch.setattr(overlay_mod, "current_screen", lambda: FakeScreen(SECOND))


def test_strip_on_game_monitor(qapp, tmp_path, monkeypatch):
    """Дота на втором мониторе — полоска справа вверху второго монитора, а не основного."""
    from app.ui.threat_strip import ThreatStrip
    on_second_monitor(monkeypatch)
    strip = ThreatStrip(StripSettings(), tmp_path)
    strip.update_recommendations([ThreatRecommendation("axe", (RecItem("blade_mail", 1, "hero", 6.0, 900),))],
                                 {"axe": "Axe"})
    strip.show()
    qapp.processEvents()
    assert SECOND.contains(strip.geometry())
    assert strip.geometry().right() > SECOND.right() - 100  # справа
    strip.close()


def test_saved_position_is_relative_to_game_monitor(qapp, tmp_path, monkeypatch):
    """Сохранённое место — от угла монитора игры: место автора (x = 1500 на основном) переезжает на второй."""
    from app.ui.overlay import Overlay
    on_second_monitor(monkeypatch)
    overlay = Overlay(OverlaySettings(x=1500, y=540, layout_version=99), tmp_path)
    assert SECOND.contains(overlay.geometry())
    assert overlay.x() == min(2560 + 1500, SECOND.right() + 1 - overlay.width())
    overlay.move(3000, 600)
    overlay._save_position()
    assert (overlay.settings.x, overlay.settings.y) == (440, 600)
    overlay.close()


def test_tracker_follows_dota_window(qapp):
    """Монитор сменился — сигнал один раз; окно Доты пропало — монитор остаётся прошлым."""
    from app.ui.game_screen import GameScreenTracker
    found = [(2560, 0, 1920, 1080)]
    tracker = GameScreenTracker(finder=lambda: found[0])
    calls = []
    tracker.changed.connect(lambda: calls.append(tracker.rect))
    tracker.check()
    tracker.check()
    found[0] = None
    tracker.check()
    assert calls == [(2560, 0, 1920, 1080)] and tracker.rect == (2560, 0, 1920, 1080)


def test_unknown_monitor_falls_back_to_primary(qapp):
    from PySide6.QtGui import QGuiApplication
    from app.ui.game_screen import screen_for
    assert screen_for(None) is QGuiApplication.primaryScreen()
    assert screen_for((-9999, -9999, 10, 10)) is QGuiApplication.primaryScreen()


def test_vision_picks_game_monitor():
    """Зрение снимает монитор игры, а не всегда основной (раньше — monitors[1])."""
    from app.vision.service import pick_monitor
    monitors = [{"left": 0, "top": 0, "width": 4480, "height": 1080},
                {"left": 0, "top": 0, "width": 2560, "height": 1080},
                {"left": 2560, "top": 0, "width": 1920, "height": 1080}]
    assert pick_monitor(monitors, (2560, 0, 1920, 1080)) is monitors[2]
    assert pick_monitor(monitors, None) is monitors[1]
    assert pick_monitor(monitors, (100, 100, 800, 600)) is monitors[1]


def test_moved_window_returns_to_game_monitor(qapp, tmp_path, monkeypatch):
    """Окно сообщений сдвинуто на основной монитор (как 16.10: 1480, 655) — проверка возвращает его на монитор
    игры (BUGLOG №53)."""
    from app.ui.overlay import Overlay
    on_second_monitor(monkeypatch)
    overlay = Overlay(OverlaySettings(), tmp_path)
    overlay.show()
    qapp.processEvents()
    overlay.move(1480, 655)
    overlay.keep_on_game_screen()
    assert SECOND.contains(overlay.geometry())
    overlay.close()


def test_tracker_checks_every_time(qapp):
    """Сигнал проверки — на каждом тике, даже если монитор не менялся (по нему окна сверяют своё место)."""
    from app.ui.game_screen import GameScreenTracker
    tracker = GameScreenTracker(finder=lambda: (2560, 0, 1920, 1080))
    ticks = []
    tracker.checked.connect(lambda: ticks.append(1))
    tracker.check()
    tracker.check()
    assert len(ticks) == 2
