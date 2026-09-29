"""Расчёт советов по сырым матчам (docs/RECOMMENDER_SPEC.md, разделы 3–7) на DuckDB.

Вход: сырые матчи matches-*.parquet и ранги ranks-*.parquet (tools/collect_raw.py),
справочники OpenDota (предметы, способности, герои) и правила механик (data/mechanics.json).
Выход: recs.json.gz — готовые ответы для программы (раздел 7).

Коротко:
- «у H есть Y», если в итоговом инвентаре есть Y или предмет, в который Y собирается (дерево из dotaconstants);
- страты: длительность (4 корзины) × роль H в этой игре по месту в нетворсе (кор 1–2, тройка 3, саппорт 4–5);
- каждая игра весит по давности: половинный срок 21 день (спека 2.2);
- A — адаптация: P(Y | X есть) − P(Y | X нет) внутри страты, взвешенно по числу игр с X;
- B — эффект, разность разностей: [WR(Y,X) − WR(¬Y,X)] − [WR(Y,¬X) − WR(¬Y,¬X)] внутри страты;
- уровни контекста: L1 — X у героя E, L2 — X у любого врага, L3 — у врага предмет с той же механикой;
  оценки стягиваются L1 → L2 → L3 с силой k, подобранной эмпирическим Байесом (раздел 5);
- запасной вариант для героя H — его класс (основной атрибут × типичная роль), таблицы *_class;
- кандидаты: механически отвечают X или E, либо в топ-5 по A («по опыту игроков»);
- S = A*/σ_A + λ·B*/σ_B, вето по верхней границе B*, уверенность по числу игр (раздел 5–6).

Все тяжёлые расчёты идут в DuckDB: в Python попадают только готовые топ-N ответов.

Запуск: python tools/compute_recs.py --raw raw --out recs.json.gz [--cache %APPDATA%/DotaTimer/cache]
"""
import argparse
import gzip
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.threats.mechanics import MechanicsTagger, load_mechanics_config  # noqa: E402

CONFIG_PATH = ROOT / "data" / "recs_config.json"
EXPERIENCE_FLAG = "exp"  # пометка «по опыту игроков»: не по правилу механик, а по топу адаптации


@dataclass(frozen=True)
class RecsConfig:
    duration_edges: dict  # корзина → границы страт по длительности, минуты
    candidate_min_cost: int
    key_item_min_cost: int
    extra_candidates: list
    rank_groups: dict  # имя группы → [min_tier, max_tier]
    half_life_days: float
    k_a_default: float
    k_b_default: float
    lam: float
    veto_b: float  # п.п.: если верхняя 90%-граница B* ниже −veto_b — предмет вредит
    z90: float
    conf_high: int
    conf_mid: int
    top_n: int
    adaptation_top: int  # сколько лучших по A пускать в кандидаты без правила механик
    min_buy_share: float  # доля игр H в роли, в которых он покупает Y; реже — Y не кандидат (спека 6.4)


def load_config(path: Path = CONFIG_PATH) -> RecsConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    fields = {k: v for k, v in raw.items() if not k.startswith("note")}
    return RecsConfig(**fields)


# ---------- справочники ----------
@dataclass
class ItemMeta:
    candidates: set  # id предметов-ответов Y
    key_items: set  # id ключевых предметов врага X
    expand: list  # пары (id предмета в инвентаре Z, id Y, который он засчитывает)
    mechanic_of: dict  # id X → список механик (меток врага)
    names: dict  # id → ключ предмета
    answers: list  # (вид контекста x/m/e, контекст, id Y, id правила) — Y механически отвечает контексту


def component_closure(key: str, items: dict, seen=None) -> set:
    """Все компоненты предмета рекурсивно (для дерева «Y засчитывается, если собран во что-то»)."""
    seen = set() if seen is None else seen
    for comp in items.get(key, {}).get("components") or []:
        if comp not in seen:
            seen.add(comp)
            component_closure(comp, items, seen)
    return seen


def build_item_meta(items: dict, tagger: MechanicsTagger, heroes: dict, cfg: RecsConfig) -> ItemMeta:
    ids = {k: v["id"] for k, v in items.items() if isinstance(v, dict) and "id" in v}
    buyable = {k for k, v in items.items() if isinstance(v, dict) and (v.get("cost") or 0) > 0
               and not k.startswith("recipe") and v.get("qual") != "consumable" and not v.get("tier")}
    candidates = {k for k in buyable if (items[k].get("created") and items[k]["cost"] >= cfg.candidate_min_cost)
                  or k in cfg.extra_candidates}
    key_items = {k for k in buyable if tagger.item_traits(k)
                 or (items[k].get("created") and items[k]["cost"] >= cfg.key_item_min_cost)}
    expand = set()
    for z in ids:
        for y in {z} | component_closure(z, items):
            if y in candidates:
                expand.add((ids[z], ids[y]))
    mechanic_of = {ids[k]: sorted(tagger.item_traits(k)) for k in key_items if k in ids and tagger.item_traits(k)}

    # какие Y механически отвечают контексту: предмету врага X, механике m, герою E
    answers = set()
    by_rule = {r.id: {ids[y] for y in tagger.candidate_items(r, candidates) if y in ids} for r in tagger.rules}

    def add(kind: str, ctx: str, traits) -> None:
        for rule in tagger.rules_for_traits(frozenset(traits)):
            for y in by_rule[rule.id]:
                answers.add((kind, ctx, y, rule.id))

    for k in key_items:
        if k in ids:
            add("x", str(ids[k]), tagger.item_traits(k))
    for rule in tagger.rules:
        add("m", rule.enemy, {rule.enemy})
    for h in heroes.values():
        add("e", str(h["id"]), tagger.hero_traits(h["name"]))
    return ItemMeta({ids[k] for k in candidates if k in ids}, {ids[k] for k in key_items if k in ids},
                    sorted(expand), mechanic_of, {v: k for k, v in ids.items()}, sorted(answers))


# ---------- подготовка игр ----------
def prepare(con: duckdb.DuckDBPyConnection, matches_glob: str, ranks_glob: str | None, meta: ItemMeta,
            heroes: dict, cfg: RecsConfig, where: str = "true") -> None:
    """Таблица pg: одна строка на игру героя (корзина, ранг-группа, роль, класс, страта, вес, победа).

    where — фильтр по матчам (например, по start_time для проверки на отложенных днях).
    """
    con.execute(f"CREATE OR REPLACE VIEW m AS SELECT * FROM read_parquet('{matches_glob}') WHERE {where}")
    if ranks_glob:
        con.execute(f"CREATE OR REPLACE VIEW r AS SELECT match_id, max(avg_rank_tier) AS rank_tier "
                    f"FROM read_parquet('{ranks_glob}') GROUP BY match_id")
    else:
        con.execute("CREATE OR REPLACE VIEW r AS SELECT NULL::BIGINT AS match_id, NULL::TINYINT AS rank_tier WHERE false")
    edges_n = cfg.duration_edges["normal"]
    edges_t = cfg.duration_edges["turbo"]
    groups = " ".join(f"WHEN rank_tier BETWEEN {lo} AND {hi} THEN '{g}'" for g, (lo, hi) in cfg.rank_groups.items())
    half_life = cfg.half_life_days * 86400.0
    con.execute(f"""
        CREATE OR REPLACE TABLE pg AS
        WITH base AS (
            SELECT m.*, r.rank_tier,
                   CASE WHEN game_mode = 23 THEN 'turbo' ELSE 'normal' END AS bucket,
                   row_number() OVER (PARTITION BY m.match_id, is_radiant ORDER BY net_worth DESC) AS nw_place,
                   max(start_time) OVER () AS newest
            FROM m LEFT JOIN r USING (match_id)
        )
        SELECT match_id, start_time, hero_id AS hero, is_radiant, bucket, patch,
               CASE {groups} ELSE NULL END AS rank_group,
               CASE WHEN nw_place <= 2 THEN 'core' WHEN nw_place = 3 THEN 'offlane' ELSE 'support' END AS role,
               CASE WHEN bucket = 'normal' THEN
                    CASE WHEN duration < {edges_n[0]}*60 THEN 0 WHEN duration < {edges_n[1]}*60 THEN 1
                         WHEN duration < {edges_n[2]}*60 THEN 2 ELSE 3 END
                    ELSE
                    CASE WHEN duration < {edges_t[0]}*60 THEN 0 WHEN duration < {edges_t[1]}*60 THEN 1
                         WHEN duration < {edges_t[2]}*60 THEN 2 ELSE 3 END END AS stratum,
               power(0.5, (newest - start_time) / {half_life}) AS wt,
               (is_radiant = radiant_win) AS win, items
        FROM base
    """)
    # класс героя: основной атрибут × роль, в которой его чаще всего играют (спека 5)
    con.execute("CREATE OR REPLACE TABLE hero_attr(hero SMALLINT, attr VARCHAR)")
    con.executemany("INSERT INTO hero_attr VALUES (?, ?)",
                    [(h["id"], h.get("primary_attr") or "all") for h in heroes.values()])
    con.execute("""
        CREATE OR REPLACE TABLE hero_cls AS
        WITH rc AS (SELECT hero, role, count(*) AS c FROM pg GROUP BY ALL)
        SELECT rc.hero, coalesce(a.attr, 'all') || '-' || arg_max(rc.role, rc.c) AS cls, arg_max(rc.role, rc.c) AS main_role
        FROM rc LEFT JOIN hero_attr a USING (hero) GROUP BY rc.hero, a.attr
    """)
    con.execute("CREATE OR REPLACE TABLE pg AS SELECT pg.*, hc.cls FROM pg JOIN hero_cls hc USING (hero)")

    con.execute("CREATE OR REPLACE TABLE expand(z SMALLINT, y SMALLINT)")
    if meta.expand:
        con.executemany("INSERT INTO expand VALUES (?, ?)", meta.expand)
    con.execute("CREATE OR REPLACE TABLE key_items(x SMALLINT)")
    if meta.key_items:
        con.executemany("INSERT INTO key_items VALUES (?)", [(x,) for x in sorted(meta.key_items)])
    # что засчитано каждому игроку: Y (ответы) и X (ключевые предметы)
    con.execute("""
        CREATE OR REPLACE TABLE owned_y AS
        SELECT DISTINCT p.match_id, p.is_radiant, p.hero, e.y
        FROM pg p, UNNEST(p.items) AS t(z) JOIN expand e ON e.z = t.z
    """)
    con.execute("""
        CREATE OR REPLACE TABLE owned_x AS
        SELECT DISTINCT p.match_id, p.is_radiant, p.hero, t.z AS x
        FROM pg p, UNNEST(p.items) AS t(z) JOIN key_items k ON k.x = t.z
    """)
    con.execute("CREATE OR REPLACE TABLE x_mech(x SMALLINT, mech VARCHAR)")
    mech_rows = [(x, m) for x, ms in meta.mechanic_of.items() for m in ms]
    if mech_rows:
        con.executemany("INSERT INTO x_mech VALUES (?, ?)", mech_rows)
    con.execute("CREATE OR REPLACE TABLE ans(kind VARCHAR, ctx VARCHAR, y SMALLINT, rule VARCHAR)")
    if meta.answers:
        con.executemany("INSERT INTO ans VALUES (?, ?, ?, ?)", meta.answers)
    con.execute("CREATE OR REPLACE TABLE ans1 AS SELECT kind, ctx, y, min(rule) AS rule FROM ans GROUP BY ALL")


# ---------- метрики по стратам (в DuckDB) ----------
CTX_SQL = {
    "hero": "SELECT DISTINCT p.match_id, p.is_radiant, CAST(e.hero AS VARCHAR) AS ctx FROM pg p "
            "JOIN pg e ON e.match_id = p.match_id AND e.is_radiant <> p.is_radiant",
    "L1": "SELECT DISTINCT p.match_id, p.is_radiant, CAST(ox.hero AS VARCHAR) || ':' || CAST(ox.x AS VARCHAR) AS ctx "
          "FROM pg p JOIN owned_x ox ON ox.match_id = p.match_id AND ox.is_radiant <> p.is_radiant",
    "L2": "SELECT DISTINCT p.match_id, p.is_radiant, CAST(ox.x AS VARCHAR) AS ctx FROM pg p "
          "JOIN owned_x ox ON ox.match_id = p.match_id AND ox.is_radiant <> p.is_radiant",
    "L3": "SELECT DISTINCT p.match_id, p.is_radiant, xm.mech AS ctx FROM pg p "
          "JOIN owned_x ox ON ox.match_id = p.match_id AND ox.is_radiant <> p.is_radiant "
          "JOIN x_mech xm ON xm.x = ox.x",
}


def level_metrics(con: duckdb.DuckDBPyConnection, level: str, rank_filter: str, dst: str, who: str = "hero",
                  min_buy_share: float = 0.0, min_ctx: float = 0.0) -> None:
    """A и B по стратам и их взвешенное среднее — в таблицу dst(bucket, role, hero, ctx, y, A, vA, B, vB, n).

    Уровни контекста: hero (герой E среди врагов), L1 (X у героя E), L2 (X у любого врага),
    L3 (у любого врага предмет с механикой). who = "hero" — считаем по герою H, "cls" — по его классу.
    Берутся только пары (контекст, Y), которые хоть раз встретились вместе; для них учитываются все страты
    (где пары нет — счётчик 0). Счётчики — суммы весов по давности. Значения — доли, не проценты.
    min_buy_share — Y берём, только если H покупает его хотя бы в такой доле игр в этой роли (спека 6.4:
    совет всё равно отсеется в программе, а без этого фильтра таблицы на миллионе матчей не помещаются в память).
    min_ctx — контекст берём, только если игр H с ним не меньше (для L1: в ответ идут связки от средней уверенности).
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE {dst} AS
        WITH g AS (SELECT * FROM pg WHERE {rank_filter}),
        ctx AS ({CTX_SQL[level]}),
        tot AS (SELECT bucket, role, {who} AS hero, stratum, sum(wt) AS n, sum(wt * win::INT) AS w
                FROM g GROUP BY ALL),
        gx AS (SELECT g.bucket, g.role, g.{who} AS hero, c.ctx, g.stratum, sum(g.wt) AS n, sum(g.wt * g.win::INT) AS w
               FROM g JOIN ctx c ON c.match_id = g.match_id AND c.is_radiant = g.is_radiant GROUP BY ALL),
        gy AS (SELECT g.bucket, g.role, g.{who} AS hero, o.y, g.stratum, sum(g.wt) AS n, sum(g.wt * g.win::INT) AS w
               FROM g JOIN owned_y o ON o.match_id = g.match_id AND o.is_radiant = g.is_radiant AND o.hero = g.hero
               GROUP BY ALL),
        gxy AS (SELECT g.bucket, g.role, g.{who} AS hero, c.ctx, o.y, g.stratum, sum(g.wt) AS n,
                       sum(g.wt * g.win::INT) AS w
                FROM g JOIN ctx c ON c.match_id = g.match_id AND c.is_radiant = g.is_radiant
                JOIN owned_y o ON o.match_id = g.match_id AND o.is_radiant = g.is_radiant AND o.hero = g.hero
                GROUP BY ALL),
        ok_y AS (SELECT y1.bucket, y1.role, y1.hero, y1.y
                 FROM (SELECT bucket, role, hero, y, sum(n) AS n FROM gy GROUP BY ALL) y1
                 JOIN (SELECT bucket, role, hero, sum(n) AS n FROM tot GROUP BY ALL) t1 USING (bucket, role, hero)
                 WHERE y1.n >= {min_buy_share} * t1.n),
        ok_ctx AS (SELECT bucket, role, hero, ctx FROM gx GROUP BY ALL HAVING sum(n) >= {min_ctx}),
        pairs AS (SELECT DISTINCT bucket, role, hero, ctx, y FROM gxy
                  SEMI JOIN ok_y USING (bucket, role, hero, y) SEMI JOIN ok_ctx USING (bucket, role, hero, ctx)),
        cells AS (
            SELECT pr.bucket, pr.role, pr.hero, pr.ctx, pr.y, gx.stratum,
                   tot.n AS N, tot.w AS W, gx.n AS NX, gx.w AS WX,
                   coalesce(gy.n, 0) AS NY, coalesce(gy.w, 0) AS WY,
                   coalesce(gxy.n, 0) AS NXY, coalesce(gxy.w, 0) AS WXY
            FROM pairs pr
            JOIN gx ON gx.bucket = pr.bucket AND gx.role = pr.role AND gx.hero = pr.hero AND gx.ctx = pr.ctx
            JOIN tot ON tot.bucket = pr.bucket AND tot.role = pr.role AND tot.hero = pr.hero AND tot.stratum = gx.stratum
            LEFT JOIN gy ON gy.bucket = pr.bucket AND gy.role = pr.role AND gy.hero = pr.hero AND gy.y = pr.y
                        AND gy.stratum = gx.stratum
            LEFT JOIN gxy ON gxy.bucket = pr.bucket AND gxy.role = pr.role AND gxy.hero = pr.hero
                         AND gxy.ctx = pr.ctx AND gxy.y = pr.y AND gxy.stratum = gx.stratum
        ),
        m AS (
            SELECT *,
                CASE WHEN NX > 0 AND N - NX > 0 THEN NXY / NX - (NY - NXY) / (N - NX) END AS a,
                CASE WHEN NX > 0 AND N - NX > 0 THEN
                    (NXY / NX) * (1 - NXY / NX) / NX + ((NY - NXY) / (N - NX)) * (1 - (NY - NXY) / (N - NX)) / (N - NX)
                END AS va,
                CASE WHEN NXY > 0 AND NX - NXY > 0 AND NY - NXY > 0 AND N - NX - NY + NXY > 0 THEN
                    (WXY / NXY - (WX - WXY) / (NX - NXY)) - ((WY - WXY) / (NY - NXY) - (W - WX - WY + WXY) / (N - NX - NY + NXY))
                END AS b
            FROM cells
        ),
        mb AS (
            SELECT *,
                CASE WHEN b IS NOT NULL THEN
                    (WXY / NXY) * (1 - WXY / NXY) / NXY
                    + ((WX - WXY) / (NX - NXY)) * (1 - (WX - WXY) / (NX - NXY)) / (NX - NXY)
                    + ((WY - WXY) / (NY - NXY)) * (1 - (WY - WXY) / (NY - NXY)) / (NY - NXY)
                    + ((W - WX - WY + WXY) / (N - NX - NY + NXY)) * (1 - (W - WX - WY + WXY) / (N - NX - NY + NXY))
                      / (N - NX - NY + NXY)
                END AS vb
            FROM m
        )
        SELECT bucket, role, hero, ctx, y,
               sum(NX * a) / nullif(sum(CASE WHEN a IS NOT NULL THEN NX END), 0) AS A,
               sum(NX * NX * va) / nullif(power(sum(CASE WHEN a IS NOT NULL THEN NX END), 2), 0) AS vA,
               sum(NX * b) / nullif(sum(CASE WHEN b IS NOT NULL THEN NX END), 0) AS B,
               sum(NX * NX * vb) / nullif(power(sum(CASE WHEN b IS NOT NULL THEN NX END), 2), 0) AS vB,
               sum(NX) AS n
        FROM mb GROUP BY ALL
    """)


def eb_k(con: duckdb.DuckDBPyConnection, table: str, metric: str, default: float) -> float:
    """Сила стягивания k = σ²/τ² методом моментов (эмпирический Байес, раздел 5)."""
    cnt, total_var, within, sigma2 = con.execute(f"""
        SELECT count(*), var_samp({metric}), avg(v{metric}), avg(v{metric} * n) FROM {table}
        WHERE {metric} IS NOT NULL AND v{metric} > 0 AND n > 0
    """).fetchone()
    if cnt < 30 or total_var is None:
        return default
    tau2 = total_var - within
    if tau2 <= 0 or not sigma2 or sigma2 <= 0:
        return default
    return max(1.0, sigma2 / tau2)


PRIOR_L2 = """
    SELECT l.bucket, l.role, l.hero, l.ctx, l.y, avg(p.A) AS pA, avg(p.B) AS pB
    FROM {src} l JOIN x_mech xm ON CAST(xm.x AS VARCHAR) = l.ctx
    JOIN {prior} p ON p.bucket = l.bucket AND p.role = l.role AND p.hero = l.hero AND p.y = l.y AND p.ctx = xm.mech
    GROUP BY ALL
"""
PRIOR_L1 = """
    SELECT l.bucket, l.role, l.hero, l.ctx, l.y, p.a_s AS pA, p.b_s AS pB
    FROM {src} l JOIN {prior} p ON p.bucket = l.bucket AND p.role = l.role AND p.hero = l.hero AND p.y = l.y
                                AND p.ctx = split_part(l.ctx, ':', 2)
"""


def smooth(con: duckdb.DuckDBPyConnection, src: str, dst: str, k: dict, prior_sql: str | None = None,
           prior: str | None = None) -> None:
    """Стягивание к более общему уровню: θ* = (n·θ + k·θ_prior) / (n + k); без приора — к нулю."""
    if prior_sql:
        join = f"LEFT JOIN ({prior_sql.format(src=src, prior=prior)}) p USING (bucket, role, hero, ctx, y)"
    else:
        join = "LEFT JOIN (SELECT NULL AS bucket, NULL AS role, NULL AS hero, NULL AS ctx, NULL AS y, " \
               "NULL::DOUBLE AS pA, NULL::DOUBLE AS pB WHERE false) p USING (bucket, role, hero, ctx, y)"
    ka, kb = k["A"], k["B"]
    con.execute(f"""
        CREATE OR REPLACE TABLE {dst} AS
        SELECT s.*,
            CASE WHEN s.A IS NULL THEN coalesce(p.pA, 0) ELSE (s.n * s.A + {ka} * coalesce(p.pA, 0)) / (s.n + {ka}) END AS a_s,
            s.vA * power(s.n / (s.n + {ka}), 2) AS va_s,
            CASE WHEN s.B IS NULL THEN coalesce(p.pB, 0) ELSE (s.n * s.B + {kb} * coalesce(p.pB, 0)) / (s.n + {kb}) END AS b_s,
            s.vB * power(s.n / (s.n + {kb}), 2) AS vb_s
        FROM {src} s {join}
    """)


# вид контекста → как найти правило механик: (вид в таблице ans, выражение контекста)
RULE_LOOKUP = {
    "L1": [("x", "split_part(t.ctx, ':', 2)"), ("e", "split_part(t.ctx, ':', 1)")],
    "L2": [("x", "t.ctx")],
    "L3": [("m", "t.ctx")],
    "hero": [("e", "t.ctx")],
}


def rank_answers(con: duckdb.DuckDBPyConnection, table: str, level: str, cfg: RecsConfig, k: dict,
                 only_confident: bool = False) -> list:
    """Топ-N ответов для каждого (bucket, role, hero, ctx): [Y, S, A*, B*, уверенность, n, правило или 'exp'].

    Кандидаты — механически отвечают контексту (правило) или входят в топ по A* («по опыту игроков»).
    """
    sd_a, sd_b = con.execute(f"SELECT stddev_samp(a_s), stddev_samp(b_s) FROM {table}").fetchone()
    sd_a, sd_b = sd_a or 1.0, sd_b or 1.0
    joins, rules = [], []
    for i, (kind, expr) in enumerate(RULE_LOOKUP[level]):
        joins.append(f"LEFT JOIN ans1 r{i} ON r{i}.kind = '{kind}' AND r{i}.ctx = {expr} AND r{i}.y = t.y")
        rules.append(f"r{i}.rule")
    k_min = min(k["A"], k["B"])
    confident = "AND conf <> 'low'" if only_confident else ""
    return con.execute(f"""
        WITH t AS (SELECT t.*, coalesce({", ".join(rules)}) AS rule FROM {table} t {" ".join(joins)}),
        s AS (
            SELECT *, a_s / {sd_a} + {cfg.lam} * coalesce(b_s, 0) / {sd_b} AS S,
                   CASE WHEN n < {k_min} THEN 'low' WHEN n >= {cfg.conf_high} THEN 'high'
                        WHEN n >= {cfg.conf_mid} THEN 'mid' ELSE 'low' END AS conf,
                   row_number() OVER (PARTITION BY bucket, role, hero, ctx ORDER BY a_s DESC) AS ra
            FROM t WHERE a_s IS NOT NULL
        ),
        f AS (
            SELECT * FROM s
            WHERE (rule IS NOT NULL OR ra <= {cfg.adaptation_top})
              AND NOT (b_s IS NOT NULL AND vb_s IS NOT NULL AND (b_s + {cfg.z90} * sqrt(greatest(vb_s, 0))) * 100 < -{cfg.veto_b})
              {confident}
        ),
        r AS (SELECT *, row_number() OVER (PARTITION BY bucket, role, hero, ctx ORDER BY S DESC) AS rs FROM f)
        SELECT bucket, role, hero, ctx, y, S, a_s, b_s, conf, n, rule FROM r WHERE rs <= {cfg.top_n}
        ORDER BY bucket, role, hero, ctx, rs
    """).fetchall()


def nest_answers(out: dict, group: str, rows: list, names: dict) -> None:
    """Строки из rank_answers → out[bucket][group][role][hero][ctx] = [[Y, S, A%, B%, conf, n, флаг], ...]."""
    for bucket, role, hero, ctx, y, s, a, b, conf, n, rule in rows:
        cell = out.setdefault(bucket, {}).setdefault(group, {}).setdefault(role, {}).setdefault(str(hero), {})
        cell.setdefault(ctx, []).append([names.get(int(y), str(y)), round(s, 3), round(a * 100, 2),
                                         round((b or 0.0) * 100, 2), conf, int(round(n)), rule or EXPERIENCE_FLAG])


# ---------- угрозы (спека 7: threat) ----------
def threat_block(con: duckdb.DuckDBPyConnection, group: str, rank_filter: str, k_b: float, out: dict) -> None:
    """Для героя E: доли ролей по нетворсу, сила в лейте, матчапы героев H против него.

    late: [WR(45+) − WR(<25) в п.п., игр 45+, игр <25] (для турбо — крайние страты турбо).
    vs: {H: [ΔWR(H против E) − WR(H) в п.п., стянутая к нулю с силой k_B, число игр]}.
    """
    g = f"(SELECT * FROM pg WHERE {rank_filter})"
    for bucket, hero, role, share in con.execute(f"""
        WITH rw AS (SELECT bucket, hero, role, sum(wt) AS n FROM {g} GROUP BY bucket, hero, role)
        SELECT bucket, hero, role, n / sum(n) OVER (PARTITION BY bucket, hero) FROM rw ORDER BY 1, 2, 3
    """).fetchall():
        cell = out.setdefault(bucket, {}).setdefault(group, {}).setdefault(str(hero), {})
        cell.setdefault("roles", {})[role] = round(share, 3)
    for bucket, hero, late, n_late, n_early in con.execute(f"""
        SELECT bucket, hero,
               sum(wt * win::INT) FILTER (WHERE stratum = 3) / nullif(sum(wt) FILTER (WHERE stratum = 3), 0)
               - sum(wt * win::INT) FILTER (WHERE stratum = 0) / nullif(sum(wt) FILTER (WHERE stratum = 0), 0),
               sum(wt) FILTER (WHERE stratum = 3), sum(wt) FILTER (WHERE stratum = 0)
        FROM {g} GROUP BY ALL
    """).fetchall():
        if late is not None:
            out[bucket][group][str(hero)]["late"] = [round(late * 100, 2), int(round(n_late)), int(round(n_early))]
    for bucket, enemy, hero, delta, n in con.execute(f"""
        WITH gg AS {g},
        hw AS (SELECT bucket, hero, sum(wt * win::INT) / sum(wt) AS wr FROM gg GROUP BY ALL),
        mu AS (SELECT gg.bucket, gg.hero, e.hero AS enemy, sum(gg.wt * gg.win::INT) / sum(gg.wt) AS wr, sum(gg.wt) AS n
               FROM gg JOIN pg e ON e.match_id = gg.match_id AND e.is_radiant <> gg.is_radiant GROUP BY ALL)
        SELECT mu.bucket, mu.enemy, mu.hero, (mu.wr - hw.wr) * mu.n / (mu.n + {k_b}), mu.n
        FROM mu JOIN hw USING (bucket, hero) ORDER BY 1, 2, 3
    """).fetchall():
        cell = out.setdefault(bucket, {}).setdefault(group, {}).setdefault(str(enemy), {})
        cell.setdefault("vs", {})[str(hero)] = [round(delta * 100, 2), int(round(n))]


# ---------- главное ----------
LEVELS = ("L3", "L2", "L1", "hero")
CLASS_LEVELS = ("L3", "L2", "hero")


def compute(matches_glob: str, ranks_glob: str | None, items: dict, abilities: dict, hero_abilities: dict,
            heroes: dict, cfg: RecsConfig, log=print, where: str = "true", con=None) -> dict:
    roles = {v["name"].removeprefix("npc_dota_hero_"): tuple(v.get("roles") or ()) for v in heroes.values()}
    tagger = MechanicsTagger(load_mechanics_config(), items, abilities, hero_abilities, roles)
    meta = build_item_meta(items, tagger, heroes, cfg)
    con = con or duckdb.connect()
    t0 = time.time()
    prepare(con, matches_glob, ranks_glob, meta, heroes, cfg, where)
    total, rows, ranked, first, last = con.execute("""
        SELECT count(DISTINCT match_id), count(*), count(DISTINCT match_id) FILTER (WHERE rank_group IS NOT NULL),
               min(start_time), max(start_time) FROM pg
    """).fetchone()
    by_bucket = dict(con.execute("SELECT bucket, count(DISTINCT match_id) FROM pg GROUP BY ALL").fetchall())
    patch = con.execute("SELECT mode(patch) FROM pg").fetchone()[0]
    log(f"матчей {total}, строк {rows}, с рангом {ranked} ({ranked / max(total, 1):.0%}), "
        f"подготовка {time.time() - t0:.0f} с")
    groups = {"all": "true", **{g: f"rank_group = '{g}'" for g in cfg.rank_groups}}
    result = {"meta": {"matches": total, "by_bucket": by_bucket, "ranked_share": round(ranked / max(total, 1), 3),
                       "patch": patch, "first_start": first, "last_start": last, "generated_at": int(time.time()),
                       "half_life_days": cfg.half_life_days, "k": {},
                       "hero_class": {str(h): [c, r] for h, c, r in con.execute("SELECT * FROM hero_cls").fetchall()}},
              "item": {}, "item_hero": {}, "hero": {}, "item_class": {}, "hero_class": {}, "threat": {}}
    for group, flt in groups.items():
        for lvl in LEVELS:
            level_metrics(con, lvl, flt, f"met_{lvl}", min_buy_share=cfg.min_buy_share,
                          min_ctx=cfg.conf_mid if lvl == "L1" else 0)
        k = {"A": eb_k(con, "met_L2", "A", cfg.k_a_default), "B": eb_k(con, "met_L2", "B", cfg.k_b_default)}
        result["meta"]["k"][group] = {m: round(v, 1) for m, v in k.items()}
        smooth(con, "met_L2", "s_L2", k, PRIOR_L2, "met_L3")
        smooth(con, "met_L1", "s_L1", k, PRIOR_L1, "s_L2")
        smooth(con, "met_hero", "s_hero", k)
        nest_answers(result["item"], group, rank_answers(con, "s_L2", "L2", cfg, k), meta.names)
        nest_answers(result["item_hero"], group, rank_answers(con, "s_L1", "L1", cfg, k, only_confident=True),
                     meta.names)
        nest_answers(result["hero"], group, rank_answers(con, "s_hero", "hero", cfg, k), meta.names)
        # запасной вариант: класс героя (атрибут × типичная роль)
        for lvl in CLASS_LEVELS:
            level_metrics(con, lvl, flt, f"met_c{lvl}", who="cls", min_buy_share=cfg.min_buy_share)
        smooth(con, "met_cL2", "s_cL2", k, PRIOR_L2, "met_cL3")
        smooth(con, "met_chero", "s_chero", k)
        nest_answers(result["item_class"], group, rank_answers(con, "s_cL2", "L2", cfg, k), meta.names)
        nest_answers(result["hero_class"], group, rank_answers(con, "s_chero", "hero", cfg, k), meta.names)
        threat_block(con, group, flt, k["B"], result["threat"])
        cells = {lvl: con.execute(f"SELECT count(*) FROM met_{lvl}").fetchone()[0] for lvl in LEVELS}
        log(f"[{group}] ячеек: " + ", ".join(f"{lvl} {c}" for lvl, c in cells.items())
            + f"; k_A={k['A']:.0f}, k_B={k['B']:.0f}; {time.time() - t0:.0f} с")
    result["meta"]["seconds"] = round(time.time() - t0)
    return result


def load_cache(cache: Path, name: str):
    return json.loads((cache / f"{name}.json").read_text(encoding="utf-8"))["data"]


def load_refs(cache: Path) -> tuple:
    """Справочники OpenDota из кеша программы: предметы, способности, способности героев, герои."""
    return (load_cache(cache, "items"), load_cache(cache, "abilities"), load_cache(cache, "hero_abilities"),
            load_cache(cache, "heroes"))


def raw_globs(raw: Path) -> tuple[str, str | None]:
    ranks = str(raw / "ranks-*.parquet") if list(raw.glob("ranks-*.parquet")) else None
    return str(raw / "matches-*.parquet"), ranks


def write_recs(result: dict, out: Path) -> None:
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Расчёт советов по сырым матчам")
    parser.add_argument("--raw", default="raw", help="папка с matches-*.parquet и ranks-*.parquet")
    parser.add_argument("--out", default="recs.json.gz")
    parser.add_argument("--cache", default=str(Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "cache"))
    args = parser.parse_args()
    matches, ranks = raw_globs(Path(args.raw))
    result = compute(matches, ranks, *load_refs(Path(args.cache)), load_config())
    write_recs(result, Path(args.out))
    print(f"{args.out}: {Path(args.out).stat().st_size // 1024} КБ, матчей {result['meta']['matches']}, "
          f"{result['meta']['seconds']} с")


if __name__ == "__main__":
    main()
