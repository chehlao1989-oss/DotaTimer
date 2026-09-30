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
CONF_RU = {"high": "высокая", "mid": "средняя", "low": "низкая"}
ROLE_RU = {"core": "кор", "offlane": "тройка", "support": "саппорт"}


def report_sections(recs: dict, heroes: dict, bucket: str = "normal", group: str = "all") -> list[dict]:
    """Данные отчёта 8.3: для каждого героя автора — строки «против героя E» и «предмет X у врага» с топ-3."""
    by_name = {v["name"].removeprefix("npc_dota_hero_"): v for v in heroes.values()}
    items_ids = recs.get("_item_ids", {})
    sections = []
    for hero_key in REPORT_HEROES:
        hero = by_name.get(hero_key)
        if not hero:
            continue
        role = main_role(recs, hero["id"])

        def answers(table: str, hero=hero, role=role) -> dict:
            return recs[table].get(bucket, {}).get(group, {}).get(role, {}).get(str(hero["id"]), {})

        rows = []
        for enemy_key in REPORT_ENEMIES:
            enemy = by_name.get(enemy_key)
            rows.append({"kind": "hero", "key": enemy_key, "title": enemy["localized_name"] if enemy else enemy_key,
                         "answers": answers("hero").get(str(enemy["id"]) if enemy else "", [])[:TOP]})
        for item_key in REPORT_ITEMS:
            rows.append({"kind": "item", "key": item_key, "title": item_key,
                         "answers": answers("item").get(str(items_ids.get(item_key)), [])[:TOP]})
        sections.append({"hero": hero, "role": role, "rows": rows})
    return sections


def report_markdown(recs: dict, heroes: dict, bucket: str = "normal", group: str = "all",
                    items: dict | None = None) -> str:
    """Топ-3 для героев автора против героев и предметов (спека 8.3). Предметы — полным английским названием."""
    def name(key: str) -> str:
        return ((items or {}).get(key) or {}).get("dname") or key

    lines = [f"# Отчёт по советам ({bucket}, ранги: {group})", "",
             f"Матчей: {recs['meta']['matches']}, патч {recs['meta']['patch']}, "
             f"доля с рангом {recs['meta']['ranked_share']:.0%}.", "",
             "Колонки: предмет, A (насколько чаще берут, п.п.), B (насколько чаще выигрывают с ним, п.п.), "
             "игр, уверенность, откуда (правило механики или «опыт» — по опыту игроков).", ""]
    for section in report_sections(recs, heroes, bucket, group):
        lines += [f"## {section['hero']['localized_name']} ({section['role']})", "",
                  "| против | 1 | 2 | 3 |", "|---|---|---|---|"]
        for row in section["rows"]:
            title = row["title"] if row["kind"] == "hero" else f"{name(row['key'])} у врага"
            cells = [_cell(r, name) for r in row["answers"]] + ["—"] * (TOP - len(row["answers"]))
            lines.append(f"| {title} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def _cell(row, name=lambda key: key) -> str:
    y, _s, a, b, conf, n, flag = row
    source = "опыт" if flag == "exp" else flag
    return f"**{name(y)}** A {a:+.1f}, B {b:+.1f}, {n} игр, {conf}, {source}"


def _num(value: int) -> str:
    return f"{value:,}".replace(",", " ")


REPORT_CSS = """
:root{--bg:#101217;--panel:#181b22;--line:#2a2f3a;--text:#e6e9ef;--muted:#9aa3b2;--accent:#d8b45a}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--text);font:14px/1.4 "Segoe UI",system-ui,sans-serif}
h1{font-size:20px;margin:0 0 4px} .sub{color:var(--muted);margin:0 0 20px;max-width:900px}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 18px;overflow-x:auto}
h2{display:flex;align-items:center;gap:10px;font-size:17px;margin:4px 0 10px}
.role{color:var(--accent);font-size:13px;font-weight:400}
table{border-collapse:collapse;width:100%;min-width:760px}
th,td{border-top:1px solid var(--line);padding:8px;vertical-align:top;text-align:left}
th{width:200px;font-weight:600} td{width:30%}
.answer{display:flex;gap:8px;align-items:flex-start}
small{display:block;color:var(--muted);font-size:12px;margin-top:2px}
.none{color:var(--muted)}
.icon{width:44px;height:32px;border-radius:3px;flex:none;vertical-align:middle;margin-right:6px}
.portrait{width:64px;height:36px;border-radius:4px;vertical-align:middle}
.portrait.small{width:48px;height:27px;margin-right:6px}
.empty{display:inline-block;background:var(--line)}
.pic{display:inline-block;background-size:cover;background-position:center}
"""


def report_html(recs: dict, heroes: dict, items: dict, images_dir: Path, rules: dict,
                bucket: str = "normal", group: str = "all") -> str:
    """Отчёт 8.3 страницей: иконки предметов и портреты героев из кеша программы, полные английские названия."""
    import base64
    import html

    styles = {}  # каждая картинка вставляется в страницу один раз (класс с фоном), дальше — ссылка на класс

    def img(kind: str, key: str, cls: str) -> str:
        path = images_dir / kind / f"{key}.png"
        if not path.is_file():
            return f'<span class="{cls} empty"></span>'
        css_class = f"img-{kind}-{key}"
        if css_class not in styles:
            data = base64.b64encode(path.read_bytes()).decode()
            styles[css_class] = f".{css_class}{{background-image:url(data:image/png;base64,{data})}}"
        return f'<span class="{cls} pic {css_class}"></span>'

    def dname(key: str) -> str:
        return html.escape((items.get(key) or {}).get("dname") or key)

    out = []
    for section in report_sections(recs, heroes, bucket, group):
        hero = section["hero"]
        hero_key = hero["name"].removeprefix("npc_dota_hero_")
        out.append(f'<section><h2>{img("heroes", hero_key, "portrait")}{html.escape(hero["localized_name"])}'
                   f'<span class="role">{ROLE_RU.get(section["role"], section["role"] or "")}</span></h2><table>')
        for row in section["rows"]:
            if row["kind"] == "hero":
                head = f'{img("heroes", row["key"], "portrait small")}против {html.escape(row["title"])}'
            else:
                head = f'{img("items", row["key"], "icon")}{dname(row["key"])} у врага'
            cells = []
            for y, _s, a, b, conf, n, flag in row["answers"]:
                source = "по опыту игроков" if flag == "exp" else "по механике: " + html.escape(rules.get(flag, flag))
                cells.append(f'<td><div class="answer">{img("items", y, "icon")}<div><b>{dname(y)}</b>'
                             f'<small>берут {a:+.1f} п.п. · побед {b:+.1f} п.п.<br>{_num(n)} игр · '
                             f'уверенность {CONF_RU.get(conf, conf)}<br>{source}</small></div></div></td>')
            cells += ['<td class="none">—</td>'] * (TOP - len(cells))
            out.append(f'<tr><th>{head}</th>{"".join(cells)}</tr>')
        out.append("</table></section>")
    meta = recs["meta"]
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Советы по предметам</title><style>{REPORT_CSS}{"".join(styles.values())}</style></head><body>'
            '<h1>Советы по предметам: проверка глазами (8.3)</h1>'
            f'<p class="sub">{_num(meta["matches"])} матчей, патч {meta["patch"]}, режим {bucket}, все ранги. '
            '«Берут» — насколько чаще покупают против этого врага, «побед» — насколько чаще выигрывают с этим '
            'предметом против него (оба в процентных пунктах).</p>'
            + "".join(out) + "</body></html>")


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
    Path(args.report).write_text(report_markdown(recs, heroes, items=items), encoding="utf-8")
    html_path = Path(args.report).with_suffix(".html")
    rules = {r["id"]: r["title_ru"] for r in json.loads((ROOT / "data" / "mechanics.json").read_text(encoding="utf-8"))["rules"]}
    html_path.write_text(report_html(recs, heroes, items, Path(args.cache) / "images", rules), encoding="utf-8")
    print(f"8.3 отчёт: {args.report}, {html_path}")


def _pct(v) -> str:
    return "—" if v is None else f"{v:.0%}"


if __name__ == "__main__":
    main()
