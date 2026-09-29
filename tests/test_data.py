"""Тесты игровых данных и кеша (без сети, подставной клиент)."""
import json

from app.threats.data import DataCache, DataUpdater, GameData
from app.threats.opendota import OpenDotaError

HEROES = {"4": {"id": 4, "name": "npc_dota_hero_bloodseeker", "localized_name": "Bloodseeker",
                "roles": ["Carry"], "primary_attr": "agi", "img": "/heroes/bloodseeker.png?"}}
ITEMS = {
    "heart": {"id": 114, "dname": "Heart of Tarrasque", "cost": 5100, "qual": "epic",
              "components": ["reaver", "ring_of_tarrasque"], "img": "/items/heart.png?t=1"},
    "tango": {"id": 44, "dname": "Tango", "cost": 90, "qual": "consumable", "components": None, "img": "/items/tango.png"},
}


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def _answer(self, name, value):
        self.calls.append(name)
        if self.fail:
            raise OpenDotaError("нет сети")
        return value

    def heroes(self):
        return self._answer("heroes", HEROES)

    def items(self):
        return self._answer("items", ITEMS)

    def hero_stats(self):
        return self._answer("hero_stats", [{"id": 4, "5_pick": 100, "5_win": 54}])

    def matchups(self, hero_id):
        return self._answer(f"matchups/{hero_id}", [{"hero_id": 1, "games_played": 50, "wins": 20}])

    def download(self, url):
        self.calls.append(url)
        if self.fail:
            raise OpenDotaError("нет сети")
        return b"png"

    def get_json(self, path):
        return self._answer(path.rsplit("/", 1)[-1], {})

    def player(self, account_id):
        return self._answer("player", {"rank_tier": 54})


def test_refresh_and_load_from_cache(tmp_path):
    cache = DataCache(tmp_path)
    updater = DataUpdater(cache, FakeClient())
    updater.refresh_core()
    data = GameData.from_cache(cache)
    assert data.ready
    assert data.heroes[4].localized == "Bloodseeker"
    assert data.hero_by_name("npc_dota_hero_bloodseeker").id == 4
    assert data.items["heart"].components == ("reaver", "ring_of_tarrasque")
    assert data.hero_stats[4]["5_win"] == 54


def test_fresh_cache_is_not_downloaded_again(tmp_path):
    cache = DataCache(tmp_path)
    DataUpdater(cache, FakeClient()).refresh_core()
    client = FakeClient()
    DataUpdater(cache, client).refresh_core()
    assert client.calls == []


def test_no_network_keeps_old_cache(tmp_path):
    cache = DataCache(tmp_path)
    DataUpdater(cache, FakeClient()).refresh_core()
    for name in ("heroes", "items", "abilities", "hero_abilities", "hero_stats"):  # делаем кеш устаревшим
        path = cache._path(name)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["fetched_at"] = 0
        path.write_text(json.dumps(raw), encoding="utf-8")
    client = FakeClient(fail=True)
    DataUpdater(cache, client).refresh_core()
    assert client.calls == ["heroes", "items", "abilities", "hero_abilities", "hero_stats"]  # пытался обновить
    assert GameData.from_cache(cache).ready


def test_empty_cache_without_network(tmp_path):
    cache = DataCache(tmp_path)
    DataUpdater(cache, FakeClient(fail=True)).refresh_core()
    assert not GameData.from_cache(cache).ready


def test_images_and_matchups(tmp_path):
    cache = DataCache(tmp_path)
    client = FakeClient()
    updater = DataUpdater(cache, client)
    updater.refresh_core()
    data = GameData.from_cache(cache)
    assert updater.refresh_images(data) == 3
    assert updater.refresh_images(data) == 0  # уже скачаны
    updater.refresh_matchups(data)
    assert GameData.from_cache(cache).matchups[4][0]["games_played"] == 50
    assert updater.fetch_rank("1") == 54
