"""GSI-рекордер: принимает пакеты от Dota 2 и записывает их в файл.

Запуск: python tools/gsi_recorder.py
При старте сам кладёт GSI-конфиг в папку Доты (если её удалось найти).
Каждый запуск создаёт новый файл в %APPDATA%\\DotaTimer\\recordings\\.
Формат файла: одна строка JSON на пакет: {"t": время компьютера, "data": пакет от игры}.
"""
import argparse
import json
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import paths  # noqa: E402
from app.gsi import installer  # noqa: E402
from app.gsi.server import GsiServer  # noqa: E402
from app.i18n import ru  # noqa: E402

log = logging.getLogger("gsi_recorder")

STATUS_INTERVAL_SEC = 10


class Recorder:
    """Пишет пакеты в файл построчно и помнит последний пакет для вывода статуса."""

    def __init__(self, out_path: Path):
        self.out_path = out_path
        self._file = open(out_path, "a", encoding="utf-8")
        self._lock = threading.Lock()
        self.packets = 0
        self.last: dict | None = None
        self.last_time: float | None = None

    def write(self, data: dict) -> None:
        now = time.time()
        line = json.dumps({"t": round(now, 3), "data": data}, ensure_ascii=False)
        with self._lock:
            # flush после каждой строки: если процесс убьют, запись не потеряется
            self._file.write(line + "\n")
            self._file.flush()
            self.packets += 1
            self.last = data
            self.last_time = now

    def close(self) -> None:
        with self._lock:
            self._file.close()


def format_clock(seconds) -> str:
    """Игровое время в виде 5:32 или -1:15."""
    if not isinstance(seconds, (int, float)):
        return "—"
    sign = "-" if seconds < 0 else ""
    seconds = abs(int(seconds))
    return f"{sign}{seconds // 60}:{seconds % 60:02d}"


def format_status(recorder: Recorder) -> str:
    if recorder.last is None:
        return ru.REC_WAITING
    game_map = recorder.last.get("map") or {}
    hero = (recorder.last.get("hero") or {}).get("name", "—")
    return ru.REC_STATUS.format(
        packets=recorder.packets,
        clock=format_clock(game_map.get("clock_time")),
        state=game_map.get("game_state", "—"),
        hero=hero,
        ago=time.time() - recorder.last_time,
    )


def setup_logging() -> None:
    log_file = paths.logs_dir() / "gsi_recorder.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8")],
    )


def ensure_cfg() -> None:
    """Кладёт GSI-конфиг в папку Доты и сообщает результат."""
    dota_dir = installer.find_dota_dir()
    if dota_dir is None:
        print(ru.REC_CFG_DOTA_NOT_FOUND.format(cfg=installer.CFG_FILENAME))
        fallback = paths.app_data_dir() / installer.CFG_FILENAME
        fallback.write_text(installer.build_cfg(), encoding="utf-8")
        print(fallback)
        return
    try:
        target, changed = installer.install_cfg(dota_dir)
    except OSError as error:
        log.exception("Ошибка записи GSI-конфига")
        print(ru.REC_CFG_WRITE_ERROR.format(error=error))
        return
    print((ru.REC_CFG_INSTALLED if changed else ru.REC_CFG_UP_TO_DATE).format(path=target))


def main() -> None:
    parser = argparse.ArgumentParser(description="Запись GSI-пакетов Dota 2 в файл")
    parser.add_argument("--port", type=int, default=installer.GSI_PORT)
    parser.add_argument("--no-install", action="store_true", help="не трогать GSI-конфиг в папке Доты")
    args = parser.parse_args()

    setup_logging()
    if not args.no_install:
        ensure_cfg()

    out_path = paths.recordings_dir() / f"gsi_{datetime.now():%Y-%m-%d_%H-%M-%S}.jsonl"
    recorder = None

    def on_packet(data: dict) -> None:
        recorder.write(data)

    try:
        server = GsiServer(on_packet, port=args.port)
    except OSError:
        print(ru.REC_PORT_BUSY.format(port=args.port))
        return
    recorder = Recorder(out_path)
    server.start()

    print(ru.REC_STARTED.format(url=installer.gsi_url(port=args.port), path=out_path))
    log.info("Запись в %s", out_path)
    try:
        while True:
            time.sleep(STATUS_INTERVAL_SEC)
            print(format_status(recorder), flush=True)
            if server.rejected:
                print(ru.REC_REJECTED.format(count=server.rejected))
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        recorder.close()
        print(ru.REC_STOPPED.format(packets=recorder.packets, path=out_path))
        log.info("Остановлено, пакетов: %d", recorder.packets)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # вывод в файл на Windows иначе в cp1251 (BUGLOG №39)
    main()
