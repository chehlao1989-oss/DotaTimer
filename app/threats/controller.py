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
from app.threats.item_hints import WIKI_HERO, Choice, CounterTable, choose
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
# снимок «врага» считается моим героем, если совпало от 80% заметных предметов и их хотя бы 3 (BUGLOG №58:
# у Ogre Magi и Viper записаны 5 из 5 предметов моего Huskar); порог — не игровое число, а проверка совпадения
OWN_MATCH_SHARE, OWN_MATCH_MIN = 0.8, 3
OWN_SUBSET_MIN = 2  # или все прочитанные у «врага» заметные предметы (от 2) есть у меня (BUGLOG №59)
OVERLAY_SLOTS = {0, 1}  # места на панели, которые закрывает строка оверлея FPS слева вверху (BUGLOG №55)


def wiki_reason(why) -> str | None:
    """Причина против героя словами: «отражает Stifling Dagger»; способностей нет — общая фраза типа."""
    if not why:
        return None
    with_ability, plain = ru.WIKI_REASONS.get(why[0], (None, None))
    if why[1] and with_ability:
        return with_ability.format(ability=ru.ABILITIES_JOIN.join(why[1]))
    return plain


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
        self.allies: list[str] = []  # союзники по верхней панели, без своего героя (DECISIONS №26)
        self.threats: list[Threat] = []
        self.own_hero: str | None = None
        self.own_team: str | None = None
        self.own_items: set[str] = set()
        self._topbar_tries = 0
        self._next_topbar_at: int | None = None
        self._was_alive: bool | None = None
        self._dropped: set[str] = set()  # выделенные герои, чьи снимки пропущены (в лог один раз за катку)
        self.card_duration: float | None = None  # сколько секунд держать полную карточку
        self.card_items = None  # функция: герой-угроза → строка советов из модуля рекомендаций
        self.on_enemies_changed = None  # служба пересчитывает рекомендации
        self.rank_threats = None  # функция: герои-враги → угрозы (из recs.zip); None — старая оценка
        self.item_counters = None  # функция: (герой-враг, предмет) → ключи советов (из recs.zip); None — старая
        self.hint_duration: float | None = None  # сколько секунд держать подсказку «у врага появился предмет»
        # подсказки v2 (BUGLOG №54): ответы — из таблицы механик и страниц Counters вики, выбор — под моего героя
        self.counter_table: CounterTable | None = None
        self.own_shares: Callable[[], dict[str, float]] | None = None  # предмет → % игр на моём герое в роли
        self.hint_min_share = 0.0
        self.hint_limit = 2
        self.hint_repeat = 3  # сколько последних подсказок показать по клавише повтора
        self.recent_hints: list[tuple[str, dict]] = []  # подсказки этой катки: текст и оформление, последняя — в конце

    def use_counter_table(self, table: CounterTable, own_shares: Callable[[], dict[str, float]], min_share: float,
                          limit: int, likely_parent=None) -> None:
        """Включить подсказки v2: важные предметы врага — только из таблицы (дешёвые подсказок не дают).
        likely_parent(герой, [предметы]) — во что этот враг обычно собирает деталь."""
        self.counter_table, self.own_shares = table, own_shares
        self.hint_min_share, self.hint_limit = min_share, limit
        self.tracker = ItemTracker(self.data, self.tagger, self.config.min_component_cost,
                                   important=table.threat_items(), likely_parent=likely_parent)

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

    def ally_ids(self) -> tuple[int, ...]:
        return tuple(h.id for h in (self.data.hero_by_name(a) for a in self.allies) if h)

    def bucket(self) -> str:
        return BUCKET_TURBO if self.turbo() else BUCKET_NORMAL

    # --- матч ---
    def new_match(self) -> None:
        self.tracker.reset()
        self.recent_hints = []
        self._dropped.clear()
        # герой прошлой катки не тянется в новую: 21.10 в катке на Spirit Breaker своим считался Lone Druid
        # (GSI не прислал героя), и имя Spirit Breaker не сверялось на нижней панели (BUGLOG №59)
        self.own_hero = None
        self.enemies, self.threats = [], []
        self.allies = []
        self._topbar_tries = 0
        self._next_topbar_at = None
        self._was_alive = None

    def on_game_state(self, clock: int | None, in_progress: bool, own_hero: str | None, own_team: str | None,
                      alive: bool | None, own_items: set[str] | None = None) -> bool:
        """Каждый пакет GSI. Возвращает True, если пора снять верхнюю панель."""
        hero = (own_hero or "").removeprefix("npc_dota_hero_") or self.own_hero
        if hero != self.own_hero:
            log.info("Мой герой: %s", hero)  # для разбора подсказок по логу (задача v2: в логе героя не было)
        self.own_hero = hero
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
        own_side = {"radiant": "dire", "dire": "radiant"}.get(side or "")
        allies = [a for a in (result.heroes(own_side) if own_side else []) if a and a != self.own_hero]
        if allies != self.allies:
            self.allies = allies
            log.info("Союзники: %s", allies)
            if self.on_enemies_changed and not (known and known != self.enemies):
                self.on_enemies_changed()  # советы пересчитать и без смены врагов
        if len(known) == 5:
            self._next_topbar_at = None  # все узнаны, больше не снимаем
        if known and known != self.enemies:
            self.set_enemies(known)
        if len(known) < 5 and self._next_topbar_at is None and self._topbar_tries >= TOPBAR_MAX_TRIES:
            missing = [i for i, e in enumerate(enemies) if not e]
            # пропали только крайние левые портреты Сил Света — их закрывает оверлей FPS (видео автора 16.10)
            overlay = side == "radiant" and missing and set(missing) <= OVERLAY_SLOTS
            text = ru.TOPBAR_OVERLAY if overlay else (ru.TOPBAR_PARTIAL if known else ru.TOPBAR_FAILED)
            log.info("Врагов узнано %d из 5, подсказка: %s", len(known), text)
            self.show(text, False, None)
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
    def owned(self, item: str) -> bool:
        """Есть сам предмет или то, что из него собирают (Orchid засчитан, если собран Bloodthorn)."""
        return item in self.own_items or any(item in self._components(z) for z in self.own_items)

    def _components(self, key: str, depth: int = 3) -> set[str]:
        item = self.data.items.get(key)
        if item is None or depth == 0:
            return set()
        found = set(item.components)
        for c in item.components:
            found |= self._components(c, depth - 1)
        return found

    def choices(self, counters) -> list[Choice]:
        shares = self.own_shares() if self.own_shares else {}
        return choose(counters, shares.get, self.owned, self.hint_min_share, self.hint_limit)

    def choices_vs_item(self, item: str) -> list[Choice]:
        return self.choices(self.counter_table.vs_item(item)) if self.counter_table else []

    def choices_vs_hero(self, hero: str) -> list[Choice]:
        return self.choices(self.counter_table.vs_hero(hero)) if self.counter_table else []

    def format_choices(self, choices: list[Choice]) -> str:
        """«Eye of Skadi (режет лечение), Spirit Vessel (режет лечение)»; против героя — причина по тексту вики
        («Lotus Orb (отражает Stifling Dagger)», BUGLOG №61), не размечена — без причины."""
        parts = []
        for c in choices:
            item = self.item_title(c.item)
            if c.answer == WIKI_HERO:
                reason = wiki_reason(c.why)
            else:
                reason = ru.PROP_REASONS.get(c.prop) or ru.MECHANIC_REASONS.get(c.answer)
            if reason:
                parts.append((ru.HINT_CHOICE_RARE if c.rare else ru.HINT_CHOICE).format(item=item, reason=reason))
            else:
                parts.append(ru.HINT_CHOICE_HERO_RARE.format(item=item) if c.rare else item)
        return ", ".join(parts)

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
                if items:  # строку «→ явного ответа нет» не показываем (задача v2, автор 16.10)
                    lines.append(ru.CARD_COUNTERS.format(items=items))
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
    def looks_like_mine(self, items: set[str]) -> bool:
        """Снимок «врага» с моими предметами — это мой герой, прочитанный под чужим именем (BUGLOG №58).
        Мой инвентарь известен из GSI; совпало от OWN_MATCH_SHARE заметных предметов (не расходников)."""
        mine = {i for i in self.own_items if not self.tracker._ignored(i)}
        seen = {i for i in items if not self.tracker._ignored(i)}
        if len(seen) >= OWN_SUBSET_MIN and seen <= mine:
            return True  # всё, что прочитано у «врага», есть у меня (21.10: Assault Cuirass и Desolator моего Lycan)
        if len(mine) < OWN_MATCH_MIN or len(seen) < OWN_MATCH_MIN:
            return False
        return len(mine & seen) >= OWN_MATCH_SHARE * max(len(mine), len(seen))

    def on_inventory(self, snapshot: InventorySnapshot, clock: int | None) -> None:
        if snapshot.hero in self.enemies and self.looks_like_mine(snapshot.items):
            if ("mine", snapshot.hero) not in self._dropped:
                self._dropped.add(("mine", snapshot.hero))
                log.info("Снимок %s совпадает с моим инвентарём — это мой герой, пропущен", snapshot.hero)
            return
        if clock is None or snapshot.hero not in self.enemies or not self.settings.enabled:
            if snapshot.hero != self.own_hero and snapshot.hero not in self._dropped:
                self._dropped.add(snapshot.hero)  # один раз за катку: почему выделенный герой не дал событий
                log.info("Выделен %s — пропущен (время %s, враги %s)", snapshot.hero, clock, self.enemies)
            return
        # подсказка — по любому врагу, по которому кликнули (раньше только по двум угрозам, BUGLOG №30)
        for hint in self.tracker.update(snapshot.hero, snapshot.items, clock, set(self.enemies)):
            self._show_hint(hint)  # без голоса (автор, 30.09)

    def manual_item(self, hero: str, item: str, clock: int | None) -> None:
        """«Вижу у врага предмет» (горячая клавиша). Считается угрозой, раз пользователь сам отметил."""
        threats = {t.hero.name for t in self.threats} | {hero}
        known = set(self.tracker.enemies[hero].items) if hero in self.tracker.enemies else set()
        for hint in self.tracker.update(hero, known | {item}, clock or 0, threats):
            self._show_hint(hint)

    def _show_hint(self, hint: ItemHint) -> None:
        text = self.hint_text(hint)
        if text is None:  # «явного ответа нет» не показываем (решение автора 05.10), только в журнал
            log.info("Подсказка не показана, явного ответа нет: %s — %s%s", hint.hero, hint.item,
                     " (ответы уже есть или не подтверждены)" if self.counter_table else "")
            return
        log.info("Подсказка: %s", text.replace("\n", " | "))
        extras = self.hint_extras(hint)
        self.recent_hints = ([h for h in self.recent_hints if h[0] != text] + [(text, extras)])[-self.hint_repeat:]
        self.show(text, True, None, self.hint_duration, **extras)

    def repeat_hints(self) -> None:
        """Клавиша повтора: последние подсказки ещё раз — прочитать спокойно, когда ждёшь возрождения
        (в драке они уходят раньше, чем успеваешь прочитать; просьба автора 23.10). Без голоса."""
        if not self.recent_hints:
            self.show(ru.HINT_REPEAT_NONE, False, None)
            return
        for text, extras in self.recent_hints[-self.hint_repeat:]:
            self.show(text, True, None, self.hint_duration, **extras)

    def hint_extras(self, hint: ItemHint) -> dict:
        """Оформление подсказки как оповещения Доты: портрет врага и иконка предмета, предмет золотом."""
        return {"icons": [("heroes", hint.hero), ("items", hint.item)], "highlights": [self.item_title(hint.item)]}

    def hint_text(self, hint: ItemHint) -> str | None:
        """Текст подсказки; None — явного ответа нет (такие подсказки не показываются, решение автора 05.10)."""
        enemy = self.data.hero_by_name(hint.hero)
        if self.counter_table is not None:
            choices = self.choices_vs_item(hint.item)
            if not choices:
                return None
            template = ru.HINT_ITEM if hint.kind == KIND_ITEM else ru.HINT_BUILDING
            return template.format(hero=self.hero_title(hint.hero), item=self.item_title(hint.item),
                                   component=self.item_title(hint.component or ""),
                                   counters=self.format_choices(choices))
        if self.item_counters is not None:
            keys = self.item_counters(hint.hero, hint.item)
            if not keys:
                return None
            counters = ", ".join(self.item_title(k) for k in keys)
            template = ru.HINT_ITEM if hint.kind == KIND_ITEM else ru.HINT_BUILDING
            return template.format(hero=self.hero_title(hint.hero), item=self.item_title(hint.item),
                                   component=self.item_title(hint.component or ""), counters=counters)
        suggestions = self.advisor.suggest(list(hint.rules), self.my_hero_id(), self.bucket(),
                                           enemy_hero_id=enemy.id if enemy else None, enemy_item=hint.item,
                                           owned=self.own_items, limit=self.config.suggestions_per_hint)
        if not suggestions:
            return None
        counters = self.format_suggestions(suggestions, self.item_title(hint.item))
        template = ru.HINT_ITEM if hint.kind == KIND_ITEM else ru.HINT_BUILDING
        return template.format(hero=self.hero_title(hint.hero), item=self.item_title(hint.item),
                               component=self.item_title(hint.component or ""), counters=counters)
