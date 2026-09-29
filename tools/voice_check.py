"""Проверка голоса: по очереди проигрывает все фразы из assets/voice/.

Запуск: python tools/voice_check.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from app.i18n import ru  # noqa: E402
from app.notify.voice import VoicePlayer  # noqa: E402

PAUSE_MS = 1800


def main() -> None:
    app = QCoreApplication(sys.argv)
    player = VoicePlayer()
    keys = list(ru.VOICE_PHRASES)
    player.preload(keys)

    def play_next():
        if not keys:
            app.quit()
            return
        key = keys.pop(0)
        print(f"{key}: «{ru.VOICE_PHRASES[key]}»", flush=True)
        player.play(key)
        QTimer.singleShot(PAUSE_MS, play_next)

    QTimer.singleShot(500, play_next)
    app.exec()


if __name__ == "__main__":
    main()
