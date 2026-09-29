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
from app.threats.mechanics import MechanicsTagger, load_mechanics_config
from app.threats.recommend import TIER_HERO, Recommender, ThreatInput, format_delta, recommend_config
from app.threats.scoring import load_threat_config
from app.threats.stats import BUCKET_NORMAL, BUCKET_TURBO, Stats, download_stats
from app.threats.tracker import IGNORED_QUALITIES
from app.vision.service import VisionService

log = logging.getLogger(__name__)

THREATS_CONFIG = Path(__file__).resolve().parents[2] / "data" / "threats.json"


class ThreatsService(QObject):
    data_ready = Signal()

    def __init__(self, settings: ThreatSettings, show, turbo):
        super().__init__()
        self.settings = settings
        self.show = show
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
        self.recommender: Recommender | None = None
        self._rec_key = None  # с какими данными пересчитывали рекомендации в последний раз
        self._last_recs = {}
        self._gold: int | None = None
        self.data_ready.connect(self._rebuild)
        self._rebuild()
        threading.Thread(target=self._update_data, name="threats-data", daemon=True).start()

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
        self.controller = ThreatsController(data, tagger, advisor, self.config, self.settings, self.show, self.turbo)
        self.controller.card_duration = self.card_duration
        self.controller.card_items = self._card_items
        self.controller.on_enemies_changed = lambda: self.refresh_recommendations(force=True)
        raw_config = json.loads(THREATS_CONFIG.read_text(encoding="utf-8"))
        costs = {k: i.cost for k, i in data.items.items()}
        self.recommender = Recommender(tagger, self.stats, buyable, costs,
                                       recommend_config(raw_config, self.config.stats_min_games,
                                                        self.config.stats_min_base_games))
        if self.vision is None and (self.cache.root / "images" / "heroes").is_dir():
            self.vision = VisionService(data, self.cache)
            self.vision.topbar_ready.connect(self._on_topbar)
            self.vision.inventory_seen.connect(self._on_inventory)
            self.vision.start()
        log.info("Угрозы готовы: героев %d, статистика %s", len(data.heroes), "есть" if self.stats.available else "нет")

    def _update_data(self) -> None:
        """Фон: обновить справочники, иконки, статистику и матчапы (только вне катки)."""
        updater = DataUpdater(self.cache)
        updater.refresh_core()
        data = GameData.from_cache(self.cache)
        if data.ready:
            updater.refresh_images(data)
        download_stats(self.cache)
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
            if self.recommender is not None:
                self.recommender.reset()
            if self.strip is not None:
                self.strip.update_recommendations([], {})
        self._gold = state.gold
        in_progress = state.game_state == STATE_IN_PROGRESS
        self._in_match = state.in_match
        if state.clock_time is not None:
            self.last_clock = state.clock_time
        if state.account_id and not self._rank_requested:
            self._request_rank(state.account_id)
        need_topbar = self.controller.on_game_state(state.clock_time, in_progress, state.hero_name, state.team,
                                                    state.hero_alive, set(state.own_items))
        if self.vision is not None:
            self.vision.set_active(in_progress and not state.paused and self.settings.enabled)
            if need_topbar:
                self.vision.request_topbar()
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
        heroes = self.controller.on_topbar(result)
        if heroes and self.vision is not None:
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
        key = (tuple(inputs), frozenset(self.controller.own_items), (self.last_clock or 0) // 30, self.turbo())
        if key == self._rec_key and not force:
            return
        self._rec_key = key
        self.recommender.bucket = BUCKET_TURBO if self.turbo() else BUCKET_NORMAL
        self.recommender.stats = self.stats
        recs = self.recommender.recommend(self.controller.my_hero_id(), inputs, set(self.controller.own_items),
                                          self.last_clock or 0, self._gold)
        self._last_recs = {r.hero: r for r in recs}
        if self.strip is not None:
            titles = {t.hero.name: t.hero.localized for t in self.controller.threats}
            self.strip.update_recommendations(recs if self.settings.enabled else [], titles)
        if any(item.changed for rec in recs for item in rec.items):
            self.show(None, True, "item_hint")

    def _card_items(self, hero: str) -> str:
        """Строка советов для полной карточки — те же, что на полоске."""
        self.refresh_recommendations(force=True)
        rec = self._last_recs.get(hero)
        if rec is None:
            return ""
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
