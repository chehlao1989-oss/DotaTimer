"""Тексты для оверлея и ключи голосовых фраз по предупреждениям движка.

Сами строки лежат в app/i18n/ru.py, здесь только выбор нужной.
"""
from app.i18n import ru
from app.timers.engine import Alert
from app.timers.timings import EventSpec


def is_first_power_rune(alert: Alert) -> bool:
    return alert.event_id == "power_rune" and alert.index == 0


def _is_night_at(spec: EventSpec, clock: int) -> bool:
    """Ночь ли в момент clock (цикл из day_night: день с 0:00, смена каждые interval сек)."""
    return (clock // spec.interval) % 2 == 1


def describe(alert: Alert, specs: dict[str, EventSpec]) -> tuple[str, str | None]:
    """(текст для оверлея, ключ голосовой фразы) для одного предупреждения."""
    e, w = alert.event_id, alert.warn_before
    spec = specs[e]
    if e == "power_rune":
        if alert.index == 0 and w >= 30:
            return ru.MSG_FIRST_POWER_RUNE_EARLY.format(time=ru.format_clock(alert.event_time)), "power_rune_30"
        return ru.MSG_POWER_RUNE.format(sec=w), ("power_rune_30" if w >= 30 else "power_rune")
    if e == "day_night":
        if _is_night_at(spec, alert.event_time):
            return ru.MSG_NIGHT.format(sec=w), "night"
        return ru.MSG_DAY.format(sec=w), "day"
    if e == "tormentor_spawn" or e == "tormentor_respawn":
        night = _is_night_at(specs["day_night"], alert.event_time) if "day_night" in specs else False
        side_spec = specs.get("tormentor_spawn", spec)
        side = side_spec.extra.get("night_side" if night else "day_side", "")
        return ru.MSG_TORMENTOR.format(side=ru.TORMENTOR_SIDES.get(side, "")), "tormentor"
    if e == "neutral_tier":
        return ru.MSG_NEUTRAL_TIER.format(tier=alert.index + 2), "neutral"
    text = ru.MSG_SIMPLE[e].format(sec=w)
    return text, ru.VOICE_KEY_BY_EVENT.get(e)
