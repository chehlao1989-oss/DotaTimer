"""Сторож сбора (BUGLOG №32, DECISIONS №6): расписание каждый час, а настоящий сбор — только если с последнего
успешного прошло не меньше min_hours. Тогда пропуск запуска GitHub стоит час, а не полсуток.

«Успешный сбор» — свежайший файл итогов run-*.json в релизе data-raw (пишется в конце шага сбора матчей);
возраст — от начала того сбора (время в имени файла), чтобы сборы шли 2 раза в сутки.
Ответ — в $GITHUB_OUTPUT: run=true (собирать) или run=false (выйти). Без сети или при ошибке — собирать.

Запуск: python tools/collect_guard.py [--min-hours 11]
"""
import argparse
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.raw_window import resolve as resolve_raw, flat_assets


MIN_HOURS = 11  # DECISIONS №6: реальных сборов 2 в сутки (решение автора 01.10)


def last_success_age_hours(assets: list[dict], now: datetime) -> float | None:
    """Сколько часов назад НАЧАЛСЯ последний успешный сбор (время старта — в имени run-<ГГГГ-ММ-ДД-ЧЧММ>.json);
    None — ни одного. Раньше считалось от выкладки run-файла, то есть от конца сбора: при сборе 5 ч цикл выходил
    ~17 ч — 1,4 сбора в сутки вместо 2 (замер 05–14.10, решение автора 15.10: 2 в сутки)."""
    times = [datetime.strptime(a["name"][4:19], "%Y-%m-%d-%H%M").replace(tzinfo=timezone.utc)
             for a in assets if a.get("name", "").startswith("run-") and a["name"].endswith(".json")]
    return (now - max(times)).total_seconds() / 3600 if times else None


def should_collect(age_hours: float | None, min_hours: float = MIN_HOURS) -> bool:
    return age_hours is None or age_hours >= min_hours


def release_assets(repo: str, release: str = "data-raw") -> list[dict]:
    return flat_assets(repo, resolve_raw(repo, release))


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
