# -*- mode: python ; coding: utf-8 -*-
# Сборка DotaTimer.exe (фаза 3): папка с программой (onedir), без окна консоли.
# Запуск: pyinstaller packaging/DotaTimer.spec  → dist/DotaTimer/DotaTimer.exe
# Данные (data/*.json) кладутся рядом с модулями: программа ищет их от своих файлов (Path(__file__).parents[2] / "data").
# Настройки, кеш, голос и логи — в %APPDATA%\DotaTimer, как и без сборки.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
ICON = ROOT / "packaging" / "dotatimer.ico"  # рисует tools/make_icon.py

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "data"), "data")],
    # edge_tts импортируется только при генерации голоса, pynput выбирает модуль под Windows на лету — сборщик
    # сам их не находит
    hiddenimports=collect_submodules("edge_tts") + ["pynput.keyboard._win32", "pynput.mouse._win32",
                                                    "pynput._util.win32"],
    # сбор и расчёт статистики идут на GitHub, программе не нужны
    excludes=["duckdb", "pyarrow", "pytest", "tools", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DotaTimer", console=False,
          icon=str(ICON) if ICON.exists() else None)
coll = COLLECT(exe, a.binaries, a.datas, name="DotaTimer")
