"""Настройки пользователя: %APPDATA%\\DotaTimer\\settings.json.

Если файла нет или он испорчен, берутся значения по умолчанию (программа не падает).
"""
import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from app import paths
from app.hotkeys import DEFAULT_BINDINGS
from app.notify.manager import NotifySettings
from app.threats.controller import ThreatSettings
from app.timers.timings import MODE_NORMAL

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"


@dataclass
class OverlaySettings:
    x: int | None = None  # None = по умолчанию справа по центру экрана
    y: int | None = None
    scale: float = 1.0
    opacity: float = 0.9
    display_sec: float = 6.0


@dataclass
class StripSettings:
    """Полоска угроз: портреты угроз и иконки контр-предметов."""

    x: int | None = None  # None = по умолчанию над миникартой справа
    y: int | None = None
    scale: float = 1.0
    opacity: float = 0.95
    visible: bool = True
    full_card_sec: float = 20.0  # полная карточка в начале катки


@dataclass
class Settings:
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    strip: StripSettings = field(default_factory=StripSettings)
    notify: NotifySettings = field(default_factory=NotifySettings)
    threats: ThreatSettings = field(default_factory=ThreatSettings)
    volume: float = 0.8
    mode: str = MODE_NORMAL  # "normal" или "turbo", GSI режим не сообщает
    hotkeys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_BINDINGS))
    dota_dir: str | None = None  # папка Доты, если её выбрали вручную
    hide_fullscreen_warning: bool = False  # «Больше не показывать»
    screen_reminder_shown: bool = False  # напоминание, когда video.txt не найден, показывается один раз


def _from_dict(cls, raw: dict):
    """Собирает dataclass из словаря, пропуская незнакомые ключи."""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (raw or {}).items() if k in known})


def settings_path() -> Path:
    return paths.app_data_dir() / SETTINGS_FILE


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        settings = _from_dict(Settings, {k: v for k, v in raw.items() if k not in ("overlay", "notify", "threats", "strip")})
        settings.overlay = _from_dict(OverlaySettings, raw.get("overlay"))
        settings.notify = _from_dict(NotifySettings, raw.get("notify"))
        settings.threats = _from_dict(ThreatSettings, raw.get("threats"))
        settings.strip = _from_dict(StripSettings, raw.get("strip"))
        # новые действия получают клавиши по умолчанию
        saved = {k: v for k, v in (raw.get("hotkeys") or {}).items() if k in DEFAULT_BINDINGS}
        settings.hotkeys = {**DEFAULT_BINDINGS, **saved}  # убранные действия (например, Рошан) отбрасываются
        return settings
    except FileNotFoundError:
        return Settings()
    except (ValueError, TypeError, AttributeError):
        log.exception("Файл настроек испорчен, беру значения по умолчанию")
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
