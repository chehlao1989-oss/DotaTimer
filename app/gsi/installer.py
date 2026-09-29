"""Поиск Dota 2 и установка GSI-конфига.

Dota 2 при запуске читает все файлы gamestate_integration_*.cfg из папки
game/dota/cfg/gamestate_integration/ и отправляет данные на указанный адрес.
"""
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

GSI_HOST = "127.0.0.1"
GSI_PORT = 53000
GSI_TOKEN = "dotatimer"
CFG_FILENAME = "gamestate_integration_dotatimer.cfg"
DOTA_FOLDER_NAME = "dota 2 beta"

# Все блоки данных, которые умеет отдавать GSI. В фазе 0 включаем всё,
# чтобы увидеть, что реально приходит в режиме игрока.
DATA_BLOCKS = (
    "provider",
    "map",
    "player",
    "hero",
    "abilities",
    "items",
    "buildings",
    "draft",
    "wearables",
    "events",
    "roshan",
    "minimap",
    "couriers",
    "neutralitems",
    "league",
)


def gsi_url(host: str = GSI_HOST, port: int = GSI_PORT) -> str:
    return f"http://{host}:{port}/"


def build_cfg(uri: str = None, token: str = GSI_TOKEN) -> str:
    """Собирает текст GSI-конфига в формате Valve (KeyValues)."""
    uri = uri or gsi_url()
    data_lines = "\n".join(f'        "{block}"  "1"' for block in DATA_BLOCKS)
    return (
        '"DotaTimer"\n'
        "{\n"
        f'    "uri"        "{uri}"\n'
        '    "timeout"    "5.0"\n'
        '    "buffer"     "0.1"\n'
        '    "throttle"   "0.5"\n'
        '    "heartbeat"  "30.0"\n'
        '    "data"\n'
        "    {\n"
        f"{data_lines}\n"
        "    }\n"
        '    "auth"\n'
        "    {\n"
        f'        "token"  "{token}"\n'
        "    }\n"
        "}\n"
    )


def steam_path_from_registry() -> Path | None:
    """Папка Steam из реестра Windows (HKCU\\Software\\Valve\\Steam\\SteamPath)."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
    except OSError:
        log.info("Steam не найден в реестре")
        return None
    return Path(value)


def parse_library_folders(text: str) -> list[Path]:
    """Достаёт пути библиотек Steam из содержимого libraryfolders.vdf."""
    paths = re.findall(r'"path"\s+"([^"]+)"', text)
    return [Path(p.replace("\\\\", "\\")) for p in paths]


def find_dota_dir(steam_path: Path | None = None) -> Path | None:
    """Ищет папку Dota 2 во всех библиотеках Steam."""
    steam_path = steam_path or steam_path_from_registry()
    if steam_path is None:
        return None
    libraries = [steam_path]
    vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    try:
        libraries += parse_library_folders(vdf.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        log.info("Не удалось прочитать %s", vdf)
    for lib in libraries:
        candidate = lib / "steamapps" / "common" / DOTA_FOLDER_NAME
        if (candidate / "game" / "dota").is_dir():
            log.info("Dota 2 найдена: %s", candidate)
            return candidate
    log.info("Dota 2 не найдена в библиотеках: %s", libraries)
    return None


def normalize_dota_dir(chosen: Path) -> Path | None:
    """Папка, выбранная вручную: подходит сама «dota 2 beta» или любая папка внутри неё."""
    for candidate in (chosen, *chosen.parents):
        if (candidate / "game" / "dota").is_dir():
            return candidate
    return None


def is_dota_running() -> bool:
    """Запущена ли Дота (по списку процессов Windows, без доступа к самой игре)."""
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq dota2.exe", "/NH"], capture_output=True,
                             text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return "dota2.exe" in out.stdout.lower()


def gsi_cfg_dir(dota_dir: Path) -> Path:
    return dota_dir / "game" / "dota" / "cfg" / "gamestate_integration"


def install_cfg(dota_dir: Path, content: str | None = None) -> tuple[Path, bool]:
    """Кладёт конфиг в папку Доты. Возвращает (путь, был ли файл изменён)."""
    content = content or build_cfg()
    target_dir = gsi_cfg_dir(dota_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / CFG_FILENAME
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return target, False
    target.write_text(content, encoding="utf-8")
    log.info("GSI-конфиг записан: %s", target)
    return target, True
