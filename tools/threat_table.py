"""Проверочная таблица: какие угрозы и советы выдаёт алгоритм для героев пользователя.

Запуск: python tools/threat_table.py [stats.json]
По умолчанию берёт stats.json из кеша программы (%APPDATA%\\DotaTimer\\cache).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.i18n import ru  # noqa: E402
from app.threats.data import DataCache, GameData  # noqa: E402
from app.threats.mechanics import MechanicsTagger, load_mechanics_config  # noqa: E402
from app.threats.recommend import TIER_MECHANIC, Recommender, ThreatInput, recommend_config  # noqa: E402
from app.threats.stats import Stats  # noqa: E402
from app.threats.threat_score import facts, threat_score_config  # noqa: E402

MY_HEROES = ["juggernaut", "void_spirit", "axe", "lion", "windrunner"]
ENEMIES = ["axe", "sniper", "phantom_assassin", "alchemist"]
GAME_TIME = 25 * 60  # середина игры: фаза пропускает предметы до ~7500 золота


def reason_text(f) -> str:
    parts = [ru.THREAT_ROLE[r] for r in f.reasons if r in ru.THREAT_ROLE]
    if "late" in f.reasons:
        parts.append(ru.THREAT_LATE.format(wr=f.late_winrate))
    if "matchup" in f.reasons:
        parts.append(ru.THREAT_MATCHUP.format(wr=f.my_winrate))
    return ", ".join(parts) or "—"


def main() -> None:
    cache = DataCache()
    stats = Stats(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))) if len(sys.argv) > 1 else Stats.load(cache)
    data = GameData.from_cache(cache)
    raw_items, _ = cache.load("items")
    abilities, _ = cache.load("abilities")
    hero_abilities, _ = cache.load("hero_abilities")
    tagger = MechanicsTagger(load_mechanics_config(), raw_items, abilities, hero_abilities,
                             {h.name: h.roles for h in data.heroes.values()})
    config = json.loads((ROOT / "data" / "threats.json").read_text(encoding="utf-8"))
    score_cfg = threat_score_config(config)
    buyable = {k for k, i in data.items.items() if i.cost > 0 and not k.startswith("recipe") and not i.tier}
    rec = Recommender(tagger, stats, buyable, {k: i.cost for k, i in data.items.items()}, recommend_config(config))
    print(f"Статистика: патч {stats.patch}, матчей {stats.raw.get('matches')}, версия {stats.version}\n")
    for enemy in ENEMIES:
        e = data.hero_by_name(enemy)
        f = facts(stats, "normal", e.id, None, score_cfg)
        late = f"{f.late_delta:+.1f}" if f.late_delta is not None else "—"
        farm = f"{f.farm_rank:.2f}" if f.farm_rank is not None else "нет данных"
        wr = f"{f.winrate:.1f}%" if f.winrate is not None else "нет данных"
        print(f"### {e.localized}: место по нетворсу {farm}, лейт {late} п.п., винрейт {wr}")
    print()
    print("| Мой герой | Угроза | Причина | Совет 1 | Совет 2 |")
    print("|---|---|---|---|---|")
    for mine in MY_HEROES:
        me = data.hero_by_name(mine)
        for enemy in ENEMIES:
            if enemy == mine:
                continue
            e = data.hero_by_name(enemy)
            f = facts(stats, "normal", e.id, me.id, score_cfg)
            rec.reset()
            [r] = rec.recommend(me.id, [ThreatInput(enemy, e.id)], set(), GAME_TIME)
            cells = []
            for item in r.items[:2]:
                name = data.items[item.item].dname
                why = ru.MECHANIC_REASONS.get(item.reason, "")
                if item.tier == TIER_MECHANIC:
                    cells.append(f"{name} — {why} ({ru.LOW_DATA})")
                else:
                    cells.append(f"{name} — {why} (+{item.delta:.1f} п.п., {item.games} игр)")
            cells += ["—"] * (2 - len(cells))
            print(f"| {me.localized} | {e.localized} | {reason_text(f)} | {cells[0]} | {cells[1]} |")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # вывод в файл на Windows иначе в cp1251 (BUGLOG №39)
    main()
