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


def test_dota_fonts_setting(tmp_path):
    """Шрифты Доты включены по умолчанию и у старого settings.json без ключа; выключаются настройкой use_dota_fonts
    (опыт 26–27.10 по падениям: с выключенными шрифтами 5 падений — не они, BUGLOG №57)."""
    import json
    from app.config import Settings, load_settings
    assert Settings().use_dota_fonts is True
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"volume": 0.5, "dota_fonts": False}), encoding="utf-8")
    assert load_settings(path).use_dota_fonts is True
    path.write_text(json.dumps({"use_dota_fonts": False}), encoding="utf-8")
    assert load_settings(path).use_dota_fonts is False
