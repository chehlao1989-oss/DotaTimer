"""Связка модуля угроз: враги с экрана → угрозы → карточка; инвентарь врагов → подсказки.

Советы выбираются двумя слоями (п. 6.6 ТЗ): механики из данных Valve + статистика матчей.
Работает в потоке интерфейса. Показывает сообщения через функцию show(text, important, voice_key).
"""
import logging
from dataclasses import dataclass
from typing import Callable

from app.i18n import ru
from app.threats.advisor import TIER_HERO, TIER_MECHANIC, TIER_TRAIT, Advisor, Suggestion
from app.threats.data import GameData
from app.threats.mechanics import MechanicsTagger
from app.threats.scoring import Threat, ThreatConfig, rank_bracket, score_enemies
from app.threats.stats import BUCKET_NORMAL, BUCKET_TURBO
from app.threats.tracker import KIND_ITEM, ItemHint, ItemTracker
from app.vision.inventory import InventorySnapshot
from app.vision.topbar import TopbarResult, enemy_side

log = logging.getLogger(__name__)

ROLE_AUTO = "auto"
TOPBAR_RETRY_SEC = 20  # если не все враги узнаны, повторяем снимок через столько секунд игры
TOPBAR_MAX_TRIES = 5


@dataclass
class ThreatSettings:
    """Выбор пользователя. Хранится в settings.json."""

    role_mode: str = ROLE_AUTO  # auto / core / support (пока только для оценки угроз в будущем)
    rank_tier: int | None = None  # выбран вручную (например, 54); None — брать из OpenDota
    auto_rank_tier: int | None = None  # последний известный ранг из OpenDota
    account_id: str | None = None
    threat_count: int = 2
    enabled: bool = True

    @property
    def effective_rank(self) -> int | None:
        return self.rank_tier or self.auto_rank_tier


class ThreatsController:
    def __init__(self, data: GameData, tagger: MechanicsTagger, advisor: Advisor, config: ThreatConfig,
                 settings: ThreatSettings, show: Callable[[str, bool, str | None], None], turbo: Callable[[], bool]):
        self.data = data
        self.tagger = tagger
        self.advisor = advisor
        self.config = config
        self.settings = settings
        self.show = show
        self.turbo = turbo
        self.tracker = ItemTracker(data, tagger, config.min_component_cost)
        self.enemies: list[str] = []
        self.threats: list[Threat] = []
        self.own_hero: str | None = None
        self.own_team: str | None = None
        self.own_items: set[str] = set()
        self._topbar_tries = 0
        self._next_topbar_at: int | None = None
        self._was_alive: bool | None = None
        self.card_duration: float | None = None  # сколько секунд держать полную карточку
        self.card_items = None  # функция: герой-угроза → строка советов из модуля рекомендаций
        self.on_enemies_changed = None  # служба пересчитывает рекомендации
        self.rank_threats = None  # функция: герои-враги → угрозы (из recs.zip); None — старая оценка
        self.item_counters = None  # функция: (герой-враг, предмет) → ключи советов (из recs.zip); None — старая
        self.hint_duration: float | None = None  # сколько секунд держать подсказку «у врага появился предмет»

    # --- названия ---
    def hero_title(self, key: str) -> str:
        hero = self.data.hero_by_name(key)
        return hero.localized if hero else key

    def item_title(self, key: str) -> str:
        item = self.data.items.get(key)
        return item.dname if item else key

    def my_hero_id(self) -> int | None:
        hero = self.data.hero_by_name(self.own_hero or "")
        return hero.id if hero else None

    def bucket(self) -> str:
        return BUCKET_TURBO if self.turbo() else BUCKET_NORMAL

    # --- матч ---
    def new_match(self) -> None:
        self.tracker.reset()
        self.enemies, self.threats = [], []
        self._topbar_tries = 0
        self._next_topbar_at = None
        self._was_alive = None

    def on_game_state(self, clock: int | None, in_progress: bool, own_hero: str | None, own_team: str | None,
                      alive: bool | None, own_items: set[str] | None = None) -> bool:
        """Каждый пакет GSI. Возвращает True, если пора снять верхнюю панель."""
        self.own_hero = (own_hero or "").removeprefix("npc_dota_hero_") or self.own_hero
        self.own_team = own_team or self.own_team
        if own_items is not None:
            self.own_items = set(own_items)
        # напоминание при смерти: один раз за смерть
        if alive is False and self._was_alive and self.enemies and self.settings.enabled:
            self.show(ru.HINT_DEAD_REMINDER, False, None)
        if alive is not None:
            self._was_alive = alive
        if not in_progress or clock is None or not self.settings.enabled:
            return False
        if self._topbar_tries == 0 or (self._next_topbar_at is not None and clock >= self._next_topbar_at):
            self._topbar_tries += 1
            self._next_topbar_at = clock + TOPBAR_RETRY_SEC if self._topbar_tries < TOPBAR_MAX_TRIES else None
            return True
        return False

    def on_topbar(self, result: TopbarResult) -> list[str]:
        """Результат снимка верхней панели. Возвращает 10 героев матча (для узнавания выделенного героя)."""
        # сторона врагов: по своей команде из GSI, а если её нет — по тому, где на панели свой герой
        side = enemy_side(self.own_team) or enemy_side(result.side_of(self.own_hero or ""))
        enemies = result.heroes(side) if side else []
        known = [e for e in enemies if e]
        if len(known) == 5:
            self._next_topbar_at = None  # все узнаны, больше не снимаем
        if known and known != self.enemies:
            self.set_enemies(known)
        if len(known) < 5 and self._next_topbar_at is None and self._topbar_tries >= TOPBAR_MAX_TRIES:
            self.show(ru.TOPBAR_PARTIAL if known else ru.TOPBAR_FAILED, False, None)
        return [h for h in result.heroes("radiant") + result.heroes("dire") if h]

    def set_enemies(self, enemies: list[str], show_card: bool = True) -> None:
        """Враги узнаны по экрану или выбраны вручную."""
        self.enemies = list(enemies)
        heroes = [h for h in (self.data.hero_by_name(e) for e in enemies) if h]
        if self.rank_threats is not None:
            self.threats = self.rank_threats(heroes)[:self.settings.threat_count]
        else:
            self.threats = score_enemies(heroes, self.data, self.config, self.settings.effective_rank, self.turbo(),
                                         self.my_hero_id())[:self.settings.threat_count]
        log.info("Враги: %s; угрозы: %s", enemies, [(t.hero.name, t.score, t.reason) for t in self.threats])
        if self.on_enemies_changed:
            self.on_enemies_changed()
        if show_card:
            self.show_card()

    # --- советы ---
    def suggestions_vs_hero(self, hero: str) -> list[Suggestion]:
        enemy = self.data.hero_by_name(hero)
        if enemy is None:
            return []
        rules = self.tagger.rules_for_traits(self.tagger.hero_traits(hero))
        return self.advisor.suggest(rules, self.my_hero_id(), self.bucket(), enemy_hero_id=enemy.id,
                                    owned=self.own_items, limit=self.config.suggestions_per_hint)

    def format_suggestions(self, suggestions: list[Suggestion], target: str) -> str:
        if not suggestions:
            return ru.HINT_NO_COUNTERS
        parts = []
        for s in suggestions:
            item = self.item_title(s.item)
            if s.tier == TIER_HERO and s.delta is not None:
                parts.append(ru.SUGGEST_VS_HERO.format(item=item, delta=s.delta, target=target))
            elif s.tier == TIER_TRAIT and s.delta is not None:
                parts.append(ru.SUGGEST_VS_TRAIT.format(item=item, delta=s.delta, rule=s.rule.title_ru.lower()))
            else:
                parts.append(ru.SUGGEST_PLAIN.format(item=item))
        text = ", ".join(parts)
        if all(s.tier == TIER_MECHANIC for s in suggestions):
            text += ru.SUGGEST_NO_STATS
        return text

    # --- карточка угроз ---
    def card_text(self) -> str | None:
        if not self.threats:
            return None
        rank = ru.RANK_NAMES.get(rank_bracket(self.settings.effective_rank) or 0, ru.RANK_UNKNOWN)
        lines = [ru.CARD_TITLE]
        for threat in self.threats:
            reason = threat.note or ru.CARD_REASON[threat.reason].format(wr=threat.winrate or 0, rank=rank)
            lines.append(ru.CARD_LINE.format(hero=threat.hero.localized, reason=reason))
            if self.card_items is not None:
                items = self.card_items(threat.hero.name)
                lines.append(ru.CARD_COUNTERS.format(items=items) if items else ru.CARD_NO_ANSWER)
                continue
            suggestions = self.suggestions_vs_hero(threat.hero.name)
            if suggestions:
                lines.append(ru.CARD_COUNTERS.format(items=self.format_suggestions(suggestions, threat.hero.localized)))
        return "\n".join(lines)

    def show_card(self) -> None:
        text = self.card_text()
        if text:
            self.show(text, True, "threat", self.card_duration)

    # --- инвентарь врагов ---
    def on_inventory(self, snapshot: InventorySnapshot, clock: int | None) -> None:
        if clock is None or snapshot.hero not in self.enemies or not self.settings.enabled:
            return
        # подсказка — по любому врагу, по которому кликнули (раньше только по двум угрозам, BUGLOG №30)
        for hint in self.tracker.update(snapshot.hero, snapshot.items, clock, set(self.enemies)):
            text = self.hint_text(hint)
            log.info("Подсказка: %s", text.replace("\n", " | "))
            self.show(text, True, None, self.hint_duration, **self.hint_extras(hint))  # без голоса (автор, 30.09)

    def manual_item(self, hero: str, item: str, clock: int | None) -> None:
        """«Вижу у врага предмет» (горячая клавиша). Считается угрозой, раз пользователь сам отметил."""
        threats = {t.hero.name for t in self.threats} | {hero}
        known = set(self.tracker.enemies[hero].items) if hero in self.tracker.enemies else set()
        for hint in self.tracker.update(hero, known | {item}, clock or 0, threats):
            text = self.hint_text(hint)
            log.info("Подсказка: %s", text.replace("\n", " | "))
            self.show(text, True, None, self.hint_duration, **self.hint_extras(hint))

    def hint_extras(self, hint: ItemHint) -> dict:
        """Оформление подсказки как оповещения Доты: портрет врага и иконка предмета, предмет золотом."""
        return {"icons": [("heroes", hint.hero), ("items", hint.item)], "highlights": [self.item_title(hint.item)]}

    def hint_text(self, hint: ItemHint) -> str:
        enemy = self.data.hero_by_name(hint.hero)
        if self.item_counters is not None:
            keys = self.item_counters(hint.hero, hint.item)
            counters = ", ".join(self.item_title(k) for k in keys) if keys else ru.HINT_NO_COUNTERS
            template = ru.HINT_ITEM if hint.kind == KIND_ITEM else ru.HINT_BUILDING
            return template.format(hero=self.hero_title(hint.hero), item=self.item_title(hint.item),
                                   component=self.item_title(hint.component or ""), counters=counters)
        suggestions = self.advisor.suggest(list(hint.rules), self.my_hero_id(), self.bucket(),
                                           enemy_hero_id=enemy.id if enemy else None, enemy_item=hint.item,
                                           owned=self.own_items, limit=self.config.suggestions_per_hint)
        counters = self.format_suggestions(suggestions, self.item_title(hint.item))
        template = ru.HINT_ITEM if hint.kind == KIND_ITEM else ru.HINT_BUILDING
        return template.format(hero=self.hero_title(hint.hero), item=self.item_title(hint.item),
                               component=self.item_title(hint.component or ""), counters=counters)
