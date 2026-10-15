"""Фон окна (вариант Б автора 15.10) и стиль: картинка игрока, тёмный градиент без неё, свои стрелки у полей с числом."""
from pathlib import Path


def test_background_image_and_fallback(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage
    from app.ui.background import BackgroundWidget
    img = tmp_path / "bg.png"
    picture = QImage(64, 36, QImage.Format_ARGB32)
    picture.fill(QColor("#336699"))
    picture.save(str(img))
    widget = BackgroundWidget(str(img))
    assert widget._custom is not None
    assert widget.set_image(None) and widget._custom is None  # «Убрать» — тёмный градиент
    assert not widget.set_image(str(tmp_path / "нет.png"))  # битый путь — сообщение игроку, фон градиентом
    widget.resize(200, 150)
    assert not widget.grab().isNull()


def test_spinbox_arrows_have_own_images(qapp):
    """BUGLOG №45: стрелки Windows под нашим стилем наезжали на «сек» и не нажимались — у стрелок свои картинки и место."""
    from app.ui.theme import app_stylesheet
    qss = app_stylesheet()
    assert "QSpinBox::up-button" in qss and "subcontrol-position: top right" in qss
    up = qss.split("QSpinBox::up-arrow { image: url(")[1].split(")")[0]
    assert Path(up).is_file()


def test_switcher_hero_in_match_map_outside(qapp, tmp_path, monkeypatch):
    """«Б в катке, А вне катки» (автор 15.10): карта при запуске, герой игрока в катке, после катки — снова карта;
    своя картинка игрока важнее; запоздавшая картинка (нужна уже другая) не ставится."""
    from types import SimpleNamespace
    from PySide6.QtGui import QColor, QImage
    from app.ui import backdrops
    from app.ui.background import BackgroundWidget
    monkeypatch.setattr(backdrops, "cache_dir", lambda: tmp_path)
    for name in ("map.jpg", "hero_drow_ranger.png"):
        picture = QImage(40, 30, QImage.Format_ARGB32)
        picture.fill(QColor("#884422"))
        picture.save(str(tmp_path / name), "PNG")
    widget = BackgroundWidget()
    switcher = backdrops.BackgroundSwitcher(widget, backdrops.Backdrops(fetch=lambda url, path: None))
    assert widget._auto_kind == backdrops.KIND_MAP
    switcher.on_state(SimpleNamespace(hero_name="npc_dota_hero_drow_ranger", in_match=True))
    assert widget._auto_kind == backdrops.KIND_HERO
    switcher.on_state(SimpleNamespace(hero_name="npc_dota_hero_drow_ranger", in_match=False))
    assert widget._auto_kind == backdrops.KIND_MAP
    switcher._on_ready(backdrops.KIND_HERO, str(tmp_path / "hero_drow_ranger.png"))  # опоздала — катка кончилась
    assert widget._auto_kind == backdrops.KIND_MAP
    assert backdrops.source(backdrops.KIND_HERO, "nevermore")[0].endswith("/heroes/renders/nevermore.png")


def test_hero_beats_custom_in_match(qapp, tmp_path):
    """В катке — всегда герой, даже если выбрана своя картинка; вне катки — своя картинка вместо карты (автор 15.10)."""
    from PySide6.QtGui import QColor, QImage
    from app.ui.backdrops import KIND_HERO, KIND_MAP
    from app.ui.background import BackgroundWidget
    paths = {}
    for name, color in (("own", "#ff0000"), ("hero", "#00ff00"), ("map", "#0000ff")):
        img = QImage(40, 30, QImage.Format_ARGB32)
        img.fill(QColor(color))
        paths[name] = tmp_path / f"{name}.png"
        img.save(str(paths[name]))
    widget = BackgroundWidget(str(paths["own"]))
    widget.resize(80, 60)
    widget.set_auto(KIND_MAP, str(paths["map"]))
    out = widget.grab().toImage()
    assert out.pixelColor(10, 30).red() > out.pixelColor(10, 30).blue()  # вне катки — своя (красная), не карта
    widget.set_auto(KIND_HERO, str(paths["hero"]))
    out = widget.grab().toImage()
    assert out.pixelColor(75, 30).green() > out.pixelColor(75, 30).red()  # в катке справа — герой (зелёный)
