"""Полоска угроз: появляется с советами и исчезает после катки."""
from app.config import StripSettings
from app.threats.recommend import RecItem, ThreatRecommendation


def test_strip_hides_on_clear(qapp, tmp_path):
    from app.ui.threat_strip import ThreatStrip
    strip = ThreatStrip(StripSettings(), tmp_path)
    recs = [ThreatRecommendation("sniper", (RecItem("black_king_bar", 1.0, "hero", 3.0, 500),))]
    strip.update_recommendations(recs, {"sniper": "Sniper"})
    assert strip.isVisible()
    strip.clear()
    assert not strip.isVisible()
    strip.update_recommendations([], {})
    assert not strip.isVisible()  # без советов не появляется


def test_strip_stays_above_minimap_after_rerender(qapp, tmp_path):
    """Полоска по умолчанию не опускается на миникарту при перерисовке (BUGLOG №13)."""
    from app.config import StripSettings
    from app.threats.recommend import RecItem, ThreatRecommendation
    from app.ui.threat_strip import MARGIN, TOP_OFFSET, ThreatStrip
    from PySide6.QtGui import QGuiApplication
    recs = [ThreatRecommendation("axe", (RecItem("blade_mail", 1, "hero", 6.0, 900),)),
            ThreatRecommendation("sniper", (RecItem("blink", 1, "mechanic", None, 0),))]
    strip = ThreatStrip(StripSettings(), tmp_path)
    strip.update_recommendations(recs, {})
    strip.show()
    qapp.processEvents()
    strip.update_recommendations(recs, {})  # перерисовка при видимой полоске
    qapp.processEvents()
    screen = QGuiApplication.primaryScreen().availableGeometry()
    # справа вверху, под полосой FPS, и не уезжает при перерисовке (BUGLOG №13, №34)
    assert strip.geometry().top() == screen.top() + round(TOP_OFFSET * strip.factor())
    assert strip.geometry().right() == screen.right() - MARGIN
    strip.close()


def test_saved_position_never_goes_off_screen(qapp, tmp_path):
    """Сохранённое место + новая ширина не выводят окно за край экрана (окно сообщений на x = 2121, BUGLOG №34)."""
    from PySide6.QtGui import QGuiApplication
    from app.config import OverlaySettings
    from app.ui.overlay import Overlay
    screen = QGuiApplication.primaryScreen().availableGeometry()
    from app.ui.overlay import LAYOUT_VERSION
    overlay = Overlay(OverlaySettings(x=screen.right() - 100, y=screen.bottom() - 50, layout_version=LAYOUT_VERSION))
    overlay.show_message("У Doom появился Daedalus\n→ Solar Crest, Aeon Disk", True, 1)
    geometry = overlay.geometry()
    assert screen.contains(geometry)
    overlay.close()


def test_rerender_leaves_no_old_captions(qapp, tmp_path):
    """После перерисовки не остаётся старых подписей и иконок (игра 30.09: «+3%» висело за портретом, BUGLOG №29)."""
    from app.ui.outlined_label import OutlinedLabel
    from app.ui.threat_strip import ThreatStrip
    recs = [ThreatRecommendation("drow_ranger", (RecItem("monkey_king_bar", 1, "hero", 3.0, 900),
                                                 RecItem("blade_mail", 1, "hero", None, 900)))]
    strip = ThreatStrip(StripSettings(), tmp_path)
    strip.show()
    for _ in range(3):
        strip.update_recommendations(recs, {"drow_ranger": "Drow Ranger"})
        qapp.processEvents()
    visible = [w for w in strip.findChildren(OutlinedLabel) if w.isVisible()]
    assert len(visible) == 1 + 2  # имя героя + подпись под каждой из двух иконок, без остатков прошлых перерисовок
    strip.close()


def test_old_saved_position_is_reset_once(qapp):
    """Место окна из прошлой схемы (слева/по центру) сбрасывается один раз: у автора окно на y = 304 наслаивалось
    на оповещение Доты о покупке (игра 01.10, BUGLOG №35). Новое место, поставленное мышью, сохраняется."""
    from PySide6.QtGui import QGuiApplication
    from app.config import OverlaySettings
    from app.ui.overlay import LAYOUT_VERSION, MESSAGES_TOP, Overlay, screen_factor
    old = OverlaySettings(x=2121, y=304)
    overlay = Overlay(old)
    screen = QGuiApplication.primaryScreen().availableGeometry()
    assert old.x is None and old.layout_version == LAYOUT_VERSION
    assert overlay.geometry().top() == screen.top() + round(MESSAGES_TOP * screen_factor())
    assert overlay.geometry().right() == screen.right()
    overlay.close()
    moved = OverlaySettings(x=100, y=200, layout_version=LAYOUT_VERSION)
    Overlay(moved).close()
    assert (moved.x, moved.y) == (100, 200)


def test_hint_message_has_icons_and_gold_item(qapp, tmp_path):
    """Подсказка как оповещение Доты: портрет врага и иконка предмета справа, предмет выделен (вариант 2 автора)."""
    from PySide6.QtGui import QImage
    from app.config import OverlaySettings
    from app.ui.outlined_label import OutlinedLabel
    from app.ui.overlay import Overlay
    for kind, key in (("heroes", "phantom_assassin"), ("items", "butterfly")):
        (tmp_path / kind).mkdir()
        image = QImage(32, 24, QImage.Format_ARGB32)
        image.fill(0xFF336699)
        image.save(str(tmp_path / kind / f"{key}.png"))
    overlay = Overlay(OverlaySettings(), tmp_path)
    overlay.show_message("У Phantom Assassin появился Butterfly\n→ Monkey King Bar", True, 1,
                         icons=[("heroes", "phantom_assassin"), ("items", "butterfly")], highlights=["Butterfly"])
    frame = overlay._frames()[-1]
    assert len(frame.icons) == 2
    label = frame.findChild(OutlinedLabel)
    assert ("Butterfly", True) in label._segments("У Phantom Assassin появился Butterfly")
    assert ("6:00", True) in label._segments("Руна силы на 6:00")
    overlay.close()


def test_messages_never_overflow_window(qapp):
    """Четыре длинные подсказки подряд не вылезают из окна (низ окна — над миникартой справа): старые убираются."""
    from app.config import OverlaySettings
    from app.ui.overlay import Overlay
    overlay = Overlay(OverlaySettings())
    for n in range(4):
        overlay.show_message(f"У Phantom Assassin появился Butterfly {n}\n→ Monkey King Bar, Bloodthorn, Silver Edge", True, 60)
    qapp.processEvents()
    frames = [f for f in overlay._frames() if f.isVisible()]
    assert frames and sum(f.sizeHint().height() + 6 for f in frames) <= overlay.height() - 12
    overlay.close()
