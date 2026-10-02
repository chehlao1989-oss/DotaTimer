"""Проверки советов до интерфейса (docs/RECOMMENDER_SPEC.md, раздел 8).

8.1 Вики — проверка здравого смысла: против E хотя бы один вики-предмет E в топ-3 советов (по достижимым парам) + разбор расхождений.
8.2 Отложенные дни: считаем всё без последних N дней; главное — знак A там, где он значим на проверке; B — отдельно (вето).
8.3 Синтетический тест (спека 9.2): на искусственном наборе B находит «Y помогает только против X», A не реагирует на длительность.
8.4 Таблица автору: Markdown/HTML топ-3 для выбранных героев против выбранных героев и предметов.
Критерии — DECISIONS №21.

Запуск:
  python tools/validate_recs.py --raw raw --recs recs.zip --report docs/recs_report.md [--holdout-days 3]
"""
import argparse
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compute_recs import (compute, level_metrics_parts, load_config, load_refs, prepare,  # noqa: E402
                                raw_globs, build_item_meta)
from app.threats.mechanics import MechanicsTagger, load_mechanics_config  # noqa: E402
from app.threats.recs_file import load_all  # noqa: E402

BENCHMARK_PATH = ROOT / "data" / "benchmark_counters.json"
# набор автора для проверки глазами (спека 8.3): его герои и враги/предметы из разговора с автором
REPORT_HEROES = ["juggernaut", "void_spirit", "axe", "lion", "windrunner"]
REPORT_ENEMIES = ["axe", "sniper", "phantom_assassin", "alchemist"]
REPORT_ITEMS = ["heart", "butterfly", "black_king_bar", "blade_mail"]
TOP = 3


def load_recs(path: Path) -> dict:
    """Весь файл советов: recs.zip (по героям) или старый recs.json.gz."""
    if Path(path).suffix == ".zip":
        return load_all(Path(path))
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def decode(recs: dict, row: list) -> tuple:
    """Строка ответа → (ключ предмета, S, A, B, уверенность, игр, источник). Понимает и старые строки с названиями."""
    if isinstance(row[0], str):
        return tuple(row)
    meta = recs["meta"]
    names = recs.get("_item_names", {})
    return (names.get(row[0], str(row[0])), row[1], row[2], row[3], meta["conf_codes"][row[4]], row[5],
            meta["flags"][row[6]])


def main_role(recs: dict, hero_id: int) -> str | None:
    """Роль, в которой героя H чаще всего играют (из meta.hero_class)."""
    cls = recs["meta"]["hero_class"].get(str(hero_id))
    return cls[1] if cls else None


# ---------- 8.1 ----------
def check_benchmark(recs: dict, benchmark: dict, candidates: set, bucket: str = "normal", group: str = "all") -> dict:
    """Доля пар (H, E), где в топ-3 советов против E есть предмет из вики-списка E.

    Вики-список фильтруется по кандидатам-ответам (расходники, нейтралки, старые предметы выпадают).
    Пары без советов не считаются. Отдельно — пары, где у первого совета уверенность не низкая, и «достижимые» пары:
    герой H покупает хотя бы один вики-предмет в ≥ 3% игр (иначе программа его не покажет по спеке 6.4).
    """
    table = recs["hero"].get(bucket, {}).get(group, {})
    names = recs.get("_item_names", {})
    pairs = hits = pairs_conf = hits_conf = pairs_reach = hits_reach = 0
    misses = []
    for hero_id in recs["meta"]["hero_class"]:
        role = main_role(recs, int(hero_id))
        answers = table.get(role, {}).get(hero_id, {})
        buys = {names.get(y, y) for y in recs.get("buys", {}).get(bucket, {}).get(role, {}).get(hero_id, [])}
        for enemy in benchmark["heroes"].values():
            wiki = set(enemy["counter_items"]) & candidates
            rows = [decode(recs, r) for r in answers.get(str(enemy["hero_id"]), [])]
            if not wiki or not rows:
                continue
            top = [r[0] for r in rows[:TOP]]
            hit = bool(wiki & set(top))
            pairs += 1
            hits += hit
            if rows[0][4] != "low":
                pairs_conf += 1
                hits_conf += hit
            if wiki & buys:
                pairs_reach += 1
                hits_reach += hit
                if not hit:  # расхождение по достижимой паре: вики-предмет герой покупает, а в топ-3 его нет
                    misses.append({"hero": int(hero_id), "enemy": enemy["hero_id"], "role": role, "top": rows[:TOP],
                                   "wiki": sorted(wiki & buys), "rows": rows})
    share = lambda h, n: h / n if n else None  # noqa: E731
    return {"pairs": pairs, "hits": hits, "share": share(hits, pairs),
            "pairs_confident": pairs_conf, "hits_confident": hits_conf, "share_confident": share(hits_conf, pairs_conf),
            "pairs_reachable": pairs_reach, "hits_reachable": hits_reach, "share_reachable": share(hits_reach, pairs_reach),
            "misses": misses}


def disagreements(misses: list[dict], limit: int = 10) -> list[dict]:
    """Самые заметные расхождения с вики (DECISIONS №21): по достижимым парам, где у вики-предмета больше всего игр.

    Кто прав — решают данные, а не вики: если против врага вики-предмет берут не чаще обычного (A ≤ 0) — данные
    вики не подтверждают; если A > 0, но ниже наших советов — оба правы, наши советы против этого врага берут чаще;
    если вики-предмета среди ответов нет — его отсёк фильтр (мало игр, промежуточный, сапоги — DECISIONS №14).
    """
    out = []
    for m in misses:
        by_item = {r[0]: (pos, r) for pos, r in enumerate(m["rows"], 1)}
        found = [(by_item[w][1][5], w, by_item[w]) for w in m["wiki"] if w in by_item]
        best = max(found) if found else None
        top = m["top"][0]
        if best is None:
            verdict = "вики-предмета нет среди ответов (отсечён фильтром: мало игр, промежуточный или сапоги)"
            wiki_row, pos = None, None
        else:
            _n, _w, (pos, wiki_row) = best
            if wiki_row[2] <= 0:
                verdict = "данные вики не подтверждают: против этого врага предмет берут не чаще обычного (A ≤ 0)"
            else:
                verdict = "оба правы: вики-предмет берут чаще (A > 0), но наши советы — ещё чаще (A выше)"
        weight = wiki_row[5] if wiki_row else 0
        out.append({**m, "wiki_row": wiki_row, "wiki_pos": pos, "first": top, "verdict": verdict, "weight": weight})
    return sorted(out, key=lambda d: -d["weight"])[:limit]


# ---------- 8.2 ----------
def check_holdout(matches_glob: str, ranks_glob: str | None, refs: tuple, cfg, days: int, log=print) -> dict:
    """Считаем на днях 1…N−days, проверяем знак B* советов с высокой уверенностью на последних days днях."""
    items, abilities, hero_abilities, heroes = refs
    newest = duckdb.connect().execute(f"SELECT max(start_time) FROM read_parquet('{matches_glob}')").fetchone()[0]
    cutoff = newest - days * 86400
    train = compute(matches_glob, ranks_glob, items, abilities, hero_abilities, heroes, cfg, log=log,
                    where=f"start_time < {cutoff}")
    ids = {k: v["id"] for k, v in items.items() if isinstance(v, dict) and "id" in v}
    train["_item_names"] = {v: k for k, v in ids.items()}
    rows = []
    for bucket, groups in train["item"].items():
        for role, by_hero in groups.get("all", {}).items():
            for hero, by_ctx in by_hero.items():
                for ctx, answers in by_ctx.items():
                    for y, _s, a, b, conf, _n, _flag in (decode(train, r) for r in answers):
                        if conf == "high" and y in ids:
                            rows.append((bucket, role, int(hero), ctx, ids[y], b, a))
    con = duckdb.connect()
    roles = {v["name"].removeprefix("npc_dota_hero_"): tuple(v.get("roles") or ()) for v in heroes.values()}
    tagger = MechanicsTagger(load_mechanics_config(), items, abilities, hero_abilities, roles)
    meta = build_item_meta(items, tagger, heroes, cfg)
    # для порога «настоящего контр-предмета» (решение автора 02.10): сырые A по парам «герой против героя»
    prepare(con, matches_glob, ranks_glob, meta, heroes, cfg, where=f"start_time < {cutoff}")
    level_metrics_parts(con, "hero", "true", "train_hero", min_buy_share=cfg.min_buy_share)
    prepare(con, matches_glob, ranks_glob, meta, heroes, cfg, where=f"start_time >= {cutoff}")
    level_metrics_parts(con, "hero", "true", "test_hero", min_buy_share=cfg.min_buy_share)
    level_metrics_parts(con, "L2", "true", "test_L2", min_buy_share=cfg.min_buy_share)
    thresholds = a_thresholds(con, cfg.z90)
    con.execute("CREATE TABLE train(bucket VARCHAR, role VARCHAR, hero SMALLINT, ctx VARCHAR, y SMALLINT, b DOUBLE, "
                "a DOUBLE)")
    if rows:
        con.executemany("INSERT INTO train VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    # спека 8.2: знак B; дополнительно — только значимые на проверочной части (там шум меньше) и то же для A
    stats = con.execute(f"""
        WITH j AS (SELECT t.*, s.A AS ta, s.vA AS tva, s.B AS tb, s.vB AS tvb FROM train t
                   JOIN test_L2 s USING (bucket, role, hero, ctx, y))
        SELECT count(*) FILTER (WHERE b <> 0 AND tb IS NOT NULL AND tb <> 0),
               count(*) FILTER (WHERE b <> 0 AND tb IS NOT NULL AND tb <> 0 AND sign(b) = sign(tb)),
               count(*) FILTER (WHERE b <> 0 AND abs(tb) > {cfg.z90} * sqrt(greatest(tvb, 0))),
               count(*) FILTER (WHERE b <> 0 AND abs(tb) > {cfg.z90} * sqrt(greatest(tvb, 0)) AND sign(b) = sign(tb)),
               count(*) FILTER (WHERE a <> 0 AND ta IS NOT NULL AND ta <> 0),
               count(*) FILTER (WHERE a <> 0 AND ta IS NOT NULL AND ta <> 0 AND sign(a) = sign(ta)),
               count(*) FILTER (WHERE a <> 0 AND abs(ta) > {cfg.z90} * sqrt(greatest(tva, 0))),
               count(*) FILTER (WHERE a <> 0 AND abs(ta) > {cfg.z90} * sqrt(greatest(tva, 0)) AND sign(a) = sign(ta))
        FROM j
    """).fetchone()
    share = lambda h, n: h / n if n else None  # noqa: E731
    checked, agree, b_sig, b_sig_agree, a_checked, a_agree, a_sig, a_sig_agree = stats
    return {"cutoff": cutoff, "high_rows": len(rows), "checked": checked, "agree": agree, "share": share(agree, checked),
            "b_significant": b_sig, "share_b_significant": share(b_sig_agree, b_sig),
            "a_checked": a_checked, "share_a": share(a_agree, a_checked),
            "a_significant": a_sig, "share_a_significant": share(a_sig_agree, a_sig), "thresholds": thresholds}


A_THRESHOLDS_PP = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0)


def a_thresholds(con: duckdb.DuckDBPyConnection, z: float) -> list[dict]:
    """Порог «настоящего контр-предмета»: совет, если на обучающих днях A значим (A − z·√vA > 0) и A ≥ порога.

    Для каждого порога: сколько таких советов, у какой доли знак A повторился на отложенных днях (всех и где A
    значим на проверке), и у какой доли пар «герой против героя» (обычный режим) остаётся хоть один совет.
    A — сырые по ячейке (без сглаживания), в п.п.; пары — по обучающей части.
    """
    out = []
    pairs = con.execute("SELECT count(*) FROM (SELECT DISTINCT role, hero, ctx FROM train_hero WHERE bucket = 'normal')"
                        ).fetchone()[0]
    for thr in A_THRESHOLDS_PP:
        good = f"t.A - {z} * sqrt(greatest(t.vA, 0)) > 0 AND t.A * 100 >= {thr}"
        n, agree, sig, sig_agree = con.execute(f"""
            SELECT count(*) FILTER (WHERE s.A IS NOT NULL AND s.A <> 0),
                   count(*) FILTER (WHERE s.A > 0),
                   count(*) FILTER (WHERE abs(s.A) > {z} * sqrt(greatest(s.vA, 0))),
                   count(*) FILTER (WHERE s.A > {z} * sqrt(greatest(s.vA, 0)))
            FROM train_hero t JOIN test_hero s USING (bucket, role, hero, ctx, y) WHERE {good}
        """).fetchone()
        kept = con.execute(f"""SELECT count(*) FROM (SELECT DISTINCT role, hero, ctx FROM train_hero t
                               WHERE bucket = 'normal' AND {good})""").fetchone()[0]
        out.append({"pp": thr, "checked": n, "share": agree / n if n else None, "significant": sig,
                    "share_significant": sig_agree / sig if sig else None, "pairs": pairs,
                    "pairs_kept": kept, "share_pairs": kept / pairs if pairs else None})
    return out


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
                         "answers": [decode(recs, r) for r in answers("hero").get(str(enemy["id"]) if enemy else "", [])[:TOP]]})
        for item_key in REPORT_ITEMS:
            rows.append({"kind": "item", "key": item_key, "title": item_key,
                         "answers": [decode(recs, r) for r in answers("item").get(str(items_ids.get(item_key)), [])[:TOP]]})
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
    parser.add_argument("--recs", default="recs.zip")
    parser.add_argument("--cache", default=str(Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "cache"))
    parser.add_argument("--holdout-days", type=int, default=3)
    parser.add_argument("--report", default="recs_report.md")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # при выводе в файл Windows берёт cp1251, а в отчёте есть «≤»
    refs = load_refs(Path(args.cache))
    items, _abilities, _hero_abilities, heroes = refs
    cfg = load_config()
    recs = load_recs(Path(args.recs))
    recs["_item_ids"] = {k: v["id"] for k, v in items.items() if isinstance(v, dict) and "id" in v}
    recs["_item_names"] = {v: k for k, v in recs["_item_ids"].items()}
    candidates = {k for k, v in items.items() if isinstance(v, dict) and (v.get("cost") or 0) > 0
                  and not k.startswith("recipe") and v.get("qual") != "consumable" and not v.get("tier")
                  and ((v.get("created") and v["cost"] >= cfg.candidate_min_cost) or k in cfg.extra_candidates)}
    bench = check_benchmark(recs, json.loads(BENCHMARK_PATH.read_text(encoding="utf-8")), candidates)
    # критерии — DECISIONS №21 (решение автора 02.10)
    print(f"8.1 вики (здравый смысл) по достижимым парам: {bench['hits_reachable']}/{bench['pairs_reachable']} = "
          f"{_pct(bench['share_reachable'])} (нужно от 50%); все пары {_pct(bench['share'])}, "
          f"с уверенностью не ниже средней {_pct(bench['share_confident'])}; расхождений {len(bench['misses'])}")
    title = lambda h: heroes.get(str(h), {}).get("localized_name", h)  # noqa: E731
    item_title = lambda k: items.get(k, {}).get("dname", k) if isinstance(items.get(k), dict) else k  # noqa: E731
    for n, d in enumerate(disagreements(bench["misses"]), 1):
        first, wiki = d["first"], d["wiki_row"]
        wiki_text = (f"{item_title(wiki[0])} — место {d['wiki_pos']}, A {wiki[2]:+.1f}, B {wiki[3]:+.1f}, игр {wiki[5]}"
                     if wiki else ", ".join(item_title(w) for w in d["wiki"]))
        print(f"  {n}. {title(d['hero'])} ({d['role']}) против {title(d['enemy'])}: наш первый — {item_title(first[0])} "
              f"(A {first[2]:+.1f}, B {first[3]:+.1f}, игр {first[5]}); вики — {wiki_text}. {d['verdict']}")
    matches, ranks = raw_globs(Path(args.raw))
    hold = check_holdout(matches, ranks, refs, cfg, args.holdout_days, log=lambda *_: None)
    print(f"8.2 отложенные {args.holdout_days} дн. (советов с высокой уверенностью {hold['high_rows']}):\n"
          f"  главное — знак A там, где значим на проверке: {_pct(hold['share_a_significant'])} из {hold['a_significant']} "
          f"(нужно от 80%); знак A всех: {_pct(hold['share_a'])} из {hold['a_checked']}\n"
          f"  B (только вето): знак совпал {_pct(hold['share'])} из {hold['checked']}, где значим — "
          f"{_pct(hold['share_b_significant'])} из {hold['b_significant']} (вернуть в порядок — от 70%, DECISIONS №20)")
    print("  порог «настоящего контр-предмета» (герой против героя; A значим на обучении и ≥ порога):")
    for t in hold["thresholds"]:
        print(f"    A ≥ {t['pp']:.1f} п.п.: советов {t['checked']}, знак A повторился {_pct(t['share'])}, "
              f"где значим на проверке — {_pct(t['share_significant'])} из {t['significant']}; "
              f"пар с советом {t['pairs_kept']} из {t['pairs']} = {_pct(t['share_pairs'])}")
    synthetic = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests" / "test_compute_recs.py"), "-k",
                                "skadi_only_against_heart or not_fooled_by_game_length"], capture_output=True, text=True)
    print(f"8.3 синтетический тест (спека 9.2: B находит «Y помогает только против X», A не реагирует на длительность): "
          f"{'пройден' if synthetic.returncode == 0 else 'НЕ ПРОЙДЕН'} — {synthetic.stdout.strip().splitlines()[-1]}")
    Path(args.report).write_text(report_markdown(recs, heroes, items=items), encoding="utf-8")
    html_path = Path(args.report).with_suffix(".html")
    rules = {r["id"]: r["title_ru"] for r in json.loads((ROOT / "data" / "mechanics.json").read_text(encoding="utf-8"))["rules"]}
    html_path.write_text(report_html(recs, heroes, items, Path(args.cache) / "images", rules), encoding="utf-8")
    print(f"8.4 таблица для автора: {args.report}, {html_path}")


def _pct(v) -> str:
    return "—" if v is None else f"{v:.0%}"


if __name__ == "__main__":
    main()
