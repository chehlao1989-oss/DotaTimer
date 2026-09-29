"""Отчёт о запуске сбора в 5 строк (по файлам run-*.json в релизе data-raw). Все цифры — замеры из журнала запуска.

Запуск: python tools/run_report.py [--run 12]  (без --run — последний запуск)
В GitHub Actions пишет отчёт и в сводку запуска ($GITHUB_STEP_SUMMARY).
"""
import argparse
import json
import os
import sys
import urllib.request
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


def _n(value) -> str:
    return f"{value:,}".replace(",", " ")


def main() -> None:
    parser = argparse.ArgumentParser(description="Отчёт о запуске сбора")
    parser.add_argument("--run", help="номер запуска GitHub (без него — последний)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    assets = release_assets()
    runs = load_runs(assets)
    lines = report(runs, assets, args.run, legacy_matches(assets, runs))
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Отчёт о сборе\n\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
