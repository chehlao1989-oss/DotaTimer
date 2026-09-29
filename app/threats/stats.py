"""Статистика слоя 2: скачивание stats.json раз в сутки и запросы к нему.

Файл собирает GitHub Actions (tools/collect_stats.py) и публикует как файл релиза «data».
Без интернета программа работает с последней скачанной копией.
"""
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.threats.data import DataCache

log = logging.getLogger(__name__)

STATS_URL = "https://github.com/chehlao1989-oss/DotaTimer/releases/download/data/stats.json"
MAX_AGE_SEC = 24 * 3600
TIMEOUT_SEC = 60
BUCKET_NORMAL, BUCKET_TURBO = "normal", "turbo"


def download_stats(cache: DataCache, url: str = STATS_URL) -> bool:
    """Скачать свежий stats.json, если копии больше суток. Вызывать в фоновом потоке."""
    path = cache.root / "stats.json"
    if path.is_file() and time.time() - path.stat().st_mtime < MAX_AGE_SEC:
        return False
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "DotaTimer/0.1"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            body = response.read()
        json.loads(body)  # проверяем, что файл целый, прежде чем заменить старый
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        log.info("Скачана статистика: %d КБ", len(body) // 1024)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        log.warning("Не удалось скачать статистику, работаю с прошлой копией", exc_info=True)
        return False


class Stats:
    """Счётчики [игры, победы] из stats.json."""

    def __init__(self, raw: dict | None):
        self.raw = raw or {}

    @classmethod
    def load(cls, cache: DataCache) -> "Stats":
        path = cache.root / "stats.json"
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls(None)

    @property
    def available(self) -> bool:
        return bool(self.raw.get("base"))

    @property
    def patch(self) -> str:
        return self.raw.get("patch", "")

    def _cell(self, table: str, *keys) -> tuple[int, int]:
        node = self.raw.get(table) or {}
        for key in keys:
            node = node.get(str(key)) if isinstance(node, dict) else None
            if node is None:
                return 0, 0
        return (node[0], node[1]) if isinstance(node, list) else (0, 0)

    def hero(self, bucket: str, hero_id: int) -> tuple[int, int]:
        """Все игры героя (для доли покупок предмета)."""
        return self._cell("hero", bucket, hero_id)

    def base(self, bucket: str, hero_id: int, item: str) -> tuple[int, int]:
        return self._cell("base", bucket, hero_id, item)

    def vs_hero(self, bucket: str, hero_id: int, enemy_id: int, item: str) -> tuple[int, int]:
        return self._cell("vs_hero", bucket, hero_id, enemy_id, item)

    def vs_trait(self, bucket: str, hero_id: int, trait: str, item: str) -> tuple[int, int]:
        return self._cell("vs_trait", bucket, hero_id, trait, item)

    def vs_item(self, bucket: str, hero_id: int, enemy_item: str, item: str) -> tuple[int, int]:
        return self._cell("vs_item", bucket, hero_id, enemy_item, item)
