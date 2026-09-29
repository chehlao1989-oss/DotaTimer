"""Сводка по сбору, чтобы не читать журналы и файлы целиком (docs/PROCESS.md, раздел 6).

Режимы:
  (по умолчанию) отчёт о законченном запуске в 5 строк — по файлам run-*.json в релизе data-raw;
  --live   идущий запуск: сколько частей выложено, матчей в них и матчей в час по времени выгрузки частей;
  --log F  сводка по скачанному журналу запуска (строки «N мин: … матчей/час …» и «итог: …»).
Все цифры — замеры; источник указан в каждой строке.
В GitHub Actions отчёт пишется и в сводку запуска ($GITHUB_STEP_SUMMARY).

Запуск: python tools/report.py [--run 12] [--live] [--log путь_к_журналу.txt]
"""
import argparse
import io
import json
import os
import re
import statistics
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = "chehlao1989-oss/DotaTimer"
RELEASE = "data-raw"
MB = 1024 * 1024


def get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "DotaTimer-report", "Accept": "application/vnd.github+json"})
    token = os.environ.get("GH_TOKEN")
    if token and "api.github.com" in url:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def release_assets() -> list[dict]:
    return get_json(f"https://api.github.com/repos/{REPO}/releases/tags/{RELEASE}")["assets"]


def load_runs(assets: list[dict]) -> list[dict]:
    runs = []
    for a in assets:
        if a["name"].startswith("run-") and a["name"].endswith(".json"):
            runs.append(get_json(a["browser_download_url"]))
    return sorted(runs, key=lambda r: r["tag"])


def legacy_matches(assets: list[dict], runs: list[dict]) -> int:
    """Матчи из файлов запусков без run-файла (до нового сборщика): скачиваем и считаем разные match_id."""
    import io

    import pyarrow.parquet as pq
    tags = {r["tag"] for r in runs}
    ids = set()
    for a in assets:
        name = a["name"]
        if name.startswith("matches-") and name.endswith(".parquet") and name[8:23] not in tags:
            with urllib.request.urlopen(a["browser_download_url"], timeout=300) as response:
                table = pq.read_table(io.BytesIO(response.read()), columns=["match_id"])
            ids.update(table.column("match_id").to_pylist())
    return len(ids)


def report(runs: list[dict], assets: list[dict], run_number: str | None = None, legacy: int = 0) -> list[str]:
    if not runs:
        return ["нет файлов run-*.json в релизе: отчёт появится после первого запуска с новым сборщиком"]
    run = next((r for r in runs if str(r.get("run_number")) == str(run_number)), None) if run_number else runs[-1]
    if run is None:
        return [f"запуск №{run_number} не найден"]
    total = sum(r.get("matches", 0) for r in runs) + legacy
    data_bytes = sum(a["size"] for a in assets if a["name"].endswith(".parquet"))
    run_bytes = run.get("matches_bytes", 0) + run.get("ranks_bytes", 0)
    ours = run.get("ours") or 0
    ranked = run.get("ranked_ours")
    label = f"запуск №{run.get('run_number')} ({run['tag']} UTC)"
    return [
        f"1. Матчей: {_n(run.get('matches', 0))} за {label}; всего: {_n(total)} "
        f"(замер: run-файлы + {_n(legacy)} матчей из файлов до нового сборщика)",
        f"2. Скорость: {_n(run.get('matches_per_hour', 0))} матчей/час, {_n(run.get('steam_requests', 0))} запросов Steam "
        f"за {run.get('minutes', 0):.0f} мин, пауза в конце {run.get('final_pause_sec')} сек (замер)",
        f"3. Ошибок 429 от Steam: {run.get('steam_429', 0)} (замер)",
        f"4. OpenDota: {run.get('opendota_requests', '—')} запросов, остаток лимита на сутки {run.get('opendota_left', '—')}; "
        f"ранг у {ranked if ranked is not None else '—'} из {ours} наших матчей"
        + (f" ({ranked / ours:.0%})" if ranked is not None and ours else "") + " (замер)",
        f"5. Данные: этот запуск {run_bytes / MB:.1f} МБ, всего в релизе {data_bytes / MB:.1f} МБ (замер)"
        + ("" if run.get("all_saved", True) else "; ВНИМАНИЕ: последняя часть матчей не выложена"),
    ]


def live(assets: list[dict], runs: list[dict]) -> list[str]:
    """Идущий запуск: части matches-<тег>-pNN без run-файла. Скорость — по времени выгрузки первой и последней части."""
    import pyarrow.parquet as pq
    tags = {r["tag"] for r in runs}
    parts = sorted((a for a in assets if re.match(r"matches-.*-p\d+\.parquet$", a["name"]) and a["name"][8:23] not in tags),
                   key=lambda a: a["name"])
    if not parts:
        return ["идущего запуска с частями нет (или он уже закончился — см. отчёт без --live)"]
    tag = parts[-1]["name"][8:23]
    parts = [a for a in parts if a["name"][8:23] == tag]
    matches = 0
    for a in parts:
        with urllib.request.urlopen(a["browser_download_url"], timeout=300) as response:
            table = pq.read_table(io.BytesIO(response.read()), columns=["match_id"])
        matches += len(set(table.column("match_id").to_pylist()))
    started = datetime.strptime(tag, "%Y-%m-%d-%H%M")
    last = datetime.strptime(parts[-1]["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
    hours = max((last - started).total_seconds() / 3600, 1e-6)
    return [f"идёт запуск {tag} UTC: выложено частей {len(parts)}, матчей {_n(matches)} "
            f"за {hours * 60:.0f} мин до последней выгрузки ≈ {_n(round(matches / hours))} матчей/час "
            f"(замер: части в релизе и время их выгрузки)"]


LOG_LINE = re.compile(r"(\d+) мин: ([\d.]+) запросов/мин, (\d+) матчей/час, 429 за отрезок (\d+) \(всего (\d+)\), "
                      r"пауза ([\d.]+) сек")


def summarize_log(text: str) -> list[str]:
    """Сводка по журналу сборщика: скорость по 10-минутным отрезкам, 429, паузы, итоговые строки."""
    rows = [tuple(float(x) for x in m.groups()) for m in LOG_LINE.finditer(text)]
    out = []
    if rows:
        per_hour = [r[2] for r in rows]
        pauses = [r[5] for r in rows]
        out.append(f"отрезков по 10 мин: {len(rows)}; матчей/час: мин {_n(round(min(per_hour)))}, "
                   f"медиана {_n(round(statistics.median(per_hour)))}, макс {_n(round(max(per_hour)))} (замер: журнал)")
        out.append(f"429 всего к последнему отрезку: {int(rows[-1][4])}; пауза: от {min(pauses)} до {max(pauses)} сек, "
                   f"в конце {pauses[-1]} сек (замер: журнал)")
    streaks = len(re.findall(r"ошибок подряд", text))
    if streaks:
        out.append(f"серий «20 ошибок подряд»: {streaks} (замер: журнал)")
    out += [line.strip() for line in text.splitlines() if "итог:" in line]
    return out or ["в журнале нет строк сборщика"]


def _n(value) -> str:
    return f"{value:,}".replace(",", " ")


def main() -> None:
    parser = argparse.ArgumentParser(description="Отчёт о запуске сбора")
    parser.add_argument("--run", help="номер запуска GitHub (без него — последний)")
    parser.add_argument("--live", action="store_true", help="идущий запуск: части в релизе")
    parser.add_argument("--log", help="скачанный журнал запуска (текст)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.log:
        print("\n".join(summarize_log(Path(args.log).read_text(encoding="utf-8", errors="replace"))))
        return
    assets = release_assets()
    runs = load_runs(assets)
    lines = live(assets, runs) if args.live else report(runs, assets, args.run, legacy_matches(assets, runs))
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Отчёт о сборе\n\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
