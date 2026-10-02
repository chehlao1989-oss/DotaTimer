"""Сторож расчёта советов (BUGLOG №38): считать, только если в data-raw есть данные новее recs.zip.

Расчёт запускается после каждого законченного запуска «Статистики матчей», а с будильником (BUGLOG №37) это
каждый час, хотя настоящий сбор — 2 раза в сутки: без сторожа советы пересчитывались по ~15 мин на тех же данных.
Новые данные — части матчей и рангов (matches-*.parquet, ranks-*.parquet) в релизе data-raw, выложенные позже
recs.zip в релизе recs. Ответ — в $GITHUB_OUTPUT: run=true (считать) или run=false (выйти).
Без сети, без recs.zip или при ошибке — считать.

Запуск: python tools/recs_guard.py
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime


def _time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def newest_data(assets: list[dict]) -> datetime | None:
    """Время выгрузки свежайшей части матчей или рангов."""
    times = [_time(a["updated_at"]) for a in assets
             if a.get("name", "").startswith(("matches-", "ranks-")) and a["name"].endswith(".parquet")]
    return max(times) if times else None


def recs_time(assets: list[dict]) -> datetime | None:
    return next((_time(a["updated_at"]) for a in assets if a.get("name") == "recs.zip"), None)


def should_compute(data: datetime | None, recs: datetime | None) -> bool:
    return recs is None or data is None or data > recs


def release_assets(repo: str, release: str) -> list[dict]:
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/tags/{release}",
                                     headers={"User-Agent": "DotaTimer-recs-guard", "Accept": "application/vnd.github+json"})
    token = os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())["assets"]
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return []  # релиза ещё нет — считаем
        raise


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    repo = os.environ.get("GITHUB_REPOSITORY", "chehlao1989-oss/DotaTimer")
    try:
        data = newest_data(release_assets(repo, "data-raw"))
        recs = recs_time(release_assets(repo, "recs"))
    except Exception as error:  # сторож не должен мешать расчёту: при любой ошибке — считаем
        print(f"сторож расчёта: не узнал время данных ({type(error).__name__}) — считаю")
        data = recs = None
    run = should_compute(data, recs)
    print(f"сторож расчёта: свежайшие данные {data or '—'} UTC, recs.zip {recs or '—'} UTC — "
          f"{'считаю' if run else 'выхожу (новых данных нет)'}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"run={'true' if run else 'false'}\n")


if __name__ == "__main__":
    main()
