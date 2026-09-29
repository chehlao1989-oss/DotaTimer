"""Тесты чтения режима экрана Доты."""
import os
import time

from app.gsi.video import ScreenMode, find_video_file, parse_video, read_screen_mode, screen_mode

SAMPLE = '''"config"
{
	"setting.defaultres"		"2560"
	"setting.fullscreen"		"0"
	"setting.coop_fullscreen"		"0"
	"setting.nowindowborder"		"1"
}'''


def test_parse_and_modes():
    values = parse_video(SAMPLE)
    assert values["setting.fullscreen"] == "0"
    assert screen_mode(values) == ScreenMode.BORDERLESS
    assert screen_mode({"setting.fullscreen": "1"}) == ScreenMode.FULLSCREEN
    assert screen_mode({"setting.fullscreen": "0", "setting.nowindowborder": "0"}) == ScreenMode.WINDOWED
    assert screen_mode({}) == ScreenMode.UNKNOWN


def test_newest_account_file_is_used(tmp_path):
    old = tmp_path / "userdata" / "111" / "570" / "local" / "cfg" / "video.txt"
    new = tmp_path / "userdata" / "222" / "570" / "local" / "cfg" / "video.txt"
    for path, fullscreen in ((old, "0"), (new, "1")):
        path.parent.mkdir(parents=True)
        path.write_text(f'"setting.fullscreen" "{fullscreen}"', encoding="utf-8")
    past = time.time() - 1000
    os.utime(old, (past, past))
    assert find_video_file(tmp_path) == new
    assert read_screen_mode(tmp_path) == ScreenMode.FULLSCREEN


def test_missing_file_is_unknown(tmp_path):
    assert read_screen_mode(tmp_path) == ScreenMode.UNKNOWN
    assert read_screen_mode(None) == ScreenMode.UNKNOWN
