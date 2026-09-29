"""Сбор сырых матчей (docs/RECOMMENDER_SPEC.md, разделы 2.1–2.3). Запускается в GitHub Actions каждые 6 часов.
Лимиты и замеры источников — docs/DATA_SOURCES.md (читать перед любой правкой темпа).

Два режима:
  matches — сыгранные чужие публичные матчи из Steam Web API (GetMatchHistoryBySequenceNum),
            одна строка на игрока, Parquet. Аккаунты игроков не сохраняются.
            Каждые 15 мин собранное сохраняется частью и сразу выкладывается в релиз.
  ranks   — средний ранг матчей из OpenDota /publicMatches: match_id → avg_rank_tier.
            Запускается после matches; запросы равномерно раскиданы по диапазону номеров собранных матчей,
            их число — по остатку суточного лимита из заголовка ответа OpenDota.
Суммы здесь не считаются: расчёт отдельно, по сырым файлам (tools/compute_recs.py, DuckDB).

Запуск:
  STEAM_API_KEY=... python tools/collect_raw.py matches --out raw --state state --release data-raw --max-minutes 290
  python tools/collect_raw.py ranks --matches "raw/matches-*.parquet" --out raw --release data-raw --max-minutes 20
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

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

# Регулятор темпа Steam (схема автора, 29.09; docs/DATA_SOURCES.md). Цель — максимум матчей в час, а не минимум 429:
# после 429 ждём Retry-After (если нет — 20 сек) и пауза ×1,5; после 20 успешных подряд пауза ×0,9; пауза 1–10 сек.
PAUSE_START_SEC, PAUSE_MIN_SEC, PAUSE_MAX_SEC = 3.0, 1.0, 10.0
PAUSE_UP_FACTOR, PAUSE_DOWN_FACTOR, CALM_CALLS = 1.5, 0.9, 20
RETRY_AFTER_DEFAULT_SEC, ERROR_PAUSE_SEC, MAX_ERRORS_IN_ROW = 20.0, 10, 20
LONG_WAIT_SEC = 300  # после MAX_ERRORS_IN_ROW ошибок подряд ждём и продолжаем (до конца отведённого времени)
LOG_EVERY_SEC = 600  # строка в журнал каждые 10 мин
SAVE_EVERY_SEC = 900  # часть файла в релиз каждые 15 мин: падение теряет не больше 15 мин
OPENDOTA_PAUSE_SEC = 1.1  # OpenDota: 60 запросов в минуту
OPENDOTA_DAY_RESERVE = 100  # остановка, когда в заголовке X-Rate-Limit-Remaining-Day осталось столько (лимит по IP)

MATCH_SCHEMA = pa.schema([
    ("match_id", pa.int64()), ("start_time", pa.int32()), ("duration", pa.int16()), ("game_mode", pa.int8()),
    ("lobby_type", pa.int8()), ("radiant_win", pa.bool_()), ("patch", pa.string()), ("is_radiant", pa.bool_()),
    ("hero_id", pa.int16()), ("items", pa.list_(pa.int16())), ("item_neutral", pa.int16()),
    ("net_worth", pa.int32()), ("gold_per_min", pa.int16()), ("xp_per_min", pa.int16()),
    ("kills", pa.int8()), ("deaths", pa.int8()), ("assists", pa.int8()), ("last_hits", pa.int16()),
])
RANK_SCHEMA = pa.schema([("match_id", pa.int64()), ("avg_rank_tier", pa.int8()), ("num_rank_tier", pa.int8())])


def http_json(url: str, timeout: int = 60):
    return http_json_headers(url, timeout)[0]


def http_json_headers(url: str, timeout: int = 60):
    """Ответ и заголовки (у OpenDota в заголовках остаток лимита: X-Rate-Limit-Remaining-Day)."""
    request = urllib.request.Request(url, headers={"User-Agent": "DotaTimer-stats/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read()), response.headers


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
    """Откуда продолжить: номер из next_seq.txt (последний сохранённый матч) или по последнему файлу матчей.

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


def retry_after(error) -> float:
    """Сколько ждать после 429: заголовок Retry-After (секунды), иначе RETRY_AFTER_DEFAULT_SEC."""
    headers = getattr(error, "headers", None)
    try:
        return max(1.0, float(headers.get("Retry-After")))
    except (AttributeError, TypeError, ValueError):
        return RETRY_AFTER_DEFAULT_SEC


def gh_upload(release: str | None, paths: list[Path]) -> bool:
    """Выложить файлы в релиз GitHub (gh есть на раннере Actions). Без релиза — только локально."""
    if not release:
        return True
    try:
        done = subprocess.run(["gh", "release", "upload", release, *map(str, paths), "--clobber"],
                              capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"выгрузка в релиз не удалась: {type(error).__name__}", flush=True)
        return False
    if done.returncode != 0:
        print(f"выгрузка в релиз не удалась: {done.stderr.strip()[:200]}", flush=True)
    return done.returncode == 0


class PartSaver:
    """Сохраняет собранное частями и сразу выкладывает в релиз.

    next_seq.txt (откуда продолжит следующий запуск) обновляется только после того, как все части до этого
    места выложены. Если запуск упадёт, следующий продолжит с последнего СОХРАНЁННОГО матча; если какой-то
    матч попадёт в релиз дважды, расчёт посчитает его один раз.
    """

    def __init__(self, out: Path, tag: str, release: str | None, upload=gh_upload):
        self.out, self.tag, self.release, self.upload = out, tag, release, upload
        self.parts, self.pending, self.bytes = 0, [], 0
        self.saved_seq = None

    def save(self, rows: list[dict], next_seq: int) -> bool:
        """Записать rows частью и выложить; True — всё до next_seq сохранено в релизе."""
        if rows:
            path = self.out / f"matches-{self.tag}-p{self.parts:02d}.parquet"
            write_parquet(rows, MATCH_SCHEMA, path)
            self.parts += 1
            self.bytes += path.stat().st_size
            self.pending.append(path)
        if self.pending and not self.upload(self.release, self.pending):
            return False  # выложим вместе со следующей частью; next_seq не двигаем
        self.pending = []
        seq_file = self.out / "next_seq.txt"
        seq_file.write_text(str(next_seq))
        if not self.upload(self.release, [seq_file]):
            return False
        self.saved_seq = next_seq
        return True


def collect_matches(key: str, calls: int, max_minutes: float, out: Path, tag: str, state: Path | None = None,
                    release: str | None = None) -> dict:
    patches = http_json(f"{OPENDOTA}/constants/patch")
    seq = resume_seq_num(state)
    print(f"старт: {'продолжаю с последнего сохранённого матча' if seq else 'от якоря (матчи суточной давности)'}; "
          f"пауза {PAUSE_START_SEC} сек", flush=True)
    seq = seq or anchor_seq_num()
    saver = PartSaver(out, tag, release)
    rows, kept, errors, calm, pause = [], 0, 0, 0, PAUSE_START_SEC
    requests = limits = 0
    started = time.monotonic()
    deadline = started + max_minutes * 60
    last_save = last_log = started
    window = {"requests": 0, "kept": 0, "limits": 0}
    for i in range(calls):
        now = time.monotonic()
        if now > deadline:
            print(f"время вышло ({max_minutes:.0f} мин), сохраняю собранное", flush=True)
            break
        if now - last_log >= LOG_EVERY_SEC:
            span = (now - last_log) / 60
            print(f"{(now - started) / 60:.0f} мин: {window['requests'] / span:.1f} запросов/мин, "
                  f"{window['kept'] / span * 60:.0f} матчей/час, 429 за отрезок {window['limits']} (всего {limits}), "
                  f"пауза {pause:.2f} сек, всего матчей {kept}", flush=True)
            window, last_log = {"requests": 0, "kept": 0, "limits": 0}, now
        if now - last_save >= SAVE_EVERY_SEC:
            if saver.save(rows, seq):
                rows = []
            last_save = now
        requests += 1
        window["requests"] += 1
        try:
            result = http_json(f"{STEAM_URL}?key={key}&start_at_match_seq_num={seq}&matches_requested=100")["result"]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            # в лог — только тип и код ошибки: адрес запроса содержит ключ
            code = getattr(error, "code", "")
            errors, calm = errors + 1, 0
            if errors >= MAX_ERRORS_IN_ROW:
                # не бросаем сбор: запуск №5 (29.09) остановился через 112 мин из 210 именно здесь
                print(f"запрос {i}: {errors} ошибок подряд (последняя {code}), жду {LONG_WAIT_SEC // 60} мин "
                      f"и продолжаю", flush=True)
                time.sleep(LONG_WAIT_SEC)
                errors = 0
                continue
            if code == 429:
                limits += 1
                window["limits"] += 1
                pause = min(PAUSE_MAX_SEC, pause * PAUSE_UP_FACTOR)
                time.sleep(retry_after(error))
            else:
                print(f"запрос {i}: ошибка {type(error).__name__} {code}", flush=True)
                time.sleep(ERROR_PAUSE_SEC)
            continue
        errors, calm = 0, calm + 1
        if calm >= CALM_CALLS:
            pause, calm = max(PAUSE_MIN_SEC, pause * PAUSE_DOWN_FACTOR), 0
        matches = result.get("matches") or []
        if not matches:
            print("Steam отдал пустой список — дошли до свежих матчей", flush=True)
            break
        seq = matches[-1]["match_seq_num"] + 1
        for match in matches:
            new = match_rows(match, patches)
            if new:
                rows.extend(new)
                kept += 1
                window["kept"] += 1
        time.sleep(pause)
    saved = saver.save(rows, seq)
    minutes = (time.monotonic() - started) / 60
    stats = {"steam_requests": requests, "steam_429": limits, "matches": kept, "minutes": round(minutes, 1),
             "matches_per_hour": round(kept / max(minutes, 0.01) * 60), "final_pause_sec": round(pause, 2),
             "parts": saver.parts, "matches_bytes": saver.bytes, "all_saved": saved}
    print(f"итог: матчей {kept} за {minutes:.0f} мин ({stats['matches_per_hour']} в час), запросов {requests}, "
          f"429: {limits}, частей {saver.parts}, {saver.bytes // 1024} КБ, всё сохранено: {saved}", flush=True)
    return stats


def match_files(pattern: str) -> list[Path]:
    return [Path(p) for p in sorted(glob.glob(pattern))]


def match_ids(files: list[Path]) -> set:
    return {i for f in files for i in pq.read_table(f, columns=["match_id"]).column("match_id").to_pylist()}


def id_range(files) -> tuple[int, int]:
    """5-й и 95-й процентили номеров собранных матчей: там их основная масса."""
    files = [files] if isinstance(files, (str, Path)) else files
    ids = sorted(match_ids(files))
    if not ids:
        raise SystemExit("в файлах матчей пусто")
    return ids[int(len(ids) * 0.05)], ids[min(len(ids) - 1, int(len(ids) * 0.95))]


def collect_ranks(max_minutes: float, out: Path, tag: str, files: list[Path]) -> dict:
    """Ранги из /publicMatches по диапазону номеров собранных матчей.

    Лимит OpenDota считается по IP, у раннера GitHub свой. Поэтому единственная граница лимита — остаток
    на сутки из заголовка ответа X-Rate-Limit-Remaining-Day: по нему после первого запроса считается, сколько
    запросов сделать (и шаг, чтобы раскидать их по всему диапазону), и по нему же сбор останавливается
    при остатке OPENDOTA_DAY_RESERVE. Кроме лимита шаг ограничен временем (max_minutes).
    """
    low, high = id_range(files)
    ranks, cursor, planned, step, requests, left = {}, high + 1, None, 1, 0, None
    deadline = time.monotonic() + max_minutes * 60
    while cursor > low and time.monotonic() < deadline:
        requests += 1
        try:
            page, headers = http_json_headers(f"{OPENDOTA}/publicMatches?less_than_match_id={cursor}")
        except (urllib.error.URLError, OSError, ValueError) as error:
            code = getattr(error, "code", "")
            print(f"запрос {requests}: ошибка {type(error).__name__} {code}", flush=True)
            if code == 429:
                print("OpenDota ответил 429 — лимит исчерпан, заканчиваю сбор рангов", flush=True)
                break
            time.sleep(ERROR_PAUSE_SEC)
            continue
        for m in page or []:
            if m.get("avg_rank_tier") is not None and low <= m["match_id"] <= high:
                ranks[m["match_id"]] = (m["avg_rank_tier"], m.get("num_rank_tier") or 0)
        raw_left = headers.get("X-Rate-Limit-Remaining-Day")
        left = int(raw_left) if raw_left and raw_left.isdigit() else None
        if planned is None:
            by_time = int(max_minutes * 60 / (OPENDOTA_PAUSE_SEC + 0.5))
            planned = max(1, min(by_time, left - OPENDOTA_DAY_RESERVE) if left is not None else by_time)
            step = max(1, (high - low) // planned)
            print(f"диапазон {low}–{high}, остаток лимита OpenDota на сутки {left}, план {planned} запросов, "
                  f"шаг {step}", flush=True)
        if requests % 100 == 0:
            print(f"запрос {requests}/{planned}, матчей с рангом {len(ranks)}, остаток лимита {left}", flush=True)
        if left is not None and left <= OPENDOTA_DAY_RESERVE:
            print(f"остаток лимита OpenDota {left} — заканчиваю сбор рангов", flush=True)
            break
        cursor = high + 1 - requests * step
        time.sleep(OPENDOTA_PAUSE_SEC)
    rows = [{"match_id": k, "avg_rank_tier": v[0], "num_rank_tier": v[1]} for k, v in ranks.items()]
    path = out / f"ranks-{tag}.parquet"
    write_parquet(rows, RANK_SCHEMA, path)
    ours = match_ids(files)
    ranked = len(ours & ranks.keys())
    print(f"итог: запросов OpenDota {requests}, остаток лимита {left}, матчей с рангом {len(rows)}, "
          f"из них наших {ranked} из {len(ours)} ({ranked / max(len(ours), 1):.0%})", flush=True)
    return {"opendota_requests": requests, "opendota_left": left, "ranked_ours": ranked, "ours": len(ours),
            "ranks_bytes": path.stat().st_size, "ranks_file": path.name}


def update_run_stats(out: Path, tag: str, stats: dict) -> Path:
    """Итоги запуска в run-<тег>.json (для отчёта tools/run_report.py): номер запуска GitHub и цифры шагов."""
    path = out / f"run-{tag}.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "tag": tag, "run_number": os.environ.get("GITHUB_RUN_NUMBER"), "run_id": os.environ.get("GITHUB_RUN_ID")}
    data.update(stats)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Сбор сырых матчей и рангов")
    parser.add_argument("what", choices=["matches", "ranks"])
    parser.add_argument("--out", default="raw")
    parser.add_argument("--calls", type=int, default=100000, help="предел запросов к Steam (обычно ограничивает время)")
    parser.add_argument("--max-minutes", type=float, default=240)
    parser.add_argument("--matches", help="для ranks: шаблон файлов матчей этого запуска, например raw/matches-*.parquet")
    parser.add_argument("--state", help="для matches: папка с next_seq.txt или последним файлом матчей прошлого запуска")
    parser.add_argument("--release", help="релиз GitHub, куда сразу выкладывать части (на раннере Actions)")
    parser.add_argument("--tag", help="тег запуска (общий для matches и ranks); по умолчанию — текущее время UTC")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = args.tag or datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")  # старые файлы не перезаписываются
    if args.what == "matches":
        key = os.environ.get("STEAM_API_KEY", "").strip()
        if not key:
            sys.exit("нет STEAM_API_KEY")
        stats = collect_matches(key, args.calls, args.max_minutes, out, tag,
                                Path(args.state) if args.state else None, args.release)
    else:
        if not args.matches:
            sys.exit("для ranks нужен --matches")
        stats = collect_ranks(args.max_minutes, out, tag, match_files(args.matches))
        gh_upload(args.release, [out / stats["ranks_file"]])
    gh_upload(args.release, [update_run_stats(out, tag, stats)])


if __name__ == "__main__":
    main()
