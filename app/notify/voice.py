"""Проигрывание голосовых фраз (готовые файлы, без синтеза во время игры).

Откуда берётся фраза <ключ>:
1. файл пользователя %APPDATA%\\DotaTimer\\voice\\<ключ>.wav или .mp3 — можно подменить любую фразу;
2. из программы: assets/voice/<ключ>.mp3, если записан с нынешним текстом (DECISIONS №29);
3. сгенерированная %APPDATA%\\DotaTimer\\voice_cache\\<ключ>.mp3 — если текст поменяли, а файл в программе старый.
Одновременно звучит одна фраза, следующая ждёт окончания текущей.
Работает только в потоке интерфейса Qt.
"""
import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

from app import paths
from app.i18n import ru
from app.notify import voice_gen

log = logging.getLogger(__name__)

USER_EXTS = (".wav", ".mp3")
# фразы ~2,3 с (замер 15.10, power_rune_30.mp3); дольше — фраза зависла, очередь не должна ждать её вечно (BUGLOG №52)
PHRASE_MAX_SEC = 10


def default_output():
    """Звуковое устройство Windows по умолчанию — то, куда сейчас играет Дота."""
    return QMediaDevices.defaultAudioOutput()


def user_voice_dir() -> Path:
    path = paths.app_data_dir() / "voice"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_voice_file(key: str, user_dir: Path | None = None, generated_dir: Path | None = None,
                    bundled_dir: Path | None = None) -> Path | None:
    """Файл фразы: сначала пользовательский, потом из программы, потом сгенерированный."""
    user_dir = user_dir if user_dir is not None else user_voice_dir()
    for ext in USER_EXTS:
        candidate = user_dir / f"{key}{ext}"
        if candidate.is_file():
            return candidate
    bundled = voice_gen.bundled_file(key, ru.VOICE_PHRASES.get(key, ""), bundled_dir)
    if bundled is not None:
        return bundled
    generated = (generated_dir or voice_gen.cache_dir()) / f"{key}{voice_gen.EXT}"
    return generated if generated.is_file() else None


class VoicePlayer(QObject):
    def __init__(self, volume: float = 0.8, parent: QObject | None = None):
        super().__init__(parent)
        self._players: dict[str, QMediaPlayer] = {}
        self._outputs: dict[str, QAudioOutput] = {}
        self._queue: list[str] = []
        self._current: QMediaPlayer | None = None
        self._started_at = 0.0
        self._volume = volume
        self._device = None  # устройство, на которое настроены все фразы

    def set_volume(self, volume: float) -> None:
        """Громкость 0.0–1.0, отдельно от системной."""
        self._volume = max(0.0, min(1.0, volume))
        for output in self._outputs.values():
            output.setVolume(self._volume)

    def _player(self, key: str) -> QMediaPlayer | None:
        if key not in self._players:
            path = find_voice_file(key)
            if path is None:
                log.info("Нет файла голосовой фразы %s (ещё не сгенерирована?)", key)
                return None
            output = QAudioOutput(self)
            output.setVolume(self._volume)
            if self._device is not None:
                output.setDevice(self._device)
            player = QMediaPlayer(self)
            player.setAudioOutput(output)
            player.setSource(QUrl.fromLocalFile(str(path)))
            player.mediaStatusChanged.connect(self._on_status)
            player.errorOccurred.connect(lambda error, text, k=key: log.warning("Голос %s: ошибка %s", k, text))
            self._players[key], self._outputs[key] = player, output
        return self._players[key]

    def preload(self, keys) -> None:
        """Подготовить фразы заранее, чтобы первая не звучала с задержкой."""
        for key in keys:
            self._player(key)

    def reload(self) -> None:
        """Файлы появились или поменялись (например, после генерации) — загрузить заново."""
        self._players.clear()
        self._outputs.clear()

    def is_playing(self) -> bool:
        if self._current is None or self._current.playbackState() != QMediaPlayer.PlayingState:
            return False
        if time.monotonic() - self._started_at > PHRASE_MAX_SEC:
            log.warning("Голос: фраза играет дольше %d с — остановлена", PHRASE_MAX_SEC)
            self._current.stop()
            return False
        return True

    def follow_default_device(self) -> None:
        """Играть туда же, куда Дота: устройство по умолчанию могло смениться после запуска программы
        (колонка, наушники, звук монитора) — фразы уходили в старое устройство (BUGLOG №52)."""
        device = default_output()
        if self._device is not None and device == self._device:
            return
        if self._device is not None:
            log.info("Голос: устройство вывода сменилось на %s", device.description())
        self._device = device
        for output in self._outputs.values():
            output.setDevice(device)

    def play(self, key: str) -> None:
        if self.is_playing():
            self._queue.append(key)
            return
        player = self._player(key)
        if player is not None:
            self.follow_default_device()
            log.info("Голос: %s", key)
            self._current = player
            self._started_at = time.monotonic()
            player.setPosition(0)
            player.play()

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia and self._queue:
            self.play(self._queue.pop(0))
