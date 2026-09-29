"""Ядро программы: связывает GSI, таймеры, уведомления, голос и оверлей."""
import logging
import time

from app.config import Settings
from app.gsi.models import parse_packet
from app.i18n import ru
from app.notify.manager import NotificationManager, load_presets
from app.notify.voice import VoicePlayer
from app.timers.engine import MatchTimers, TimerEngine
from app.timers.timings import EventSpec, load_timings
from app.ui.overlay import Overlay

log = logging.getLogger(__name__)

# Dota шлёт пакет минимум раз в 30 сек (heartbeat в GSI-конфиге), с запасом
CONNECTED_TIMEOUT_SEC = 35


class TimerApp:
    def __init__(self, settings: Settings, overlay: Overlay, voice: VoicePlayer):
        self.settings = settings
        self.overlay = overlay
        self.voice = voice
        self.presets = load_presets()
        self.muted = False
        self.last_clock: int | None = None
        self._last_packet_at: float | None = None
        self.specs: dict[str, EventSpec] = {}
        self.state_listeners = []  # кто ещё хочет получать каждый пакет (модуль угроз)
        self.extra_hotkeys = {}  # действие → функция (горячие клавиши модуля угроз)
        self.reload()

    def reload(self) -> None:
        """Пересобрать таймеры (смена режима Обычный/Турбо). Запущенные таймеры (терзатель) сохраняются."""
        old = getattr(self, "timers", None)
        old_triggers = old.engine.snapshot_triggers() if old else {}
        old_match = old.match_id if old else None
        self.specs = load_timings(self.settings.mode)
        self.manager = NotificationManager(self.specs, self.presets, self.settings.notify)
        engine = TimerEngine(self.specs, self.settings.notify.warn_overrides(self.specs))
        engine.restore_triggers(old_triggers)
        self.timers = MatchTimers(engine, lookahead=self.presets.merge_window_sec, on_new_match=self.manager.reset,
                                  deduper=old.deduper if old else None)
        self.timers.match_id = old_match
        log.info("Таймеры загружены, режим %s", self.settings.mode)

    def apply_settings(self) -> None:
        """Галочки и секунды поменялись: обновить движок без перезапуска."""
        self.timers.engine.warn_overrides = self.settings.notify.warn_overrides(self.specs)

    @property
    def connected(self) -> bool:
        return self._last_packet_at is not None and time.monotonic() - self._last_packet_at < CONNECTED_TIMEOUT_SEC

    def on_packet(self, data: dict) -> None:
        self._last_packet_at = time.monotonic()
        state = parse_packet(data)
        self.manager.push(self.timers.on_state(state))
        for listener in self.state_listeners:
            try:
                listener(state)
            except Exception:
                log.exception("Ошибка в обработчике состояния игры")
        if state.clock_time is None or not state.in_match or state.paused:
            return
        self.last_clock = state.clock_time
        result = self.manager.tick(state.clock_time, time.monotonic())
        if self.muted:
            return
        for message in result.texts:
            self.overlay.show_message(message.text, message.important)
        for key in result.voices:
            self.voice.play(key)

    def manual_trigger(self, name: str) -> bool:
        """Горячая клавиша «терзатель убит». False, если матч ещё не идёт."""
        if self.last_clock is None:
            return False
        self.timers.manual_trigger(name, self.last_clock)
        return True

    def say(self, text: str, important: bool, voice_key: str | None) -> None:
        """Сообщение вне расписания таймеров (угрозы): надпись всегда, голос — с учётом тишины и лимита."""
        if self.muted:
            return
        self.overlay.show_message(text, important)
        top = max(self.presets.priorities.values())
        if voice_key and self.manager.try_voice(top if important else 1, time.monotonic()):
            self.voice.play(voice_key)

    def silence(self) -> None:
        self.manager.silence(time.monotonic())

    def on_hotkey(self, action: str) -> None:
        """Горячая клавиша: запустить таймер или включить тишину, показать подтверждение."""
        if action in self.extra_hotkeys:
            self.extra_hotkeys[action]()
            return
        if action == "silence":
            self.silence()
        elif not self.manual_trigger(action):
            self.overlay.show_message(ru.HOTKEY_NO_MATCH)
            return
        self.overlay.show_message(ru.HOTKEY_FEEDBACK[action])
