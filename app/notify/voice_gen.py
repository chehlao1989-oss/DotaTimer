"""Генерация голосовых фраз у пользователя при первом запуске (edge-tts, нужен интернет).

Готовые звуки не распространяются вместе с программой: у голосов Microsoft Edge нет
явного разрешения на распространение. Поэтому каждый пользователь генерирует фразы
у себя, один раз; дальше они лежат в %APPDATA%\\DotaTimer\\voice_cache\\ и работают офлайн.
Без интернета программа показывает только надписи и пробует сгенерировать позже.
"""
import asyncio
import logging
from pathlib import Path

from app import paths
from app.i18n import ru

log = logging.getLogger(__name__)

VOICE = "ru-RU-DmitryNeural"  # выбран автором 28.09.2026
RATE = "+10%"
ATTEMPTS = 4
EXT = ".mp3"


def cache_dir() -> Path:
    folder = paths.app_data_dir() / "voice_cache"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def generated_path(key: str) -> Path:
    return cache_dir() / f"{key}{EXT}"


def missing_phrases(phrases: dict[str, str] = ru.VOICE_PHRASES) -> list[str]:
    return [key for key in phrases if not generated_path(key).is_file()]


async def _synth(text: str, target: Path, communicate_cls) -> None:
    # без знака препинания в конце сервис иногда не отдаёт звук
    if text[-1] not in ".!?":
        text += "."
    tmp = target.with_suffix(".part")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            await communicate_cls(text, VOICE, rate=RATE).save(str(tmp))
            tmp.replace(target)
            return
        except Exception:
            if attempt == ATTEMPTS:
                raise
            await asyncio.sleep(2 * attempt)


def generate_missing(phrases: dict[str, str] = ru.VOICE_PHRASES, communicate_cls=None) -> int:
    """Сгенерировать недостающие фразы. Вызывать в фоновом потоке. Возвращает, сколько сделано."""
    if communicate_cls is None:
        import edge_tts
        communicate_cls = edge_tts.Communicate
    done = 0

    async def run():
        nonlocal done
        for key in missing_phrases(phrases):
            try:
                await _synth(phrases[key], generated_path(key), communicate_cls)
                done += 1
            except Exception:
                log.warning("Не удалось сгенерировать фразу %s (нет интернета?)", key, exc_info=True)
                return

    asyncio.run(run())
    if done:
        log.info("Сгенерировано голосовых фраз: %d", done)
    return done
