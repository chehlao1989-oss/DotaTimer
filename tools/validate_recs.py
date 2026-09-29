"""Проверки советов до интерфейса (docs/RECOMMENDER_SPEC.md, раздел 8).

8.1 Вики-эталон: против героя E хотя бы один предмет из вики-списка E попадает в топ-3 советов (таблица hero).
8.2 Отложенные дни: считаем всё без последних N дней, проверяем на них знак B у советов с высокой уверенностью.
8.3 Отчёт автору: Markdown-таблица топ-3 для выбранных героев против выбранных героев и предметов.

Запуск:
  python tools/validate_recs.py --raw raw --recs recs.json.gz --report docs/recs_report.md [--holdout-days 3]
"""
import argparse
import gzip
import json
import os
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compute_recs import (compute, level_metrics, load_config, load_refs, prepare,  # noqa: E402
                                raw_globs, build_item_meta)
from app.threats.mechanics import MechanicsTagger, load_mechanics_config  # noqa: E402

BENCHMARK_PATH = ROOT / "data" / "benchmark_counters.json"
# набор автора для проверки глазами (спека 8.3): его герои и враги/предметы из разговора с автором
REPORT_HEROES = ["juggernaut", "void_spirit", "axe", "lion", "windrunner"]
REPORT_ENEMIES = ["axe", "sniper", "phantom_assassin", "alchemist"]
REPORT_ITEMS = ["heart", "butterfly", "black_king_bar", "blade_mail"]
TOP = 3


def load_recs(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main_role(recs: dict, hero_id: int) -> str | None:
    """Роль, в которой героя H чаще всего играют (из meta.hero_class)."""
    cls = recs["meta"]["hero_class"].get(str(hero_id))
    return cls[1] if cls else None


# ---------- 8.1 ----------
def check_benchmark(recs: dict, benchmark: dict, candidates: set, bucket: str = "normal", group: str = "all") -> dict:
    """Доля пар (H, E), где в топ-3 советов против E есть предмет из вики-списка E.

    Вики-список фильтруется по кандидатам-ответам (расходники, нейтралки, старые предметы выпадают).
    Пары без советов не считаются. Отдельно — пары, где у первого совета уверенность не низкая.
    """
    table = recs["hero"].get(bucket, {}).get(group, {})
    pairs = hits = pairs_conf = hits_conf = 0
    misses = []
    for hero_id in recs["meta"]["hero_class"]:
        role = main_role(recs, int(hero_id))
        answers = table.get(role, {}).get(hero_id, {})
        for enemy in benchmark["heroes"].values():
            wiki = set(enemy["counter_items"]) & candidates
            rows = answers.get(str(enemy["hero_id"]))
            if not wiki or not rows:
                continue
            top = [r[0] for r in rows[:TOP]]
            hit = bool(wiki & set(top))
            pairs += 1
            hits += hit
            if rows[0][4] != "low":
                pairs_conf += 1
                hits_conf += hit
            if not hit:
                misses.append((int(hero_id), enemy["hero_id"], top))
    return {"pairs": pairs, "hits": hits, "share": hits / pairs if pairs else None,
            "pairs_confident": pairs_conf, "hits_confident": hits_conf,
            "share_confident": hits_conf / pairs_conf if pairs_conf else None, "misses": misses}


# ---------- 8.2 ----------
def check_holdout(matches_glob: str, ranks_glob: str | None, refs: tuple, cfg, days: int, log=print) -> dict:
    """Считаем на днях 1…N−days, проверяем знак B* советов с высокой уверенностью на последних days днях."""
    items, abilities, hero_abilities, heroes = refs
    newest = duckdb.connect().execute(f"SELECT max(start_time) FROM read_parquet('{matches_glob}')").fetchone()[0]
    cutoff = newest - days * 86400
    train = compute(matches_glob, ranks_glob, items, abilities, hero_abilities, heroes, cfg, log=log,
                    where=f"start_time < {cutoff}")
    ids = {k: v["id"] for k, v in items.items() if isinstance(v, dict) and "id" in v}
    rows = []
    for bucket, groups in train["item"].items():
        for role, by_hero in groups.get("all", {}).items():
            for hero, by_ctx in by_hero.items():
                for ctx, answers in by_ctx.items():
                    for y, _s, _a, b, conf, _n, _flag in answers:
                        if conf == "high" and b != 0 and y in ids:
                            rows.append((bucket, role, int(hero), ctx, ids[y], b))
    con = duckdb.connect()
    roles = {v["name"].removeprefix("npc_dota_hero_"): tuple(v.get("roles") or ()) for v in heroes.values()}
    tagger = MechanicsTagger(load_mechanics_config(), items, abilities, hero_abilities, roles)
    prepare(con, matches_glob, ranks_glob, build_item_meta(items, tagger, heroes, cfg), heroes, cfg,
            where=f"start_time >= {cutoff}")
    level_metrics(con, "L2", "true", "test_L2", min_buy_share=cfg.min_buy_share)
    con.execute("CREATE TABLE train(bucket VARCHAR, role VARCHAR, hero SMALLINT, ctx VARCHAR, y SMALLINT, b DOUBLE)")
    if rows:
        con.executemany("INSERT INTO train VALUES (?, ?, ?, ?, ?, ?)", rows)
    checked, agree = con.execute("""
        SELECT count(*), count(*) FILTER (WHERE sign(t.b) = sign(s.B))
        FROM train t JOIN test_L2 s USING (bucket, role, hero, ctx, y) WHERE s.B IS NOT NULL AND s.B <> 0
    """).fetchone()
    return {"cutoff": cutoff, "high_rows": len(rows), "checked": checked, "agree": agree,
            "share": agree / checked if checked else None}


# ---------- 8.3 ----------
def report_markdown(recs: dict, heroes: dict, bucket: str = "normal", group: str = "all") -> str:
    """Топ-3 для героев автора против героев и предметов из набора (спека 8.3)."""
    by_name = {v["name"].removeprefix("npc_dota_hero_"): v for v in heroes.values()}
    items_ids = recs.get("_item_ids", {})
    lines = [f"# Отчёт по советам ({bucket}, ранги: {group})", "",
             f"Матчей: {recs['meta']['matches']}, патч {recs['meta']['patch']}, "
             f"доля с рангом {recs['meta']['ranked_share']:.0%}.", "",
             "Колонки: предмет, A (насколько чаще берут, п.п.), B (насколько чаще выигрывают с ним, п.п.), "
             "игр, уверенность, откуда (правило механики или «опыт» — по опыту игроков).", ""]
    for hero_key in REPORT_HEROES:
        hero = by_name.get(hero_key)
        if not hero:
            continue
        role = main_role(recs, hero["id"])
        lines += [f"## {hero['localized_name']} ({role})", "", "| против | 1 | 2 | 3 |", "|---|---|---|---|"]
        for enemy_key in REPORT_ENEMIES:
            enemy = by_name.get(enemy_key)
            rows = recs["hero"].get(bucket, {}).get(group, {}).get(role, {}).get(str(hero["id"]), {}).get(
                str(enemy["id"]) if enemy else "", [])
            name = enemy["localized_name"] if enemy else enemy_key
            lines.append(_row(name, rows))
        for item_key in REPORT_ITEMS:
            x = items_ids.get(item_key)
            rows = recs["item"].get(bucket, {}).get(group, {}).get(role, {}).get(str(hero["id"]), {}).get(str(x), [])
            lines.append(_row(f"{item_key} у врага", rows))
        lines.append("")
    return "\n".join(lines)


def _cell(row) -> str:
    y, _s, a, b, conf, n, flag = row
    source = "опыт" if flag == "exp" else flag
    return f"**{y}** A {a:+.1f}, B {b:+.1f}, {n} игр, {conf}, {source}"


def _row(title: str, rows: list) -> str:
    cells = [_cell(r) for r in rows[:TOP]] + ["—"] * (TOP - len(rows[:TOP]))
    return f"| {title} | " + " | ".join(cells) + " |"


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверки советов (спека, раздел 8)")
    parser.add_argument("--raw", default="raw")
    parser.add_argument("--recs", default="recs.json.gz")
    parser.add_argument("--cache", default=str(Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "cache"))
    parser.add_argument("--holdout-days", type=int, default=3)
    parser.add_argument("--report", default="recs_report.md")
    args = parser.parse_args()
    refs = load_refs(Path(args.cache))
    items, _abilities, _hero_abilities, heroes = refs
    cfg = load_config()
    recs = load_recs(Path(args.recs))
    recs["_item_ids"] = {k: v["id"] for k, v in items.items() if isinstance(v, dict) and "id" in v}
    candidates = {k for k, v in items.items() if isinstance(v, dict) and (v.get("cost") or 0) > 0
                  and not k.startswith("recipe") and v.get("qual") != "consumable" and not v.get("tier")
                  and ((v.get("created") and v["cost"] >= cfg.candidate_min_cost) or k in cfg.extra_candidates)}
    bench = check_benchmark(recs, json.loads(BENCHMARK_PATH.read_text(encoding="utf-8")), candidates)
    print(f"8.1 вики-эталон: {bench['hits']}/{bench['pairs']} пар = {_pct(bench['share'])}; "
          f"с уверенностью не ниже средней {bench['hits_confident']}/{bench['pairs_confident']} = "
          f"{_pct(bench['share_confident'])} (нужно от 80%)")
    matches, ranks = raw_globs(Path(args.raw))
    hold = check_holdout(matches, ranks, refs, cfg, args.holdout_days, log=lambda *_: None)
    print(f"8.2 отложенные {args.holdout_days} дн.: знак B совпал {hold['agree']}/{hold['checked']} = "
          f"{_pct(hold['share'])} (нужно от 70%; советов с высокой уверенностью {hold['high_rows']})")
    Path(args.report).write_text(report_markdown(recs, heroes), encoding="utf-8")
    print(f"8.3 отчёт: {args.report}")


def _pct(v) -> str:
    return "—" if v is None else f"{v:.0%}"


if __name__ == "__main__":
    main()
