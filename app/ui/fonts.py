"""Шрифты окна (вариант 2 автора 15.10): шрифты Доты из папки игры игрока, иначе свободные похожие.

Radiance (текст) и Reaver (заголовки, вкладки) берутся прямо из установленной у игрока Доты
(game/dota/panorama/fonts) — программа их не копирует и не распространяет. Русские буквы в них есть (замер 15.10
по кодам символов). Если Дота не найдена — свободные Fira Sans и Forum (лицензия OFL, лежат в assets/fonts вместе
с текстом лицензии).
"""
import logging
from pathlib import Path

from PySide6.QtGui import QFontDatabase

log = logging.getLogger(__name__)

DOTA_FONTS_SUBDIR = Path("game") / "dota" / "panorama" / "fonts"
DOTA_FILES = {"body": ("radiance-regular.otf", "radiance-semibold.otf", "radiance-bold.otf"),
              "head": ("reaver-regular.otf", "reaver-semibold.otf", "reaver-bold.otf")}
BUNDLED_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
BUNDLED_FILES = {"body": ("FiraSans-Regular.ttf", "FiraSans-SemiBold.ttf", "FiraSans-Bold.ttf"),
                 "head": ("Forum-Regular.ttf",)}
SYSTEM_FALLBACK = "Segoe UI"


def _load(folder: Path, files: tuple) -> str | None:
    """Подключить файлы шрифта; вернуть имя семейства или None, если ни один не загрузился."""
    family = None
    for name in files:
        path = folder / name
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        family = family or (families[0] if families else None)
    return family


def load_fonts(dota_dir: Path | None) -> tuple[str, str]:
    """(семейство для текста, семейство для заголовков). Нужен уже созданный QApplication."""
    result = {}
    for role in ("body", "head"):
        family = _load(Path(dota_dir) / DOTA_FONTS_SUBDIR, DOTA_FILES[role]) if dota_dir else None
        source = "Дота"
        if family is None:
            family, source = _load(BUNDLED_DIR, BUNDLED_FILES[role]), "свои"
        result[role] = family or SYSTEM_FALLBACK
        log.info("Шрифт (%s): %s — %s", role, result[role], source if family else "системный")
    return result["body"], result["head"]
