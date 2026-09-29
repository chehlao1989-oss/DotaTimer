"""Главное окно (вкладки настроек) и иконка в трее."""
import logging
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QKeySequenceEdit,
    QLabel, QMainWindow,
    QMenu, QMessageBox, QPushButton, QRadioButton, QSlider, QSpinBox, QSystemTrayIcon, QTabWidget,
    QVBoxLayout, QWidget,
)

from app import autostart
from app.config import Settings, save_settings
from app.core import TimerApp
from app.hotkeys import DEFAULT_BINDINGS, HotkeyManager, parse_binding
from app.i18n import ru
from app.timers.timings import MODE_NORMAL, MODE_TURBO
from app.ui.theme import app_icon

log = logging.getLogger(__name__)

STATUS_REFRESH_MS = 1000
DEMO_INTERVAL_MS = 2500
WARN_MAX_SEC = 120
SPIN_WIDTH = 90


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, core: TimerApp, hotkeys: HotkeyManager, threats=None):
        super().__init__()
        self.threats = threats
        self.settings = settings
        self.core = core
        self.hotkeys = hotkeys
        self._key_edits: dict[str, QKeySequenceEdit] = {}
        self._event_rows: dict[str, tuple[QCheckBox, QSpinBox | None]] = {}
        self._quitting = False
        self.setWindowTitle(ru.APP_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(660, 640)

        central = QWidget()
        layout = QVBoxLayout(central)
        self.status = QLabel(objectName="status")
        layout.addWidget(self.status)
        self.status_hint = QLabel(ru.STATUS_WAITING_HINT, objectName="hint", wordWrap=True)
        layout.addWidget(self.status_hint)
        tabs = QTabWidget()
        tabs.addTab(self._timers_tab(), ru.TAB_TIMERS)
        tabs.addTab(self._threats_tab(), ru.TAB_THREATS)
        tabs.addTab(self._voice_screen_tab(), ru.TAB_VOICE_SCREEN)
        tabs.addTab(self._hotkeys_tab(), ru.TAB_HOTKEYS)
        tabs.addTab(self._general_tab(), ru.TAB_GENERAL)
        tabs.addTab(self._about_tab(), ru.TAB_ABOUT)
        layout.addWidget(tabs)
        self.setCentralWidget(central)

        self._build_tray()
        self._status_timer = QTimer(self, interval=STATUS_REFRESH_MS, timeout=self.refresh_status)
        self._status_timer.start()
        self.refresh_status()

    # --- вкладка «Таймеры» ---
    def _timers_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(ru.LABEL_MODE))
        self.mode_group = QButtonGroup(tab)
        for mode, title in ((MODE_NORMAL, ru.MODE_NORMAL), (MODE_TURBO, ru.MODE_TURBO)):
            radio = QRadioButton(title)
            radio.setChecked(self.settings.mode == mode)
            radio.setProperty("mode", mode)
            self.mode_group.addButton(radio)
            mode_row.addWidget(radio)
        mode_row.addStretch(1)
        self.mode_group.buttonClicked.connect(self._on_mode)
        layout.addLayout(mode_row)
        hint = QLabel(ru.MODE_HINT, objectName="hint")
        layout.addWidget(hint)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel(ru.LABEL_PRESET))
        self.preset_box = QComboBox()
        for key, title in ru.PRESETS.items():
            self.preset_box.addItem(title, key)
        self.preset_box.setCurrentIndex(max(0, self.preset_box.findData(self.settings.notify.preset)))
        self.preset_box.currentIndexChanged.connect(self._on_preset)
        preset_row.addWidget(self.preset_box)
        preset_row.addStretch(1)
        layout.addLayout(preset_row)

        group = QGroupBox(ru.GROUP_EVENTS)
        grid = QGridLayout(group)
        for row, (event_id, title) in enumerate(ru.EVENT_TITLES.items()):
            check = QCheckBox(title)
            check.toggled.connect(lambda on, e=event_id: self._on_event_toggled(e, on))
            grid.addWidget(check, row, 0)
            spin = None
            spec = self.core.specs.get(event_id)
            if spec is not None and spec.warn_before and spec.warn_before[0] > 0:
                spin = QSpinBox(minimum=1, maximum=WARN_MAX_SEC, suffix=ru.SUFFIX_SEC)
                spin.setFixedWidth(SPIN_WIDTH)
                spin.setValue(self.settings.notify.warn_seconds.get(event_id, spec.warn_before[0]))
                spin.valueChanged.connect(lambda sec, e=event_id: self._on_warn_changed(e, sec))
                grid.addWidget(spin, row, 1)
            self._event_rows[event_id] = (check, spin)
        grid.setColumnStretch(0, 1)
        layout.addWidget(group)

        self.first_rune = QCheckBox(ru.CHECK_FIRST_POWER_RUNE)
        self.first_rune.setChecked(self.settings.notify.first_power_rune)
        self.first_rune.toggled.connect(self._on_first_rune)
        layout.addWidget(self.first_rune)
        self.every_rune = QCheckBox(ru.CHECK_EVERY_POWER_RUNE_30)
        self.every_rune.setChecked(self.settings.notify.every_power_rune_30)
        self.every_rune.toggled.connect(self._on_every_rune)
        layout.addWidget(self.every_rune)
        layout.addStretch(1)
        self._refresh_event_checks()
        return tab

    def _refresh_event_checks(self) -> None:
        enabled = self.core.manager.enabled_events()
        for event_id, (check, _) in self._event_rows.items():
            check.blockSignals(True)
            check.setChecked(event_id in enabled)
            check.blockSignals(False)

    def _on_mode(self, button) -> None:
        self.settings.mode = button.property("mode")
        self.core.reload()
        self._save()

    def _on_preset(self) -> None:
        self.settings.notify.preset = self.preset_box.currentData()
        self.settings.notify.enabled_overrides.clear()  # новый пресет: ручные галочки сбрасываются
        self._refresh_event_checks()
        self._save()

    def _on_event_toggled(self, event_id: str, on: bool) -> None:
        in_preset = event_id in self.core.presets.presets.get(self.settings.notify.preset, ())
        if on == in_preset:
            self.settings.notify.enabled_overrides.pop(event_id, None)
        else:
            self.settings.notify.enabled_overrides[event_id] = on
        self._save()

    def _on_warn_changed(self, event_id: str, sec: int) -> None:
        self.settings.notify.warn_seconds[event_id] = sec
        self.core.apply_settings()
        self._save()

    def _on_first_rune(self, on: bool) -> None:
        self.settings.notify.first_power_rune = on
        self._save()

    def _on_every_rune(self, on: bool) -> None:
        self.settings.notify.every_power_rune_30 = on
        self.core.apply_settings()
        self._save()

    # --- вкладка «Угрозы» ---
    def _threats_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        ts = self.settings.threats
        self.threats_enabled = QCheckBox(ru.CHECK_THREATS_ENABLED)
        self.threats_enabled.setChecked(ts.enabled)
        self.threats_enabled.toggled.connect(lambda on: (setattr(ts, "enabled", on), self._save()))
        layout.addWidget(self.threats_enabled)

        row = QHBoxLayout()
        row.addWidget(QLabel(ru.LABEL_THREAT_COUNT))
        count = QSpinBox(minimum=1, maximum=3)
        count.setValue(ts.threat_count)
        count.valueChanged.connect(lambda n: (setattr(ts, "threat_count", n), self._save()))
        row.addWidget(count)
        row.addStretch(1)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel(ru.LABEL_RANK))
        self.rank_box = QComboBox()
        self.rank_box.addItem(ru.RANK_NONE, None)
        for bracket, name in ru.RANK_NAMES.items():
            self.rank_box.addItem(name, bracket * 10 + 1)
        current = (ts.rank_tier // 10 * 10 + 1) if ts.rank_tier else None
        self.rank_box.setCurrentIndex(max(0, self.rank_box.findData(current)))
        self.rank_box.currentIndexChanged.connect(
            lambda: (setattr(ts, "rank_tier", self.rank_box.currentData()), self._save()))
        row.addWidget(self.rank_box)
        row.addStretch(1)
        layout.addLayout(row)

        self.stats_label = QLabel(objectName="hint", wordWrap=True)
        layout.addWidget(self.stats_label)
        layout.addWidget(QLabel(ru.THREATS_HINT, objectName="hint", wordWrap=True))
        for text, action in ((ru.BUTTON_PICK_HEROES, "pick_heroes"), (ru.BUTTON_SEEN_ITEM, "seen_item"),
                             (ru.BUTTON_SHOW_CARD, "show_threats")):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, a=action: self.core.on_hotkey(a))
            button.setEnabled(self.threats is not None)
            layout.addWidget(button)
        layout.addStretch(1)
        self._refresh_stats_label()
        return tab

    def _refresh_stats_label(self) -> None:
        if self.threats is None:
            return
        stats, mtime = self.threats.stats_status()
        if stats.available and mtime:
            matches = stats.raw.get("matches", {})
            when = time.strftime("%d.%m %H:%M", time.localtime(mtime))
            text = ru.STATS_STATUS.format(patch=stats.patch, normal=matches.get("normal", 0),
                                          turbo=matches.get("turbo", 0), when=when)
        else:
            text = ru.STATS_MISSING
        self.stats_label.setText(f"{ru.LABEL_STATS} {text}")

    # --- вкладка «Голос и экран» ---
    def _voice_screen_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        row.addWidget(QLabel(ru.LABEL_VOLUME))
        self.volume = QSlider(Qt.Horizontal, minimum=0, maximum=100)
        self.volume.setValue(round(self.settings.volume * 100))
        self.volume.valueChanged.connect(self._on_volume)
        row.addWidget(self.volume)
        layout.addLayout(row)
        test_voice = QPushButton(ru.BUTTON_TEST_VOICE)
        test_voice.clicked.connect(lambda: self.core.voice.play("power_rune_30"))
        layout.addWidget(test_voice)
        layout.addSpacing(16)
        layout_button = QPushButton(ru.BUTTON_LAYOUT)
        layout_button.clicked.connect(self._on_layout)
        layout.addWidget(layout_button)
        layout.addWidget(QLabel(ru.LAYOUT_HINT_WINDOW, objectName="hint"))
        demo_button = QPushButton(ru.BUTTON_DEMO)
        demo_button.clicked.connect(self.show_demo)
        layout.addWidget(demo_button)
        layout.addStretch(1)
        return tab

    def _on_volume(self, value: int) -> None:
        self.settings.volume = value / 100
        self.core.voice.set_volume(self.settings.volume)
        self._save()

    def _on_layout(self) -> None:
        self.core.overlay.set_layout_mode(True)
        for text, important in ru.OVERLAY_DEMO[:2]:
            self.core.overlay.show_message(text, important)

    def show_demo(self) -> None:
        demo = list(ru.OVERLAY_DEMO)

        def next_demo():
            if demo:
                self.core.overlay.show_message(*demo.pop(0))
                QTimer.singleShot(DEMO_INTERVAL_MS, next_demo)

        next_demo()

    # --- вкладка «Горячие клавиши» ---
    def _hotkeys_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel(ru.HOTKEYS_HINT, objectName="hint", wordWrap=True))
        grid = QGridLayout()
        for row, (action, title) in enumerate(ru.HOTKEY_TITLES.items()):
            grid.addWidget(QLabel(title), row, 0)
            edit = QKeySequenceEdit(QKeySequence(self.settings.hotkeys.get(action, "")))
            edit.setMaximumSequenceLength(1)
            edit.editingFinished.connect(lambda a=action: self._on_hotkey_edited(a))
            grid.addWidget(edit, row, 1)
            self._key_edits[action] = edit
        grid.setColumnStretch(0, 1)
        layout.addLayout(grid)
        reset = QPushButton(ru.BUTTON_RESET_HOTKEYS)
        reset.clicked.connect(self._reset_hotkeys)
        layout.addWidget(reset)
        layout.addStretch(1)
        return tab

    def _on_hotkey_edited(self, action: str) -> None:
        edit = self._key_edits[action]
        combo = edit.keySequence().toString(QKeySequence.PortableText)
        if parse_binding(combo) is None:
            QMessageBox.warning(self, ru.APP_TITLE, ru.HOTKEY_INVALID.format(combo=combo or "—"))
            edit.setKeySequence(QKeySequence(self.settings.hotkeys.get(action, "")))
            return
        self.settings.hotkeys[action] = combo
        self.hotkeys.start(self.settings.hotkeys)
        self._save()

    def _reset_hotkeys(self) -> None:
        self.settings.hotkeys = dict(DEFAULT_BINDINGS)
        for action, edit in self._key_edits.items():
            edit.setKeySequence(QKeySequence(self.settings.hotkeys[action]))
        self.hotkeys.start(self.settings.hotkeys)
        self._save()

    # --- вкладка «Общие» ---
    def _general_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.autostart = QCheckBox(ru.CHECK_AUTOSTART)
        self.autostart.setChecked(autostart.is_enabled())
        self.autostart.toggled.connect(self._on_autostart)
        layout.addWidget(self.autostart)
        layout.addStretch(1)
        return tab

    def _on_autostart(self, on: bool) -> None:
        try:
            autostart.set_enabled(on)
        except OSError as error:
            log.exception("Ошибка автозапуска")
            QMessageBox.warning(self, ru.APP_TITLE, ru.AUTOSTART_ERROR.format(error=error))
            self.autostart.blockSignals(True)
            self.autostart.setChecked(autostart.is_enabled())
            self.autostart.blockSignals(False)

    # --- вкладка «О программе» ---
    def _about_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        text = QLabel(ru.ABOUT_TEXT)
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch(1)
        return tab

    # --- трей ---
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(ru.APP_TITLE)
        menu = QMenu()
        menu.addAction(ru.TRAY_OPEN, self.show_window)
        self.pause_action = menu.addAction(ru.TRAY_PAUSE)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self._on_pause)
        menu.addSeparator()
        menu.addAction(ru.TRAY_EXIT, self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_window() if reason == QSystemTrayIcon.DoubleClick else None)
        self.tray.show()

    def _on_pause(self, on: bool) -> None:
        self.core.muted = on
        self.refresh_status()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def refresh_status(self) -> None:
        self._refresh_stats_label()
        text = ru.STATUS_CONNECTED if self.core.connected else ru.STATUS_WAITING
        self.status_hint.setVisible(not self.core.connected)
        if self.core.muted:
            text += ru.STATUS_MUTED
        self.status.setText(text)
        self.tray.setToolTip(f"{ru.APP_TITLE}: {text}")

    def _save(self) -> None:
        save_settings(self.settings)

    def quit(self) -> None:
        self._quitting = True
        self.hotkeys.stop()
        self._save()
        self.tray.hide()
        self.close()
        QApplication.quit()

    def closeEvent(self, event):
        # крестик прячет окно в трей, программа продолжает работать
        if self._quitting:
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(ru.APP_TITLE, ru.TRAY_STILL_RUNNING, app_icon(), 3000)
