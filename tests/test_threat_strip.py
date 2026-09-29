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
