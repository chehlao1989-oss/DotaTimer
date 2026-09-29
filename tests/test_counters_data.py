"""Проверка data/counters.json: формат и что все герои и предметы существуют в данных OpenDota."""
import pytest

from app import paths
from app.threats.counters import ROLE_ANY, ROLE_CORE, ROLE_SUPPORT, load_counters
from app.threats.data import DataCache, GameData

TABLE = load_counters()


def test_format():
    assert len(TABLE.rules) >= 10
    pairs = sum(len(r.counters) * max(1, len(r.trigger_items) + len(r.trigger_heroes)) for r in TABLE.rules)
    assert pairs >= 30  # ТЗ: минимум 30 ключевых случаев
    for rule in TABLE.rules:
        assert rule.title_ru and rule.counters and rule.checked_patch
        for counter in rule.counters:
            assert counter.why_ru
            assert set(counter.roles) <= {ROLE_CORE, ROLE_SUPPORT, ROLE_ANY}


def test_every_role_gets_advice_for_every_rule():
    """Для каждой угрозы и кору, и саппорту найдётся хоть один совет (иначе подсказка будет пустой)."""
    for rule in TABLE.rules:
        for role in (ROLE_CORE, ROLE_SUPPORT):
            if rule.id in ("strong_passives", "mana", "saves"):
                continue  # эти контры по смыслу только для коров
            assert TABLE.pick_counters([rule], role), (rule.id, role)


@pytest.mark.skipif(not (paths.cache_dir() / "heroes.json").exists(), reason="нет кеша OpenDota")
def test_keys_exist_in_opendota():
    data = GameData.from_cache(DataCache())
    heroes = {h.name for h in data.heroes.values()}
    for rule in TABLE.rules:
        assert rule.trigger_heroes <= heroes, (rule.id, rule.trigger_heroes - heroes)
        items = rule.trigger_items | {c.item for c in rule.counters}
        assert items <= set(data.items), (rule.id, items - set(data.items))
    assert set(TABLE.heroes) <= heroes
