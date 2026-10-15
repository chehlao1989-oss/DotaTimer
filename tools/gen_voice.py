"""Записать голосовые фразы программы заново в assets/voice/ (DECISIONS №29): нужен после правки текстов в ru.py.

Запуск: python tools/gen_voice.py
Фразы и опись phrases.json кладутся в репозиторий и попадают в сборку. Тест test_voice.py::test_bundle_matches_texts
падает, если текст фразы в ru.py поменяли, а файл не перезаписали.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.notify import voice_gen  # noqa: E402


def main() -> None:
    done = voice_gen.build_bundle()
    print(f"Записано фраз: {done}")
    print(f"Папка: {voice_gen.BUNDLED_DIR}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # вывод в файл на Windows иначе в cp1251 (BUGLOG №39)
    main()
