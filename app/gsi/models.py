"""Разбор GSI-пакета в удобный вид и отсев повторяющихся событий."""
import json
from dataclasses import dataclass, field

STATE_PRE_GAME = "DOTA_GAMERULES_STATE_PRE_GAME"
STATE_IN_PROGRESS = "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"
STATE_POST_GAME = "DOTA_GAMERULES_STATE_POST_GAME"

# События GSI, которые запускают таймеры (event_type → имя сигнала для движка).
# Пока пусто: Рошан убран по решению автора (игра сама объявляет его смерть и возрождение),
# но GSI присылает roshan_killed / aegis_picked_up, если понадобится.
GSI_TRIGGERS: dict[str, str] = {}


@dataclass(frozen=True)
class GameEvent:
    """Событие из блока events. clock_time пересчитан в игровые часы."""

    event_type: str
    clock_time: int
    game_time: float | None = None  # исходное время из пакета, по нему отсеиваются повторы
    data: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class GameState:
    """Нужные программе поля одного пакета."""

    match_id: str | None
    clock_time: int | None
    game_state: str | None
    paused: bool
    daytime: bool | None
    hero_name: str | None
    team: str | None
    account_id: str | None
    events: tuple[GameEvent, ...] = ()
    hero_alive: bool | None = None
    own_items: frozenset[str] = frozenset()  # предметы героя пользователя (слоты, рюкзак, тайник)
    income_gold: int | None = None  # пассивное золото с начала игры (для определения Турбо)
    gold: int | None = None  # текущее золото пользователя

    @property
    def in_match(self) -> bool:
        """Идёт подготовка к горну или сама игра (таймеры работают только тут)."""
        return self.game_state in (STATE_PRE_GAME, STATE_IN_PROGRESS)


ITEM_PREFIX = "item_"


def parse_own_items(items_block: dict) -> frozenset[str]:
    """Блок items из GSI → ключи предметов без префикса item_ (как в OpenDota)."""
    found = set()
    for slot, value in (items_block or {}).items():
        if not isinstance(value, dict) or not slot.startswith(("slot", "stash", "neutral")):
            continue
        name = value.get("name", "")
        if name.startswith(ITEM_PREFIX):
            found.add(name.removeprefix(ITEM_PREFIX))
    return frozenset(found)


def parse_packet(data: dict) -> GameState:
    game_map = data.get("map") or {}
    player = data.get("player") or {}
    clock = game_map.get("clock_time")
    game_time = game_map.get("game_time")
    # события датируются game_time; разница с clock_time почти постоянна (±1 сек из-за округления)
    offset = (game_time - clock) if isinstance(clock, (int, float)) and isinstance(game_time, (int, float)) else None
    events = []
    for raw in data.get("events") or []:
        if not isinstance(raw, dict) or "event_type" not in raw:
            continue
        ev_time = raw.get("game_time")
        if offset is not None and isinstance(ev_time, (int, float)):
            ev_clock = int(ev_time - offset)
        else:
            ev_clock = int(clock) if isinstance(clock, (int, float)) else 0
        payload = {k: v for k, v in raw.items() if k not in ("event_type", "game_time")}
        events.append(GameEvent(raw["event_type"], ev_clock, ev_time, payload))
    return GameState(
        match_id=str(game_map["matchid"]) if game_map.get("matchid") not in (None, "", "0", 0) else None,
        clock_time=int(clock) if isinstance(clock, (int, float)) else None,
        game_state=game_map.get("game_state"),
        paused=bool(game_map.get("paused", False)),
        daytime=game_map.get("daytime"),
        hero_name=(data.get("hero") or {}).get("name"),
        team=player.get("team_name"),
        account_id=str(player["accountid"]) if player.get("accountid") else None,
        events=tuple(events),
        hero_alive=(data.get("hero") or {}).get("alive"),
        own_items=parse_own_items(data.get("items")),
        income_gold=player.get("gold_from_income"),
        gold=player.get("gold"),
    )


class EventDeduper:
    """GSI повторяет одно событие в нескольких пакетах подряд. Пропускает только новые."""

    def __init__(self):
        self._seen: set[str] = set()
        self._match_id: str | None = None

    def new_events(self, state: GameState) -> list[GameEvent]:
        if state.match_id and state.match_id != self._match_id:
            self._match_id = state.match_id
            self._seen.clear()
        fresh = []
        for event in state.events:
            key = json.dumps([event.event_type, event.game_time, event.data], sort_keys=True, ensure_ascii=False)
            if key not in self._seen:
                self._seen.add(key)
                fresh.append(event)
        return fresh
