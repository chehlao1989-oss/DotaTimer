"""Менеджер уведомлений (п. 5.4 ТЗ): пресеты, склейка, приоритеты, лимит голоса, тишина.

Как работает:
- движок таймеров вызывается с забеганием вперёд на окно склейки, поэтому менеджер
  заранее знает предупреждения ближайших секунд (push);
- tick(clock, now) выдаёт то, что пора показать и сказать. clock — игровые часы,
  now — время компьютера в секундах (для лимита голоса и тишины).
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.notify.messages import describe, is_first_power_rune
from app.timers.engine import Alert
from app.timers.timings import EventSpec

DEFAULT_PRESETS_PATH = Path(__file__).resolve().parents[2] / "data" / "presets.json"


@dataclass(frozen=True)
class PresetsConfig:
    presets: dict[str, tuple[str, ...]]
    default_preset: str
    priorities: dict[str, int]
    merge_window_sec: int
    voice_min_interval_sec: float
    silence_sec: float


def load_presets(path: Path = DEFAULT_PRESETS_PATH) -> PresetsConfig:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return PresetsConfig(
        presets={name: tuple(ids) for name, ids in raw["presets"].items()},
        default_preset=raw["default_preset"],
        priorities=dict(raw["priorities"]),
        merge_window_sec=raw["merge_window_sec"],
        voice_min_interval_sec=raw["voice_min_interval_sec"],
        silence_sec=raw["silence_sec"],
    )


@dataclass
class NotifySettings:
    """Выбор пользователя. Хранится в настройках (шаг 7)."""

    preset: str = "standard"
    enabled_overrides: dict[str, bool] = field(default_factory=dict)  # ручные галочки поверх пресета
    first_power_rune: bool = True  # первая активка работает в любом пресете, пока стоит эта галочка
    every_power_rune_30: bool = False  # предупреждать за 30 сек о каждой руне силы (п. 5.2.1)
    warn_seconds: dict[str, int] = field(default_factory=dict)  # «за сколько секунд» по событиям
    voice_min_interval_sec: float | None = None  # None = из presets.json

    def warn_overrides(self, specs: dict[str, EventSpec]) -> dict[str, tuple[int, ...]]:
        """Переопределения «за сколько секунд» для движка таймеров."""
        overrides = {event_id: (sec,) for event_id, sec in self.warn_seconds.items() if event_id in specs}
        if self.every_power_rune_30 and "power_rune" in specs:
            base = overrides.get("power_rune", specs["power_rune"].warn_before)
            overrides["power_rune"] = tuple(sorted(set(base) | {30}, reverse=True))
        return overrides


@dataclass(frozen=True)
class OverlayMessage:
    text: str
    important: bool = False  # высший приоритет: оверлей выделяет цветом


@dataclass(frozen=True)
class TickResult:
    texts: tuple[OverlayMessage, ...] = ()  # что показать на оверлее
    voices: tuple[str, ...] = ()  # ключи голосовых фраз, которые пора проиграть


@dataclass(frozen=True)
class _VoiceItem:
    key: str
    priority: int
    event_time: int


class NotificationManager:
    def __init__(self, specs: dict[str, EventSpec], config: PresetsConfig, settings: NotifySettings):
        self.specs = specs
        self.config = config
        self.settings = settings
        self._pending: list[Alert] = []
        self._voice_queue: list[_VoiceItem] = []
        self._last_voice_at: float | None = None
        self._silence_until: float = float("-inf")

    # --- настройки ---
    def enabled_events(self) -> set[str]:
        enabled = set(self.config.presets.get(self.settings.preset, ()))
        for event_id, on in self.settings.enabled_overrides.items():
            (enabled.add if on else enabled.discard)(event_id)
        return enabled

    def accepts(self, alert: Alert) -> bool:
        if is_first_power_rune(alert):
            return self.settings.first_power_rune
        return alert.event_id in self.enabled_events()

    def priority(self, alert: Alert) -> int:
        if is_first_power_rune(alert):
            return self.config.priorities.get("power_rune_first", self.config.priorities["default"])
        return self.config.priorities.get(alert.event_id, self.config.priorities["default"])

    @property
    def voice_interval(self) -> float:
        value = self.settings.voice_min_interval_sec
        return self.config.voice_min_interval_sec if value is None else value

    # --- управление ---
    def reset(self) -> None:
        """Новый матч: забыть всё запланированное."""
        self._pending.clear()
        self._voice_queue.clear()

    def silence(self, now: float) -> None:
        """Тишина: голос молчит silence_sec секунд, надписи показываются."""
        self._silence_until = now + self.config.silence_sec
        self._voice_queue.clear()

    def is_silenced(self, now: float) -> bool:
        return now < self._silence_until

    def push(self, alerts: list[Alert]) -> None:
        """Предупреждения от движка (могут быть на несколько секунд вперёд)."""
        for alert in alerts:
            if self.accepts(alert) and alert not in self._pending:
                self._pending.append(alert)
        self._pending.sort(key=lambda a: a.fire_time)

    # --- выдача ---
    def tick(self, clock: int, now: float) -> TickResult:
        texts = []
        window = self.config.merge_window_sec
        while self._pending and self._pending[0].fire_time <= clock:
            head = self._pending[0].fire_time
            group = [a for a in self._pending if a.fire_time <= head + window]
            self._pending = [a for a in self._pending if a not in group]
            if clock - head > window:
                continue  # сильно опоздало (например, после долгого переподключения)
            group.sort(key=lambda a: (-self.priority(a), a.fire_time, a.event_id))
            described = [describe(a, self.specs) for a in group]
            # голос у склеенной группы один: фраза самого важного события
            top_alert, (_, top_voice) = group[0], described[0]
            important = self.priority(top_alert) >= max(self.config.priorities.values())
            texts.append(OverlayMessage("\n".join(dict.fromkeys(text for text, _ in described)), important))
            if top_voice and not self.is_silenced(now):
                self._voice_queue.append(_VoiceItem(top_voice, self.priority(top_alert), top_alert.event_time))
        return TickResult(tuple(texts), self._next_voices(clock, now))

    def try_voice(self, priority: int, now: float) -> bool:
        """Можно ли сказать фразу вне расписания (подсказки угроз): тишина и лимит голоса соблюдаются."""
        if self.is_silenced(now):
            return False
        top_priority = max(self.config.priorities.values())
        if priority >= top_priority or self._last_voice_at is None or now - self._last_voice_at >= self.voice_interval:
            self._last_voice_at = now
            return True
        return False

    def _next_voices(self, clock: int, now: float) -> tuple[str, ...]:
        grace = self.config.merge_window_sec
        # устаревшие фразы (событие уже прошло) выбрасываем
        self._voice_queue = [v for v in self._voice_queue if clock <= v.event_time + grace]
        if not self._voice_queue or self.is_silenced(now):
            return ()
        self._voice_queue.sort(key=lambda v: (-v.priority, v.event_time))
        item = self._voice_queue[0]
        top_priority = max(self.config.priorities.values())
        limit_ok = self._last_voice_at is None or now - self._last_voice_at >= self.voice_interval
        if item.priority >= top_priority or limit_ok:
            self._voice_queue.pop(0)
            self._last_voice_at = now
            return (item.key,)
        return ()
