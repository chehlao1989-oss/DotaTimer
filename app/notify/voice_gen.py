"""Голосовые фразы: готовые в сборке (assets/voice) и генерация недостающих (edge-tts, нужен интернет).

DECISIONS №29 (решение автора 15.10): фразы голосом Microsoft лежат в программе — assets/voice/*.mp3 и опись
phrases.json (какая фраза каким текстом записана). Раньше каждый игрок генерировал их у себя при первом запуске,
и когда Microsoft поменял правила, у новых игроков голоса не было вовсе (BUGLOG №50).
Генерация осталась запасной: если текст фразы в ru.py поменяли, а файл в сборке старый, фраза делается заново
в %APPDATA%\\DotaTimer\\voice_cache\\. Пересобрать фразы сборки: python tools/gen_voice.py
"""
import asyncio
import json
import logging
from pathlib import Path

from app import paths
from app.i18n import ru

log = logging.getLogger(__name__)

VOICE = "ru-RU-DmitryNeural"  # выбран автором 28.09.2026
RATE = "+10%"
ATTEMPTS = 4
EXT = ".mp3"
BUNDLED_DIR = Path(__file__).resolve().parents[2] / "assets" / "voice"
MANIFEST = "phrases.json"


def cache_dir() -> Path:
    folder = paths.app_data_dir() / "voice_cache"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def generated_path(key: str) -> Path:
    return cache_dir() / f"{key}{EXT}"


def bundled_phrases(folder: Path | None = None) -> dict[str, str]:
    """Опись фраз сборки: ключ → текст, которым записан файл. Нет описи — пусто."""
    try:
        return json.loads(((folder or BUNDLED_DIR) / MANIFEST).read_text(encoding="utf-8"))["phrases"]
    except (OSError, ValueError, KeyError):
        return {}


def bundled_file(key: str, text: str, folder: Path | None = None) -> Path | None:
    """Файл фразы из сборки, если он записан с тем же текстом, что сейчас в ru.py."""
    folder = folder or BUNDLED_DIR
    path = folder / f"{key}{EXT}"
    return path if path.is_file() and bundled_phrases(folder).get(key) == text else None


def missing_phrases(phrases: dict[str, str] = ru.VOICE_PHRASES, bundled_dir: Path | None = None) -> list[str]:
    """Фразы, которых нет ни в сборке (с тем же текстом), ни среди сгенерированных."""
    return [key for key, text in phrases.items()
            if bundled_file(key, text, bundled_dir) is None and not generated_path(key).is_file()]


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


def generate_missing(phrases: dict[str, str] = ru.VOICE_PHRASES, communicate_cls=None,
                     bundled_dir: Path | None = None) -> int:
    """Сгенерировать недостающие фразы. Вызывать в фоновом потоке. Возвращает, сколько сделано."""
    if communicate_cls is None:
        import edge_tts
        communicate_cls = edge_tts.Communicate
    done = 0

    async def run():
        nonlocal done
        for key in missing_phrases(phrases, bundled_dir):
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


def build_bundle(phrases: dict[str, str] = ru.VOICE_PHRASES, folder: Path | None = None, communicate_cls=None) -> int:
    """Записать все фразы заново в папку сборки и опись к ним (tools/gen_voice.py). Возвращает, сколько записано."""
    if communicate_cls is None:
        import edge_tts
        communicate_cls = edge_tts.Communicate
    folder = folder or BUNDLED_DIR
    folder.mkdir(parents=True, exist_ok=True)

    async def run():
        for key, text in phrases.items():
            await _synth(text, folder / f"{key}{EXT}", communicate_cls)

    asyncio.run(run())
    manifest = {"voice": VOICE, "rate": RATE, "phrases": dict(phrases)}
    (folder / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(phrases)
