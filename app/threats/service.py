"""Служба угроз: данные и статистика в фоне, зрение, связка с GSI, окна ручного ввода.

Сеть используется только в фоновом потоке и не во время катки (матчапы докачиваются, пока
пользователь в меню). Без интернета всё работает из кеша.
"""
import json
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from app.gsi.models import STATE_IN_PROGRESS, GameState
from app.i18n import ru
from app.threats.advisor import Advisor
from app.threats.controller import ThreatSettings, ThreatsController
from app.threats.data import DataCache, DataUpdater, GameData
from app.threats.item_hints import CounterTable, pick_parent
from app.threats.mechanics import MechanicsTagger, load_mechanics_config
from app.paths import app_data_dir
from app.threats.recommend import (TIER_HERO, TIER_MECHANIC, RecItem, Recommender, ThreatInput, ThreatRecommendation,
                                   format_delta, recommend_config)
from app.threats.recs_advice import RecsRecommender, RecsSource, advice_config, rank_threats
from app.threats.recs_file import download_recs
from app.threats.scoring import load_threat_config
from app.threats.threat_score import threat_score_config
from app.threats.stats import BUCKET_NORMAL, BUCKET_TURBO, Stats, download_stats
from app.threats.tracker import IGNORED_QUALITIES
from app.vision.service import VisionService

log = logging.getLogger(__name__)

THREATS_CONFIG = Path(__file__).resolve().parents[2] / "data" / "threats.json"


def recs_path() -> Path:
    """Файл готовых советов: %APPDATA%\\DotaTimer\\recs.zip."""
    return app_data_dir() / "recs.zip"


def describe_threat(reasons: dict) -> str:
    """Причина угрозы для карточки: роль, лейт, матчап (цифры — из файла советов)."""
    parts = []
    if "role" in reasons:
        parts.append(ru.THREAT_ROLE[reasons["role"]])
    if "late" in reasons:
        parts.append(ru.THREAT_LATE_PP.format(pp=reasons["late"]))
    if "matchup" in reasons:
        parts.append(ru.THREAT_MATCHUP_PP.format(pp=reasons["matchup"]))
    return ru.THREAT_JOIN.join(parts)


class ThreatsService(QObject):
    data_ready = Signal()

    def __init__(self, settings: ThreatSettings, show, turbo):
        super().__init__()
        self.settings = settings
        self.overlays_enabled = json.loads(THREATS_CONFIG.read_text(encoding="utf-8")).get("overlays_enabled", True)
        self._show = show
        self.show = self._show_if_enabled
        self.turbo = turbo
        self.cache = DataCache()
        self.config = load_threat_config()
        self.mechanics_config = load_mechanics_config()
        self.controller: ThreatsController | None = None
        self.vision: VisionService | None = None
        self.stats = Stats(None)
        self.last_clock: int | None = None
        self._match_id: str | None = None
        self._in_match = False
        self._rank_requested = False
        self.strip = None  # полоска угроз (ThreatStrip), подключается из main
        self.card_duration: float | None = None
        self.recommender: Recommender | RecsRecommender | None = None
        self.recs: RecsSource | None = None  # готовые советы recs.zip (если файл есть)
        self.tagger: MechanicsTagger | None = None
        self._rec_key = None  # с какими данными пересчитывали рекомендации в последний раз
        self._last_recs = {}
        self._gold: int | None = None
        self._match_heroes: list[str] = []  # герои с верхней панели (уверенно узнанные)
        self._name_candidates: list[str] = []  # кому сейчас сверяем имя на нижней панели
        self._v2_first: dict[str, str] = {}  # подсказки v2: угроза → первый совет на полоске (подсветка смены)
        self._v2_choices: dict[str, list] = {}  # угроза → выбранные ответы (для карточки, с пометкой «редко»)
        self._shares_key = None
        self._shares: dict[str, float] = {}
        self.data_ready.connect(self._rebuild)
        self._rebuild()
        threading.Thread(target=self._update_data, name="threats-data", daemon=True).start()

    def _show_if_enabled(self, text, important, voice, *rest, **extra) -> None:
        """Пока угрозы переделываются, на экран ничего не выводим — только в лог."""
        if self.overlays_enabled:
            self._show(text, important, voice, *rest, **extra)
        elif text:
            log.info("Угрозы (не показано): %s", text.replace("\n", " | "))

    # --- данные ---
    def _rebuild(self) -> None:
        """Собрать всё из кеша (при запуске и после фонового обновления, если сейчас не катка)."""
        if self._in_match and self.controller is not None:
            return
        data = GameData.from_cache(self.cache)
        if not data.ready:
            log.info("Данных OpenDota ещё нет, угрозы включатся после загрузки")
            return
        raw_items, _ = self.cache.load("items")
        abilities, _ = self.cache.load("abilities")
        hero_abilities, _ = self.cache.load("hero_abilities")
        tagger = MechanicsTagger(self.mechanics_config, raw_items or {}, abilities or {}, hero_abilities or {},
                                 {h.name: h.roles for h in data.heroes.values()})
        self.stats = Stats.load(self.cache)
        buyable = {k for k, i in data.items.items() if i.cost > 0 and not k.startswith("recipe") and not i.tier}
        advisor = Advisor(tagger, self.stats, buyable, {k: i.cost for k, i in data.items.items()},
                          self.config.stats_min_games, self.config.stats_min_base_games)
        self.data = data
        self.tagger = tagger
        self.controller = ThreatsController(data, tagger, advisor, self.config, self.settings, self.show, self.turbo)
        self.controller.card_duration = self.card_duration
        self.controller.card_items = self._card_items
        self.controller.on_enemies_changed = lambda: self.refresh_recommendations(force=True)
        raw_config = json.loads(THREATS_CONFIG.read_text(encoding="utf-8"))
        costs = {k: i.cost for k, i in data.items.items()}
        self.recommender = Recommender(tagger, self.stats, buyable, costs,
                                       recommend_config(raw_config))
        self._use_recs_file(data, raw_config)
        self._use_counter_table(raw_config)
        if self.vision is None and (self.cache.root / "images" / "heroes").is_dir():
            self.vision = VisionService(data, self.cache)
            self.vision.topbar_ready.connect(self._on_topbar)
            self.vision.inventory_seen.connect(self._on_inventory)
            self.vision.start()
        log.info("Угрозы готовы: героев %d, статистика %s", len(data.heroes), "есть" if self.stats.available else "нет")

    def _use_recs_file(self, data: GameData, raw_config: dict) -> None:
        """Угрозы и советы — из готового файла советов recs.zip, если он есть (спека 7, recs_advice.py)."""
        path = recs_path()
        self.recs = RecsSource.open(path) if path.is_file() else None
        if self.recs is None:
            log.info("Файла советов %s нет — угрозы и советы по старой схеме", path)
            return
        self.recommender = RecsRecommender(self.recs, data.items, advice_config(raw_config))
        score_cfg = threat_score_config(raw_config)
        controller = self.controller
        controller.rank_threats = lambda heroes: rank_threats(self.recs, heroes, controller.my_hero_id(),
                                                              controller.bucket(), score_cfg, describe_threat)

        def counters(hero: str, item: str) -> list[str]:
            enemy = data.hero_by_name(hero)
            return self.recommender.counters(controller.my_hero_id(), enemy.id, item,
                                             ally_ids=controller.ally_ids()) if enemy else []

        controller.item_counters = counters
        controller.hint_duration = raw_config["recs_advice"].get("hint_display_sec")
        log.info("Советы из %s: матчей %s, патч %s", path.name, self.recs.meta.get("matches"), self.recs.meta.get("patch"))

    def _use_counter_table(self, raw_config: dict) -> None:
        """Подсказки v2 (BUGLOG №54): ответы из таблицы механик и страниц Counters вики, выбор — под моего героя."""
        cfg = raw_config.get("item_hints", {})
        if not cfg.get("enabled"):
            return
        table = CounterTable.load(self.mechanics_config, consumables={
            k for k, i in self.data.items.items() if i.qual in IGNORED_QUALITIES})
        if table is None:
            return
        self._fallback_share = cfg["fallback_buys_share"]
        self.controller.use_counter_table(table, self._own_shares, cfg["min_own_share"], cfg["limit"],
                                          self._likely_parent)
        log.info("Подсказки v2: опасных предметов в таблице %d, героев со страницей Counters %d",
                 len(table.threat_items()), len(table.hero_counters))

    def _own_shares(self) -> dict[str, float]:
        """Доли покупки предметов моим героем в роли (recs.zip); пересчитываются при смене героя или режима."""
        if not isinstance(self.recommender, RecsRecommender) or self.controller is None:
            return {}
        bucket = BUCKET_TURBO if self.turbo() else BUCKET_NORMAL
        key = (self.controller.my_hero_id(), bucket, self.settings.role_mode)
        if key != self._shares_key:
            self.recommender.bucket, self.recommender.role_mode = bucket, self.settings.role_mode
            self._shares = self.recommender.buy_shares(key[0], self._fallback_share)
            self._shares_key = key
        return self._shares

    def _likely_parent(self, hero: str, candidates: list[str]) -> str | None:
        """Во что враг соберёт деталь: тот из предметов, который этот герой покупает чаще (recs.zip, его основная
        роль); ни один не покупает — None (подсказку «собирает» не даём)."""
        enemy = self.data.hero_by_name(hero)
        if enemy is None or not isinstance(self.recommender, RecsRecommender):
            return None
        self.recommender.bucket = BUCKET_TURBO if self.turbo() else BUCKET_NORMAL
        return pick_parent(self.recommender.buy_shares(enemy.id, self._fallback_share, main_role=True), candidates)

    def _recs_v2(self) -> list[ThreatRecommendation]:
        """Полоска по подсказкам v2: против угрозы — ответы на её опасные предметы (последний увиденный первым),
        потом со страницы Counters героя; купленный ответ — с галочкой, рядом следующий."""
        controller, table = self.controller, self.controller.counter_table
        result = []
        for threat in controller.threats:
            hero = threat.hero.name
            known = controller.tracker.enemies.get(hero)
            seen = sorted((x for x in (known.items if known else {}) if table.is_threat(x)),
                          key=lambda x: -known.items[x])
            counters = [c for x in seen for c in table.vs_item(x)] + table.vs_hero(hero)
            bought = next((c.item for c in counters if controller.owned(c.item)), None)
            items = [RecItem(bought, 0.0, "bought", None, 0, bought=True)] if bought else []
            choices = controller.choices(counters)
            self._v2_choices[hero] = choices
            for index, choice in enumerate(choices):
                changed = index == 0 and hero in self._v2_first and self._v2_first[hero] != choice.item
                if index == 0:
                    self._v2_first[hero] = choice.item
                cost = self.data.items[choice.item].cost if choice.item in self.data.items else 0
                items.append(RecItem(choice.item, choice.share or 0.0, TIER_MECHANIC, None, 0, choice.answer,
                                     changed=changed, affordable=self._gold is not None and self._gold >= cost))
            result.append(ThreatRecommendation(hero, tuple(items)))
        return result

    def _update_data(self) -> None:
        """Фон: обновить справочники, иконки, статистику и матчапы (только вне катки)."""
        updater = DataUpdater(self.cache)
        updater.refresh_core()
        data = GameData.from_cache(self.cache)
        if data.ready:
            updater.refresh_images(data)
        download_stats(self.cache)
        download_recs(recs_path())
        self.data_ready.emit()
        updater.refresh_matchups(data, stop=lambda: self._in_match)

    def stats_status(self) -> tuple[Stats, float | None]:
        path = self.cache.root / "stats.json"
        return self.stats, (path.stat().st_mtime if path.is_file() else None)

    # --- игра ---
    def on_state(self, state: GameState) -> None:
        if self.controller is None:
            return
        if state.match_id and state.match_id != self._match_id:
            self._match_id = state.match_id
            self.controller.new_match()
            self._match_heroes = []  # герои прошлой катки в кандидатах имён не нужны
            if self.recommender is not None:
                self.recommender.reset()
            if self.strip is not None:
                self.strip.update_recommendations([], {})
        self._gold = state.gold
        in_progress = state.game_state == STATE_IN_PROGRESS
        self._in_match = state.in_match
        if not in_progress:
            self.hide_overlays()  # экран итогов, выбор героев, главное меню — полоска не нужна
        if state.clock_time is not None:
            self.last_clock = state.clock_time
        if state.account_id and not self._rank_requested:
            self._request_rank(state.account_id)
        need_topbar = self.controller.on_game_state(state.clock_time, in_progress, state.hero_name, state.team,
                                                    state.hero_alive, set(state.own_items))
        self._update_name_candidates()  # мой герой мог стать известен уже после снимка верхней панели
        if self.vision is not None:
            self.vision.set_active(in_progress and not state.paused and self.settings.enabled)
            if need_topbar:
                self.vision.request_topbar()
        if in_progress:
            self.refresh_recommendations()

    def _request_rank(self, account_id: str) -> None:
        self._rank_requested = True
        if self.settings.rank_tier or self.settings.account_id == account_id:
            return
        self.settings.account_id = account_id

        def work():
            self.settings.auto_rank_tier = DataUpdater(self.cache).fetch_rank(account_id)

        threading.Thread(target=work, name="rank", daemon=True).start()

    def _on_topbar(self, result) -> None:
        if self.controller is None:
            return
        self._match_heroes = self.controller.on_topbar(result)
        self._update_name_candidates()

    def _update_name_candidates(self) -> None:
        """Имена-кандидаты для нижней панели: герои с верхней панели и всегда мой герой (он известен из GSI).
        Без своего героя его имя не узнавалось или читалось как имя врага, и мои предметы записывались врагу:
        19.10 у Ogre Magi и Viper — сборка моего Huskar, у Io — сборка Storm Spirit (BUGLOG №58)."""
        own = self.controller.own_hero if self.controller else None
        heroes = list(self._match_heroes)
        if own and own not in heroes:
            heroes.append(own)
        if heroes and self.vision is not None and heroes != self._name_candidates:
            self._name_candidates = heroes
            self.vision.set_match_heroes(heroes)

    def _on_inventory(self, snapshot) -> None:
        if self.controller is not None:
            self.controller.on_inventory(snapshot, self.last_clock)
            self.refresh_recommendations()

    # --- рекомендации и полоска ---
    def _threat_inputs(self) -> list[ThreatInput]:
        inputs = []
        for threat in self.controller.threats:
            known = self.controller.tracker.enemies.get(threat.hero.name)
            items = frozenset(known.items) if known else frozenset()
            inputs.append(ThreatInput(threat.hero.name, threat.hero.id, items))
        return inputs

    def refresh_recommendations(self, force: bool = False) -> None:
        """Пересчитать советы: при смене врагов/предметов и раз в 30 секунд игры (меняется фаза)."""
        if self.controller is None or self.recommender is None or not self.controller.threats:
            return
        inputs = self._threat_inputs()
        allies = self.controller.ally_ids()
        key = (tuple(inputs), frozenset(self.controller.own_items), (self.last_clock or 0) // 30, self.turbo(), allies)
        if key == self._rec_key and not force:
            return
        self._rec_key = key
        self.recommender.bucket = BUCKET_TURBO if self.turbo() else BUCKET_NORMAL
        self.recommender.stats = self.stats
        self.recommender.role_mode = self.settings.role_mode
        if self.controller.counter_table is not None:
            recs = self._recs_v2()
        else:
            recs = self.recommender.recommend(self.controller.my_hero_id(), inputs, set(self.controller.own_items),
                                              self.last_clock or 0, self._gold, allies)
        self._last_recs = {r.hero: r for r in recs}
        if self.strip is not None and self.overlays_enabled:
            titles = {t.hero.name: t.hero.localized for t in self.controller.threats}
            # названия предметов — полоска показывает их 5 с, когда совет появился или сменился (выбор автора 16.10)
            titles.update({i.item: self.controller.item_title(i.item) for r in recs for i in r.items})
            self.strip.update_recommendations(recs if self.settings.enabled else [], titles)
        # голосовая фраза «Совет по предметам» убрана: при смене советов она повторялась (замечание автора 30.09)

    def _card_items(self, hero: str) -> str:
        """Строка советов для полной карточки — те же, что на полоске."""
        self.refresh_recommendations(force=True)
        rec = self._last_recs.get(hero)
        if rec is None:
            return ""
        if self.controller.counter_table is not None:
            return ru.CARD_ITEMS_JOIN.join(self.controller.format_choices([c])
                                           for c in self._v2_choices.get(hero, []))
        if self.recs is not None:
            return self._card_items_short(rec)
        parts = []
        for item in rec.items:
            name = self.controller.item_title(item.item)
            if item.bought:
                parts.append(ru.CARD_BOUGHT.format(item=name))
            elif item.delta is not None:
                delta = format_delta(item.delta, ru.DELTA_ZERO, ru.STRIP_DELTA)
                if item.tier == TIER_HERO:  # прибавка именно против этого героя
                    target = self.controller.hero_title(hero)
                    parts.append(ru.CARD_ITEM_VS.format(item=name, delta=delta, target=target))
                else:  # против его предмета или механики — без уточнения
                    parts.append(ru.CARD_ITEM_DELTA.format(item=name, delta=delta))
            else:
                parts.append(name)
        return ", ".join(parts)

    def _card_items_short(self, rec) -> str:
        """Советы для карточки: каждый предмет с новой строки, причина механики по-русски, без процентов
        (карточка переносилась посреди фраз — предпросмотр 30.09; «+X%» в карточке автор не просил, 29.09)."""
        lines = []
        for item in rec.items:
            if item.bought:
                continue
            name = self.controller.item_title(item.item)
            reason = self._mechanic_reason(item.item, item.reason)
            lines.append(ru.CARD_ITEM_LINE.format(item=name, reason=reason) if reason else name)
        return ru.CARD_ITEMS_JOIN.join(lines)

    def _mechanic_reason(self, item: str, rule_id: str | None) -> str | None:
        """«пробивает уклонение», «режет лечение»: метка ответа предмета по правилу механик."""
        if rule_id is None or self.tagger is None:
            return None
        rule = next((r for r in self.tagger.rules if r.id == rule_id), None)
        if rule is None:
            return None
        tags = [t for t in rule.answers if t in self.tagger.item_answers(item)]
        return ru.MECHANIC_REASONS.get(tags[0]) if tags else None

    def hide_overlays(self) -> None:
        """Убрать полоску угроз (конец катки, Дота закрыта, данные не приходят)."""
        if self.strip is not None and self.strip.isVisible():
            self.strip.clear()
        self._rec_key = None

    def toggle_strip(self) -> None:
        if self.strip is not None:
            self.strip.toggle()


    # --- горячие клавиши и кнопки ---
    def show_card(self) -> None:
        if self.controller is None or not self.controller.threats:
            self.show(ru.CARD_NONE, False, None)
            return
        self.controller.show_card()

    def pick_heroes(self) -> None:
        if self.controller is None:
            return
        from app.ui.pickers import HeroPicker
        dialog = HeroPicker(self.data, self.cache, self.controller.enemies)
        if dialog.exec() and dialog.selected():
            enemies = dialog.selected()
            self.controller.set_enemies(enemies)
            if self.vision is not None:
                self.vision.set_match_heroes(enemies + ([self.controller.own_hero] if self.controller.own_hero else []))

    def repeat_hints(self) -> None:
        if self.controller is not None:
            self.controller.repeat_hints()

    def seen_item(self) -> None:
        if self.controller is None:
            return
        if not self.controller.enemies:
            self.show(ru.PICKER_NO_ENEMIES, False, None)
            return
        from app.ui.pickers import ItemPicker
        items = [k for k, i in self.data.items.items()
                 if i.cost > 0 and i.qual not in IGNORED_QUALITIES and not k.startswith("recipe")]
        dialog = ItemPicker(self.data, self.cache, self.controller.enemies, items)
        if dialog.exec():
            hero, item = dialog.selected()
            if hero and item:
                self.controller.manual_item(hero, item, self.last_clock)

    def stop(self) -> None:
        if self.vision is not None:
            self.vision.stop()
        self._in_match = True  # останавливает докачку матчапов

