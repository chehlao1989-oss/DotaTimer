"""Проигрывание голосовых фраз (готовые файлы, без синтеза во время игры).

Откуда берётся фраза <ключ>:
1. файл пользователя %APPDATA%\\DotaTimer\\voice\\<ключ>.wav или .mp3 — можно подменить любую фразу;
2. сгенерированная при первом запуске %APPDATA%\\DotaTimer\\voice_cache\\<ключ>.mp3 (voice_gen.py).
Одновременно звучит одна фраза, следующая ждёт окончания текущей.
Работает только в потоке интерфейса Qt.
"""
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from app import paths
from app.notify import voice_gen

log = logging.getLogger(__name__)

USER_EXTS = (".wav", ".mp3")


def user_voice_dir() -> Path:
    path = paths.app_data_dir() / "voice"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_voice_file(key: str, user_dir: Path | None = None, generated_dir: Path | None = None) -> Path | None:
    """Файл фразы: сначала пользовательский, потом сгенерированный."""
    user_dir = user_dir if user_dir is not None else user_voice_dir()
    for ext in USER_EXTS:
        candidate = user_dir / f"{key}{ext}"
        if candidate.is_file():
            return candidate
    generated = (generated_dir or voice_gen.cache_dir()) / f"{key}{voice_gen.EXT}"
    return generated if generated.is_file() else None


class VoicePlayer(QObject):
    def __init__(self, volume: float = 0.8, parent: QObject | None = None):
        super().__init__(parent)
        self._players: dict[str, QMediaPlayer] = {}
        self._outputs: dict[str, QAudioOutput] = {}
        self._queue: list[str] = []
        self._current: QMediaPlayer | None = None
        self._volume = volume

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
            player = QMediaPlayer(self)
            player.setAudioOutput(output)
            player.setSource(QUrl.fromLocalFile(str(path)))
            player.mediaStatusChanged.connect(self._on_status)
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
        return self._current is not None and self._current.playbackState() == QMediaPlayer.PlayingState

    def play(self, key: str) -> None:
        if self.is_playing():
            self._queue.append(key)
            return
        player = self._player(key)
        if player is not None:
            self._current = player
            player.setPosition(0)
            player.play()

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia and self._queue:
            self.play(self._queue.pop(0))
