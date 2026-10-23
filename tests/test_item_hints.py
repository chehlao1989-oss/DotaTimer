"""Подсказки v2 (docs/TASK_ITEM_HINTS_V2.md, BUGLOG №54): ответы — из таблицы механик и страниц Counters вики,
выбор 1–2 — по доле покупки на моём герое; предметы без подтверждённого свойства подсказок не дают."""
import json
from pathlib import Path

from app.threats.item_hints import WIKI_HERO, Counter, CounterTable, choose
from app.threats.mechanics import load_mechanics_config

DATA = Path(__file__).resolve().parent.parent / "data"


def table() -> CounterTable:
    return CounterTable.load(load_mechanics_config())


def test_table_is_consistent_with_rules_and_checked():
    """Свойства и типы ответа — из утверждённых правил mechanics.json; проверка по вики покрывает каждую строку
    (таблицу поправили, а tools/check_item_counters.py не перезапустили — тест падает)."""
    raw = json.loads((DATA / "item_counters.json").read_text(encoding="utf-8"))
    checked = json.loads((DATA / "item_counters_checked.json").read_text(encoding="utf-8"))
    rules = load_mechanics_config()["rules"]
    props, answers = {r["enemy"] for r in rules}, {a for r in rules for a in r["answers"]}
    for item, spec in raw["threat_items"].items():
        assert set(spec["props"]) <= props, item
        assert set(checked["threat_items"][item]) == set(spec["props"]), item
    for answer, keys in raw["counters"].items():
        assert answer in answers
        assert set(checked["counters"][answer]) == set(keys), answer
    for rows in [*checked["threat_items"].values(), *checked["counters"].values()]:
        for row in rows.values():
            assert not row["verified"] or row["source"].startswith("https://dota2.fandom.com/wiki/")


def test_cheap_items_never_hint():
    """Magic Wand, Magic Stick, Quelling Blade, Helm of Iron Will, Tango — не в таблице: подсказки нет никогда
    (по описаниям Valve им доставались «лечение», «урон», «броня», логи 16.10)."""
    t = table()
    for item in ("magic_wand", "magic_stick", "quelling_blade", "helm_of_iron_will", "tango", "boots", "bottle"):
        assert not t.is_threat(item)
        assert t.vs_item(item) == []


def test_real_items_have_answers():
    t = table()
    assert {c.item for c in t.vs_item("butterfly")} >= {"monkey_king_bar", "bloodthorn"}
    assert {c.item for c in t.vs_item("heart")} >= {"skadi", "spirit_vessel", "shivas_guard"}
    assert {c.item for c in t.vs_item("silver_edge")} >= {"dust", "ward_sentry", "gem"}
    assert t.vs_hero("phantom_assassin")  # страница Counters вики


def test_rule16_slow_items_get_dispel_answers(qapp):
    """Правило 16 (автор 16.10): Eye of Skadi, Diffusal Blade замедляют → BKB / Manta / Lotus, «снимает замедление»."""
    t = table()
    for item in ("skadi", "diffusal_blade"):
        assert t.props(item) == ("slow",)
        assert {"black_king_bar", "manta", "lotus_orb"} <= {c.item for c in t.vs_item(item)}
    picked = choose(t.vs_item("skadi"), {"black_king_bar": 58.4}.get, lambda k: False, 2.0)
    assert [(c.item, c.prop) for c in picked] == [("black_king_bar", "slow")]


def test_unverified_row_is_not_shown():
    """Строка без подтверждения на вики (Solar Crest — снижение брони) в ответы не попадает."""
    t = table()
    assert "solar_crest" not in {c.item for c in t.vs_item("assault") if c.answer == "armor_reduction"}


def test_choose_by_own_buy_share():
    counters = [Counter("monkey_king_bar", "true_strike", "evasion"), Counter("bloodthorn", "true_strike", "evasion")]
    shares = {"monkey_king_bar": 4.0, "bloodthorn": 12.0}
    picked = choose(counters, shares.get, lambda k: False, 2.0)
    assert [c.item for c in picked] == ["bloodthorn", "monkey_king_bar"] and not picked[0].rare


def test_choose_skips_owned_and_falls_back_to_universal():
    """Купленное пропускается; ни один не берут от 2% — самый универсальный с пометкой «редко»."""
    counters = [Counter("bfury", "multi_target", "illusions"), Counter("mjollnir", "multi_target", "illusions"),
                Counter("mjollnir", "illusion_kill", "summons"), Counter("radiance", "multi_target", "illusions")]
    picked = choose(counters, {"bfury": 0.5}.get, lambda k: k == "radiance", 2.0)
    assert [(c.item, c.rare) for c in picked] == [("mjollnir", True)]
    assert choose(counters, {}.get, lambda k: True, 2.0) == []


def test_hero_counters_from_wiki():
    t = table()
    assert all(c.answer == WIKI_HERO for c in t.vs_hero("axe"))


def test_consumables_not_advised_vs_hero():
    """Town Portal Scroll со страницы Counters (Razor) не советуем: он у всех и так; Dust/Sentry/Gem — можно."""
    t = CounterTable.load(load_mechanics_config(), consumables={"tpscroll", "dust", "ward_sentry", "tango"})
    assert "tpscroll" not in {c.item for c in t.vs_hero("razor")}
    assert "tpscroll" in {c.item for c in table().vs_hero("razor")}  # на странице вики он есть
    assert {"dust", "ward_sentry"} <= {c.item for c in t.vs_item("invis_sword")}


def test_controller_hint_text(qapp):
    """Подсказка: «У Phantom Assassin появился Butterfly → Bloodthorn (пробивает уклонение)»; на Magic Wand — нет."""
    from app.threats.controller import ThreatsController, ThreatSettings
    from app.threats.data import DataCache, GameData
    from app.threats.mechanics import MechanicsTagger
    from app.threats.scoring import load_threat_config
    from app.threats.tracker import KIND_ITEM, ItemHint
    data = GameData.from_cache(DataCache())
    if not data.ready:  # нет кеша OpenDota на машине — проверка логики выше
        return
    tagger = MechanicsTagger(load_mechanics_config(), {}, {}, {})
    controller = ThreatsController(data, tagger, None, load_threat_config(), ThreatSettings(), lambda *a, **k: None,
                                   lambda: False)
    controller.use_counter_table(table(), lambda: {"bloodthorn": 9.0, "monkey_king_bar": 3.0}, 2.0, 2)
    text = controller.hint_text(ItemHint(KIND_ITEM, "phantom_assassin", "butterfly", None, ()))
    assert "Bloodthorn (пробивает уклонение), Monkey King Bar (пробивает уклонение)" in text
    assert controller.tracker.update("phantom_assassin", {"magic_wand"}, 100, {"phantom_assassin"}) == []


def test_wiki_reasons_are_valid():
    """Разметка причин «предмет против героя» (BUGLOG №61): каждая пара со страниц Counters размечена, тип есть
    в ru.WIKI_REASONS, способности — дословно из текста пункта, отпечаток текста совпадает."""
    import sys
    from app.i18n import ru
    from app.threats.item_hints import text_key
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from fetch_wiki_counters import plain_text, text_key as tool_key
    data = Path(__file__).resolve().parents[1] / "data"
    wiki = json.loads((data / "benchmark_counters.json").read_text(encoding="utf-8"))["heroes"]
    pairs = json.loads((data / "wiki_reasons.json").read_text(encoding="utf-8"))["pairs"]
    for hero, entry in wiki.items():
        for item in entry["counter_items"]:
            row = pairs[f"{hero}/{item}"]
            line = entry["counter_texts"][item]
            assert row["text_key"] == text_key(line) == tool_key(line)
            assert row["kind"] == "none" or row["kind"] in ru.WIKI_REASONS
            assert all(a in plain_text(line) for a in row["abilities"])
            assert not row["abilities"] or ru.WIKI_REASONS[row["kind"]][0]


def test_hero_counter_has_reason():
    """23.10: против Phantom Assassin советовался Lotus Orb без причины — выглядело как «отражение магии», хотя она
    бьёт физикой. Вики объясняет: отражает Stifling Dagger — это и показываем."""
    from app.threats.controller import wiki_reason
    lotus = next(c for c in table().vs_hero("phantom_assassin") if c.item == "lotus_orb")
    assert lotus.why == ("reflect", ("Stifling Dagger",))
    assert wiki_reason(lotus.why) == "отражает Stifling Dagger"
    assert wiki_reason(("heal_reduction", ())) == "режет лечение"
    assert wiki_reason(None) is None


def test_reason_dropped_when_wiki_text_changed():
    from app.threats.item_hints import hero_reasons, text_key
    wiki = {"heroes": {"pa": {"counter_texts": {"lotus_orb": "new text"}}}}
    old = {"pa/lotus_orb": {"kind": "reflect", "abilities": ["Stifling Dagger"], "text_key": text_key("old text")}}
    assert hero_reasons(wiki, old) == {}
    new = {"pa/lotus_orb": {"kind": "reflect", "abilities": [], "text_key": text_key("new text")}}
    assert hero_reasons(wiki, new) == {("pa", "lotus_orb"): ("reflect", ())}
