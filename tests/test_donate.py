"""Донат (ТЗ п. 9): ссылка на Boosty из data/donate.json, без неё кнопок нет."""
import json

from app.ui.donate import donate_url


def test_donate_link_from_data():
    assert donate_url() == "https://boosty.to/dota2timer"


def test_no_link_no_button(tmp_path):
    """Пустая, не https или битая ссылка — кнопки «Поддержать автора» скрыты."""
    path = tmp_path / "donate.json"
    for raw in ({"url": ""}, {"url": "http://boosty.to/x"}, {}):
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert donate_url(path) is None
    path.write_text("{битый", encoding="utf-8")
    assert donate_url(path) is None
    assert donate_url(tmp_path / "нет.json") is None
