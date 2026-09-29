"""Клиент OpenDota API (открытая статистика, без ключа).

Лимиты (проверено 29.09.2026 по заголовкам ответа): 60 запросов в минуту, 3000 в день.
Запросы идут только вне матча (при запуске и раз в сутки), во время игры — только кеш.
"""
import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

API_URL = "https://api.opendota.com/api"
CDN_URL = "https://cdn.cloudflare.steamstatic.com"
USER_AGENT = "DotaTimer/0.1 (+https://github.com)"
TIMEOUT_SEC = 30
MIN_INTERVAL_SEC = 1.1  # чуть реже 60 запросов в минуту


class OpenDotaError(Exception):
    """Сеть недоступна или API ответил ошибкой."""


class OpenDotaClient:
    def __init__(self, api_url: str = API_URL):
        self.api_url = api_url
        self._last_request = 0.0

    def _get(self, url: str, throttle: bool = True) -> bytes:
        if throttle:
            wait = MIN_INTERVAL_SEC - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
                return response.read()
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise OpenDotaError(f"{url}: {error}") from error

    def get_json(self, path: str):
        try:
            return json.loads(self._get(self.api_url + path))
        except ValueError as error:
            raise OpenDotaError(f"{path}: не JSON") from error

    def download(self, cdn_path: str) -> bytes:
        """Картинка с CDN Valve (иконки предметов и героев). Лимиты OpenDota на CDN не действуют."""
        return self._get(CDN_URL + cdn_path.split("?")[0], throttle=False)

    # --- эндпоинты ---
    def heroes(self) -> dict:
        return self.get_json("/constants/heroes")

    def items(self) -> dict:
        return self.get_json("/constants/items")

    def hero_stats(self) -> list:
        return self.get_json("/heroStats")

    def matchups(self, hero_id: int) -> list:
        return self.get_json(f"/heroes/{hero_id}/matchups")

    def player(self, account_id: str) -> dict:
        return self.get_json(f"/players/{account_id}")
