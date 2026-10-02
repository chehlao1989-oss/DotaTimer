"""Сгенерировать голосовые фразы вручную (то же, что программа делает при первом запуске).

Запуск: python tools/gen_voice.py [--force]
--force — удалить уже сгенерированные фразы и сделать заново (например, после правки текста).
Фразы попадают в %APPDATA%\\DotaTimer\\voice_cache\\, в репозиторий не кладутся.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.notify import voice_gen  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Генерация голосовых фраз")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.force:
        for path in voice_gen.cache_dir().glob(f"*{voice_gen.EXT}"):
            path.unlink()
    done = voice_gen.generate_missing()
    left = voice_gen.missing_phrases()
    print(f"Сгенерировано: {done}, не хватает: {len(left)} {left if left else ''}")
    print(f"Папка: {voice_gen.cache_dir()}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # вывод в файл на Windows иначе в cp1251 (BUGLOG №39)
    main()
