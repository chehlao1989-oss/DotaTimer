"""Стенд: вся программа (окна, полоска, голос, угрозы, подсказки) на записи GSI — без сети и без Доты (BUGLOG №57).

Пакеты из записи (tools/gsi_recorder.py) подаются в программу напрямую, минуя GSI-сервер и порт 53000, — поэтому
можно запускать, даже когда работает программа автора. Время записи ускоряется в --speed раз. Вместо зрения стенд
сам подкладывает верхнюю панель (5 врагов) и «клики по врагам» с растущим инвентарём — работают подсказки и полоска.
Настройки, кеш и логи — во временной папке (копия %APPDATA%\\DotaTimer без логов и записей).
Голос стенда немой (только строка в логе). Итог: код выхода программы, последние строки её лога и crash.log; 0 — дошла до конца записи и вышла сама.

Запуск: python tools/stress_app.py [--recording файл.jsonl] [--speed 20] [--offscreen]
"""
import argparse
import logging
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL = Path(os.environ.get("APPDATA", ".")) / "DotaTimer"
ENEMIES = ["phantom_assassin", "slark", "chaos_knight", "lina", "lion"]
ALLIES = ["axe", "crystal_maiden", "juggernaut", "pudge"]
ITEMS = ["magic_wand", "boots", "power_treads", "diffusal_blade", "butterfly", "heart", "skadi", "black_king_bar",
         "manta", "sange_and_yasha", "blink", "invis_sword", "silver_edge", "radiance", "mjollnir", "satanic",
         "abyssal_blade", "mithril_hammer", "demon_edge", "lesser_crit", "greater_crit", "reaver", "perseverance"]


def child(recording: Path, speed: float, clicks: float) -> None:
    """Сама программа с подменённым GSI-сервером (запускается этим же файлом с --child-run)."""
    sys.path.insert(0, str(ROOT))
    import app.main as main_mod
    from app.threats.service import ThreatsService
    from app.vision.inventory import InventorySnapshot
    from app.vision.matcher import Match
    from app.vision.topbar import TopbarResult

    services = []
    original = ThreatsService.__init__

    def capture(self, *a, **k):
        original(self, *a, **k)
        services.append(self)

    ThreatsService.__init__ = capture

    class FakeServer:
        port = 0

        def __init__(self, on_packet, *a, **k):
            self.on_packet = on_packet
            self._stop = threading.Event()

        def start(self):
            threading.Thread(target=self._run, name="fake-gsi", daemon=True).start()

        def stop(self):
            self._stop.set()

        def _run(self):
            from PySide6.QtCore import QCoreApplication, QMetaObject, Qt
            packets = [json.loads(line) for line in recording.read_text(encoding="utf-8").splitlines() if line]
            rnd, inventories, topbar_sent, next_click = random.Random(57), {e: [] for e in ENEMIES}, False, 0.0
            started = time.monotonic()
            for prev, cur in zip([packets[0]] + packets, packets):
                if self._stop.wait(max(0.0, (cur["t"] - prev["t"]) / speed)):
                    return
                self.on_packet(cur["data"])
                clock = cur["data"].get("map", {}).get("clock_time")
                vision = services[0].vision if services else None
                if vision is None or clock is None:
                    continue
                if not topbar_sent and clock >= 0:  # как снимок верхней панели: свои слева, враги справа
                    topbar_sent = True
                    radiant = tuple(Match(h, 0.8, 0.3) for h in ALLIES + ["windrunner"])
                    vision.topbar_ready.emit(TopbarResult(radiant, tuple(Match(h, 0.8, 0.3) for h in ENEMIES)))
                if topbar_sent and time.monotonic() - started >= next_click:  # «клик по врагу» — как сигнал зрения
                    next_click = time.monotonic() - started + clicks
                    enemy = rnd.choice(ENEMIES)
                    if len(inventories[enemy]) < 6 and rnd.random() < 0.6:
                        inventories[enemy].append(rnd.choice(ITEMS))
                    slots = tuple(inventories[enemy] + [None] * (6 - len(inventories[enemy])))
                    vision.inventory_seen.emit(InventorySnapshot(enemy, slots, (None, None, None), None))
            time.sleep(3)
            QMetaObject.invokeMethod(QCoreApplication.instance(), "quit", Qt.QueuedConnection)

    # голос не в колонки: 20.10 прогон стенда полчаса говорил фразы автору, пока тот работал (стенд — немой)
    import app.notify.voice as voice_mod
    voice_mod.VoicePlayer.play = lambda self, key: logging.getLogger("stress").info("Голос: %s (без звука)", key)
    voice_mod.VoicePlayer.preload = lambda self, keys: None
    main_mod.GsiServer = FakeServer
    sys.argv = [sys.argv[0], "--minimized", "--child"]
    main_mod.main()


def main() -> None:
    parser = argparse.ArgumentParser(description="Вся программа на записи GSI без сети")
    parser.add_argument("--recording", help="запись GSI; по умолчанию — самая длинная из recordings")
    parser.add_argument("--speed", type=float, default=20.0)
    parser.add_argument("--clicks", type=float, default=2.0, help="раз в сколько секунд «кликать» по врагу")
    parser.add_argument("--offscreen", action="store_true", help="без окон на экране")
    parser.add_argument("--child-run", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # BUGLOG №39
    if args.recording is None:
        args.recording = str(max((REAL / "recordings").glob("*.jsonl"), key=lambda p: p.stat().st_size))
    if args.child_run:
        child(Path(args.recording), args.speed, args.clicks)
        return
    tmp = Path(tempfile.mkdtemp(prefix="dotatimer-stress-"))
    home = tmp / "DotaTimer"
    home.mkdir()
    for name in ("settings.json", "recs.zip", "stats.json"):
        if (REAL / name).is_file():
            shutil.copy(REAL / name, home / name)
    if (REAL / "cache").is_dir():
        shutil.copytree(REAL / "cache", home / "cache")
    env = {**os.environ, "APPDATA": str(tmp), "PYTHONIOENCODING": "utf-8"}
    if args.offscreen:
        env["QT_QPA_PLATFORM"] = "offscreen"
    print(f"Запись: {args.recording}, ускорение ×{args.speed:g}, папка: {tmp}", flush=True)
    started = time.monotonic()
    code = subprocess.call([sys.executable, __file__, "--child-run", "--recording", args.recording,
                            "--speed", str(args.speed), "--clicks", str(args.clicks)], env=env, cwd=str(ROOT))
    logs = home / "logs"
    print(f"Код выхода: {code} (0x{code & 0xFFFFFFFF:08X}), {time.monotonic() - started:.0f} с")
    text = (logs / "dotatimer.log").read_text(encoding="utf-8", errors="replace") if (logs / "dotatimer.log").is_file() else ""
    print("Подсказок:", text.count("Подсказка:"), "| голос:", text.count("Голос:"), "| ошибок в логе:",
          text.count(" ERROR ") + text.count(" CRITICAL "), "| «Выход»:", "Выход" in text)
    for line in [l for l in text.splitlines() if " ERROR " in l or " CRITICAL " in l or " WARNING " in l][-15:]:
        print("  ", line[:240])
    crash = (logs / "crash.log").read_text(encoding="utf-8", errors="replace") if (logs / "crash.log").is_file() else ""
    if "Fatal Python error" in crash or "Windows fatal exception" in crash:
        print("crash.log:\n" + crash[-4000:])
    sys.exit(0 if code == 0 else 1)


if __name__ == "__main__":
    main()
