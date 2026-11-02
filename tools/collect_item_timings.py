"""Ограниченная выборка историй покупок; публичные завершённые матчи, без аккаунтов игроков."""
import argparse
from collections import defaultdict
import gzip
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.collect_raw import MODE_BUCKETS, MIN_DURATION_SEC
from tools.raw_window import flat_assets, asset_bytes, gh
from app.threats.item_timing import validate

API = "https://api.opendota.com/api"


def eligible_items():
    table = json.loads((ROOT / "data/item_counters.json").read_text(encoding="utf-8"))
    wiki = json.loads((ROOT / "data/benchmark_counters.json").read_text(encoding="utf-8"))
    return {k for keys in table["counters"].values() for k in keys} | {
        k for hero in wiki["heroes"].values() for k in hero.get("counter_items", [])}


def sample_match(match, allowed, lobbies=(0, 7)):
    """Одна запись на матч: герой, режим, роль и первые покупки; имён и аккаунтов нет."""
    bucket = MODE_BUCKETS.get(match.get("game_mode"))
    players = match.get("players") or []
    if match.get("lobby_type") not in lobbies or not bucket or len(players) != 10 or match.get("duration", 0) < MIN_DURATION_SEC[bucket]:
        return None
    if not match.get("version") or any(not p.get("hero_id") or p.get("leaver_status", 0) > 1 for p in players):
        return None
    roles = {}
    for side in (False, True):
        team = [p for p in players if (p.get("player_slot", 0) < 128) == side]
        if len(team) != 5 or any(not isinstance(p.get("net_worth"), (int, float)) for p in team):
            return None
        for pos, p in enumerate(sorted(team, key=lambda p: (-p["net_worth"], p["player_slot"])), 1):
            roles[p["player_slot"]] = "core" if pos <= 2 else "offlane" if pos == 3 else "support"
    rows = []
    for player in players:
        first = {}
        for event in player.get("purchase_log") or []:
            key, clock = event.get("key"), event.get("time")
            if key not in allowed or not isinstance(clock, (int, float)) or not math.isfinite(clock):
                continue
            if clock < 0 or clock > match["duration"]:
                continue
            first[key] = min(first.get(key, clock), clock)
        rows.extend([player["hero_id"], roles[player["player_slot"]], item, clock]
                    for item, clock in first.items())
    if not rows:
        return None
    return {"match_id": int(match["match_id"]), "start_time": int(match["start_time"]),
            "patch": match.get("patch"), "bucket": bucket, "rows": rows}


def aggregate(samples, cfg, now=None):
    """Медиана первых покупок; один игрок в матче не даёт повторных наблюдений одного предмета."""
    now = time.time() if now is None else now
    patches = [s["patch"] for s in samples if isinstance(s.get("patch"), (int, float))]
    patch = max(patches) if patches else None
    cells = defaultdict(list)
    used = 0
    for sample in samples:
        if patch is not None and sample.get("patch") != patch:
            continue
        if now - sample["start_time"] > cfg["max_age_days"] * 86400:
            continue
        used += 1
        for hero, role, item, clock in sample["rows"]:
            cells[(str(hero), sample["bucket"], role, item)].append(clock)
            cells[(str(hero), sample["bucket"], "all", item)].append(clock)
    heroes = {}
    for (hero, bucket, role, item), times in sorted(cells.items()):
        if len(times) < cfg["min_samples"]:
            continue
        heroes.setdefault(hero, {}).setdefault(bucket, {}).setdefault(role, {})[item] = [
            float(statistics.median(times)), len(times)]
    result = {"version": 1, "generated_at": int(now), "max_age_days": cfg["max_age_days"],
              "min_samples": cfg["min_samples"], "patch": patch, "matches": used,
              "source": API + "/matches/{match_id}", "method": "first_purchase_median",
              "roles": "team_net_worth_place", "heroes": heroes}
    validate(result)
    return result


class BudgetEnded(Exception):
    """Суточный запас исчерпан: сохраняем уже полученные наблюдения."""


class Client:
    def __init__(self, cfg):
        self.cfg, self.calls, self.left = cfg, 0, None
        self.next_call = 0

    def get(self, path):
        if self.left is not None and self.left <= self.cfg["day_reserve"]:
            raise BudgetEnded("Достигнут запас суточного лимита OpenDota")
        for attempt in range(4):
            time.sleep(max(0, self.next_call - time.monotonic()))
            self.calls += 1
            request = urllib.request.Request(API + path, headers={"User-Agent": "DotaTimer-item-timing"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    left = response.headers.get("X-Rate-Limit-Remaining-Day")
                    self.left = int(left) if left is not None else self.left
                    result = json.load(response)
                self.next_call = time.monotonic() + self.cfg["api_delay_sec"]
                return result
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise
                self.next_call = time.monotonic() + float(error.headers.get("Retry-After", 60))
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 3:
                    raise
                self.next_call = time.monotonic() + 2 ** attempt
        raise RuntimeError("OpenDota не ответила")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default="item-timings")
    parser.add_argument("--max-requests", type=int, default=200)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    cfg = json.loads((ROOT / "data/item_timing_config.json").read_text(encoding="utf-8"))
    repo = os.environ.get("GITHUB_REPOSITORY", "chehlao1989-oss/DotaTimer")
    state = {"samples": [], "seen": [], "updated_at": 0}
    try:
        assets = flat_assets(repo, args.release)
    except subprocess.CalledProcessError:
        # Только настоящий 404 означает отсутствие релиза; остальные ошибки не скрываем.
        check = subprocess.run(["gh", "api", f"repos/{repo}/releases/tags/{args.release}"], capture_output=True)
        if b"404" not in check.stderr and b"Not Found" not in check.stderr:
            raise
        assets = []
        gh("release", "create", args.release, "--repo", repo, "--prerelease", "--target", "main",
           "--title", args.release, "--notes", "Ориентиры времени покупки из публичных разобранных матчей OpenDota.")
    previous = next((a for a in assets if a["name"] == "timing_state.json.gz"), None)
    if previous:
        state = json.loads(gzip.decompress(asset_bytes(repo, previous)))
    if not args.force and time.time() - state["updated_at"] < cfg["refresh_hours"] * 3600:
        print("Тайминги свежие: сбор пропущен")
        return
    client = Client(cfg)
    definitions = client.get("/constants/items")
    allowed = {k for k in eligible_items() if definitions.get(k, {}).get("qual") != "consumable"}
    seen, samples = set(state["seen"]), {s["match_id"]: s for s in state["samples"]}
    def checkpoint(updated_at):
        kept = sorted(samples.values(), key=lambda s: (s["start_time"], s["match_id"]), reverse=True)[:cfg["max_matches"]]
        payload = {"samples": kept, "seen": sorted(seen, reverse=True)[:cfg["seen_limit"]], "updated_at": updated_at}
        Path("timing_state.json.gz").write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode()))
        gh("release", "upload", args.release, "timing_state.json.gz", "--repo", repo, "--clobber")

    cursor, added = None, 0
    try:
        while client.calls < args.max_requests:
            page = client.get("/parsedMatches" + (f"?less_than_match_id={cursor}" if cursor else ""))
            ids = [int(r["match_id"]) for r in page]
            if not ids or all(i in seen for i in ids):
                break
            for mid in ids:
                if mid in seen or client.calls >= args.max_requests:
                    continue
                try:
                    match = client.get(f"/matches/{mid}")
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
                    seen.add(mid)
                    continue
                sample = sample_match(match, allowed, cfg["allowed_lobbies"])
                seen.add(mid)
                if sample:
                    samples[mid] = sample
                    added += 1
                if client.calls % 100 == 0:
                    checkpoint(state["updated_at"])
                if client.calls % 25 == 0:
                    print(f"Истории покупок: запросов {client.calls}, новых матчей {added}, остаток OpenDota {client.left}", flush=True)
            cursor = min(ids)
    except BudgetEnded as error:
        print(str(error), flush=True)
    except Exception:
        checkpoint(state["updated_at"])
        raise
    kept = sorted(samples.values(), key=lambda s: (s["start_time"], s["match_id"]), reverse=True)[:cfg["max_matches"]]
    result = aggregate(kept, cfg)
    state = {"samples": kept, "seen": sorted(seen, reverse=True)[:cfg["seen_limit"]], "updated_at": int(time.time())}
    Path("item_timings.json").write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    Path("timing_state.json.gz").write_bytes(gzip.compress(json.dumps(state, separators=(",", ":")).encode()))
    gh("release", "upload", args.release, "timing_state.json.gz", "--repo", repo, "--clobber")
    gh("release", "upload", args.release, "item_timings.json", "--repo", repo, "--clobber")
    print(f"Тайминги: новых матчей {added}, в окне {len(kept)}, в расчёте {result['matches']}, "
          f"героев с таймингами {len(result['heroes'])}, запросов {client.calls}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
