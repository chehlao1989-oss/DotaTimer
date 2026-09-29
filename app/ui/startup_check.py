"""Проверки при запуске (п. 4 ТЗ): режим экрана и установка GSI-конфига."""
import logging
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from app.config import Settings, save_settings
from app.gsi import installer
from app.gsi.video import ScreenMode, read_screen_mode
from app.i18n import ru

log = logging.getLogger(__name__)


def check_screen_mode(parent: QWidget | None, settings: Settings) -> None:
    if settings.hide_fullscreen_warning:
        return
    mode = read_screen_mode(installer.steam_path_from_registry())
    if mode == ScreenMode.FULLSCREEN:
        box = QMessageBox(QMessageBox.Warning, ru.APP_TITLE, ru.CHECK_FULLSCREEN, parent=parent)
        box.addButton(ru.BUTTON_OK, QMessageBox.AcceptRole)
        dont_show = box.addButton(ru.BUTTON_DONT_SHOW, QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is dont_show:
            settings.hide_fullscreen_warning = True
            save_settings(settings)
    elif mode == ScreenMode.UNKNOWN and not settings.screen_reminder_shown:
        QMessageBox.information(parent, ru.APP_TITLE, ru.CHECK_SCREEN_REMINDER)
        settings.screen_reminder_shown = True
        save_settings(settings)


def _find_dota(parent: QWidget | None, settings: Settings) -> Path | None:
    if settings.dota_dir and installer.normalize_dota_dir(Path(settings.dota_dir)):
        return Path(settings.dota_dir)
    found = installer.find_dota_dir()
    if found:
        return found
    QMessageBox.information(parent, ru.APP_TITLE, ru.CHECK_DOTA_NOT_FOUND)
    while True:
        chosen = QFileDialog.getExistingDirectory(parent, ru.CHECK_CHOOSE_DOTA)
        if not chosen:
            return None  # пользователь отказался; таймеры просто будут ждать данных
        dota_dir = installer.normalize_dota_dir(Path(chosen))
        if dota_dir:
            settings.dota_dir = str(dota_dir)
            save_settings(settings)
            return dota_dir
        QMessageBox.warning(parent, ru.APP_TITLE, ru.CHECK_WRONG_FOLDER)


def check_gsi_config(parent: QWidget | None, settings: Settings) -> None:
    dota_dir = _find_dota(parent, settings)
    if dota_dir is None:
        log.warning("Папка Доты не указана, GSI-конфиг не установлен")
        return
    try:
        _, changed = installer.install_cfg(dota_dir)
    except OSError as error:
        log.exception("Не удалось записать GSI-конфиг")
        QMessageBox.warning(parent, ru.APP_TITLE, ru.CHECK_CFG_WRITE_ERROR.format(error=error))
        return
    if changed and installer.is_dota_running():
        QMessageBox.information(parent, ru.APP_TITLE, ru.CHECK_RESTART_DOTA)


def run_startup_checks(parent: QWidget | None, settings: Settings) -> None:
    check_gsi_config(parent, settings)
    check_screen_mode(parent, settings)
