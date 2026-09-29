"""Движок таймеров: по игровым часам решает, какие предупреждения пора выдать.

Движок ничего не знает о настройках и голосе: он выдаёт все предупреждения
из timings.json, а фильтрует их по пресетам менеджер уведомлений.
"""
import logging
from dataclasses import dataclass

from app.gsi.models import GSI_TRIGGERS, EventDeduper, GameState
from app.timers.timings import EventSpec

log = logging.getLogger(__name__)

# Если часы прыгнули вперёд (переподключение, запуск программы посреди матча),
# старые предупреждения не выдаём: только те, что опоздали не больше чем на столько секунд.
MAX_LATE_SEC = 3

# Повторный сигнал того же вида ближе этого окна считается дублем
# (например, дважды нажали Ctrl+Alt+T после одного убийства терзателя).
TRIGGER_DEDUP_SEC = 60


@dataclass(frozen=True)
class Alert:
    """Одно предупреждение: событие event_id наступит в event_time, до него warn_before сек."""

    event_id: str
    event_time: int
    warn_before: int
    index: int = 0  # номер появления (0 = первое) или номер задержки у after_trigger

    @property
    def fire_time(self) -> int:
        return self.event_time - self.warn_before


def _periodic_times(spec: EventSpec, lo: int, hi: int):
    """Моменты периодического события в полуинтервале (lo, hi] с их номерами."""
    if hi < spec.first:
        return
    k = max(0, (lo - spec.first) // spec.interval + 1)
    while True:
        t = spec.first + k * spec.interval
        if t > hi or (spec.until is not None and t > spec.until):
            return
        if t > lo:
            yield k, t
        k += 1


class TimerEngine:
    def __init__(self, specs: dict[str, EventSpec], warn_overrides: dict[str, tuple[int, ...]] | None = None):
        self.specs = specs
        self.warn_overrides = warn_overrides or {}
        self._last_clock: int | None = None
        self._triggers: dict[str, list[int]] = {}

    def reset(self) -> None:
        self._last_clock = None
        self._triggers.clear()

    def _warns(self, spec: EventSpec, index: int) -> tuple[int, ...]:
        if index == 0 and spec.first_warn_before and spec.kind != "after_trigger":
            return spec.first_warn_before
        return self.warn_overrides.get(spec.id, spec.warn_before)

    def snapshot_triggers(self) -> dict[str, list[int]]:
        """Копия запущенных сигналов (чтобы пересобрать движок, не потеряв таймер терзателя)."""
        return {name: list(times) for name, times in self._triggers.items()}

    def restore_triggers(self, triggers: dict[str, list[int]]) -> None:
        for name, times in triggers.items():
            for clock in times:
                self.trigger(name, clock)

    def trigger(self, name: str, clock: int) -> None:
        """Сигнал (событие GSI или горячая клавиша) в момент clock по игровым часам."""
        times = self._triggers.setdefault(name, [])
        if any(abs(clock - t) < TRIGGER_DEDUP_SEC for t in times):
            log.info("Сигнал %s в %d пропущен как повтор", name, clock)
            return
        times.append(clock)
        log.info("Сигнал %s в %d", name, clock)

    def _occurrences(self, spec: EventSpec, lo: int, hi: int):
        """(номер, время события) в полуинтервале (lo, hi]."""
        if spec.kind == "periodic":
            yield from _periodic_times(spec, lo, hi)
        elif spec.kind == "at_times":
            for i, t in enumerate(spec.times):
                if lo < t <= hi:
                    yield i, t
        elif spec.kind == "after_trigger":
            for start in self._triggers.get(spec.trigger, ()):
                for i, delay in enumerate(spec.delays):
                    t = start + delay
                    if lo < t <= hi:
                        yield i, t

    def update(self, clock: int) -> list[Alert]:
        """Игровые часы сдвинулись до clock. Возвращает предупреждения, которым пора сработать."""
        if self._last_clock is None or clock < self._last_clock:
            # первый пакет или часы пошли назад (новый матч): начинаем с текущего момента
            self._last_clock = clock - 1
        lo = max(self._last_clock, clock - MAX_LATE_SEC - 1)
        self._last_clock = clock
        if clock <= lo:
            return []
        alerts = []
        for spec in self.specs.values():
            # предупреждение за w секунд срабатывает в t - w, поэтому ищем события в (lo + w, clock + w]
            max_warn = max(self._warns(spec, 0) + self._warns(spec, 1) + (0,))
            for index, t in self._occurrences(spec, lo, clock + max_warn):
                for w in self._warns(spec, index):
                    if lo < t - w <= clock:
                        alerts.append(Alert(spec.id, t, w, index))
        alerts.sort(key=lambda a: (a.fire_time, a.event_id))
        return alerts


class MatchTimers:
    """Связка GSI → движок: следит за матчем, паузой и событиями."""

    def __init__(self, engine: TimerEngine, lookahead: int = 0, on_new_match=None,
                 deduper: EventDeduper | None = None):
        """lookahead: на сколько секунд вперёд выдавать предупреждения (окно склейки менеджера).
        on_new_match: вызывается при смене матча (например, чтобы очистить очередь уведомлений).
        """
        self.engine = engine
        self.lookahead = lookahead
        self.on_new_match = on_new_match
        self.deduper = deduper or EventDeduper()
        self.match_id: str | None = None

    def on_state(self, state: GameState) -> list[Alert]:
        if state.match_id and state.match_id != self.match_id:
            log.info("Новый матч %s", state.match_id)
            self.match_id = state.match_id
            self.engine.reset()
            if self.on_new_match:
                self.on_new_match()
        for event in self.deduper.new_events(state):
            name = GSI_TRIGGERS.get(event.event_type)
            if name:
                self.engine.trigger(name, event.clock_time)
        if not state.in_match or state.paused or state.clock_time is None:
            return []
        return self.engine.update(state.clock_time + self.lookahead)

    def manual_trigger(self, name: str, clock: int) -> None:
        """Горячая клавиша: «терзатель убит»."""
        self.engine.trigger(name, clock)
