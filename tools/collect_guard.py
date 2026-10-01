"""Сторож сбора (BUGLOG №32, DECISIONS №6): расписание каждый час, а настоящий сбор — только если с последнего
успешного прошло не меньше min_hours. Тогда пропуск запуска GitHub стоит час, а не полсуток.

«Успешный сбор» — свежайший файл итогов run-*.json в релизе data-raw (пишется в конце шага сбора матчей).
Ответ — в $GITHUB_OUTPUT: run=true (собирать) или run=false (выйти). Без сети или при ошибке — собирать.

Запуск: python tools/collect_guard.py [--min-hours 11]
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

MIN_HOURS = 11  # DECISIONS №6: реальных сборов 2 в сутки (решение автора 01.10)


def last_success_age_hours(assets: list[dict], now: datetime) -> float | None:
    """Сколько часов назад выложен свежайший run-*.json; None — ни одного."""
    times = [datetime.strptime(a["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
             for a in assets if a.get("name", "").startswith("run-") and a["name"].endswith(".json")]
    return (now - max(times)).total_seconds() / 3600 if times else None


def should_collect(age_hours: float | None, min_hours: float = MIN_HOURS) -> bool:
    return age_hours is None or age_hours >= min_hours


def release_assets(repo: str, release: str = "data-raw") -> list[dict]:
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/tags/{release}",
                                     headers={"User-Agent": "DotaTimer-guard", "Accept": "application/vnd.github+json"})
    token = os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())["assets"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Сторож сбора")
    parser.add_argument("--min-hours", type=float, default=MIN_HOURS)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        age = last_success_age_hours(release_assets(os.environ.get("GITHUB_REPOSITORY", "chehlao1989-oss/DotaTimer")),
                                     datetime.now(timezone.utc))
    except Exception as error:  # сторож не должен мешать сбору: при любой ошибке — собираем
        print(f"сторож: не узнал время прошлого сбора ({type(error).__name__}) — собираю")
        age = None
    run = should_collect(age, args.min_hours)
    print(f"сторож: последний успешный сбор {'не найден' if age is None else f'{age:.1f} ч назад'} — "
          f"{'собираю' if run else f'выхожу (меньше {args.min_hours:g} ч)'}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"run={'true' if run else 'false'}\n")


if __name__ == "__main__":
    main()
