"""Загрузка игровых таймингов из data/timings.json.

Все игровые числа живут только в JSON. Здесь они читаются, проверяются
и для режима Turbo накладываются переопределения из раздела "turbo".

Виды событий (поле "kind"):
- "periodic": первое появление "first", дальше каждые "interval" сек (до "until", если задан);
- "at_times": фиксированные моменты "times";
- "after_trigger": отсчёт от сигнала "trigger" (событие GSI или горячая клавиша),
  моменты "delays" (сек после сигнала).
Время везде в секундах по игровым часам (map.clock_time).
"""
import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "timings.json"

MODE_NORMAL = "normal"
MODE_TURBO = "turbo"

KINDS = ("periodic", "at_times", "after_trigger")
REQUIRED_META = ("source", "checked_patch")


class TimingsError(ValueError):
    """Ошибка в файле таймингов."""


@dataclass(frozen=True)
class EventSpec:
    """Описание одного игрового события."""

    id: str
    kind: str
    warn_before: tuple[int, ...]
    first_warn_before: tuple[int, ...] = ()
    first: int | None = None
    interval: int | None = None
    until: int | None = None
    times: tuple[int, ...] = ()
    trigger: str | None = None
    delays: tuple[int, ...] = ()
    source: str = ""
    checked_patch: str = ""
    verified: bool = True
    extra: dict = field(default_factory=dict)


_KNOWN_KEYS = {
    "kind", "warn_before", "first_warn_before", "first", "interval", "until",
    "times", "trigger", "delays", "source", "checked_patch", "verified",
}


def _build_spec(event_id: str, raw: dict) -> EventSpec:
    kind = raw.get("kind")
    if kind not in KINDS:
        raise TimingsError(f"{event_id}: неизвестный kind {kind!r}")
    for key in REQUIRED_META:
        if not raw.get(key):
            raise TimingsError(f"{event_id}: нет поля {key}")
    if kind == "periodic" and (raw.get("first") is None or not raw.get("interval")):
        raise TimingsError(f"{event_id}: для periodic нужны first и interval")
    if kind == "at_times" and not raw.get("times"):
        raise TimingsError(f"{event_id}: для at_times нужен список times")
    if kind == "after_trigger" and (not raw.get("trigger") or not raw.get("delays")):
        raise TimingsError(f"{event_id}: для after_trigger нужны trigger и delays")
    return EventSpec(
        id=event_id,
        kind=kind,
        warn_before=tuple(raw.get("warn_before", ())),
        first_warn_before=tuple(raw.get("first_warn_before", ())),
        first=raw.get("first"),
        interval=raw.get("interval"),
        until=raw.get("until"),
        times=tuple(raw.get("times", ())),
        trigger=raw.get("trigger"),
        delays=tuple(raw.get("delays", ())),
        source=raw["source"],
        checked_patch=raw["checked_patch"],
        verified=raw.get("verified", True),
        extra={k: v for k, v in raw.items() if k not in _KNOWN_KEYS},
    )


def parse_timings(data: dict, mode: str = MODE_NORMAL) -> dict[str, EventSpec]:
    """Разбирает содержимое timings.json для нужного режима."""
    events = copy.deepcopy(data.get("events") or {})
    if not events:
        raise TimingsError("в файле нет раздела events")
    if mode == MODE_TURBO:
        for event_id, override in (data.get("turbo") or {}).items():
            if event_id not in events:
                raise TimingsError(f"turbo: событие {event_id} не описано в events")
            events[event_id].update(override)
    elif mode != MODE_NORMAL:
        raise TimingsError(f"неизвестный режим {mode!r}")
    return {event_id: _build_spec(event_id, raw) for event_id, raw in events.items()}


def load_timings(mode: str = MODE_NORMAL, path: Path = DEFAULT_PATH) -> dict[str, EventSpec]:
    """Читает data/timings.json."""
    with open(path, encoding="utf-8") as f:
        return parse_timings(json.load(f), mode)
