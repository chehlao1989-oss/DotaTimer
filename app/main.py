"""Точка входа: python -m app.main

Флаги:
  --minimized  запуститься свёрнутым в трей (для автозапуска с Windows)
"""
import argparse
import logging
import sys
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from app import paths
from app.config import load_settings, save_settings
from app.core import TimerApp
from app.gsi import installer
from app.gsi.server import GsiServer
from app.hotkeys import HotkeyManager
from app.i18n import ru
from app.notify import voice_gen
from app.notify.voice import VoicePlayer
from app.ui.main_window import MainWindow
from app.ui.overlay import Overlay
from app.ui.startup_check import run_startup_checks
from app.ui.theme import DARK_QSS, app_icon

log = logging.getLogger("dotatimer")


class GsiBridge(QObject):
    """Передаёт пакеты из потока GSI-сервера в поток интерфейса."""

    packet = Signal(object)
    voice_ready = Signal()


def generate_voice_in_background(bridge: GsiBridge) -> None:
    """Первый запуск: сгенерировать недостающие голосовые фразы (нужен интернет)."""
    if not voice_gen.missing_phrases():
        return

    def work():
        if voice_gen.generate_missing():
            bridge.voice_ready.emit()

    threading.Thread(target=work, name="voice-gen", daemon=True).start()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(paths.logs_dir() / "dotatimer.log", encoding="utf-8")],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Dota таймер")
    parser.add_argument("--minimized", action="store_true", help="запуститься свёрнутым в трей")
    args = parser.parse_args()

    setup_logging()
    log.info("Запуск")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # закрытое окно прячется в трей
    app.setStyleSheet(DARK_QSS)
    app.setWindowIcon(app_icon())
    settings = load_settings()

    bridge = GsiBridge()
    try:
        server = GsiServer(bridge.packet.emit)
    except OSError:
        QMessageBox.warning(None, ru.APP_PORT_BUSY_TITLE, ru.APP_PORT_BUSY.format(port=installer.GSI_PORT))
        return

    overlay = Overlay(settings.overlay)
    overlay.layout_finished.connect(lambda: save_settings(settings))
    voice = VoicePlayer(settings.volume)
    core = TimerApp(settings, overlay, voice)
    hotkeys = HotkeyManager()
    hotkeys.triggered.connect(core.on_hotkey)
    hotkeys.start(settings.hotkeys)
    window = MainWindow(settings, core, hotkeys)

    run_startup_checks(window, settings)
    bridge.packet.connect(core.on_packet)
    server.start()
    voice.preload(ru.VOICE_PHRASES)
    bridge.voice_ready.connect(lambda: (voice.reload(), voice.preload(ru.VOICE_PHRASES)))
    generate_voice_in_background(bridge)
    overlay.show()
    if not args.minimized:
        window.show()

    code = app.exec()
    server.stop()
    hotkeys.stop()
    save_settings(settings)
    log.info("Выход")
    sys.exit(code)


if __name__ == "__main__":
    main()
