"""Глобальные горячие клавиши (работают, когда в фокусе Дота).

pynput только слушает клавиатуру, ничего не нажимает. Сочетания хранятся в виде
«Ctrl+Alt+R» (как их показывает Qt) и сравниваются по виртуальным кодам клавиш:
на Windows с зажатым Ctrl символ буквы приходит искажённым, а код клавиши надёжен.
"""
import logging

from pynput import keyboard
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

# действие → сочетание по умолчанию (п. 7.3 ТЗ; остальные клавиши появятся в фазе 2)
DEFAULT_BINDINGS = {
    "tormentor_killed": "Ctrl+Alt+T",
    "silence": "Ctrl+Alt+M",
    "show_threats": "Ctrl+Alt+H",
    "seen_item": "Ctrl+Alt+I",
    "pick_heroes": "Ctrl+Alt+P",
}

_MODIFIERS = {
    keyboard.Key.ctrl: "ctrl", keyboard.Key.ctrl_l: "ctrl", keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt: "alt", keyboard.Key.alt_l: "alt", keyboard.Key.alt_r: "alt", keyboard.Key.alt_gr: "alt",
    keyboard.Key.shift: "shift", keyboard.Key.shift_l: "shift", keyboard.Key.shift_r: "shift",
    keyboard.Key.cmd: "meta", keyboard.Key.cmd_l: "meta", keyboard.Key.cmd_r: "meta",
}
_MOD_NAMES = {"ctrl": "ctrl", "alt": "alt", "shift": "shift", "meta": "meta"}
VK_F1 = 0x70
MAX_F_KEY = 24


def parse_binding(text: str) -> tuple[frozenset[str], int] | None:
    """«Ctrl+Alt+R» → ({"ctrl", "alt"}, код клавиши R). None, если сочетание не подходит."""
    parts = [p.strip() for p in (text or "").split("+") if p.strip()]
    if len(parts) < 2:
        return None  # одиночные клавиши мешали бы играть
    *mods, key = parts
    mod_set = set()
    for mod in mods:
        name = _MOD_NAMES.get(mod.lower())
        if name is None:
            return None
        mod_set.add(name)
    if len(key) == 1 and key.isalnum() and key.isascii():
        vk = ord(key.upper())
    elif key.upper().startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= MAX_F_KEY:
        vk = VK_F1 + int(key[1:]) - 1
    else:
        return None
    return frozenset(mod_set), vk


def key_vk(key) -> int | None:
    """Виртуальный код клавиши из события pynput."""
    if isinstance(key, keyboard.KeyCode):
        return key.vk
    value = getattr(key, "value", None)
    return getattr(value, "vk", None)


class HotkeyMatcher:
    """Следит за зажатыми модификаторами и сообщает, какое действие нажато."""

    def __init__(self, bindings: dict[str, str]):
        self._pressed: set[str] = set()
        self._combos: dict[tuple[frozenset[str], int], str] = {}
        for action, text in bindings.items():
            parsed = parse_binding(text)
            if parsed is None:
                log.warning("Не понял сочетание %r для %s", text, action)
                continue
            self._combos[parsed] = action

    def press(self, key) -> str | None:
        mod = _MODIFIERS.get(key)
        if mod:
            self._pressed.add(mod)
            return None
        vk = key_vk(key)
        if vk is None:
            return None
        return self._combos.get((frozenset(self._pressed), vk))

    def release(self, key) -> None:
        mod = _MODIFIERS.get(key)
        if mod:
            self._pressed.discard(mod)


class HotkeyManager(QObject):
    """Слушатель клавиатуры в отдельном потоке; сигнал triggered приходит в поток интерфейса."""

    triggered = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._listener: keyboard.Listener | None = None

    def start(self, bindings: dict[str, str]) -> None:
        self.stop()
        matcher = HotkeyMatcher(bindings)

        def on_press(key):
            action = matcher.press(key)
            if action:
                log.info("Горячая клавиша: %s", action)
                self.triggered.emit(action)

        self._listener = keyboard.Listener(on_press=on_press, on_release=matcher.release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
