"""Тесты поиска Доты и установки GSI-конфига."""
from pathlib import Path

from app.gsi import installer


def test_build_cfg_contains_all_blocks_and_token():
    cfg = installer.build_cfg("http://127.0.0.1:1234/", "secret")
    assert '"uri"        "http://127.0.0.1:1234/"' in cfg
    assert '"token"  "secret"' in cfg
    for block in installer.DATA_BLOCKS:
        assert f'"{block}"  "1"' in cfg


def test_parse_library_folders():
    text = '''"libraryfolders"
{
    "0" { "path"  "C:\\\\Program Files (x86)\\\\Steam" "apps" { "570" "1" } }
    "1" { "path"  "D:\\\\SteamLibrary" }
}'''
    assert installer.parse_library_folders(text) == [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"D:\SteamLibrary"),
    ]


def test_find_dota_in_second_library(tmp_path):
    steam = tmp_path / "Steam"
    lib2 = tmp_path / "Lib2"
    (steam / "steamapps").mkdir(parents=True)
    vdf_path = str(lib2).replace("\\", "\\\\")
    (steam / "steamapps" / "libraryfolders.vdf").write_text(f'"path" "{vdf_path}"', encoding="utf-8")
    dota = lib2 / "steamapps" / "common" / installer.DOTA_FOLDER_NAME
    (dota / "game" / "dota").mkdir(parents=True)
    assert installer.find_dota_dir(steam) == dota


def test_find_dota_missing(tmp_path):
    assert installer.find_dota_dir(tmp_path) is None


def test_normalize_manually_chosen_folder(tmp_path):
    dota = tmp_path / installer.DOTA_FOLDER_NAME
    (dota / "game" / "dota" / "cfg").mkdir(parents=True)
    assert installer.normalize_dota_dir(dota) == dota
    assert installer.normalize_dota_dir(dota / "game" / "dota" / "cfg") == dota
    assert installer.normalize_dota_dir(tmp_path) is None


def test_install_cfg_is_idempotent(tmp_path):
    target, changed = installer.install_cfg(tmp_path)
    assert changed and target.exists()
    assert target.parent == installer.gsi_cfg_dir(tmp_path)
    _, changed_again = installer.install_cfg(tmp_path)
    assert not changed_again
