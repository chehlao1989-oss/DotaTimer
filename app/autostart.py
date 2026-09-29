"""Автозапуск вместе с Windows через реестр текущего пользователя (HKCU\\...\\Run).

Включается только галочкой пользователя. Пока программа не собрана в .exe (фаза 3),
запускается pythonw.exe из .venv с файлом run_dotatimer.pyw из папки проекта.
"""
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DotaTimer"
ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "run_dotatimer.pyw"


def build_command() -> str:
    """Команда запуска: свёрнуто в трей."""
    if getattr(sys, "frozen", False):  # собранный .exe (фаза 3)
        return f'"{sys.executable}" --minimized'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{LAUNCHER}" --minimized'


def is_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, build_command())
            log.info("Автозапуск включён: %s", build_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
            log.info("Автозапуск выключен")
