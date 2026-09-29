"""Сбор сырых матчей (docs/RECOMMENDER_SPEC.md, разделы 2.1–2.3). Запускается в GitHub Actions раз в сутки.

Два режима:
  matches — сыгранные чужие публичные матчи из Steam Web API (GetMatchHistoryBySequenceNum),
            одна строка на игрока, Parquet. Аккаунты игроков не сохраняются.
  ranks   — средний ранг матчей из OpenDota /publicMatches: match_id → avg_rank_tier.
            Запускается после matches и идёт ровно по диапазону номеров собранных матчей
            (от 95-го процентиля вниз к 5-му). /publicMatches отдаёт номера плотно: в пробе
            29.09.2026 ранг был у ~60% матчей подряд, 100 матчей на ~160 номеров.
Суммы здесь не считаются: расчёт отдельно, по сырым файлам (tools/compute_recs.py, DuckDB).

Запуск:
  STEAM_API_KEY=... python tools/collect_raw.py matches --out raw --calls 2500 --max-minutes 240
  python tools/collect_raw.py ranks --matches raw/matches-*.parquet --out raw --calls 2400 --max-minutes 60
"""
import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import json

import pyarrow as pa
import pyarrow.parquet as pq

STEAM_URL = "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistoryBySequenceNum/V001/"
OPENDOTA = "https://api.opendota.com/api"

# Режимы: All Pick / Ranked All Pick → normal, Turbo → turbo (спека 2.2). Остальные отбрасываются.
MODE_BUCKETS = {1: "normal", 22: "normal", 23: "turbo"}
MIN_DURATION_SEC = {"normal": 15 * 60, "turbo": 10 * 60}  # спека 2.2
# Точка старта: номера матчей растут примерно на 1.5 млн в сутки; берём с запасом, чтобы матчи были доиграны
ANCHOR_BACK_IDS = 1_800_000
ANCHOR_TRIES = 10  # сколько настоящих матчей пробовать как стартовый

# Темп для Steam — только по замерам из docs/DATA_SOURCES.md (раздел «Steam: лимит частоты»). Не менять без новых замеров.
# Коротко: пауза 1,5 сек → «429» на каждом 10–12-м запросе; 3–7,5 сек → на каждом ~22-м; 8–9 сек → раз в сотни запросов.
# Поэтому стартуем сразу с 8 сек и не опускаемся ниже 7.
PAUSE_START_SEC, PAUSE_MIN_SEC, PAUSE_MAX_SEC = 8.0, 7.0, 15.0
PAUSE_UP_SEC, PAUSE_DOWN_SEC, CALM_CALLS = 1.0, 0.5, 50
RATE_LIMIT_PAUSE_SEC, ERROR_PAUSE_SEC, MAX_ERRORS_IN_ROW = 30, 10, 20
OPENDOTA_PAUSE_SEC = 1.1  # бесплатный лимит OpenDota — 60 запросов в минуту

MATCH_SCHEMA = pa.schema([
    ("match_id", pa.int64()), ("start_time", pa.int32()), ("duration", pa.int16()), ("game_mode", pa.int8()),
    ("lobby_type", pa.int8()), ("radiant_win", pa.bool_()), ("patch", pa.string()), ("is_radiant", pa.bool_()),
    ("hero_id", pa.int16()), ("items", pa.list_(pa.int16())), ("item_neutral", pa.int16()),
    ("net_worth", pa.int32()), ("gold_per_min", pa.int16()), ("xp_per_min", pa.int16()),
    ("kills", pa.int8()), ("deaths", pa.int8()), ("assists", pa.int8()), ("last_hits", pa.int16()),
])
RANK_SCHEMA = pa.schema([("match_id", pa.int64()), ("avg_rank_tier", pa.int8()), ("num_rank_tier", pa.int8())])


def http_json(url: str, timeout: int = 60):
    request = urllib.request.Request(url, headers={"User-Agent": "DotaTimer-stats/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def patch_for(start_time: int, patches: list) -> str:
    """Крупный патч по дате матча (список патчей из dotaconstants через OpenDota)."""
    name = patches[0]["name"]
    for p in sorted(patches, key=lambda x: x["date"]):
        when = datetime.fromisoformat(p["date"].replace("Z", "+00:00")).timestamp()
        if when <= start_time:
            name = p["name"]
    return name


def anchor_seq_num() -> int:
    """Номер последовательности матча примерно суточной давности — точка старта сбора.

    Номер «самый свежий минус N» может попасть в дыру (такого матча нет, OpenDota отвечает 404),
    поэтому берём настоящие матчи чуть старше этого номера и пробуем по очереди.
    """
    newest = max(m["match_id"] for m in http_json(f"{OPENDOTA}/publicMatches"))
    older = http_json(f"{OPENDOTA}/publicMatches?less_than_match_id={newest - ANCHOR_BACK_IDS}")
    for match_id in sorted((m["match_id"] for m in older), reverse=True)[:ANCHOR_TRIES]:
        try:
            return http_json(f"{OPENDOTA}/matches/{match_id}")["match_seq_num"]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            print(f"стартовый матч {match_id}: ошибка {type(error).__name__} {getattr(error, 'code', '')}", flush=True)
            time.sleep(ERROR_PAUSE_SEC)
    sys.exit("не удалось найти стартовый матч в OpenDota")


def match_rows(match: dict, patches: list) -> list[dict]:
    """10 строк (по игроку) или пусто, если матч не подходит под фильтры спеки 2.2."""
    bucket = MODE_BUCKETS.get(match.get("game_mode"))
    players = match.get("players") or []
    if bucket is None or len(players) != 10 or match.get("duration", 0) < MIN_DURATION_SEC[bucket]:
        return []
    if any(p.get("leaver_status", 0) > 1 or not p.get("hero_id") for p in players):
        return []
    patch = patch_for(match.get("start_time", 0), patches)
    rows = []
    for p in players:
        items = [p.get(f"item_{k}", 0) for k in range(6)] + [p.get(f"backpack_{k}", 0) for k in range(3)]
        rows.append({
            "match_id": match["match_id"], "start_time": match.get("start_time", 0),
            "duration": min(match["duration"], 32767), "game_mode": match["game_mode"],
            "lobby_type": match.get("lobby_type", 0), "radiant_win": bool(match.get("radiant_win")),
            "patch": patch, "is_radiant": p.get("player_slot", 0) < 128, "hero_id": p["hero_id"],
            "items": [i for i in items if i], "item_neutral": p.get("item_neutral", 0) or 0,
            "net_worth": p.get("net_worth", 0), "gold_per_min": min(p.get("gold_per_min", 0), 32767),
            "xp_per_min": min(p.get("xp_per_min", 0), 32767), "kills": min(p.get("kills", 0), 127),
            "deaths": min(p.get("deaths", 0), 127), "assists": min(p.get("assists", 0), 127),
            "last_hits": min(p.get("last_hits", 0), 32767),
        })
    return rows


def write_parquet(rows: list[dict], schema: pa.Schema, path: Path) -> None:
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path, compression="zstd")


def resume_seq_num(state: Path | None) -> int | None:
    """Откуда продолжить: номер из next_seq.txt прошлого запуска или по последнему матчу в прошлом файле.

    Сбор идёт несколько раз в сутки подряд, поэтому каждый запуск продолжает с места, где остановился прошлый
    (без дыр и без повторов). Если прошлого состояния нет — None, тогда старт от якоря.
    """
    if not state or not state.exists():
        return None
    saved = state / "next_seq.txt"
    if saved.exists() and saved.read_text().strip().isdigit():
        return int(saved.read_text().strip())
    files = sorted(state.glob("matches-*.parquet"))
    if not files:
        return None
    last_id = max(pq.read_table(files[-1], columns=["match_id"]).column("match_id").to_pylist() or [0])
    try:
        return http_json(f"{OPENDOTA}/matches/{last_id}")["match_seq_num"] + 1
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        print(f"не нашёл последний матч {last_id} в OpenDota ({type(error).__name__}), старт от якоря", flush=True)
        return None


def collect_matches(key: str, calls: int, max_minutes: float, out: Path, tag: str, state: Path | None = None) -> Path:
    patches = http_json(f"{OPENDOTA}/constants/patch")
    seq = resume_seq_num(state)
    print(f"старт: {'продолжаю с прошлого запуска' if seq else 'от якоря (матчи суточной давности)'}", flush=True)
    seq = seq or anchor_seq_num()
    rows, kept, errors, calm, pause = [], 0, 0, 0, PAUSE_START_SEC
    started = time.monotonic()
    deadline = started + max_minutes * 60
    limits = 0
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
            errors, calm = errors + 1, 0
            if errors >= MAX_ERRORS_IN_ROW:
                print("слишком много ошибок подряд, заканчиваю сбор")
                break
            if code == 429:
                pause, limits = min(PAUSE_MAX_SEC, pause + PAUSE_UP_SEC), limits + 1
            time.sleep(RATE_LIMIT_PAUSE_SEC if code == 429 else ERROR_PAUSE_SEC)
            continue
        errors, calm = 0, calm + 1
        if calm >= CALM_CALLS:
            pause, calm = max(PAUSE_MIN_SEC, pause - PAUSE_DOWN_SEC), 0
        matches = result.get("matches") or []
        if not matches:
            break
        seq = matches[-1]["match_seq_num"] + 1
        for match in matches:
            new = match_rows(match, patches)
            if new:
                rows.extend(new)
                kept += 1
        if i % 100 == 0:
            minutes = (time.monotonic() - started) / 60
            print(f"запрос {i}/{calls}, матчей {kept}, пауза {pause:.1f} сек, {minutes:.0f} мин, "
                  f"{i / max(minutes, 0.01):.1f} запросов в минуту, ошибок «429» {limits}", flush=True)
        time.sleep(pause)
    path = out / f"matches-{tag}.parquet"
    write_parquet(rows, MATCH_SCHEMA, path)
    (out / "next_seq.txt").write_text(str(seq))  # следующий запуск продолжит отсюда
    print(f"матчей: {kept}, строк: {len(rows)}, файл {path.name}: {path.stat().st_size // 1024} КБ")
    return path


def id_range(matches_file: Path) -> tuple[int, int]:
    """5-й и 95-й процентили номеров собранных матчей: там их основная масса."""
    ids = sorted(set(pq.read_table(matches_file, columns=["match_id"]).column("match_id").to_pylist()))
    if not ids:
        raise SystemExit("в файле матчей пусто")
    return ids[int(len(ids) * 0.05)], ids[min(len(ids) - 1, int(len(ids) * 0.95))]


def collect_ranks(calls: int, max_minutes: float, out: Path, tag: str, matches_file: Path) -> Path:
    """Ранги из /publicMatches по диапазону номеров собранных матчей, сверху вниз."""
    low, high = id_range(matches_file)
    print(f"диапазон номеров матчей: {low}–{high} ({high - low} номеров)")
    ranks, cursor = {}, high + 1
    deadline = time.monotonic() + max_minutes * 60
    for i in range(calls):
        if time.monotonic() > deadline or cursor <= low:
            break
        try:
            page = http_json(f"{OPENDOTA}/publicMatches?less_than_match_id={cursor}")
        except (urllib.error.URLError, OSError, ValueError) as error:
            print(f"запрос {i}: ошибка {type(error).__name__} {getattr(error, 'code', '')}", flush=True)
            time.sleep(ERROR_PAUSE_SEC)
            continue
        if not page:
            break
        for m in page:
            if m.get("avg_rank_tier") is not None:
                ranks[m["match_id"]] = (m["avg_rank_tier"], m.get("num_rank_tier") or 0)
        cursor = min(m["match_id"] for m in page)
        if i % 200 == 0:
            print(f"запрос {i}/{calls}, матчей с рангом {len(ranks)}, осталось номеров {cursor - low}", flush=True)
        time.sleep(OPENDOTA_PAUSE_SEC)
    rows = [{"match_id": k, "avg_rank_tier": v[0], "num_rank_tier": v[1]} for k, v in ranks.items()]
    path = out / f"ranks-{tag}.parquet"
    write_parquet(rows, RANK_SCHEMA, path)
    print(f"матчей с рангом: {len(rows)}, файл {path.name}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Сбор сырых матчей и рангов")
    parser.add_argument("what", choices=["matches", "ranks"])
    parser.add_argument("--out", default="raw")
    parser.add_argument("--calls", type=int, default=2500)
    parser.add_argument("--max-minutes", type=float, default=240)
    parser.add_argument("--matches", help="для ranks: файл матчей этого запуска")
    parser.add_argument("--state", help="для matches: папка с next_seq.txt или последним файлом матчей прошлого запуска")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")  # один файл на запуск; старые не перезаписываются
    if args.what == "matches":
        key = os.environ.get("STEAM_API_KEY", "").strip()
        if not key:
            sys.exit("нет STEAM_API_KEY")
        collect_matches(key, args.calls, args.max_minutes, out, tag, Path(args.state) if args.state else None)
    else:
        if not args.matches:
            sys.exit("для ranks нужен --matches")
        collect_ranks(args.calls, args.max_minutes, out, tag, Path(args.matches))


if __name__ == "__main__":
    main()
