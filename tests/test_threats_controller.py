"""Тесты связки угроз: карточка, подсказки, напоминание при смерти (без экрана и сети)."""
from app.threats.controller import ThreatSettings, ThreatsController
from app.threats.scoring import load_threat_config
from app.vision.inventory import InventorySnapshot
from app.vision.matcher import Match
from app.vision.topbar import TopbarResult
from tests.test_threats import DATA, advisor, tagger


def make():
    shown = []
    ctrl = ThreatsController(DATA, tagger(), advisor(), load_threat_config(), ThreatSettings(),
                             lambda text, important, voice, *rest, **extra: shown.append((text, voice)), lambda: False)
    return ctrl, shown


def topbar(radiant, dire):
    return TopbarResult(tuple(Match(h, 0.8, 0.3) for h in radiant), tuple(Match(h, 0.8, 0.3) for h in dire))


def test_topbar_gives_enemies_and_card():
    ctrl, shown = make()
    assert ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True) is True  # пора снять панель
    heroes = ctrl.on_topbar(topbar(["sven", "axe", "tiny", "lina", "zuus"], ["alchemist", "medusa", "lion", "pudge", "viper"]))
    assert len(heroes) == 10
    assert ctrl.enemies == ["alchemist", "medusa", "lion", "pudge", "viper"]
    text, voice = shown[-1]
    assert voice == "threat" and "Главные угрозы" in text and "Alchemist" in text
    assert "Eye of Skadi (+10% против Alchemist)" in text  # совет из статистики


def test_enemy_side_from_own_hero_when_team_unknown():
    ctrl, _ = make()
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", None, True)
    ctrl.on_topbar(topbar(["alchemist", "medusa", "lion", "pudge", "viper"], ["sven", "axe", "tiny", "lina", "zuus"]))
    assert ctrl.enemies[0] == "alchemist"
    assert ctrl.allies == ["axe", "tiny", "lina", "zuus"]  # союзники — без своего героя (DECISIONS №26)
    ctrl.new_match()
    assert ctrl.allies == []


def test_item_hint_uses_stats_and_skips_owned():
    ctrl, shown = make()
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True, own_items={"skadi"})
    ctrl.set_enemies(["alchemist", "medusa", "lion"])
    shown.clear()
    ctrl.on_inventory(InventorySnapshot("alchemist", ("heart",), (None, None, None), None), 1200)
    text, voice = shown[-1]
    assert voice is None  # фраза «Совет по предметам» убрана (BUGLOG №28)
    assert "У Alchemist появился Heart of Tarrasque" in text
    assert "Spirit Vessel (+12% против Heart of Tarrasque)" in text and "Skadi" not in text
    shown.clear()
    ctrl.on_inventory(InventorySnapshot("sven", ("heart",), (None, None, None), None), 1200)  # союзник
    assert shown == []


def test_dead_reminder_once_per_death():
    ctrl, shown = make()
    ctrl.set_enemies(["alchemist"], show_card=False)
    ctrl.on_game_state(100, True, "npc_dota_hero_sven", "radiant", True)
    ctrl.on_game_state(101, True, "npc_dota_hero_sven", "radiant", False)
    ctrl.on_game_state(102, True, "npc_dota_hero_sven", "radiant", False)
    assert len([t for t, _ in shown if "Кликни" in t]) == 1


def test_manual_item():
    ctrl, shown = make()
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True)
    ctrl.set_enemies(["medusa"], show_card=False)
    ctrl.manual_item("medusa", "heart", 900)
    assert "У Medusa появился Heart of Tarrasque" in shown[-1][0]


def test_item_hint_uses_hint_duration():
    """Подсказку по предмету держим дольше таймеров: 6 с автор не успевал прочитать (игра 01.10, BUGLOG №34)."""
    shown = []
    ctrl = ThreatsController(DATA, tagger(), advisor(), load_threat_config(), ThreatSettings(),
                             lambda text, important, voice, *rest, **extra: shown.append(rest), lambda: False)
    ctrl.hint_duration = 12
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True)
    ctrl.set_enemies(["alchemist", "medusa", "lion"])
    shown.clear()
    ctrl.on_inventory(InventorySnapshot("alchemist", ("heart",), (None, None, None), None), 1200)
    assert shown and shown[-1] == (12,)


def test_item_hint_sends_icons():
    """В подсказку уходят портрет врага и иконка предмета (оформление как у оповещений Доты, BUGLOG №35)."""
    extras = []
    ctrl = ThreatsController(DATA, tagger(), advisor(), load_threat_config(), ThreatSettings(),
                             lambda text, important, voice, *rest, **extra: extras.append(extra), lambda: False)
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True)
    ctrl.set_enemies(["alchemist", "medusa", "lion"])
    extras.clear()
    ctrl.on_inventory(InventorySnapshot("alchemist", ("heart",), (None, None, None), None), 1200)
    assert extras[-1]["icons"] == [("heroes", "alchemist"), ("items", "heart")]
    assert extras[-1]["highlights"] == ["Heart of Tarrasque"]


def test_no_hint_without_counter():
    """Подсказку «явного ответа нет» не показываем (решение автора 05.10): на экран ничего, только журнал."""
    shown = []
    ctrl = ThreatsController(DATA, tagger(), advisor(), load_threat_config(), ThreatSettings(),
                             lambda text, important, voice, *rest, **extra: shown.append(text), lambda: False)
    ctrl.item_counters = lambda hero, item: []
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True)
    ctrl.set_enemies(["alchemist", "medusa", "lion"])
    shown.clear()
    ctrl.on_inventory(InventorySnapshot("alchemist", ("heart",), (None, None, None), None), 1200)
    assert shown == []


def test_overlay_hint_when_left_portraits_missing():
    """Не узнаны только два крайних левых портрета Сил Света — подсказка про оверлей FPS (видео автора 16.10:
    строка MSI Afterburner лежит на портретах), иначе — обычная «Ctrl+Alt+P» (BUGLOG №55)."""
    from app.i18n import ru
    from app.threats.controller import TOPBAR_MAX_TRIES
    for missing, expected in (({0, 1}, ru.TOPBAR_OVERLAY), ({3}, ru.TOPBAR_PARTIAL)):
        ctrl, shown = make()
        radiant = [None if i in missing else h for i, h in enumerate(["alchemist", "medusa", "lion", "pudge", "viper"])]
        result = TopbarResult(tuple(Match(h, 0.8 if h else 0.3, 0.3) for h in radiant),
                              tuple(Match(h, 0.8, 0.3) for h in ["sven", "axe", "tiny", "lina", "zuus"]))
        clock = 0
        for _ in range(TOPBAR_MAX_TRIES):
            while not ctrl.on_game_state(clock, True, "npc_dota_hero_sven", "dire", True):
                clock += 1
            ctrl.on_topbar(result)
        assert shown[-1][0] == expected


def test_own_inventory_under_enemy_name_is_dropped():
    """Снимок «врага» с моими предметами — мой герой, прочитанный под чужим именем: не событие врага (BUGLOG №58).
    19.10: у Ogre Magi и Viper записана сборка моего Huskar."""
    ctrl, shown = make()
    ctrl.enemies = ["ogre_magi", "viper"]
    huskar = {"blade_mail", "blitz_knuckles", "power_treads", "sange_and_yasha", "satanic"}
    ctrl.on_game_state(600, True, "npc_dota_hero_huskar", "radiant", True, huskar | {"tango"})
    ctrl.on_inventory(InventorySnapshot("ogre_magi", tuple(sorted(huskar)) + (None,), (None, None, None), None), 600)
    assert "ogre_magi" not in ctrl.tracker.enemies or not ctrl.tracker.enemies["ogre_magi"].items
    ctrl.on_inventory(InventorySnapshot("viper", ("satanic", "skadi", "dragon_lance", "power_treads", None, None),
                                        (None, None, None), None), 610)
    assert "skadi" in ctrl.tracker.enemies["viper"].items  # настоящий инвентарь врага — проходит


def test_own_hero_always_among_name_candidates():
    """Мой герой (из GSI) всегда среди имён для нижней панели, даже если на верхней он не узнан (BUGLOG №58)."""
    from types import SimpleNamespace
    from app.threats.service import ThreatsService
    sent = []
    fake = SimpleNamespace(controller=SimpleNamespace(own_hero="huskar"), _match_heroes=["ogre_magi", "viper"],
                           _name_candidates=[], vision=SimpleNamespace(set_match_heroes=sent.append))
    ThreatsService._update_name_candidates(fake)
    assert sent == [["ogre_magi", "viper", "huskar"]]
    ThreatsService._update_name_candidates(fake)
    assert len(sent) == 1  # без изменений — заново не пересобираем


def test_own_hero_reset_on_new_match():
    """Герой прошлой катки не считается моим в новой, пока GSI не пришлёт нового (BUGLOG №59)."""
    ctrl, _ = make()
    ctrl.on_game_state(600, True, "npc_dota_hero_lone_druid", "radiant", True)
    ctrl.new_match()
    ctrl.on_game_state(0, True, None, "radiant", True)
    assert ctrl.own_hero is None
    ctrl.on_game_state(5, True, "npc_dota_hero_spirit_breaker", "radiant", True)
    assert ctrl.own_hero == "spirit_breaker"


def test_two_of_my_items_under_enemy_name_dropped():
    """Два заметных предмета «врага», и оба есть у меня — мой герой (21.10: Assault Cuirass и Desolator Lycan)."""
    ctrl, _ = make()
    ctrl.enemies = ["techies"]
    ctrl.on_game_state(1800, True, "npc_dota_hero_lycan", "dire", True, {"assault", "desolator", "black_king_bar"})
    ctrl.on_inventory(InventorySnapshot("techies", ("assault", "desolator", None, None, None, None),
                                        (None, None, None), None), 1800)
    assert "techies" not in ctrl.tracker.enemies or not ctrl.tracker.enemies["techies"].items


def test_repeat_hints_shows_last_ones_again():
    """Клавиша повтора: подсказки уходят в драке, их можно вызвать ещё раз, пока ждёшь возрождения (автор 23.10)."""
    ctrl, shown = make()
    ctrl.on_game_state(0, True, "npc_dota_hero_sven", "radiant", True)
    ctrl.set_enemies(["alchemist", "medusa"], show_card=False)
    card = ctrl.card_text()
    assert card
    ctrl.repeat_hints(now=100)
    assert shown[-1] == (card, None)  # первое нажатие — карточка про героев, без голоса (автор 26.10)
    ctrl.repeat_hints(now=105)
    assert "ещё не было" in shown[-1][0]  # второе подряд — подсказки по предметам
    ctrl.manual_item("medusa", "heart", 900)
    ctrl.manual_item("alchemist", "heart", 950)
    hints = [t for t, _ in shown if "появился" in t]
    shown.clear()
    ctrl.repeat_hints(now=200)
    ctrl.repeat_hints(now=201)
    assert [t for t, _ in shown] == [card] + hints[-ctrl.hint_repeat:] and all(v is None for _, v in shown)
    shown.clear()
    ctrl.repeat_hints(now=300)  # нажатие позже — снова карточка
    assert [t for t, _ in shown] == [card]
    ctrl.new_match()
    ctrl.threats = []
    shown.clear()
    ctrl.repeat_hints(now=400)
    assert "ещё не было" in shown[-1][0]


def test_selftest_fails_while_vision_disabled():
    """Зрение выключено для опыта по падениям (28.10, BUGLOG №57): самопроверка сборки должна это поймать,
    чтобы выпуск без зрения не ушёл игрокам."""
    import inspect
    import json
    from app import main
    from app.threats.service import THREATS_CONFIG
    assert "vision_enabled" in inspect.getsource(main.selftest) and "frozen" in inspect.getsource(main.selftest)
    assert isinstance(json.loads(THREATS_CONFIG.read_text(encoding="utf-8")).get("vision_enabled", True), bool)
