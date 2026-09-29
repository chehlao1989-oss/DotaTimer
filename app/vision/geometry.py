"""Разметка HUD Доты: где на экране верхняя панель, инвентарь и имя выделенного героя.

Все прямоугольники заданы в «эталонном» экране 1920×1080. HUD Доты стоит по центру
и масштабируется по высоте экрана, поэтому для любого разрешения:
    scale = высота / 1080,  сдвиг по x = (ширина − 1920·scale) / 2.
Замерено по скриншотам автора 2560×1080 (21:9) 29.09.2026. Для нестандартных
экранов есть калибровка: сдвиг и масштаб правятся в настройках.
"""
from dataclasses import dataclass

REF_W, REF_H = 1920, 1080

# Верхняя панель: 5 портретов Света слева и 5 портретов Тьмы справа
TOPBAR_RADIANT_X = 545
TOPBAR_DIRE_X = 1062
TOPBAR_PITCH = 63
TOPBAR_SLOT_W, TOPBAR_Y, TOPBAR_SLOT_H = 58, 4, 32

# Инвентарь выделенного героя: 3×2 слота, ряд рюкзака, нейтралка
INV_X, INV_Y = 1142, 943
INV_PITCH_X, INV_PITCH_Y = 65.5, 48.5
INV_SLOT_W, INV_SLOT_H = 58, 43
BACKPACK_ROW = 2
NEUTRAL_BOX = (1343, 977, 40, 40)  # круглая ячейка нейтралки, берём вписанный квадрат

# Имя выделенного героя над портретом
NAME_BOX = (555, 917, 150, 20)


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Calibration:
    """Ручная поправка для экранов, где HUD стоит не по стандарту."""

    dx: int = 0
    dy: int = 0
    scale: float = 1.0


class HudLayout:
    def __init__(self, width: int, height: int, calibration: Calibration = Calibration()):
        self.width, self.height = width, height
        self.scale = height / REF_H * calibration.scale
        self.offset_x = (width - REF_W * self.scale) / 2 + calibration.dx
        self.offset_y = calibration.dy

    def to_screen(self, x: float, y: float, w: float, h: float) -> Rect:
        s = self.scale
        return Rect(round(self.offset_x + x * s), round(self.offset_y + y * s), round(w * s), round(h * s))

    def topbar_slots(self) -> list[Rect]:
        """10 портретов: первые 5 — Свет (слева), следующие 5 — Тьма (справа)."""
        slots = []
        for start in (TOPBAR_RADIANT_X, TOPBAR_DIRE_X):
            for i in range(5):
                slots.append(self.to_screen(start + i * TOPBAR_PITCH, TOPBAR_Y, TOPBAR_SLOT_W, TOPBAR_SLOT_H))
        return slots

    def inventory_slots(self) -> list[Rect]:
        """9 слотов: 6 основных (слева направо, сверху вниз) и 3 слота рюкзака."""
        slots = []
        for row in range(3):
            for col in range(3):
                slots.append(self.to_screen(INV_X + col * INV_PITCH_X, INV_Y + row * INV_PITCH_Y,
                                            INV_SLOT_W, INV_SLOT_H))
        return slots

    def neutral_slot(self) -> Rect:
        return self.to_screen(*NEUTRAL_BOX)

    def name_box(self) -> Rect:
        return self.to_screen(*NAME_BOX)

    def inventory_area(self) -> Rect:
        """Общий прямоугольник для захвата: имя героя + инвентарь + нейтралка (захватываем только его)."""
        name = self.name_box()
        slots = self.inventory_slots()
        neutral = self.neutral_slot()
        right = max(neutral.x + neutral.w, slots[-1].x + slots[-1].w)
        bottom = min(self.height, max(s.y + s.h for s in slots))
        return Rect(name.x, name.y, right - name.x, bottom - name.y)

    def topbar_area(self) -> Rect:
        slots = self.topbar_slots()
        return Rect(slots[0].x, slots[0].y, slots[-1].x + slots[-1].w - slots[0].x, slots[0].h)
