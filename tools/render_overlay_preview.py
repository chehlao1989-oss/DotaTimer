"""Предпросмотр оверлея поверх настоящих игровых скриншотов (docs/PROCESS.md, класс «Не проверено глазами»).

Рисует окна программы (сообщения таймеров и полоску угроз) в их положении по умолчанию поверх скриншотов
автора в двух разрешениях — 1920×1080 и 2560×1080 — и обводит зоны HUD (миникарта, верхняя панель,
инвентарь). Пишет, пересекаются ли окна с этими зонами (в пикселях): 0 — не пересекаются.

Скриншоты берутся из папки --screens (по умолчанию tests/fixtures/screens, если там полноэкранные,
иначе папка скриншотов Доты в Steam). Если скриншот другого размера, он растягивается до нужного
разрешения (подпись «растянут»): HUD Доты привязан к краям и центру, поэтому для проверки положения этого
хватает, но пропорции картинки искажены.
Картинки с чужими никами в репозиторий не кладём: результат — в %APPDATA%/DotaTimer/previews.

Запуск: python tools/render_overlay_preview.py [--screens папка] [--out папка]
"""
import argparse
import glob
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))  # иначе вместо букв квадраты

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import app.ui.overlay as overlay_mod  # noqa: E402
from app.config import OverlaySettings, StripSettings  # noqa: E402
from app.i18n import ru  # noqa: E402
from app.threats.recommend import RecItem, ThreatRecommendation  # noqa: E402
from app.vision.geometry import HudLayout  # noqa: E402

TARGETS = [(1920, 1080), (2560, 1080)]
# Миникарта с кнопками справа от неё. Не входит в центрированный HUD: прижата к левому нижнему углу и
# масштабируется по высоте. Замер по скриншоту автора 20260929094950 (2560×1080), глазами по кропу, ±5 px.
MINIMAP_REF = (0, 798, 328, 282)  # x, y, ширина, высота при высоте экрана 1080
# Килфид и чат над миникартой слева. Замер по скриншоту автора 20260930224009 (игра 30.09): 2 строки килфида
# на 685–745 px; с новыми убийствами растёт вверх — запас на ~6 строк до 540 px (оценка).
KILLFEED_REF = (0, 540, 380, 250)
STEAM_SCREENS = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "userdata"
SAMPLE_MESSAGES = [(ru.MSG_POWER_RUNE.format(sec=30), False),
                   (ru.MSG_FIRST_POWER_RUNE_EARLY.format(time="6:00"), True)]
SAMPLE_THREATS = [ThreatRecommendation("axe", (RecItem("blade_mail", 1, "hero", 6.0, 900),
                                               RecItem("black_king_bar", 1, "hero", 4.0, 900))),
                  ThreatRecommendation("phantom_assassin", (RecItem("monkey_king_bar", 1, "hero", 9.0, 800),)),
                  ThreatRecommendation("sniper", (RecItem("blink", 1, "mechanic", None, 0),))]


def find_screens(folder: str | None) -> list[Path]:
    if folder:
        return sorted(Path(p) for p in glob.glob(str(Path(folder) / "*")) if p.lower().endswith((".png", ".jpg")))
    fixtures = [p for p in (ROOT / "tests" / "fixtures" / "screens").glob("*") if p.suffix.lower() in (".png", ".jpg")]
    full = [p for p in fixtures if QImage(str(p)).height() >= 1080]
    if full:
        return sorted(full)
    return sorted(Path(p) for p in glob.glob(str(STEAM_SCREENS / "*" / "760" / "remote" / "570" / "screenshots" / "*.jpg")))


def hud_zones(w: int, h: int) -> dict[str, QRect]:
    hud = HudLayout(w, h)
    s = h / 1080
    top = hud.topbar_slots()
    inv = hud.inventory_slots()
    mx, my, mw, mh = MINIMAP_REF
    kx, ky, kw, kh = KILLFEED_REF
    return {
        "миникарта": QRect(round(mx * s), round(my * s), round(mw * s), round(mh * s)),
        "килфид и чат": QRect(round(kx * s), round(ky * s), round(kw * s), round(kh * s)),
        "верхняя панель": QRect(top[0].x, top[0].y, top[-1].x + top[-1].w - top[0].x, top[0].h),
        "инвентарь": QRect(inv[0].x, inv[0].y, inv[-1].x + inv[-1].w - inv[0].x, inv[-1].y + inv[-1].h - inv[0].y),
    }


REAL_SCENARIO = {"hero": "juggernaut", "enemies": ["phantom_assassin", "axe"], "minute": 15, "gold": 3000,
                 "enemy_items": {"phantom_assassin": {"butterfly"}}}


def real_threats():
    """Советы из настоящего recs.zip (если есть) для сцены REAL_SCENARIO — как их покажет программа."""
    from app.paths import app_data_dir
    from app.threats.data import DataCache, GameData
    from app.threats.recommend import ThreatInput
    from app.threats.recs_advice import AdviceConfig, RecsRecommender, RecsSource
    import json
    path = app_data_dir() / "recs.zip"
    source = RecsSource.open(path) if path.is_file() else None
    data = GameData.from_cache(DataCache())
    if source is None or not data.ready:
        return None
    raw = json.loads((ROOT / "data" / "threats.json").read_text(encoding="utf-8"))["recs_advice"]
    rec = RecsRecommender(source, data.items, AdviceConfig(raw["phase_base_cost"], raw["phase_cost_per_min"],
                                                            raw["multi_threat_bonus"], raw["hysteresis"],
                                                            raw["top_for_bonus"]))
    me = data.hero_by_name(REAL_SCENARIO["hero"])
    inputs = [ThreatInput(e, data.hero_by_name(e).id, frozenset(REAL_SCENARIO["enemy_items"].get(e, set())))
              for e in REAL_SCENARIO["enemies"]]
    threats = rec.recommend(me.id, inputs, set(), REAL_SCENARIO["minute"] * 60, REAL_SCENARIO["gold"])
    return threats, {e: data.hero_by_name(e).localized for e in REAL_SCENARIO["enemies"]}


def build_windows(w: int, h: int, images_dir: Path) -> list[QWidget]:
    """Окна программы как на экране w×h: подменяем размер экрана, от которого они считают масштаб и место."""
    screen = QRect(0, 0, w, h)
    overlay_mod.screen_factor = lambda: h / 1080

    def place(self, width, height, default_pos):
        self.resize(width, height)
        if self.settings.x is None or self.settings.y is None:
            self.move(*default_pos(screen, width, height))
        else:
            self.move(self.settings.x, self.settings.y)

    overlay_mod.DraggableOverlay.place = place
    from app.ui.threat_strip import ThreatStrip  # после подмены: полоска берёт overlay_font и place отсюда
    messages = overlay_mod.Overlay(OverlaySettings())
    for text, important in SAMPLE_MESSAGES:
        messages.show_message(text, important=important, duration_sec=3600)
    for frame in messages._frames():
        frame.setGraphicsEffect(None)  # эффект плавного угасания не рисуется через render() — снимаем для снимка
    strip = ThreatStrip(StripSettings(), images_dir)
    threats, titles = real_threats() or (SAMPLE_THREATS, {"axe": "Axe", "phantom_assassin": "Phantom Assassin",
                                                          "sniper": "Sniper"})
    strip.update_recommendations(threats, titles)
    for widget in (messages, strip):
        widget.show()
        widget.relayout()
    QApplication.processEvents()
    return [messages, strip]


def render(screen_path: Path, w: int, h: int, images_dir: Path, out: Path) -> list[str]:
    image = QImage(str(screen_path)).convertToFormat(QImage.Format_ARGB32)
    stretched = image.width() != w or image.height() != h
    if stretched:
        image = image.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    windows = build_windows(w, h, images_dir)
    zones = hud_zones(w, h)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    report = []
    for name, rect in zones.items():
        painter.setPen(QPen(QColor(255, 80, 80), 2, Qt.DashLine))
        painter.drawRect(rect)
    for widget in windows:
        widget.render(painter, QPoint(widget.x(), widget.y()), renderFlags=QWidget.RenderFlag.DrawChildren)
        box = QRect(widget.x(), widget.y(), widget.width(), widget.height())
        painter.setPen(QPen(QColor(80, 200, 255), 1, Qt.DotLine))
        painter.drawRect(box)
        for name, rect in zones.items():
            cross = box.intersected(rect)
            area = cross.width() * cross.height() if not cross.isEmpty() else 0
            report.append(f"{type(widget).__name__} × {name}: {area} px²")
        inside = QRect(0, 0, w, h).contains(box)
        report.append(f"{type(widget).__name__}: {box.x()},{box.y()} {box.width()}×{box.height()}"
                      f"{'' if inside else ' — ВЫХОДИТ ЗА ЭКРАН'}")
    painter.setPen(QColor(255, 255, 255))
    painter.setFont(QFont("Segoe UI", 14))
    painter.drawText(12, h - 12, f"{w}×{h}{' (скриншот растянут)' if stretched else ''} — {screen_path.name}")
    painter.end()
    for widget in windows:
        widget.close()
        widget.deleteLater()
    QApplication.processEvents()
    path = out / f"{screen_path.stem}_{w}x{h}.png"
    image.save(str(path))
    return [str(path)] + report


def main() -> None:
    parser = argparse.ArgumentParser(description="Оверлей поверх игровых скриншотов")
    parser.add_argument("--screens", help="папка со скриншотами игры")
    parser.add_argument("--out", default=str(Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "previews"))
    parser.add_argument("--limit", type=int, default=3, help="сколько скриншотов взять")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    images_dir = Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "cache" / "images"
    screens = find_screens(args.screens)[: args.limit]
    if not screens:
        sys.exit("нет скриншотов: укажи --screens")
    for screen in screens:
        for w, h in TARGETS:
            lines = render(screen, w, h, images_dir, out)
            print(lines[0])
            for line in lines[1:]:
                print("   ", line)


if __name__ == "__main__":
    main()
