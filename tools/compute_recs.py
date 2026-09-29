"""Расчёт советов по сырым матчам (docs/RECOMMENDER_SPEC.md, разделы 3–7) на DuckDB.

Вход: сырые матчи matches-*.parquet и ранги ranks-*.parquet (tools/collect_raw.py),
справочники OpenDota (предметы, способности, герои) и правила механик (data/mechanics.json).
Выход: recs.json.gz — готовые ответы для программы (раздел 7).

Коротко:
- «у H есть Y», если в итоговом инвентаре есть Y или предмет, в который Y собирается (дерево из dotaconstants);
- страты: длительность (4 корзины) × роль H в этой игре по месту в нетворсе (кор 1–2, тройка 3, саппорт 4–5);
- A — адаптация: P(Y | X есть) − P(Y | X нет) внутри страты, взвешенно по числу игр с X;
- B — эффект, разность разностей: [WR(Y,X) − WR(¬Y,X)] − [WR(Y,¬X) − WR(¬Y,¬X)] внутри страты;
- уровни контекста: L1 — X у героя E, L2 — X у любого врага, L3 — у врага предмет с той же механикой;
  оценки стягиваются L1 → L2 → L3 с силой k, подобранной эмпирическим Байесом (раздел 5);
- S = A*/σ_A + λ·B*/σ_B, вето по верхней границе B*, уверенность по числу игр (раздел 5–6).

Запуск: python tools/compute_recs.py --raw raw --out recs.json.gz [--cache %APPDATA%/DotaTimer/cache]
"""
import argparse
import gzip
import json
import math
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


@dataclass(frozen=True)
class RecsConfig:
    duration_edges: dict  # корзина → границы страт по длительности, минуты
    candidate_min_cost: int
    key_item_min_cost: int
    extra_candidates: list
    rank_groups: dict  # имя группы → [min_tier, max_tier]
    k_a_default: float
    k_b_default: float
    lam: float
    veto_b: float  # п.п.: если верхняя 90%-граница B* ниже −veto_b — предмет вредит
    z90: float
    conf_high: int
    conf_mid: int
    top_n: int
    min_context_games: int  # меньше игр в контексте — ячейку не считаем вовсе


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


def component_closure(key: str, items: dict, seen=None) -> set:
    """Все компоненты предмета рекурсивно (для дерева «Y засчитывается, если собран во что-то»)."""
    seen = set() if seen is None else seen
    for comp in items.get(key, {}).get("components") or []:
        if comp not in seen:
            seen.add(comp)
            component_closure(comp, items, seen)
    return seen


def build_item_meta(items: dict, tagger: MechanicsTagger, cfg: RecsConfig) -> ItemMeta:
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
    return ItemMeta({ids[k] for k in candidates if k in ids}, {ids[k] for k in key_items if k in ids},
                    sorted(expand), mechanic_of, {v: k for k, v in ids.items()})


# ---------- подготовка игр ----------
def prepare(con: duckdb.DuckDBPyConnection, matches_glob: str, ranks_glob: str | None, meta: ItemMeta,
            cfg: RecsConfig) -> None:
    """Таблица pg: одна строка на игру героя (корзина, ранг-группа, роль, страта, победа) и таблица предметов."""
    con.execute(f"CREATE OR REPLACE VIEW m AS SELECT * FROM read_parquet('{matches_glob}')")
    if ranks_glob:
        con.execute(f"CREATE OR REPLACE VIEW r AS SELECT match_id, max(avg_rank_tier) AS rank_tier "
                    f"FROM read_parquet('{ranks_glob}') GROUP BY match_id")
    else:
        con.execute("CREATE OR REPLACE VIEW r AS SELECT NULL::BIGINT AS match_id, NULL::TINYINT AS rank_tier WHERE false")
    edges_n = cfg.duration_edges["normal"]
    edges_t = cfg.duration_edges["turbo"]
    groups = " ".join(f"WHEN rank_tier BETWEEN {lo} AND {hi} THEN '{g}'" for g, (lo, hi) in cfg.rank_groups.items())
    con.execute(f"""
        CREATE OR REPLACE TABLE pg AS
        WITH base AS (
            SELECT m.*, r.rank_tier,
                   CASE WHEN game_mode = 23 THEN 'turbo' ELSE 'normal' END AS bucket,
                   row_number() OVER (PARTITION BY m.match_id, is_radiant ORDER BY net_worth DESC) AS nw_place
            FROM m LEFT JOIN r USING (match_id)
        )
        SELECT match_id, hero_id AS hero, is_radiant, bucket,
               CASE {groups} ELSE NULL END AS rank_group,
               CASE WHEN nw_place <= 2 THEN 'core' WHEN nw_place = 3 THEN 'offlane' ELSE 'support' END AS role,
               CASE WHEN bucket = 'normal' THEN
                    CASE WHEN duration < {edges_n[0]}*60 THEN 0 WHEN duration < {edges_n[1]}*60 THEN 1
                         WHEN duration < {edges_n[2]}*60 THEN 2 ELSE 3 END
                    ELSE
                    CASE WHEN duration < {edges_t[0]}*60 THEN 0 WHEN duration < {edges_t[1]}*60 THEN 1
                         WHEN duration < {edges_t[2]}*60 THEN 2 ELSE 3 END END AS stratum,
               (is_radiant = radiant_win) AS win, items
        FROM base
    """)
    con.execute("CREATE OR REPLACE TABLE expand(z SMALLINT, y SMALLINT)")
    con.executemany("INSERT INTO expand VALUES (?, ?)", meta.expand)
    con.execute("CREATE OR REPLACE TABLE key_items(x SMALLINT)")
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
    mech_rows = [(x, m) for x, ms in meta.mechanic_of.items() for m in ms]
    con.execute("CREATE OR REPLACE TABLE x_mech(x SMALLINT, mech VARCHAR)")
    if mech_rows:
        con.executemany("INSERT INTO x_mech VALUES (?, ?)", mech_rows)


# ---------- счётчики и метрики (в DuckDB) ----------
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


def level_metrics(con: duckdb.DuckDBPyConnection, level: str, rank_filter: str) -> dict:
    """A и B по стратам и их взвешенное среднее — прямо в DuckDB.

    Уровни контекста: hero (герой E среди врагов), L1 (X у героя E), L2 (X у любого врага),
    L3 (у любого врага предмет с механикой). Берутся только пары (контекст, Y), которые хоть раз
    встретились вместе; для них учитываются все страты (где пары нет — счётчик 0).
    Возвращает {(bucket, role, hero, ctx, y): {"A", "vA", "B", "vB", "n"}} (доли, не проценты).
    """
    rows = con.execute(f"""
        WITH g AS (SELECT * FROM pg WHERE {rank_filter}),
        ctx AS ({CTX_SQL[level]}),
        tot AS (SELECT bucket, role, hero, stratum, count(*) AS n, sum(win::INT) AS w FROM g GROUP BY ALL),
        gx AS (SELECT g.bucket, g.role, g.hero, c.ctx, g.stratum, count(*) AS n, sum(g.win::INT) AS w
               FROM g JOIN ctx c ON c.match_id = g.match_id AND c.is_radiant = g.is_radiant GROUP BY ALL),
        gy AS (SELECT g.bucket, g.role, g.hero, o.y, g.stratum, count(*) AS n, sum(g.win::INT) AS w
               FROM g JOIN owned_y o ON o.match_id = g.match_id AND o.is_radiant = g.is_radiant AND o.hero = g.hero
               GROUP BY ALL),
        gxy AS (SELECT g.bucket, g.role, g.hero, c.ctx, o.y, g.stratum, count(*) AS n, sum(g.win::INT) AS w
                FROM g JOIN ctx c ON c.match_id = g.match_id AND c.is_radiant = g.is_radiant
                JOIN owned_y o ON o.match_id = g.match_id AND o.is_radiant = g.is_radiant AND o.hero = g.hero
                GROUP BY ALL),
        pairs AS (SELECT DISTINCT bucket, role, hero, ctx, y FROM gxy),
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
    """).fetchall()
    return {(r[0], r[1], r[2], r[3], r[4]): {"A": r[5], "vA": r[6], "B": r[7], "vB": r[8], "n": int(r[9])}
            for r in rows}


def eb_k(cells: dict, metric: str, default: float) -> float:
    """Сила стягивания k = σ²/τ² методом моментов (эмпирический Байес, раздел 5)."""
    values = [(c[metric], c["v" + metric], c["n"]) for c in cells.values()
              if c.get(metric) is not None and c.get("v" + metric) and c["n"] > 0]
    if len(values) < 30:
        return default
    mean = sum(v for v, _, _ in values) / len(values)
    total_var = sum((v - mean) ** 2 for v, _, _ in values) / (len(values) - 1)
    within = sum(var for _, var, _ in values) / len(values)
    tau2 = total_var - within
    sigma2 = sum(var * n for _, var, n in values) / len(values)
    if tau2 <= 0 or sigma2 <= 0:
        return default
    return max(1.0, sigma2 / tau2)


def shrink(value, n, prior, k):
    if value is None:
        return prior
    if prior is None:
        return value
    return (n * value + k * prior) / (n + k)


def confidence(n: int, k: float, cfg: RecsConfig) -> str:
    if n < k:
        return "low"
    return "high" if n >= cfg.conf_high else ("mid" if n >= cfg.conf_mid else "low")


# ---------- главное ----------
def compute(matches_glob: str, ranks_glob: str | None, items: dict, abilities: dict, hero_abilities: dict,
            heroes: dict, cfg: RecsConfig, log=print) -> dict:
    roles = {v["name"].removeprefix("npc_dota_hero_"): tuple(v.get("roles") or ()) for v in heroes.values()}
    tagger = MechanicsTagger(load_mechanics_config(), items, abilities, hero_abilities, roles)
    meta = build_item_meta(items, tagger, cfg)
    con = duckdb.connect()
    t0 = time.time()
    prepare(con, matches_glob, ranks_glob, meta, cfg)
    total = con.execute("SELECT count(DISTINCT match_id), count(*) FROM pg").fetchone()
    ranked = con.execute("SELECT count(DISTINCT match_id) FROM pg WHERE rank_group IS NOT NULL").fetchone()[0]
    log(f"матчей {total[0]}, строк {total[1]}, с рангом {ranked} ({ranked / max(total[0], 1):.0%}), "
        f"подготовка {time.time() - t0:.0f} с")
    groups = {"all": "true", **{g: f"rank_group = '{g}'" for g in cfg.rank_groups}}
    result = {"meta": {"matches": total[0], "ranked_share": round(ranked / max(total[0], 1), 3),
                       "generated_at": int(time.time())},
              "item": {}, "item_hero": {}, "hero": {}}
    for group, flt in groups.items():
        levels = {lvl: level_metrics(con, lvl, flt) for lvl in ("L3", "L2", "L1", "hero")}
        k = {m: eb_k(levels["L2"], m, cfg.k_a_default if m == "A" else cfg.k_b_default) for m in ("A", "B")}
        log(f"[{group}] ячеек: " + ", ".join(f"{lvl} {len(c)}" for lvl, c in levels.items())
            + f"; k_A={k['A']:.0f}, k_B={k['B']:.0f}")
        smoothed = smooth_levels(levels, meta, k)
        result["item"][group] = rank_answers(smoothed["L2"], meta, cfg, k)
        result["item_hero"][group] = rank_answers(smoothed["L1"], meta, cfg, k, only_confident=True)
        result["hero"][group] = rank_answers(smoothed["hero"], meta, cfg, k)
    result["meta"]["seconds"] = round(time.time() - t0)
    return result


def smooth_levels(levels: dict, meta: ItemMeta, k: dict) -> dict:
    """L3 → L2 → L1: каждая оценка стягивается к более общей; героя E — к L3 его механик нет, к нулю."""
    out = {"L3": levels["L3"], "L2": {}, "L1": {}, "hero": {}}
    for key, c in levels["L2"].items():
        bucket, role, hero, x, y = key
        priors = [levels["L3"].get((bucket, role, hero, m, y)) for m in meta.mechanic_of.get(int(x), [])]
        priors = [p for p in priors if p]
        out["L2"][key] = _shrunk(c, priors, k)
    for key, c in levels["L1"].items():
        bucket, role, hero, ctx, y = key
        _e, x = ctx.split(":")
        prior = out["L2"].get((bucket, role, hero, x, y))
        out["L1"][key] = _shrunk(c, [prior] if prior else [], k)
    for key, c in levels["hero"].items():
        out["hero"][key] = _shrunk(c, [], k)
    return out


def _shrunk(cell: dict, priors: list, k: dict) -> dict:
    res = dict(cell)
    for m in ("A", "B"):
        prior_values = [p[m + "*"] if m + "*" in p else p.get(m) for p in priors if p.get(m) is not None]
        prior = sum(prior_values) / len(prior_values) if prior_values else 0.0
        res[m + "*"] = shrink(cell.get(m), cell["n"], prior, k[m])
        # дисперсия после стягивания уменьшается в n/(n+k) раз (приближённо)
        v = cell.get("v" + m)
        res["v" + m + "*"] = v * (cell["n"] / (cell["n"] + k[m])) ** 2 if v is not None else None
    return res


def rank_answers(cells: dict, meta: ItemMeta, cfg: RecsConfig, k: dict, only_confident: bool = False) -> dict:
    """Топ-N ответов для каждого (bucket, role, hero, ctx): [Y, S, A*, B*, уверенность, n, по механике?]."""
    by_query: dict = {}
    for (bucket, role, hero, ctx, y), c in cells.items():
        by_query.setdefault((bucket, role, hero, ctx), []).append((y, c))
    a_all = [c["A*"] for q in by_query.values() for _, c in q if c.get("A*") is not None]
    b_all = [c["B*"] for q in by_query.values() for _, c in q if c.get("B*") is not None]
    sd_a = _sd(a_all) or 1.0
    sd_b = _sd(b_all) or 1.0
    out: dict = {}
    for (bucket, role, hero, ctx), answers in by_query.items():
        rows = []
        for y, c in answers:
            a, b = c.get("A*"), c.get("B*")
            if a is None:
                continue
            vb = c.get("vB*")
            if b is not None and vb is not None and (b + cfg.z90 * math.sqrt(vb)) * 100 < -cfg.veto_b:
                continue  # вредит против этого контекста
            conf = confidence(c["n"], min(k["A"], k["B"]), cfg)
            if only_confident and conf == "low":
                continue
            s = a / sd_a + cfg.lam * (b or 0.0) / sd_b
            rows.append([meta.names.get(int(y), str(y)), round(s, 3), round(a * 100, 2),
                         round((b or 0.0) * 100, 2), conf, c["n"]])
        rows.sort(key=lambda r: -r[1])
        if rows:
            out.setdefault(bucket, {}).setdefault(role, {}).setdefault(str(hero), {})[ctx] = rows[:cfg.top_n]
    return out


def _sd(values):
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def load_cache(cache: Path, name: str):
    return json.loads((cache / f"{name}.json").read_text(encoding="utf-8"))["data"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Расчёт советов по сырым матчам")
    parser.add_argument("--raw", default="raw", help="папка с matches-*.parquet и ranks-*.parquet")
    parser.add_argument("--out", default="recs.json.gz")
    parser.add_argument("--cache", default=str(Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "cache"))
    args = parser.parse_args()
    cache = Path(args.cache)
    raw = Path(args.raw)
    ranks = str(raw / "ranks-*.parquet") if list(raw.glob("ranks-*.parquet")) else None
    result = compute(str(raw / "matches-*.parquet"), ranks, load_cache(cache, "items"), load_cache(cache, "abilities"),
                     load_cache(cache, "hero_abilities"), load_cache(cache, "heroes"), load_config())
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{args.out}: {Path(args.out).stat().st_size // 1024} КБ, {result['meta']}")


if __name__ == "__main__":
    main()
