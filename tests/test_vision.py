"""Тесты зрения на вырезках из скриншотов автора (2560×1080).

Нужны иконки из кеша OpenDota (%APPDATA%\\DotaTimer\\cache\\images). Если кеша нет
(например, на чистой машине), тесты пропускаются.
"""
from pathlib import Path

import numpy as np
import pytest

from app import paths
from app.vision.geometry import HudLayout, Rect
from app.vision.matcher import IconLibrary, load_image

SCREENS = Path(__file__).parent / "fixtures" / "screens"
ICONS = paths.cache_dir() / "images"
LAYOUT = HudLayout(2560, 1080)
MATCH_HEROES = {"void_spirit": "Void Spirit", "drow_ranger": "Drow Ranger", "tidehunter": "Tidehunter",
                "lich": "Lich", "vengefulspirit": "Vengeful Spirit", "earthshaker": "Earthshaker",
                "zuus": "Zeus", "warlock": "Warlock", "sven": "Sven", "witch_doctor": "Witch Doctor"}

needs_icons = pytest.mark.skipif(not (ICONS / "items").is_dir() or not (ICONS / "heroes").is_dir(),
                                 reason="нет кеша иконок OpenDota")


def test_layout_scales_for_other_resolutions():
    for w, h in ((1920, 1080), (2560, 1440), (3840, 2160)):
        layout = HudLayout(w, h)
        first = layout.topbar_slots()[0]
        assert first.x == round((w - 1920 * h / 1080) / 2 + 545 * h / 1080)
        area = layout.inventory_area()
        assert area.y + area.h <= h and area.x + area.w <= w
    # 21:9 автора: HUD сдвинут на 320 пикселей
    assert LAYOUT.topbar_slots()[0].x == 545 + 320


@pytest.fixture(scope="module")
def item_lib():
    slot = LAYOUT.inventory_slots()[0]
    files = {p.stem: p for p in (ICONS / "items").glob("*.png") if not p.stem.startswith("recipe")}
    return IconLibrary.from_files(files, slot.w, slot.h)


@needs_icons
def test_topbar_heroes():
    from app.vision.topbar import recognize_topbar
    slot = LAYOUT.topbar_slots()[0]
    heroes = IconLibrary.from_files({p.stem: p for p in (ICONS / "heroes").glob("*.png")}, slot.w, slot.h)
    strip = load_image(SCREENS / "topbar_094950.png")
    area = LAYOUT.topbar_area()
    frame = np.zeros((1080, 2560, 3), dtype=np.uint8)
    frame[area.y:area.y + area.h, area.x:area.x + area.w] = strip
    result = recognize_topbar(frame, LAYOUT, heroes)
    assert result.heroes("radiant") == ["void_spirit", "drow_ranger", "tidehunter", "lich", "vengefulspirit"]
    assert result.heroes("dire") == ["earthshaker", "zuus", "warlock", "sven", "witch_doctor"]
    assert result.side_of("zuus") == "dire"


@needs_icons
@pytest.mark.parametrize("shot, hero, expected", [
    ("094950", "void_spirit", {"tango", "circlet", "quelling_blade", "mantle"}),
    ("095733", "zuus", {"enchanted_mango", "null_talisman", "wizard_hat", "blood_grenade", "boots"}),
    ("095736", "witch_doctor", {"tango", "enchanted_mango", "boots", "blood_grenade", "wizard_hat", "sobi_mask"}),
    ("095738", "earthshaker", {"boots", "belt_of_strength"}),
    ("095123", "warlock", None),
])
def test_inventory(qapp, item_lib, shot, hero, expected):
    from app.vision.inventory import InventoryReader
    from app.vision.names import NameMatcher
    neutral = LAYOUT.neutral_slot()
    reader = InventoryReader(LAYOUT, item_lib, IconLibrary({}, neutral.w, neutral.h), NameMatcher(MATCH_HEROES))
    area = LAYOUT.inventory_area()
    snapshot = reader.read(load_image(SCREENS / f"inv_{shot}.png"), Rect(area.x, area.y, area.w, area.h))
    assert snapshot.hero == hero
    if expected is not None:
        # ветка (branches) похожа на другую иконку и не распознаётся уверенно — это нормально, она игнорируется
        assert expected <= snapshot.items
        assert snapshot.backpack == (None, None, None)


@needs_icons
def test_unchanged_cells_are_not_matched_again(qapp, item_lib):
    from app.vision.inventory import InventoryReader
    from app.vision.names import NameMatcher
    neutral = LAYOUT.neutral_slot()
    reader = InventoryReader(LAYOUT, item_lib, IconLibrary({}, neutral.w, neutral.h), NameMatcher(MATCH_HEROES))
    area = LAYOUT.inventory_area()
    origin = Rect(area.x, area.y, area.w, area.h)
    image = load_image(SCREENS / "inv_095733.png")
    first = reader.read(image, origin)
    calls = []
    original = item_lib.match
    item_lib.match = lambda *a, **k: calls.append(1) or original(*a, **k)
    try:
        second = reader.read(image, origin)
    finally:
        item_lib.match = original
    assert second == first and calls == []


def test_topbar_duplicates_are_rejected():
    from app.vision.matcher import Match
    from app.vision.topbar import TopbarResult
    result = TopbarResult(tuple(Match("ogre_magi", 0.63, 0.4) for _ in range(4)) + (Match("sven", 0.8, 0.3),),
                          tuple(Match(h, 0.8, 0.3) for h in ("lina", "lion", "zuus", "axe", "tiny")))
    assert result.heroes("radiant") == [None, None, None, None, "sven"]
    assert result.heroes("dire") == ["lina", "lion", "zuus", "axe", "tiny"]
