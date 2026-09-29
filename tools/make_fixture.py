"""Делает из большой записи матча маленькую для тестов (tests/fixtures/).

Оставляет только поля, нужные таймерам, и убирает личное: чат игроков,
id аккаунта, ник.
Запуск: python tools/make_fixture.py запись.jsonl tests/fixtures/имя.jsonl
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.analyze_recording import load_records  # noqa: E402

MAP_KEYS = ("matchid", "clock_time", "game_time", "game_state", "paused", "daytime", "win_team")
KEEP_EVENTS = ("roshan_killed", "aegis_picked_up", "bounty_rune_pickup")


def shrink(data: dict) -> dict:
    """Урезанная копия одного пакета."""
    out = {"auth": data.get("auth", {})}
    if "map" in data:
        out["map"] = {k: data["map"][k] for k in MAP_KEYS if k in data["map"]}
    if "player" in data:
        out["player"] = {"team_name": data["player"].get("team_name")}
    if "hero" in data:
        out["hero"] = {"name": data["hero"].get("name")}
    out["events"] = [e for e in data.get("events") or [] if e.get("event_type") in KEEP_EVENTS]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Урезать запись для тестов")
    parser.add_argument("source")
    parser.add_argument("target")
    args = parser.parse_args()
    records = load_records(Path(args.source))
    with open(args.target, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps({"t": record["t"], "data": shrink(record["data"])}, ensure_ascii=False) + "\n")
    print(f"{len(records)} → {args.target}")


if __name__ == "__main__":
    main()
