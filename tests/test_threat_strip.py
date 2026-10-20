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


def test_item_name_only_when_advice_appears_or_changes(qapp, tmp_path):
    """Название под иконкой — 5 с, когда совет появился или сменился (выбор автора 16.10), потом пропадает:
    постоянные названия закрывали лавку (BUGLOG №49). Имя героя — всегда."""
    from app.ui.outlined_label import OutlinedLabel
    from app.ui.threat_strip import ThreatStrip
    strip = ThreatStrip(StripSettings(), tmp_path)
    titles = {"axe": "Axe", "blade_mail": "Blade Mail", "black_king_bar": "Black King Bar"}

    def texts():
        qapp.processEvents()
        return [w.text() for w in strip.findChildren(OutlinedLabel) if w.isVisible()]

    strip.update_recommendations([ThreatRecommendation("axe", (RecItem("blade_mail", 1, "hero", 6.0, 900),))], titles)
    strip.show()
    assert "Blade Mail" in texts() and "Axe" in texts()  # совет появился
    strip._unname(("axe", "blade_mail"))
    assert "Blade Mail" not in texts() and "Axe" in texts()  # через 5 с
    strip.update_recommendations([ThreatRecommendation("axe", (RecItem("blade_mail", 1, "hero", 6.0, 900),))], titles)
    assert "Blade Mail" not in texts()  # тот же совет — без названия
    strip.update_recommendations([ThreatRecommendation("axe", (RecItem("black_king_bar", 1, "hero", 4.0, 900,
                                                                       changed=True),))], titles)
    assert "Black King Bar" in texts()  # совет сменился
    strip.close()


def test_moved_strip_stays_on_screen_after_growing(qapp, tmp_path):
    """Полоска, сдвинутая игроком к правому краю, после роста (больше советов) не вылезает за экран (15.10)."""
    from PySide6.QtGui import QGuiApplication
    from app.ui.threat_strip import ThreatStrip
    screen = QGuiApplication.primaryScreen().availableGeometry()
    strip = ThreatStrip(StripSettings(x=screen.right() - 30, y=screen.top() + 60), tmp_path)
    strip.update_recommendations([ThreatRecommendation("axe", (RecItem("blade_mail", 1, "hero", 6.0, 900),
                                                               RecItem("black_king_bar", 1, "hero", 4.0, 900)))],
                                 {"axe": "Axe", "blade_mail": "Blade Mail", "black_king_bar": "Black King Bar"})
    strip.show()
    qapp.processEvents()
    assert screen.contains(strip.geometry())
    strip.close()


def test_opacity_dims_only_message_background(qapp, tmp_path):
    """Прозрачность сообщений — только у подложки, текст на 100% (у автора 60% — уведомления бледные, игра 15.10)."""
    from app.config import OverlaySettings
    from app.ui.overlay import FRAME_ALPHA_END, Overlay
    overlay = Overlay(OverlaySettings(opacity=0.6), tmp_path)
    overlay.show_message("Руна силы на 6:00", False, 1)
    assert overlay.windowOpacity() == 1.0
    assert f"rgba(8, 10, 14, {round(FRAME_ALPHA_END * 0.6)})" in overlay._frames()[-1].styleSheet()
    overlay.close()


def test_strip_keeps_threats_without_answer(qapp, tmp_path):
    """Угрозы без контр-предмета остаются на полоске с «нет явного ответа» (иначе полоска пропадала, игра 15.10)."""
    from app.i18n import ru
    from app.ui.outlined_label import OutlinedLabel
    from app.ui.threat_strip import ThreatStrip
    strip = ThreatStrip(StripSettings(), tmp_path)
    strip.update_recommendations([ThreatRecommendation("sand_king", ()), ThreatRecommendation("sven", ())],
                                 {"sand_king": "Sand King", "sven": "Sven"})
    qapp.processEvents()
    assert strip.isVisible()
    texts = [label._text for label in strip.findChildren(OutlinedLabel) if label.isVisibleTo(strip)]
    assert texts.count(ru.STRIP_NO_ANSWER) == 2 and "Sand King" in texts
    strip.close()


def test_strip_not_rebuilt_without_changes(qapp, tmp_path):
    """Те же советы (пересчёт раз в 30 с игры) — полоска не пересобирается; изменились — новое содержимое целиком,
    старое удаляется одним deleteLater (падения 16–19.10 — внутри пересборки, BUGLOG №57)."""
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    from app.ui.threat_strip import ThreatStrip
    strip = ThreatStrip(StripSettings(), tmp_path)
    recs = [ThreatRecommendation("axe", (RecItem("blade_mail", 1, "mechanic", None, 0),))]
    strip.update_recommendations(recs, {"axe": "Axe"})
    strip._named.clear()
    strip._render()
    first = strip._content
    for _ in range(5):
        strip.update_recommendations(recs, {"axe": "Axe"})
    assert strip._content is first  # ничего не поменялось — тот же виджет
    strip.update_recommendations([ThreatRecommendation("axe", (RecItem("black_king_bar", 1, "mechanic", None, 0),))],
                                 {"axe": "Axe"})
    assert strip._content is not first
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not shiboken6.isValid(first)
    strip.close()


def test_strip_code_has_no_piecewise_takeat():
    """Пересборка без takeAt: элементы не вынимаются из разметки по одному (BUGLOG №57)."""
    from pathlib import Path
    source = (Path(__file__).parent.parent / "app" / "ui" / "threat_strip.py").read_text(encoding="utf-8")
    assert ".takeAt(" not in source
