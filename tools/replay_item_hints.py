"""Повтор подсказок v2 по логу автора (docs/TASK_ITEM_HINTS_V2.md, «Как проверить», п. 1).

Берёт из %APPDATA%\\DotaTimer\\logs\\dotatimer.log все строки «У <герой> новые предметы: [...]» за выбранные дни,
прогоняет их через новые подсказки (таблица механик + выбор под моего героя по recs.zip) и пишет таблицу
docs/reports/item_hints_v2_replay.md: событие → было (старая подсказка из лога) → стало → почему → источник.
Мой герой: строка «Мой герой» в логе, иначе — тот из моей пятёрки на верхней панели, кого нет среди «Союзников».

Запуск: python tools/replay_item_hints.py [--days 2026-10-05,2026-10-16]
"""
import argparse
import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.i18n import ru  # noqa: E402
from app.threats.data import DataCache, GameData  # noqa: E402
from app.threats.item_hints import CounterTable, choose, pick_parent  # noqa: E402
from app.threats.mechanics import load_mechanics_config  # noqa: E402
from app.threats.recs_advice import RecsRecommender, RecsSource, advice_config  # noqa: E402
from app.threats.scoring import load_threat_config  # noqa: E402
from app.threats.tracker import KIND_ITEM, ItemTracker  # noqa: E402

LOG = Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "logs" / "dotatimer.log"
OUT = ROOT / "docs" / "reports" / "item_hints_v2_replay.md"
LINE = re.compile(r"^(\S+ \S+),\d+ INFO (\S+): (.*)$")


def parse(days: set[str]):
    """События по каткам: [{match, date, own, enemies, events: [(время, герой, [предметы], [старые подсказки])]}]."""
    matches, cur, topbar = [], None, []
    for raw in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        stamp, source, text = m.groups()
        if stamp[:10] not in days:
            continue
        if text.startswith("Новый матч") or text == "Запуск" or cur is None:
            cur = {"match": text.removeprefix("Новый матч ").strip() if text.startswith("Новый матч") else "—",
                   "date": stamp[:16], "own": None, "enemies": [], "events": [], "allies": None}
            matches.append(cur)
        if text.startswith("Мой герой: "):
            cur["own"] = text.removeprefix("Мой герой: ")
        elif text.startswith("Верхняя панель: "):
            topbar = [h for h, _ in ast.literal_eval(text.removeprefix("Верхняя панель: "))]
        elif text.startswith("Союзники: "):
            cur["allies"] = ast.literal_eval(text.removeprefix("Союзники: "))
        elif text.startswith("Враги: "):
            cur["enemies"] = ast.literal_eval(text.removeprefix("Враги: ").split("; угрозы")[0])
            if cur["own"] is None and cur["allies"] is not None and len(topbar) == 10:
                own_side = topbar[5:] if set(cur["enemies"]) & set(topbar[:5]) else topbar[:5]
                left = [h for h in own_side if h not in cur["allies"]]
                cur["own"] = left[0] if len(left) == 1 else None
        elif "новые предметы:" in text:
            hero, items = text.removeprefix("У ").split(" новые предметы: ")
            cur["events"].append([stamp[11:16], hero, ast.literal_eval(items), []])
        elif text.startswith("Подсказка") and cur["events"]:
            cur["events"][-1][3].append(text.split(": ", 1)[1].replace(" | ", " "))
    return [m for m in matches if m["events"]]


def main() -> None:
    parser = argparse.ArgumentParser(description="Повтор подсказок v2 по логу")
    parser.add_argument("--days", default="2026-10-05,2026-10-16")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # BUGLOG №39
    import json
    data = GameData.from_cache(DataCache())
    table = CounterTable.load(load_mechanics_config())
    hints_cfg = json.loads((ROOT / "data" / "threats.json").read_text(encoding="utf-8"))
    cfg = hints_cfg["item_hints"]
    recs_path = Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "recs.zip"
    source = RecsSource.open(recs_path)
    recommender = RecsRecommender(source, data.items, advice_config(hints_cfg)) if source else None
    shares_kind = "buy_share" if source and any("buy_share" in source.hero(h.id) for h in
                                                 list(data.heroes.values())[:3]) else "buys (≥3%)"
    rows, stats = [], {"items": 0, "hinted": 0, "no_prop_hinted": 0, "threat_items": 0, "threat_no_hint": 0}
    for match in parse(set(args.days.split(","))):
        own = data.hero_by_name(match["own"] or "")
        shares = recommender.buy_shares(own.id, cfg["fallback_buys_share"]) if own and recommender else {}
        def likely_parent(hero, candidates):
            enemy = data.hero_by_name(hero)
            enemy_shares = (recommender.buy_shares(enemy.id, cfg["fallback_buys_share"], main_role=True)
                            if enemy and recommender else {})
            return pick_parent(enemy_shares, candidates)

        tracker = ItemTracker(data, None, load_threat_config().min_component_cost, important=table.threat_items(),
                              likely_parent=likely_parent)
        seen: dict[str, set] = {}
        for time_, hero, items, old in match["events"]:
            seen.setdefault(hero, set()).update(items)
            hints = {h.item if h.kind == KIND_ITEM else h.component: h
                     for h in tracker.update(hero, seen[hero], 0, {hero})}
            for item in items:
                stats["items"] += 1
                hint = hints.get(item)
                threat = table.is_threat(item)
                stats["threat_items"] += threat
                old_text = next((o for o in old if data.items.get(item) and data.items[item].dname in o), "—")
                if hint is None:
                    why = "в таблице, подсказка уже была" if threat else (
                        "деталь: во что соберёт этот герой — неизвестно" if tracker._parents.get(item) else
                        "предмет без опасного свойства — подсказки нет")
                    stats["threat_no_hint"] += threat
                    rows.append((match, time_, hero, item, old_text, "—", why, ""))
                    continue
                target = hint.item
                choices = choose(table.vs_item(target), shares.get, lambda k: False, cfg["min_own_share"], cfg["limit"])
                stats["hinted"] += 1
                stats["no_prop_hinted"] += not table.props(target)
                new = ", ".join(f"{data.items[c.item].dname}{' (редко на герое)' if c.rare else ''}"
                                f"{f' {c.share:.0f}%' if c.share else ''}" for c in choices) or "нет подтверждённых"
                props = ", ".join(table.props(target))
                answers = ", ".join(sorted({ru.MECHANIC_REASONS.get(c.answer, c.answer) for c in choices}))
                why = (f"{'собирает ' + data.items[target].dname + ' → ' if hint.kind != KIND_ITEM else ''}"
                       f"свойства: {props}; ответ: {answers}")
                src = " ".join(f"[{data.items[c.item].dname}]({table.source(next(x for x in table.vs_item(target) if x.item == c.item))})"
                               for c in choices)
                rows.append((match, time_, hero, item, old_text, new, why, src))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Повтор подсказок v2 по логу ({args.days})", "",
             f"Создано tools/replay_item_hints.py. Доли покупки моего героя — из recs.zip ({shares_kind}).", "",
             f"- новых предметов у врагов: {stats['items']} (замер, лог)",
             f"- подсказок v2: {stats['hinted']}; из них на предмет без свойства: {stats['no_prop_hinted']}",
             f"- предметов из таблицы без подсказки (уже подсказано раньше): {stats['threat_no_hint']}", "",
             "| Матч | Время | Мой герой | Враг | Предмет | Было (лог) | Стало (v2) | Почему | Источник |",
             "|---|---|---|---|---|---|---|---|---|"]
    for match, time_, hero, item, old_text, new, why, src in rows:
        name = data.items[item].dname if item in data.items else item
        lines.append(f"| {match['match']} | {time_} | {match['own'] or 'неизвестен'} | {hero} | {name} | {old_text} | "
                     f"{new} | {why} | {src} |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:9]))
    print(f"Таблица: {OUT}")


if __name__ == "__main__":
    main()
