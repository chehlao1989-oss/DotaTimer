"""Тесты разбора и распознавания горячих клавиш (без настоящей клавиатуры)."""
from pynput.keyboard import Key, KeyCode

from app.hotkeys import DEFAULT_BINDINGS, VK_F1, HotkeyMatcher, parse_binding


def test_parse_binding():
    assert parse_binding("Ctrl+Alt+R") == (frozenset({"ctrl", "alt"}), ord("R"))
    assert parse_binding("Shift+F5") == (frozenset({"shift"}), VK_F1 + 4)
    assert parse_binding("R") is None  # без модификатора нельзя
    assert parse_binding("Ctrl+Space") is None
    assert parse_binding("") is None


def test_defaults_are_valid():
    assert all(parse_binding(text) for text in DEFAULT_BINDINGS.values())


def test_matcher_detects_combo_by_vk():
    matcher = HotkeyMatcher(DEFAULT_BINDINGS)
    matcher.press(Key.ctrl_l)
    matcher.press(Key.alt_l)
    # с зажатым Ctrl Windows присылает искажённый символ, но правильный код клавиши
    assert matcher.press(KeyCode(vk=ord("T"), char="\x14")) == "tormentor_killed"
    matcher.release(Key.alt_l)
    assert matcher.press(KeyCode(vk=ord("T"))) is None  # остался только Ctrl


def test_matcher_ignores_extra_modifiers():
    matcher = HotkeyMatcher(DEFAULT_BINDINGS)
    for key in (Key.ctrl_l, Key.alt_l, Key.shift):
        matcher.press(key)
    assert matcher.press(KeyCode(vk=ord("T"))) is None
