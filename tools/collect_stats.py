"""Сборщик статистики для слоя 2 (п. 6.6 ТЗ). Запускается в GitHub Actions раз в сутки.

Берёт уже сыгранные чужие публичные матчи из Steam Web API (GetMatchHistoryBySequenceNum):
герои, итоговые предметы, победа. Никаких аккаунтов и ников не сохраняет.
Копит счётчики за текущий патч в state.json.gz и строит компактный stats.json для программы.

Счётчики (игры, победы) для героя H и предмета I в итоговом инвентаре H:
- base[H][I]            — в целом;
- vs_hero[H][E][I]      — в играх против героя E;
- vs_trait[H][T][I]     — против врага с механикой T (слой 1: метки героев);
- vs_item[H][J][I]      — против врага, у которого в конце был предмет J с механикой.
Считаются только предметы-ответы из правил слоя 1: так объём данных остаётся небольшим.

Запуск: STEAM_API_KEY=... python tools/collect_stats.py --state state.json.gz --out out --calls 3000
"""
import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.threats.mechanics import MechanicsTagger, load_mechanics_config  # noqa: E402

STEAM_URL = "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistoryBySequenceNum/V001/"
OPENDOTA = "https://api.opendota.com/api"
MODE_BUCKETS = {1: "normal", 2: "normal", 3: "normal", 4: "normal", 5: "normal", 22: "normal", 23: "turbo"}
MIN_DURATION_SEC = 900  # короче 15 минут — скорее всего ливы и ремейки
DAY_SEC = 86400
# Steam ограничивает частоту (код 429). Пауза между запросами подстраивается сама:
# после 429 растёт, после серии удачных запросов понемногу уменьшается.
PAUSE_START_SEC = 3.0
PAUSE_MIN_SEC = 2.0
PAUSE_MAX_SEC = 15.0
PAUSE_UP_SEC = 1.5
PAUSE_DOWN_SEC = 0.25
CALM_CALLS = 40  # столько удачных запросов подряд — можно чуть быстрее
ERROR_PAUSE_SEC = 10
RATE_LIMIT_PAUSE_SEC = 30
MAX_ERRORS_IN_ROW = 20
STATS_MIN_GAMES = 20  # в stats.json попадают ячейки хотя бы с таким числом игр (меньше программа не использует)
STATE_VERSION = 1


def http_json(url: str, timeout: int = 60):
    request = urllib.request.Request(url, headers={"User-Agent": "DotaTimer-stats/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


# --- справочники ---
def load_constants():
    """Предметы, способности, герои и патчи из OpenDota (это данные Valve, dotaconstants)."""
    items = http_json(f"{OPENDOTA}/constants/items")
    abilities = http_json(f"{OPENDOTA}/constants/abilities")
    hero_abilities = http_json(f"{OPENDOTA}/constants/hero_abilities")
    heroes = http_json(f"{OPENDOTA}/constants/heroes")
    patches = http_json(f"{OPENDOTA}/constants/patch")
    return items, abilities, hero_abilities, heroes, patches


def current_patch(patches) -> str:
    return max(patches, key=lambda p: p.get("date", ""))["name"]


class Maps:
    """Что считать: id предметов → ключи, какие предметы — ответы, какие — механики врага."""

    def __init__(self, items: dict, tagger: MechanicsTagger, heroes: dict):
        self.item_by_id = {v["id"]: k for k, v in items.items() if isinstance(v, dict) and "id" in v}
        buyable = {k for k, v in items.items()
                   if isinstance(v, dict) and (v.get("cost") or 0) > 0 and not k.startswith("recipe")}
        self.answers = set()
        for rule in tagger.rules:
            self.answers |= tagger.candidate_items(rule, buyable)
        self.enemy_items = {k for k in buyable if tagger.item_traits(k)}
        self.hero_traits = {}
        for value in heroes.values():
            name = value["name"].removeprefix("npc_dota_hero_")
            self.hero_traits[value["id"]] = sorted(tagger.hero_traits(name))


# --- счётчики ---
def empty_state(patch: str) -> dict:
    return {"version": STATE_VERSION, "patch": patch, "matches": {}, "base": {}, "vs_hero": {}, "vs_trait": {},
            "vs_item": {}, "hero": {}}


def _bump(table: dict, keys: list, win: bool) -> None:
    node = table
    for key in keys[:-1]:
        node = node.setdefault(str(key), {})
    cell = node.setdefault(str(keys[-1]), [0, 0])
    cell[0] += 1
    cell[1] += int(win)


def match_rows(match: dict):
    """Проверка матча и игроки: [(hero_id, radiant, [item ids])]. None — матч не подходит."""
    bucket = MODE_BUCKETS.get(match.get("game_mode"))
    players = match.get("players") or []
    if bucket is None or len(players) != 10 or match.get("duration", 0) < MIN_DURATION_SEC:
        return None, None
    if any(p.get("leaver_status", 0) > 1 for p in players) or any(not p.get("hero_id") for p in players):
        return None, None
    rows = []
    for p in players:
        items = [p.get(f"item_{k}", 0) for k in range(6)] + [p.get(f"backpack_{k}", 0) for k in range(3)]
        rows.append((p["hero_id"], p.get("player_slot", 0) < 128, [i for i in items if i]))
    return bucket, rows


def aggregate(match: dict, state: dict, maps: Maps) -> bool:
    bucket, rows = match_rows(match)
    if rows is None:
        return False
    radiant_win = bool(match.get("radiant_win"))
    state["matches"][bucket] = state["matches"].get(bucket, 0) + 1
    for hero, radiant, item_ids in rows:
        win = radiant == radiant_win
        mine = {maps.item_by_id.get(i) for i in item_ids} & maps.answers
        enemies = [(h, [maps.item_by_id.get(i) for i in its]) for h, r, its in rows if r != radiant]
        enemy_traits = {t for h, _ in enemies for t in maps.hero_traits.get(h, [])}
        enemy_items = {k for _, its in enemies for k in its if k in maps.enemy_items}
        _bump(state["hero"], [bucket, hero], win)
        for item in mine:
            _bump(state["base"], [bucket, hero, item], win)
            for enemy, _ in enemies:
                _bump(state["vs_hero"], [bucket, hero, enemy, item], win)
            for trait in enemy_traits:
                _bump(state["vs_trait"], [bucket, hero, trait, item], win)
            for enemy_item in enemy_items:
                _bump(state["vs_item"], [bucket, hero, enemy_item, item], win)
    return True


def _prune(node, min_games: int):
    """Оставить только ячейки с достаточным числом игр."""
    if isinstance(node, list):
        return node if node[0] >= min_games else None
    out = {}
    for key, value in node.items():
        pruned = _prune(value, min_games)
        if pruned:
            out[key] = pruned
    return out or None


def build_stats(state: dict, min_games: int = STATS_MIN_GAMES) -> dict:
    """Компактный файл для программы: только ячейки с достаточной выборкой."""
    return {
        "version": STATE_VERSION,
        "patch": state["patch"],
        "generated_at": int(time.time()),
        "matches": state["matches"],
        "min_games": min_games,
        "source": "Steam Web API (сыгранные публичные матчи), OpenDota constants (данные Valve)",
        **{name: _prune(state[name], min_games) or {} for name in ("hero", "base", "vs_hero", "vs_trait", "vs_item")},
    }


# --- сбор ---
def start_seq_yesterday() -> int:
    """Номер последовательности матча примерно суточной давности (через OpenDota /publicMatches)."""
    recent = http_json(f"{OPENDOTA}/publicMatches")
    newest = max(m["match_id"] for m in recent)
    # номера матчей растут примерно на 1.5 млн в сутки; берём с запасом
    older = http_json(f"{OPENDOTA}/publicMatches?less_than_match_id={newest - 1_800_000}")
    match_id = older[0]["match_id"]
    return http_json(f"{OPENDOTA}/matches/{match_id}")["match_seq_num"]


def collect(key: str, state: dict, maps: Maps, calls: int, seq: int, max_minutes: float) -> int:
    kept = errors = calm = 0
    pause = PAUSE_START_SEC
    deadline = time.monotonic() + max_minutes * 60
    for i in range(calls):
        if time.monotonic() > deadline:
            print(f"время вышло ({max_minutes:.0f} мин), сохраняю собранное", flush=True)
            break
        try:
            result = http_json(f"{STEAM_URL}?key={key}&start_at_match_seq_num={seq}&matches_requested=100")["result"]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            # в лог — только тип и код ошибки: адрес запроса содержит ключ
            code = getattr(error, "code", "")
            print(f"запрос {i}: ошибка {type(error).__name__} {code}, пауза", flush=True)
            errors += 1
            calm = 0
            if errors >= MAX_ERRORS_IN_ROW:
                print("слишком много ошибок подряд, заканчиваю сбор")
                break
            if code == 429:
                pause = min(PAUSE_MAX_SEC, pause + PAUSE_UP_SEC)
            time.sleep(RATE_LIMIT_PAUSE_SEC if code == 429 else ERROR_PAUSE_SEC)
            continue
        errors = 0
        calm += 1
        if calm >= CALM_CALLS:
            pause, calm = max(PAUSE_MIN_SEC, pause - PAUSE_DOWN_SEC), 0
        matches = result.get("matches") or []
        if not matches:
            break
        seq = matches[-1]["match_seq_num"] + 1
        kept += sum(aggregate(m, state, maps) for m in matches)
        if i % 100 == 0:
            print(f"запрос {i}/{calls}, матчей учтено {kept}, пауза {pause:.1f} сек", flush=True)
        time.sleep(pause)
    return kept


def load_state(path: Path, patch: str) -> dict:
    if path.is_file():
        with gzip.open(path, "rt", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("version") == STATE_VERSION and state.get("patch") == patch:
            return state
        print(f"новый патч {patch} (было {state.get('patch')}): счётчики обнулены")
    return empty_state(patch)


def main() -> None:
    parser = argparse.ArgumentParser(description="Сбор статистики предметов по сыгранным матчам")
    parser.add_argument("--state", default="state.json.gz")
    parser.add_argument("--out", default="out")
    parser.add_argument("--calls", type=int, default=2500, help="запросов к Steam (по 100 матчей)")
    parser.add_argument("--max-minutes", type=float, default=240, help="после этого сбор заканчивается и сохраняется")
    args = parser.parse_args()

    key = os.environ.get("STEAM_API_KEY", "").strip()
    if not key:
        sys.exit("нет STEAM_API_KEY")
    items, abilities, hero_abilities, heroes, patches = load_constants()
    patch = current_patch(patches)
    roles = {v["name"].removeprefix("npc_dota_hero_"): tuple(v.get("roles") or ()) for v in heroes.values()}
    maps = Maps(items, MechanicsTagger(load_mechanics_config(), items, abilities, hero_abilities, roles), heroes)
    state = load_state(Path(args.state), patch)
    print(f"патч {patch}; предметов-ответов {len(maps.answers)}; матчей в копилке {state['matches']}")

    kept = collect(key, state, maps, args.calls, start_seq_yesterday(), args.max_minutes)
    print(f"за запуск учтено матчей: {kept}; всего: {state['matches']}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / "state.json.gz", "wt", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"))
    (out / "stats.json").write_text(json.dumps(build_stats(state), ensure_ascii=False, separators=(",", ":")),
                                    encoding="utf-8")
    print(f"stats.json: {(out / 'stats.json').stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
