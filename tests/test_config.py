"""Тесты настроек и автозапуска."""
from app import autostart
from app.config import Settings, load_settings, save_settings


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.mode = "turbo"
    settings.overlay.x, settings.overlay.y = 100, 200
    settings.notify.preset = "all"
    settings.notify.enabled_overrides["lotus"] = False
    settings.notify.warn_seconds["bounty_rune"] = 20
    save_settings(settings, path)
    loaded = load_settings(path)
    assert loaded == settings


def test_missing_or_broken_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "nope.json") == Settings()
    broken = tmp_path / "broken.json"
    broken.write_text("{не json", encoding="utf-8")
    assert load_settings(broken) == Settings()


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"volume": 0.5, "old_key": 1, "overlay": {"scale": 1.2, "junk": 2}}', encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.volume == 0.5 and loaded.overlay.scale == 1.2


def test_removed_hotkey_actions_are_dropped(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"hotkeys": {"roshan_killed": "Ctrl+Alt+R", "silence": "Ctrl+Alt+S"}}', encoding="utf-8")
    loaded = load_settings(path)
    assert "roshan_killed" not in loaded.hotkeys
    assert loaded.hotkeys["silence"] == "Ctrl+Alt+S"
    assert loaded.hotkeys["tormentor_killed"] == "Ctrl+Alt+T"


def test_autostart_command_uses_pythonw_and_launcher():
    command = autostart.build_command()
    assert "pythonw.exe" in command
    assert "run_dotatimer.pyw" in command
    assert command.endswith("--minimized")
