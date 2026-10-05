"""Значок для DotaTimer.exe: тот же, что в окне программы (app/ui/theme.py: app_icon), в файл .ico.

Запуск: python tools/make_icon.py packaging/dotatimer.ico
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.theme import app_icon  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "packaging/dotatimer.ico")
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841 — без него Qt не рисует
    if not app_icon().pixmap(256, 256).save(str(out), "ICO"):
        sys.exit(f"не удалось сохранить значок {out}")
    print(f"значок: {out}, {out.stat().st_size} байт")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # вывод в файл на Windows иначе в cp1251 (BUGLOG №39)
    main()
