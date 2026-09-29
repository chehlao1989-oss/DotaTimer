"""Распознавание инвентаря выделенного героя в нижней панели HUD.

Пользователь сам кликает по портрету врага в верхней панели, игра показывает его
последний виденный инвентарь (даже в тумане). Программа только смотрит на экран.
Заново распознаются только изменившиеся ячейки, поэтому нагрузка почти нулевая.
"""
import logging
from dataclasses import dataclass

import cv2
import numpy as np

from app.vision.geometry import HudLayout, Rect
from app.vision.matcher import IconLibrary, is_empty_cell
from app.vision.names import NameMatcher

log = logging.getLogger(__name__)

# Пороги по скриншотам автора: верные предметы 0.76–0.96, случайные совпадения пустых ячеек ≤ 0.6
MIN_ITEM_SCORE = 0.7
MIN_ITEM_MARGIN = 0.03
CHANGE_THRESHOLD = 6.0  # средняя разница яркости ячейки, после которой распознаём заново
THUMB = (12, 9)


@dataclass(frozen=True)
class InventorySnapshot:
    hero: str | None  # ключ выделенного героя или None, если не поняли
    slots: tuple[str | None, ...]  # 6 основных слотов (None — пусто или не распознано)
    backpack: tuple[str | None, ...]
    neutral: str | None

    @property
    def items(self) -> set[str]:
        return {i for i in (*self.slots, *self.backpack, self.neutral) if i}


def crop(frame: np.ndarray, rect: Rect, origin: Rect | None = None) -> np.ndarray:
    """Вырезать rect из frame. origin — если frame уже вырезан из экрана по этому прямоугольнику."""
    ox, oy = (origin.x, origin.y) if origin else (0, 0)
    return frame[rect.y - oy:rect.y - oy + rect.h, rect.x - ox:rect.x - ox + rect.w]


class InventoryReader:
    def __init__(self, layout: HudLayout, items: IconLibrary, neutral_items: IconLibrary, names: NameMatcher):
        self.layout = layout
        self.items = items
        self.neutral_items = neutral_items
        self.names = names
        self.slot_rects = layout.inventory_slots()
        self.neutral_rect = layout.neutral_slot()
        self.name_rect = layout.name_box()
        self._cache: dict[int, tuple[np.ndarray, str | None]] = {}  # ячейка → (миниатюра, ответ)

    def _cell(self, index: int, image: np.ndarray, library: IconLibrary) -> str | None:
        thumb = cv2.resize(image, THUMB, interpolation=cv2.INTER_AREA).astype(np.float32)
        cached = self._cache.get(index)
        if cached is not None and float(np.abs(cached[0] - thumb).mean()) < CHANGE_THRESHOLD:
            return cached[1]
        if is_empty_cell(image):
            result = None
        else:
            match = library.match(image)
            ok = match.score >= MIN_ITEM_SCORE and match.score - match.runner_up >= MIN_ITEM_MARGIN
            result = match.key if ok else None
        self._cache[index] = (thumb, result)
        return result

    def read(self, area: np.ndarray, origin: Rect) -> InventorySnapshot:
        """area — захваченная область inventory_area() (BGR), origin — её положение на экране."""
        hero = self.names.match(crop(area, self.name_rect, origin), self.layout.scale)
        cells = [self._cell(i, crop(area, rect, origin), self.items) for i, rect in enumerate(self.slot_rects)]
        neutral = self._cell(len(self.slot_rects), crop(area, self.neutral_rect, origin), self.neutral_items)
        return InventorySnapshot(hero, tuple(cells[:6]), tuple(cells[6:9]), neutral)
