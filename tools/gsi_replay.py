"""Проигрывание записи GSI: отправляет пакеты из файла так, будто их шлёт Dota 2.

Запуск: python tools/gsi_replay.py файл.jsonl [--speed 10] [--url http://127.0.0.1:53000/]
--speed 0 значит «без пауз, как можно быстрее».
Нужен, чтобы проверять программу без запуска Доты.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.gsi import installer  # noqa: E402
from app.i18n import ru  # noqa: E402
from tools.analyze_recording import load_records  # noqa: E402


def iter_with_delays(records: list[dict], speed: float):
    """Выдаёт (задержка перед отправкой, пакет) с учётом скорости проигрывания."""
    prev_t = None
    for record in records:
        t = record.get("t", 0)
        delay = 0.0 if prev_t is None or speed <= 0 else max(0.0, (t - prev_t) / speed)
        prev_t = t
        yield delay, record.get("data") or {}


def send(url: str, data: dict) -> None:
    body = json.dumps(data).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5):
        pass


def replay(path: Path, url: str, speed: float) -> int:
    count = 0
    for delay, data in iter_with_delays(load_records(path), speed):
        if delay:
            time.sleep(delay)
        try:
            send(url, data)
        except (urllib.error.URLError, OSError) as error:
            print(ru.RP_SEND_ERROR.format(error=error))
            continue
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Проигрывание записи GSI")
    parser.add_argument("file")
    parser.add_argument("--url", default=installer.gsi_url())
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()
    print(ru.RP_START.format(path=args.file, url=args.url, speed=args.speed))
    print(ru.RP_DONE.format(count=replay(Path(args.file), args.url, args.speed)))


if __name__ == "__main__":
    main()
