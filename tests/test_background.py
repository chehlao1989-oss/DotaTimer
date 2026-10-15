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
    assert widget._pixmap is not None
    assert widget.set_image(None) and widget._pixmap is None  # «Убрать» — тёмный градиент
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
