"""Связка модуля угроз: враги с экрана → угрозы → карточка; инвентарь врагов → подсказки.

Работает в потоке интерфейса. Показывает сообщения через функцию show(text, important, voice_key).
"""
import logging
from dataclasses import dataclass
from typing import Callable

from app.i18n import ru
from app.threats.counters import ROLE_CORE, ROLE_SUPPORT, CounterTable
from app.threats.data import GameData
from app.threats.scoring import Threat, ThreatConfig, hero_role, rank_bracket, score_enemies
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

    role_mode: str = ROLE_AUTO  # auto / core / support
    rank_tier: int | None = None  # выбран вручную (например, 54); None — брать из OpenDota
    auto_rank_tier: int | None = None  # последний известный ранг из OpenDota
    account_id: str | None = None
    threat_count: int = 2
    enabled: bool = True

    @property
    def effective_rank(self) -> int | None:
        return self.rank_tier or self.auto_rank_tier


class ThreatsController:
    def __init__(self, data: GameData, counters: CounterTable, config: ThreatConfig, settings: ThreatSettings,
                 show: Callable[[str, bool, str | None], None], turbo: Callable[[], bool]):
        self.data = data
        self.counters = counters
        self.config = config
        self.settings = settings
        self.show = show
        self.turbo = turbo
        self.tracker = ItemTracker(data, counters, config.min_component_cost, config.counters_per_hint)
        self.enemies: list[str] = []
        self.threats: list[Threat] = []
        self.own_hero: str | None = None
        self.own_team: str | None = None
        self._topbar_tries = 0
        self._next_topbar_at: int | None = None
        self._was_alive: bool | None = None

    # --- роль и названия ---
    def user_role(self) -> str:
        if self.settings.role_mode in (ROLE_CORE, ROLE_SUPPORT):
            return self.settings.role_mode
        hero = self.data.hero_by_name(self.own_hero or "")
        return hero_role(hero) if hero else ROLE_CORE

    def hero_title(self, key: str) -> str:
        hero = self.data.hero_by_name(key)
        return hero.localized if hero else key

    def item_title(self, key: str) -> str:
        item = self.data.items.get(key)
        return item.dname if item else key

    # --- матч ---
    def new_match(self) -> None:
        self.tracker.reset()
        self.enemies, self.threats = [], []
        self._topbar_tries = 0
        self._next_topbar_at = None
        self._was_alive = None

    def on_game_state(self, clock: int | None, in_progress: bool, own_hero: str | None, own_team: str | None,
                      alive: bool | None) -> bool:
        """Каждый пакет GSI. Возвращает True, если пора снять верхнюю панель."""
        self.own_hero = (own_hero or "").removeprefix("npc_dota_hero_") or self.own_hero
        self.own_team = own_team or self.own_team
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
        my = self.data.hero_by_name(self.own_hero or "")
        self.threats = score_enemies(heroes, self.data, self.counters, self.config, self.settings.effective_rank,
                                     self.turbo(), my.id if my else None)[:self.settings.threat_count]
        log.info("Враги: %s; угрозы: %s", enemies, [(t.hero.name, t.score, t.reason) for t in self.threats])
        if show_card:
            self.show_card()

    # --- карточка угроз ---
    def card_text(self) -> str | None:
        if not self.threats:
            return None
        rank = ru.RANK_NAMES.get(rank_bracket(self.settings.effective_rank) or 0, ru.RANK_UNKNOWN)
        lines = [ru.CARD_TITLE]
        for threat in self.threats:
            reason = ru.CARD_REASON[threat.reason].format(wr=threat.winrate or 0, rank=rank)
            lines.append(ru.CARD_LINE.format(hero=threat.hero.localized, reason=reason))
            counters = self.counters.pick_counters(self.counters.rules_for_hero(threat.hero.name), self.user_role(),
                                                   limit=self.config.counters_per_hint)
            if counters:
                lines.append(ru.CARD_COUNTERS.format(items=", ".join(self.item_title(c.item) for c in counters)))
        return "\n".join(lines)

    def show_card(self) -> None:
        text = self.card_text()
        if text:
            self.show(text, True, "threat")

    # --- инвентарь врагов ---
    def on_inventory(self, snapshot: InventorySnapshot, clock: int | None, own_items: set[str]) -> None:
        if clock is None or snapshot.hero not in self.enemies or not self.settings.enabled:
            return
        threats = {t.hero.name for t in self.threats}
        for hint in self.tracker.update(snapshot.hero, snapshot.items, clock, threats, self.user_role(), own_items):
            self.show(self.hint_text(hint), True, "item_hint")

    def manual_item(self, hero: str, item: str, clock: int | None, own_items: set[str]) -> None:
        """«Вижу у врага предмет» (горячая клавиша). Считается угрозой, раз пользователь сам отметил."""
        threats = {t.hero.name for t in self.threats} | {hero}
        known = set(self.tracker.enemies.get(hero).items) if hero in self.tracker.enemies else set()
        for hint in self.tracker.update(hero, known | {item}, clock or 0, threats, self.user_role(), own_items):
            self.show(self.hint_text(hint), True, "item_hint")

    def hint_text(self, hint: ItemHint) -> str:
        counters = ", ".join(self.item_title(c.item) for c in hint.counters) or ru.HINT_NO_COUNTERS
        template = ru.HINT_ITEM if hint.kind == KIND_ITEM else ru.HINT_BUILDING
        return template.format(hero=self.hero_title(hint.hero), item=self.item_title(hint.item),
                               component=self.item_title(hint.component or ""), counters=counters)
