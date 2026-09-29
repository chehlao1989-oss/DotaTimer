"""Автоопределение режима «Обычный / Турбо» по пассивному золоту из GSI.

GSI режим не сообщает, но в Турбо пассивное золото идёт вдвое быстрее.
По записям автора (28.09.2026): к 1:00 олпик — 91 золота, Турбо — 182; к 2:00 — 181 и 362.
То есть ~1.5 и ~3.0 золота в секунду. Порог посередине.
"""
from dataclasses import dataclass

from app.timers.timings import MODE_NORMAL, MODE_TURBO

MIN_CLOCK_SEC = 30  # раньше выборка слишком маленькая
MAX_CLOCK_SEC = 300  # позже уже не определяем: режим за катку не меняется
TURBO_THRESHOLD = 2.25  # золота в секунду


@dataclass
class ModeDetector:
    detected: str | None = None

    def reset(self) -> None:
        self.detected = None

    def update(self, clock: int | None, income_gold: int | None) -> str | None:
        """Возвращает режим один раз, когда он определился; иначе None."""
        if self.detected is not None or clock is None or income_gold is None:
            return None
        if not MIN_CLOCK_SEC <= clock <= MAX_CLOCK_SEC:
            return None
        self.detected = MODE_TURBO if income_gold / clock > TURBO_THRESHOLD else MODE_NORMAL
        return self.detected
