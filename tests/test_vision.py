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

# вырезки inv_*.png сняты прямоугольником нижней панели до BUGLOG №60: имя (x 875) + инвентарь, без запаса на сдвиг
INV_FIXTURE_X, INV_FIXTURE_Y = 875, 917


def inventory_frame(shot: str) -> np.ndarray:
    """Кадр 2560×1080 с вырезкой нижней панели на её месте."""
    part = load_image(SCREENS / f"inv_{shot}.png")
    frame = np.zeros((1080, 2560, 3), dtype=np.uint8)
    frame[INV_FIXTURE_Y:INV_FIXTURE_Y + part.shape[0], INV_FIXTURE_X:INV_FIXTURE_X + part.shape[1]] = part
    return frame


def read_frame(reader, frame: np.ndarray):
    area = LAYOUT.inventory_area()
    return reader.read(frame[area.y:area.y + area.h, area.x:area.x + area.w], area)


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
    snapshot = read_frame(reader, inventory_frame(shot))
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
    frame = inventory_frame("095733")
    first = read_frame(reader, frame)
    calls = []
    original = item_lib.match
    item_lib.match = lambda *a, **k: calls.append(1) or original(*a, **k)
    try:
        second = read_frame(reader, frame)
    finally:
        item_lib.match = original
    assert second == first and calls == []


@needs_icons
@pytest.mark.parametrize("shot, hero", [("095733", "zuus"), ("095736", "witch_doctor")])
def test_wide_hud_is_read(qapp, item_lib, shot, hero):
    """Широкая нижняя панель (у героя больше 4 способностей): имя с портретом левее, инвентарь правее на 44 пикселя.
    22.10 Rubick не узнавался 99–100% минут — имя искалось на обычном месте (BUGLOG №60). Кадр — обычная панель,
    раздвинутая так же, как у Rubick на снимке 22.10."""
    from app.vision.inventory import InventoryReader
    from app.vision.names import NameMatcher
    shift = 44
    normal = inventory_frame(shot)
    wide = np.zeros_like(normal)
    split = INV_FIXTURE_X + 300  # между портретом и инвентарём — способности, они не нужны
    wide[:, :split - shift] = normal[:, shift:split]
    wide[:, split + shift:] = normal[:, split:-shift]
    neutral = LAYOUT.neutral_slot()

    def reader():
        return InventoryReader(LAYOUT, item_lib, IconLibrary({}, neutral.w, neutral.h), NameMatcher(MATCH_HEROES))

    expected = read_frame(reader(), normal)
    snapshot = read_frame(reader(), wide)
    assert snapshot.hero == expected.hero == hero
    assert snapshot.slots == expected.slots and snapshot.items


def test_topbar_duplicates_are_rejected():
    from app.vision.matcher import Match
    from app.vision.topbar import TopbarResult
    result = TopbarResult(tuple(Match("ogre_magi", 0.63, 0.4) for _ in range(4)) + (Match("sven", 0.8, 0.3),),
                          tuple(Match(h, 0.8, 0.3) for h in ("lina", "lion", "zuus", "axe", "tiny")))
    assert result.heroes("radiant") == [None, None, None, None, "sven"]
    assert result.heroes("dire") == ["lina", "lion", "zuus", "axe", "tiny"]


def test_gold_hero_name_is_read(qapp):
    """Имя выделенного героя бывает золотым (враг, иногда свой) — раньше читались только белые буквы, и в катке 16.10
    21:23 программа не узнала ни одного кадра: событий «новые предметы» — 0 (BUGLOG №55). Кадры — из видео автора."""
    import cv2
    from pathlib import Path
    from app.vision.geometry import HudLayout
    from app.vision.names import NameMatcher
    folder = Path(__file__).parent / "fixtures" / "names"
    names = {"windrunner": "Windranger", "bloodseeker": "Bloodseeker", "necrolyte": "Necrophos",
             "templar_assassin": "Templar Assassin", "meepo": "Meepo", "spirit_breaker": "Spirit Breaker"}
    matcher = NameMatcher(names)
    scale = HudLayout(1920, 1080).scale
    assert matcher.match(cv2.imread(str(folder / "gold_windranger.png")), scale) == "windrunner"
    assert matcher.match(cv2.imread(str(folder / "gold_meepo.png")), scale) == "meepo"
    # на этом кадре слабые пороги давали Bloodseeker — предметы своего героя приписались бы врагу
    assert matcher.match(cv2.imread(str(folder / "gold_windranger_trap.png")), scale) in ("windrunner", None)


@pytest.mark.parametrize("shot, hero, wide", [("strip_gold_leshrac", "leshrac", False),
                                              ("strip_gold_phantom_lancer", "phantom_lancer", False),
                                              ("strip_gold_invoker_wide", "invoker", True),
                                              ("strip_white_phantom_assassin", "phantom_assassin", True)])
def test_name_strip_from_debug_images(qapp, shot, hero, wide):
    """Полосы имени (2560×1080) из logs/vision_debug 23–24.10: «не узнан» всю катку (BUGLOG №64). Золото насыщеннее
    прежнего порога (R − B ≈ 126, был предел 110) — бралась золотая стрелка уровня; PHANTOM ASSASSIN шире рамки."""
    import cv2
    from app.vision.names import NameMatcher
    # кандидаты — герои тех каток (из лога «Имена для нижней панели»)
    names = {"strip_gold_leshrac": {"leshrac": "Leshrac", "disruptor": "Disruptor", "enchantress": "Enchantress",
                                    "lion": "Lion", "razor": "Razor", "shadow_shaman": "Shadow Shaman",
                                    "spectre": "Spectre", "tiny": "Tiny", "zuus": "Zeus"},
             "strip_gold_phantom_lancer": {"phantom_lancer": "Phantom Lancer", "huskar": "Huskar", "lina": "Lina",
                                           "shadow_shaman": "Shadow Shaman", "skeleton_king": "Wraith King",
                                           "ursa": "Ursa", "viper": "Viper", "weaver": "Weaver"},
             "strip_gold_invoker_wide": {"rubick": "Rubick", "ancient_apparition": "Ancient Apparition",
                                         "earthshaker": "Earthshaker", "hoodwink": "Hoodwink", "invoker": "Invoker",
                                         "pugna": "Pugna", "necrolyte": "Necrophos"},
             "strip_white_phantom_assassin": {"phantom_assassin": "Phantom Assassin", "bloodseeker": "Bloodseeker",
                                              "clinkz": "Clinkz", "drow_ranger": "Drow Ranger",
                                              "enchantress": "Enchantress", "life_stealer": "Lifestealer",
                                              "ogre_magi": "Ogre Magi", "zuus": "Zeus", "warlock": "Warlock"}}[shot]
    strip_rect, box = LAYOUT.name_strip(), LAYOUT.name_box()
    found, shift = NameMatcher(names).find(cv2.imread(str(SCREENS.parent / "names" / f"{shot}.png")), LAYOUT.scale,
                                           box.x - strip_rect.x, box.w, LAYOUT.name_center() - strip_rect.x)
    assert found == hero and (shift > 0) == wide


def test_debug_image_saved_when_names_unknown(monkeypatch, tmp_path):
    """Имя не узнаётся почти весь минутный отрезок — картинка нижней панели в logs/vision_debug, не больше 3
    (20.10: «не узнан» 99–100% в двух катках, по логу причину не понять, BUGLOG №58)."""
    import numpy as np
    import app.vision.service as vs
    from app import paths
    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path)
    service = vs.VisionService.__new__(vs.VisionService)
    service._last_unknown = np.zeros((20, 40, 3), np.uint8)
    for _ in range(vs.DEBUG_MAX_PER_RUN + 2):
        service._save_debug()
    assert len(list((tmp_path / "vision_debug").glob("*.png"))) >= 1
    assert service._debug_saved == vs.DEBUG_MAX_PER_RUN


def test_single_frame_name_not_trusted():
    """Имя врага принимается, только если прочитано два раза подряд: одиночный кадр — ошибка прочтения
    (21.10 мой Lycan по одному кадру — Techies и Slark, BUGLOG №59)."""
    import numpy as np
    import app.vision.service as vs
    from app.vision.inventory import InventorySnapshot
    service = vs.VisionService.__new__(vs.VisionService)
    vs.QObject.__init__(service)
    emitted = []
    service.inventory_seen.connect(lambda s: emitted.append(s.hero))
    service._grab = lambda *a: np.zeros((5, 5, 3), np.uint8)
    reads = iter(["lycan", "techies", "lycan", "lycan", None, "slark", "slark"])

    class Reader:
        def read(self, image, area):
            return InventorySnapshot(next(reads), (None,) * 6, (None,) * 3, None)

    class Layout:
        def inventory_area(self):
            return None

    for _ in range(7):
        service._scan_inventory(None, None, Layout(), Reader())
    assert emitted == ["lycan", "slark"]


def test_outworld_destroyer_name_as_in_game(qapp):
    """27.10: свой OD «не узнан» 99% в двух катках — OpenDota зовёт его «Outworld Devourer», а в игре «OUTWORLD
    DESTROYER» (BUGLOG №65). Кандидаты — герои той катки (лог «Имена для нижней панели»), имя OD — из data/hero_names.json."""
    import cv2
    from app.threats.data import game_hero_names
    from app.vision.names import NameMatcher
    names = {"obsidian_destroyer": game_hero_names()["obsidian_destroyer"], "arc_warden": "Arc Warden",
             "chaos_knight": "Chaos Knight", "clinkz": "Clinkz", "crystal_maiden": "Crystal Maiden",
             "faceless_void": "Faceless Void", "magnataur": "Magnus", "razor": "Razor",
             "templar_assassin": "Templar Assassin"}
    strip_rect, box = LAYOUT.name_strip(), LAYOUT.name_box()
    found, _ = NameMatcher(names).find(cv2.imread(str(SCREENS.parent / "names" / "strip_gold_outworld_destroyer.png")),
                                       LAYOUT.scale, box.x - strip_rect.x, box.w, LAYOUT.name_center() - strip_rect.x)
    assert found == "obsidian_destroyer"
