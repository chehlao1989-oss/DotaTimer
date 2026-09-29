"""Разбор записи GSI: какие поля реально приходят и как ведёт себя игра.

Запуск: python tools/analyze_recording.py [файл.jsonl]
Без аргумента берётся самая свежая запись из %APPDATA%\\DotaTimer\\recordings\\.
Рядом с записью появляется отчёт <имя>.report.md.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import paths  # noqa: E402
from app.i18n import ru  # noqa: E402

MAX_SAMPLES = 8
MAX_SAMPLE_LEN = 60


def load_records(path: Path) -> list[dict]:
    """Читает запись. Битые строки (например, оборванная последняя) пропускаются."""
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def normalize_key(key: str) -> str:
    """slot0, slot1 → slot#: чтобы отчёт не раздувался одинаковыми ключами."""
    return re.sub(r"\d+", "#", key)


def flatten(obj, prefix: str = ""):
    """Разворачивает вложенный JSON в пары (путь, значение)."""
    if isinstance(obj, dict):
        if not obj:
            yield prefix, {}
        for key, value in obj.items():
            path = f"{prefix}.{normalize_key(str(key))}" if prefix else normalize_key(str(key))
            yield from flatten(value, path)
    elif isinstance(obj, list):
        if not obj:
            yield prefix + "[]", []
        for item in obj:
            yield from flatten(item, prefix + "[]")
    else:
        yield prefix, obj


def summarize(records: list[dict]) -> dict:
    """Собирает сводку по записи."""
    fields: dict[str, dict] = {}
    blocks: dict[str, int] = {}
    matches: dict[str, dict] = {}
    state_changes, pause_changes, daytime_changes = [], [], []
    events = []
    last_state = last_paused = last_daytime = None

    for record in records:
        data = record.get("data") or {}
        for block in data:
            blocks[block] = blocks.get(block, 0) + 1
        # previously/added показывают, что изменилось с прошлого пакета, их поля не считаем
        payload = {k: v for k, v in data.items() if k not in ("previously", "added", "auth")}
        for path, value in flatten(payload):
            stat = fields.setdefault(path, {"count": 0, "samples": []})
            stat["count"] += 1
            sample = str(value)[:MAX_SAMPLE_LEN]
            if len(stat["samples"]) < MAX_SAMPLES and sample not in stat["samples"]:
                stat["samples"].append(sample)

        game_map = data.get("map") or {}
        clock = game_map.get("clock_time")
        match_id = game_map.get("matchid")
        if match_id:
            match = matches.setdefault(str(match_id), {"packets": 0, "first_clock": clock, "last_clock": clock})
            match["packets"] += 1
            match["last_clock"] = clock

        state = game_map.get("game_state")
        if state is not None and state != last_state:
            state_changes.append((clock, state))
            last_state = state
        paused = game_map.get("paused")
        if paused is not None and paused != last_paused:
            pause_changes.append((clock, paused))
            last_paused = paused
        daytime = game_map.get("daytime")
        if daytime is not None and daytime != last_daytime:
            daytime_changes.append((clock, daytime))
            last_daytime = daytime

        for event in data.get("events") or []:
            text = json.dumps(event, ensure_ascii=False, sort_keys=True)
            if text not in events:
                events.append(text)

    return {
        "packets": len(records),
        "duration_sec": (records[-1]["t"] - records[0]["t"]) if len(records) > 1 else 0,
        "blocks": blocks,
        "fields": fields,
        "matches": matches,
        "state_changes": state_changes,
        "pause_changes": pause_changes,
        "daytime_changes": daytime_changes,
        "events": events,
    }


def _clock(seconds) -> str:
    if not isinstance(seconds, (int, float)):
        return "—"
    sign = "-" if seconds < 0 else ""
    seconds = abs(int(seconds))
    return f"{sign}{seconds // 60}:{seconds % 60:02d}"


def render_markdown(summary: dict, source: str) -> str:
    """Отчёт для человека (и для заполнения docs/gsi_findings.md)."""
    out = [f"# Отчёт по записи {source}", ""]
    out.append(f"Пакетов: {summary['packets']}, длительность записи: {summary['duration_sec'] / 60:.1f} мин")
    out += ["", "## Матчи (map.matchid)"]
    for match_id, m in summary["matches"].items():
        out.append(f"- {match_id}: пакетов {m['packets']}, время {_clock(m['first_clock'])} → {_clock(m['last_clock'])}")
    out += ["", "## Блоки верхнего уровня (в скольких пакетах)"]
    for block, count in sorted(summary["blocks"].items()):
        out.append(f"- `{block}`: {count}")
    for title, key in (("Смена game_state", "state_changes"), ("Паузы (map.paused)", "pause_changes"),
                       ("День/ночь (map.daytime)", "daytime_changes")):
        out += ["", f"## {title}"]
        out += [f"- {_clock(clock)}: {value}" for clock, value in summary[key]] or ["- нет данных"]
    out += ["", "## События (events)"]
    out += [f"- `{e}`" for e in summary["events"]] or ["- ни одного события"]
    out += ["", "## Все поля", "", "| Поле | Пакетов | Примеры значений |", "|---|---|---|"]
    for path, stat in sorted(summary["fields"].items()):
        samples = ", ".join(s.replace("|", "/") for s in stat["samples"])
        out.append(f"| `{path}` | {stat['count']} | {samples} |")
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Разбор записи GSI")
    parser.add_argument("file", nargs="?", help="файл .jsonl (по умолчанию самый свежий)")
    args = parser.parse_args()

    if args.file:
        path = Path(args.file)
    else:
        files = sorted(paths.recordings_dir().glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            print(ru.AN_NO_FILES.format(path=paths.recordings_dir()))
            return
        path = files[-1]

    summary = summarize(load_records(path))
    report = path.with_suffix(".report.md")
    report.write_text(render_markdown(summary, path.name), encoding="utf-8")
    print(ru.AN_DONE.format(path=report))


if __name__ == "__main__":
    main()
