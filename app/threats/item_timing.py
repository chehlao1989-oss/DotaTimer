"""Ориентиры покупки из разобранных завершённых матчей OpenDota (DECISIONS №33).

Время относится к первой покупке готового предмета, не к обнаружению его зрением.
Выбор контр-предметов этот модуль не меняет.
"""
from dataclasses import dataclass
import json
import logging
import time
from pathlib import Path
import urllib.error
import urllib.request

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
TIMINGS_URL = "https://github.com/chehlao1989-oss/DotaTimer/releases/download/item-timings/item_timings.json"


@dataclass(frozen=True)
class PurchaseTiming:
    seconds: float
    samples: int

    @property
    def minute(self):
        return max(0, int(self.seconds / 60 + 0.5))


class TimingTable:
    def __init__(self, raw: dict, now=None):
        self.raw = raw
        self.now = now

    @classmethod
    def load(cls, cache: Path | None = None):
        """Повреждённая или отсутствующая статистика не мешает обычным советам."""
        paths = ([cache] if cache else []) + [DATA_DIR / "item_timings.json"]
        candidates = []
        for path in paths:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                validate(raw)
                candidates.append(raw)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return cls(max(candidates, key=lambda raw: raw["generated_at"]) if candidates else {})

    def get(self, hero_id, bucket, role, item) -> PurchaseTiming | None:
        """Не переносим тайминг с другого героя, режима или явно выбранной роли."""
        raw = self.raw
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return None
        now = time.time() if self.now is None else self.now
        if now - raw["generated_at"] > raw["max_age_days"] * 86400:
            return None
        roles = raw.get("heroes", {}).get(str(hero_id), {}).get(bucket, {})
        row = roles.get(role or "all", {}).get(item)
        if not row or row[1] < raw["min_samples"]:
            return None
        return PurchaseTiming(float(row[0]), int(row[1]))


def validate(raw):
    """Проверяем формат до замены рабочей копии кеша."""
    import math
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("heroes"), dict):
        raise ValueError("Неверный формат таймингов")
    for field in ("generated_at", "max_age_days", "min_samples"):
        if not isinstance(raw.get(field), (int, float)) or not math.isfinite(raw[field]) or raw[field] <= 0:
            raise ValueError("Неверные сведения о таймингах")
    for hero, modes in raw["heroes"].items():
        if not isinstance(modes, dict) or not str(hero).isdigit():
            raise ValueError("Неверный герой тайминга")
        for bucket, roles in modes.items():
            if not isinstance(roles, dict) or bucket not in ("normal", "turbo"):
                raise ValueError("Неверный режим тайминга")
            for role, items in roles.items():
                if not isinstance(items, dict) or role not in ("core", "offlane", "support", "all"):
                    raise ValueError("Неверная роль тайминга")
                for key, row in items.items():
                    if not isinstance(row, list) or len(row) != 2:
                        raise ValueError("Неверная строка тайминга")
                    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in row):
                        raise ValueError("Неверное число тайминга")
                    if row[0] < 0 or row[1] < raw["min_samples"]:
                        raise ValueError("Недостаточно данных тайминга")


def download_timings(path: Path, url=TIMINGS_URL, max_age=24 * 3600):
    """Фоновое суточное обновление публичной статистики; при сбое остаётся прежняя копия."""
    if path.is_file() and time.time() - path.stat().st_mtime < max_age:
        return False
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "DotaTimer-item-timing"})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
        validate(json.loads(body))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(body)
        temporary.replace(path)
        return True
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        log.warning("Статистика времени покупок не обновлена; обычные советы продолжают работать")
        return False
