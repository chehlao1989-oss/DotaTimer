"""Точка входа: python -m app.main

Флаги:
  --minimized  запуститься свёрнутым в трей (для автозапуска с Windows)
  --selftest F самопроверка собранной программы (библиотеки, данные, окна, приём GSI), итог — в файл F
"""
import argparse
import logging
import sys
import threading
from pathlib import Path

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
from app.threats.service import ThreatsService
from app.timers.timings import MODE_TURBO
from app.ui.main_window import MainWindow
from app.ui.overlay import Overlay
from app.ui.threat_strip import ThreatStrip
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


def selftest(report: Path) -> int:
    """Самопроверка собранной программы (фаза 3, сборка на GitHub): библиотеки, данные, окна, приём GSI.

    Работает с временной папкой настроек (не трогает настройки пользователя), без диалогов и голоса.
    Итог — построчно в файл report; код выхода 0 — всё в порядке, 1 — есть ошибки.
    """
    import json
    import os
    import tempfile
    import urllib.request
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="dotatimer-selftest-")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines, failed = [], False

    def step(name: str, fn):
        nonlocal failed
        try:
            result = fn()
            lines.append(f"ок   {name}{f': {result}' if isinstance(result, str) else ''}")
            return result
        except Exception as error:  # самопроверка должна дойти до конца и перечислить все ошибки
            failed = True
            lines.append(f"ОШИБКА {name}: {type(error).__name__}: {error}")
            return None

    for module in ("cv2", "numpy", "mss", "pynput.keyboard", "PySide6.QtMultimedia", "edge_tts"):
        step(f"библиотека {module}", lambda m=module: __import__(m) and None)
    app = step("окно Qt", lambda: QApplication(sys.argv))
    settings = step("настройки", load_settings)
    packets = []
    server = step("сервер GSI", lambda: GsiServer(packets.append, port=0))
    if app and settings and server:
        overlay = step("оверлей", lambda: Overlay(settings.overlay))
        voice = step("голос", lambda: VoicePlayer(settings.volume))
        core = step("таймеры", lambda: TimerApp(settings, overlay, voice))
        hotkeys = step("горячие клавиши", HotkeyManager)
        threats = step("угрозы", lambda: ThreatsService(settings.threats, core.say,
                                                        lambda: settings.mode == MODE_TURBO))
        step("главное окно", lambda: MainWindow(settings, core, hotkeys, threats) and None)
        server.start()

        def send_packet():
            body = json.dumps({"auth": {"token": installer.GSI_TOKEN}, "provider": {"name": "Dota 2"}}).encode()
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{server.port}/", data=body,
                                                          headers={"Content-Type": "application/json"}), timeout=5)
            if not packets:
                raise RuntimeError("пакет не дошёл")
            return f"порт {server.port}"
        step("приём пакета GSI", send_packet)
        server.stop()
        if threats:
            threats.stop()
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Dota таймер")
    parser.add_argument("--minimized", action="store_true", help="запуститься свёрнутым в трей")
    parser.add_argument("--selftest", metavar="ФАЙЛ", help="самопроверка собранной программы, итог — в ФАЙЛ")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(selftest(Path(args.selftest)))

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
    threats = ThreatsService(settings.threats, core.say, lambda: settings.mode == MODE_TURBO)
    strip = ThreatStrip(settings.strip, threats.cache.root / "images")
    overlay.images_dir = threats.cache.root / "images"  # портрет врага и иконка предмета в подсказке
    strip.layout_finished.connect(lambda: save_settings(settings))
    threats.strip = strip
    threats.card_duration = settings.strip.full_card_sec
    if threats.controller is not None:
        threats.controller.card_duration = settings.strip.full_card_sec
    core.state_listeners.append(threats.on_state)
    core.extra_hotkeys = {"show_threats": threats.toggle_strip, "seen_item": threats.seen_item,
                          "pick_heroes": threats.pick_heroes}
    window = MainWindow(settings, core, hotkeys, threats)

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
    threats.stop()
    save_settings(settings)
    log.info("Выход")
    sys.exit(code)


if __name__ == "__main__":
    main()
