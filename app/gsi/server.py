"""Приём GSI-пакетов от Dota 2 на локальном HTTP-сервере в отдельном потоке."""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from app.gsi import installer

log = logging.getLogger(__name__)


class ExclusiveHTTPServer(ThreadingHTTPServer):
    """HTTP-сервер, который не даёт второй копии программы занять тот же порт.

    По умолчанию Python разрешает повторное занятие порта, и на Windows две копии
    слушали порт одновременно: пакеты уходили в первую, а вторая молчала.
    """

    allow_reuse_address = False
    daemon_threads = True


def make_handler(on_packet: Callable[[dict], None], token: str, on_rejected: Callable[[], None] | None = None):
    """Обработчик запросов: проверяет токен и передаёт пакет в on_packet."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                log.warning("Пришёл пакет, который не удалось разобрать (%d байт)", length)
                self._reply(400)
                return
            if (data.get("auth") or {}).get("token") != token:
                if on_rejected:
                    on_rejected()
                self._reply(403)
                return
            try:
                on_packet(data)
            except Exception:
                # ошибка в обработке одного пакета не должна ронять сервер
                log.exception("Ошибка обработки GSI-пакета")
            self._reply(200)

        def _reply(self, code: int) -> None:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return Handler


class GsiServer:
    """Сервер GSI в фоновом потоке. start() бросает OSError, если порт занят."""

    def __init__(self, on_packet: Callable[[dict], None], host: str = installer.GSI_HOST,
                 port: int = installer.GSI_PORT, token: str = installer.GSI_TOKEN):
        self.rejected = 0
        self._server = ExclusiveHTTPServer((host, port), make_handler(on_packet, token, self._count_rejected))
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def _count_rejected(self) -> None:
        self.rejected += 1

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, name="gsi-server", daemon=True)
        self._thread.start()
        log.info("GSI-сервер слушает порт %d", self.port)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
