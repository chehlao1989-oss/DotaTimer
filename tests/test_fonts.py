"""Шрифты окна (вариант 2 автора 15.10): из Доты игрока, без неё — свободные из assets/fonts."""
import pytest

from app.gsi import installer


def test_bundled_fonts_without_dota(qapp):
    from app.ui.fonts import load_fonts
    assert load_fonts(None) == ("Fira Sans", "Forum")


def test_dota_fonts_from_game_folder(qapp):
    from app.ui.fonts import load_fonts
    dota = installer.find_dota_dir()
    if dota is None:
        pytest.skip("Дота не установлена — шрифты из игры не проверить")
    assert load_fonts(dota) == ("Radiance", "Reaver")


def test_stylesheet_uses_fonts(qapp):
    from app.ui.theme import app_stylesheet
    qss = app_stylesheet("Radiance", "Reaver")
    assert 'font-family: "Radiance"' in qss and 'QTabBar::tab { font-family: "Reaver"' in qss
    assert "Segoe UI" not in qss


def test_dota_fonts_off_for_crash_experiment(tmp_path):
    """Опыт 26.10 (BUGLOG №57): шрифты Доты выключены, и у старого settings.json без ключа тоже."""
    import json
    from app.config import Settings, load_settings
    assert Settings().dota_fonts is False
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"volume": 0.5}), encoding="utf-8")
    assert load_settings(path).dota_fonts is False
    path.write_text(json.dumps({"dota_fonts": True}), encoding="utf-8")
    assert load_settings(path).dota_fonts is True
