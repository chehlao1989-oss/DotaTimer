"""Фоновый поток зрения: снимает только нужные области экрана и распознаёт их.

- верхняя панель: по запросу (в начале катки), чтобы узнать 10 героев;
- инвентарь выделенного героя: 2–3 раза в секунду, пока идёт катка.
Работает вне потока интерфейса, результаты приходят сигналами Qt.
"""
import logging
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Signal

from app.threats.data import DataCache, GameData
from app.vision.geometry import Calibration, HudLayout
from app.vision.inventory import InventoryReader
from app.vision.matcher import IconLibrary
from app.vision.names import NameMatcher
from app.vision.topbar import recognize_topbar

log = logging.getLogger(__name__)

INVENTORY_INTERVAL_SEC = 0.4  # ~2.5 раза в секунду
IDLE_SLEEP_SEC = 0.5


class VisionService(QObject):
    topbar_ready = Signal(object)  # TopbarResult
    inventory_seen = Signal(object)  # InventorySnapshot (только когда понятно, какой герой выделен)

    def __init__(self, data: GameData, cache: DataCache, calibration: Calibration = Calibration()):
        super().__init__()
        self.data = data
        self.cache = cache
        self.calibration = calibration
        self._active = threading.Event()
        self._stop = threading.Event()
        self._topbar_requested = threading.Event()
        self._names: NameMatcher | None = None
        self._reader: InventoryReader | None = None
        self._thread: threading.Thread | None = None

    # --- управление из потока интерфейса ---
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="vision", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_active(self, active: bool) -> None:
        """Смотреть ли на инвентарь (только во время катки)."""
        if active:
            self._active.set()
        else:
            self._active.clear()

    def request_topbar(self) -> None:
        self._topbar_requested.set()

    def set_match_heroes(self, hero_keys: list[str]) -> None:
        """10 героев матча → кандидаты для надписи с именем. Вызывать в потоке интерфейса (рисует шрифтом)."""
        names = {}
        for key in hero_keys:
            hero = self.data.hero_by_name(key)
            if hero:
                names[key] = hero.localized
        self._names = NameMatcher(names)
        self._reader = None  # пересоберётся с новыми кандидатами

    # --- фоновый поток ---
    def _libraries(self, layout: HudLayout):
        folder = self.cache.root / "images"
        slot = layout.inventory_slots()[0]
        neutral = layout.neutral_slot()
        items = {p.stem: p for p in (folder / "items").glob("*.png") if not p.stem.startswith("recipe")}
        neutral_keys = {i.key for i in self.data.items.values() if i.tier} | {k for k in items if k.startswith("enhancement")}
        return (IconLibrary.from_files(items, slot.w, slot.h),
                IconLibrary.from_files({k: p for k, p in items.items() if k in neutral_keys}, neutral.w, neutral.h))

    def _run(self) -> None:
        import mss  # объект mss должен жить в том потоке, где снимает экран
        try:
            with mss.MSS() as screen:
                monitor = screen.monitors[1]  # основной экран
                layout = HudLayout(monitor["width"], monitor["height"], self.calibration)
                log.info("Зрение: экран %dx%d", monitor["width"], monitor["height"])
                top = layout.topbar_slots()[0]
                heroes = IconLibrary.from_files(
                    {p.stem: p for p in (self.cache.root / "images" / "heroes").glob("*.png")}, top.w, top.h)
                items, neutral = self._libraries(layout)
                while not self._stop.is_set():
                    if self._topbar_requested.is_set():
                        self._topbar_requested.clear()
                        self._scan_topbar(screen, monitor, layout, heroes)
                    if self._active.is_set() and self._names is not None:
                        if self._reader is None:
                            self._reader = InventoryReader(layout, items, neutral, self._names)
                        self._scan_inventory(screen, monitor, layout)
                        time.sleep(INVENTORY_INTERVAL_SEC)
                    else:
                        time.sleep(IDLE_SLEEP_SEC)
        except Exception:
            log.exception("Поток зрения упал, распознавание отключено до перезапуска")

    @staticmethod
    def _grab(screen, monitor, rect) -> np.ndarray:
        shot = screen.grab({"left": monitor["left"] + rect.x, "top": monitor["top"] + rect.y,
                            "width": rect.w, "height": rect.h})
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])  # BGRA → BGR

    def _scan_topbar(self, screen, monitor, layout, heroes) -> None:
        area = layout.topbar_area()
        frame = np.zeros((layout.height, layout.width, 3), dtype=np.uint8)
        frame[area.y:area.y + area.h, area.x:area.x + area.w] = self._grab(screen, monitor, area)
        self.topbar_ready.emit(recognize_topbar(frame, layout, heroes))

    def _scan_inventory(self, screen, monitor, layout) -> None:
        area = layout.inventory_area()
        snapshot = self._reader.read(self._grab(screen, monitor, area), area)
        if snapshot.hero:
            self.inventory_seen.emit(snapshot)
