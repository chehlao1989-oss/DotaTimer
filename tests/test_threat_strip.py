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
    from app.ui.threat_strip import BOTTOM_OFFSET, ThreatStrip
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
    assert strip.geometry().bottom() <= screen.bottom() - round(BOTTOM_OFFSET * strip.factor())
    strip.close()
