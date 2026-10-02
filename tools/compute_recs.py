"""Расчёт советов по сырым матчам (docs/RECOMMENDER_SPEC.md, разделы 3–7) на DuckDB.

Вход: сырые матчи matches-*.parquet и ранги ranks-*.parquet (tools/collect_raw.py),
справочники OpenDota (предметы, способности, герои) и правила механик (data/mechanics.json).
Выход: recs.zip — готовые ответы для программы, разложенные по героям (раздел 7, app/threats/recs_file.py).

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

Запуск: python tools/compute_recs.py --raw raw --out recs.zip [--cache %APPDATA%/DotaTimer/cache]
"""
import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.threats.mechanics import MechanicsTagger, load_mechanics_config  # noqa: E402
from app.threats.recs_file import write_recs_zip  # noqa: E402

CONFIG_PATH = ROOT / "data" / "recs_config.json"
EPS = 1e-6  # DECISIONS №15; остаток вычитания дробных счётчиков меньше этого — ноль (BUGLOG №21)
EXPERIENCE_FLAG = "exp"  # пометка «по опыту игроков»: не по правилу механик, а по топу адаптации
# Компактная строка ответа (размер файла, раздел 7): номера вместо названий, округление до 0,1 п.п.
ROW_FORMAT = [  # DECISIONS №16
    "item_id", "S", "A_pp", "B_pp", "conf", "games", "source"]
CONF_CODES = ["low", "mid", "high"]  # conf в строке — номер в этом списке


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
    exclude_tree_of: str  # предметы, собранные из этого (сапоги), — не ответ, кроме keep_from_excluded
    keep_from_excluded: list
    eb_min_games: float  # ячейки с меньшим числом игр в оценку τ² не берём (BUGLOG №21)
    component_final_share: float  # промежуточный предмет — ответ, только если с ним заканчивают игру хотя бы так часто
    duckdb_memory_limit: str  # предел памяти DuckDB; сверх — сброс на диск
    duckdb_threads: int  # потоков DuckDB: меньше потоков — меньше пик памяти
    store_rank_groups: bool  # считать и хранить ранговые группы G1–G3
    min_role_share: float  # роль героя пишется в файл, если его в ней играют хотя бы так часто
    mechanic_first: bool  # сначала ответы по правилу механик, потом «по опыту игроков»; внутри — по S (DECISIONS №18)
    mechanic_first_min_a: float | None  # ответ по механике идёт первым, только если A* > этого (None — всегда)
    broad_rule_share: float  # правило «широкое», если его признак есть у большей доли героев (DECISIONS №19)
    mechanic_first_b_significant: bool  # в первую группу пускать и ответ с значимо положительным B
    adaptation_top: int  # сколько лучших по A пускать в кандидаты без правила механик
    min_buy_share: float  # доля игр H в роли, в которых он покупает Y; реже — Y не кандидат (спека 6.4)
    levels: list  # уровни контекста: L3, L2, L1, hero (решение по L1 — по замеру L1 против L2, 02.10)


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
    components: set = field(default_factory=set)  # id кандидатов, которые входят в рецепт другого предмета
    broad_rules: dict = field(default_factory=dict)  # широкое правило → доля героев с его признаком (DECISIONS №19)


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
    # сапоги (всё, что собирается из Boots of Speed) — не ответ на врага, кроме гривов (решение автора 30.09, DECISIONS №14)
    candidates -= {k for k in candidates if cfg.exclude_tree_of in component_closure(k, items)
                   and k not in cfg.keep_from_excluded}
    # промежуточные: входят в рецепт другого покупаемого предмета; оставит или уберёт расчёт по данным (prepare)
    components = {c for k in buyable for c in component_closure(k, items)} & candidates
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
    # широкие правила: признак врага есть у доли героев больше broad_rule_share (DECISIONS №19)
    share = {r.id: sum(1 for h in heroes.values() if r.enemy in tagger.hero_traits(h["name"])) / max(len(heroes), 1)
             for r in tagger.rules}
    broad = {rule: round(v, 3) for rule, v in share.items() if v > cfg.broad_rule_share}
    return ItemMeta({ids[k] for k in candidates if k in ids}, {ids[k] for k in key_items if k in ids},
                    sorted(expand), mechanic_of, {v: k for k, v in ids.items()}, sorted(answers),
                    {ids[k] for k in components if k in ids}, broad)


# ---------- подготовка игр ----------
def prepare(con: duckdb.DuckDBPyConnection, matches_glob: str, ranks_glob: str | None, meta: ItemMeta,
            heroes: dict, cfg: RecsConfig, where: str = "true", epoch: float | None = None,
            drop_components: bool = True) -> None:
    """Таблица pg: одна строка на игру героя (корзина, ранг-группа, роль, класс, страта, вес, победа).

    where — фильтр по матчам (например, по start_time для проверки на отложенных днях).
    epoch — для дневных сумм (tools/recs_state.py): вес 2^((start_time − epoch)/T½) вместо 0,5^((newest − start_time)/T½).
    Вес по давности — показательный, поэтому одно умножение в конце даёт ровно то же: Σ·2^((epoch − newest)/T½).
    drop_components=False — промежуточные предметы не убирать (решение — по сумме всех дней, recs_state).
    """
    # запуски сбора могут пересекаться: один и тот же матч берём один раз (DECISIONS №4)
    con.execute(f"CREATE OR REPLACE VIEW m AS SELECT * FROM read_parquet('{matches_glob}') WHERE {where} "
                f"QUALIFY row_number() OVER (PARTITION BY match_id, hero_id ORDER BY start_time) = 1")
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
                   -- при равном нетворсе — по номеру героя: иначе роль доставалась случайно и расчёт не повторялся (BUGLOG №40)
                   row_number() OVER (PARTITION BY m.match_id, is_radiant ORDER BY net_worth DESC, hero_id) AS nw_place,
                   max(start_time) OVER () AS newest
            FROM m LEFT JOIN r USING (match_id)
        )
        SELECT match_id, start_time, hero_id AS hero, is_radiant, bucket, patch,
               CASE {groups} ELSE NULL END AS rank_group,
               -- роль по месту по нетворсу, не по тегам Valve (DECISIONS №9)
               CASE WHEN nw_place <= 2 THEN 'core' WHEN nw_place = 3 THEN 'offlane' ELSE 'support' END AS role,
               CASE WHEN bucket = 'normal' THEN
                    CASE WHEN duration < {edges_n[0]}*60 THEN 0 WHEN duration < {edges_n[1]}*60 THEN 1
                         WHEN duration < {edges_n[2]}*60 THEN 2 ELSE 3 END
                    ELSE
                    CASE WHEN duration < {edges_t[0]}*60 THEN 0 WHEN duration < {edges_t[1]}*60 THEN 1
                         WHEN duration < {edges_t[2]}*60 THEN 2 ELSE 3 END END AS stratum,
               {f"power(2.0, (start_time - {epoch}) / {half_life})" if epoch is not None
                 else f"power(0.5, (newest - start_time) / {half_life})"} AS wt,
               (is_radiant = radiant_win) AS win, items
        FROM base
    """)
    static_tables(con, meta, heroes)
    hero_classes(con, "SELECT hero, role, count(*) AS c FROM pg GROUP BY ALL")
    con.execute("CREATE OR REPLACE TABLE pg AS SELECT pg.*, hc.cls FROM pg JOIN hero_cls hc USING (hero)")
    drop_unfinished_components(con, meta, cfg, delete=drop_components)
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


def hero_classes(con: duckdb.DuckDBPyConnection, role_counts_sql: str) -> None:
    """Класс героя: основной атрибут × роль, в которой его чаще всего играют (спека 5). role_counts_sql — (hero, role, c)."""
    con.execute(f"""
        CREATE OR REPLACE TABLE hero_cls AS
        WITH rc AS ({role_counts_sql})
        SELECT rc.hero, coalesce(a.attr, 'all') || '-' || arg_max(rc.role, rc.c * 10 + r.k) AS cls,
               arg_max(rc.role, rc.c * 10 + r.k) AS main_role
        FROM rc LEFT JOIN hero_attr a USING (hero)
        -- при равном числе игр роль выбирается по порядку core > offlane > support (BUGLOG №40)
        JOIN (VALUES ('core', 3), ('offlane', 2), ('support', 1)) r(role, k) ON r.role = rc.role
        GROUP BY rc.hero, a.attr
    """)


def static_tables(con: duckdb.DuckDBPyConnection, meta: ItemMeta, heroes: dict) -> None:
    """Справочные таблицы, не зависящие от матчей: атрибуты героев, дерево предметов, ключевые X, механики, правила."""
    con.execute("CREATE OR REPLACE TABLE hero_attr(hero SMALLINT, attr VARCHAR)")
    con.executemany("INSERT INTO hero_attr VALUES (?, ?)",
                    [(h["id"], h.get("primary_attr") or "all") for h in heroes.values()])
    con.execute("CREATE OR REPLACE TABLE expand(z SMALLINT, y SMALLINT)")
    if meta.expand:
        con.executemany("INSERT INTO expand VALUES (?, ?)", meta.expand)
    con.execute("CREATE OR REPLACE TABLE key_items(x SMALLINT)")
    if meta.key_items:
        con.executemany("INSERT INTO key_items VALUES (?)", [(x,) for x in sorted(meta.key_items)])
    con.execute("CREATE OR REPLACE TABLE x_mech(x SMALLINT, mech VARCHAR)")
    mech_rows = [(x, m) for x, ms in meta.mechanic_of.items() for m in ms]
    if mech_rows:
        con.executemany("INSERT INTO x_mech VALUES (?, ?)", mech_rows)
    con.execute("CREATE OR REPLACE TABLE ans(kind VARCHAR, ctx VARCHAR, y SMALLINT, rule VARCHAR)")
    if meta.answers:
        con.executemany("INSERT INTO ans VALUES (?, ?, ?, ?)", meta.answers)
    con.execute("CREATE OR REPLACE TABLE broad(rule VARCHAR)")
    if meta.broad_rules:
        con.executemany("INSERT INTO broad VALUES (?)", [(r,) for r in sorted(meta.broad_rules)])
    # у одного Y против контекста может быть несколько правил: узкое важнее широкого
    con.execute("""
        CREATE OR REPLACE TABLE ans1 AS
        SELECT kind, ctx, y, arg_min(rule, (rule IN (SELECT rule FROM broad))::INT::VARCHAR || rule) AS rule,  -- узкое, затем по имени (BUGLOG №40)
               bool_and(rule IN (SELECT rule FROM broad)) AS broad
        FROM ans GROUP BY ALL
    """)


def drop_unfinished_components(con: duckdb.DuckDBPyConnection, meta: ItemMeta, cfg: RecsConfig,
                               delete: bool = True) -> None:
    """Промежуточный предмет (Sange, Kaya, Crystalys…) — ответ, только если с ним обычно и заканчивают игру.

    Для каждого Y-компонента: доля игроков, у которых в итоговом инвентаре лежит сам Y, среди всех, у кого есть
    Y или его доделка. Меньше component_final_share — Y обычно доделывают дальше, как ответ он не нужен
    (засчитается его доделка). Решение автора 30.09 (вариант А, DECISIONS №14). Итог — таблица component_share.
    """
    con.execute("CREATE OR REPLACE TABLE comp(y SMALLINT)")
    if meta.components:
        con.executemany("INSERT INTO comp VALUES (?)", [(y,) for y in sorted(meta.components)])
    con.execute("""
        CREATE OR REPLACE TABLE component_share AS
        WITH own AS (
            SELECT p.match_id, p.is_radiant, p.hero, e.y, max((t.z = e.y)::INT) AS itself
            FROM pg p, UNNEST(p.items) AS t(z) JOIN expand e ON e.z = t.z JOIN comp c ON c.y = e.y
            GROUP BY ALL
        )
        SELECT y, avg(itself) AS share, count(*) AS players, sum(itself) AS itself FROM own GROUP BY y
    """)
    if delete:
        con.execute(f"DELETE FROM expand WHERE y IN (SELECT y FROM component_share WHERE share < {cfg.component_final_share})")


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
    count_tables(con, level, rank_filter, "cnt", who)
    metrics_from_counts(con, dst, "cnt", min_buy_share, min_ctx)


def count_tables(con: duckdb.DuckDBPyConnection, level: str, rank_filter: str, prefix: str, who: str = "hero") -> None:
    """Суммы весов игр и побед — {prefix}_tot / _gx / _gy / _gxy (по страте). Это всё, что нужно формулам A и B,
    и суммы складываются: их можно считать по дням и хранить (инкрементальный расчёт, решение автора 02.10)."""
    g = f"(SELECT * FROM pg WHERE {rank_filter})"
    con.execute(f"CREATE OR REPLACE TEMP TABLE {prefix}_ctx AS {CTX_SQL[level].replace('FROM pg p', f'FROM {g} p')}")
    con.execute(f"""CREATE OR REPLACE TABLE {prefix}_tot AS
        SELECT bucket, role, {who} AS hero, stratum, sum(wt) AS n, sum(wt * win::INT) AS w, count(*) AS c
        FROM {g} GROUP BY ALL""")
    con.execute(f"""CREATE OR REPLACE TABLE {prefix}_gx AS
        SELECT g.bucket, g.role, g.{who} AS hero, c.ctx, g.stratum, sum(g.wt) AS n, sum(g.wt * g.win::INT) AS w
        FROM {g} g JOIN {prefix}_ctx c ON c.match_id = g.match_id AND c.is_radiant = g.is_radiant GROUP BY ALL""")
    con.execute(f"""CREATE OR REPLACE TABLE {prefix}_gy AS
        SELECT g.bucket, g.role, g.{who} AS hero, o.y, g.stratum, sum(g.wt) AS n, sum(g.wt * g.win::INT) AS w
        FROM {g} g JOIN owned_y o ON o.match_id = g.match_id AND o.is_radiant = g.is_radiant AND o.hero = g.hero
        GROUP BY ALL""")
    con.execute(f"""CREATE OR REPLACE TABLE {prefix}_gxy AS
        SELECT g.bucket, g.role, g.{who} AS hero, c.ctx, o.y, g.stratum, sum(g.wt) AS n, sum(g.wt * g.win::INT) AS w
        FROM {g} g JOIN {prefix}_ctx c ON c.match_id = g.match_id AND c.is_radiant = g.is_radiant
        JOIN owned_y o ON o.match_id = g.match_id AND o.is_radiant = g.is_radiant AND o.hero = g.hero
        GROUP BY ALL""")
    con.execute(f"DROP TABLE IF EXISTS {prefix}_ctx")


def metrics_from_counts(con: duckdb.DuckDBPyConnection, dst: str, prefix: str, min_buy_share: float = 0.0,
                        min_ctx: float = 0.0) -> None:
    """Формулы A и B (см. level_metrics) по суммам {prefix}_tot / _gx / _gy / _gxy."""
    con.execute(f"""
        CREATE OR REPLACE TABLE {dst} AS
        WITH tot AS (SELECT * FROM {prefix}_tot), gx AS (SELECT * FROM {prefix}_gx),
        gy AS (SELECT * FROM {prefix}_gy), gxy AS (SELECT * FROM {prefix}_gxy),
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
        -- четыре клетки (X есть/нет × Y есть/нет): счётчики — дробные суммы весов по давности, поэтому «ноль» после
        -- вычитания бывает остатком вроде 1e-9; такие остатки обнуляем, доли побед держим в [0, 1] (BUGLOG №21)
        c4 AS (
            SELECT *,
                CASE WHEN NXY > {EPS} THEN NXY ELSE 0 END AS n11,
                CASE WHEN NX - NXY > {EPS} THEN NX - NXY ELSE 0 END AS n10,
                CASE WHEN NY - NXY > {EPS} THEN NY - NXY ELSE 0 END AS n01,
                CASE WHEN N - NX - NY + NXY > {EPS} THEN N - NX - NY + NXY ELSE 0 END AS n00,
                greatest(WXY, 0) AS w11, greatest(WX - WXY, 0) AS w10, greatest(WY - WXY, 0) AS w01,
                greatest(W - WX - WY + WXY, 0) AS w00
            FROM cells
        ),
        pr4 AS (
            SELECT *,
                n11 + n10 AS nx, n01 + n00 AS nn,
                CASE WHEN n11 + n10 > 0 THEN n11 / (n11 + n10) END AS pa,
                CASE WHEN n01 + n00 > 0 THEN n01 / (n01 + n00) END AS pb,
                CASE WHEN n11 > 0 THEN least(1, w11 / n11) END AS r11, CASE WHEN n10 > 0 THEN least(1, w10 / n10) END AS r10,
                CASE WHEN n01 > 0 THEN least(1, w01 / n01) END AS r01, CASE WHEN n00 > 0 THEN least(1, w00 / n00) END AS r00
            FROM c4
        ),
        mb AS (
            SELECT *,
                CASE WHEN nx > 0 AND nn > 0 THEN pa - pb END AS a,
                CASE WHEN nx > 0 AND nn > 0 THEN pa * (1 - pa) / nx + pb * (1 - pb) / nn END AS va,
                CASE WHEN n11 > 0 AND n10 > 0 AND n01 > 0 AND n00 > 0 THEN (r11 - r10) - (r01 - r00) END AS b,
                CASE WHEN n11 > 0 AND n10 > 0 AND n01 > 0 AND n00 > 0 THEN
                    r11 * (1 - r11) / n11 + r10 * (1 - r10) / n10 + r01 * (1 - r01) / n01 + r00 * (1 - r00) / n00
                END AS vb
            FROM pr4
        )
        SELECT bucket, role, hero, ctx, y,
               sum(NX * a) / nullif(sum(CASE WHEN a IS NOT NULL THEN NX END), 0) AS A,
               sum(NX * NX * va) / nullif(power(sum(CASE WHEN a IS NOT NULL THEN NX END), 2), 0) AS vA,
               sum(NX * b) / nullif(sum(CASE WHEN b IS NOT NULL THEN NX END), 0) AS B,
               sum(NX * NX * vb) / nullif(power(sum(CASE WHEN b IS NOT NULL THEN NX END), 2), 0) AS vB,
               sum(NX) AS n
        FROM mb GROUP BY ALL
    """)


def level_metrics_parts(con: duckdb.DuckDBPyConnection, level: str, rank_filter: str, dst: str, **kwargs) -> None:
    """То же, что level_metrics, но по кускам «режим × роль»: метрики и так считаются внутри них, результат тот же,
    а память меньше в ~6 раз (целиком на 378 809 матчах не влезло в 8 ГБ — замер 30.09, DECISIONS №16)."""
    parts = con.execute("SELECT DISTINCT bucket, role FROM pg ORDER BY ALL").fetchall()
    for i, (bucket, role) in enumerate(parts):
        level_metrics(con, level, f"({rank_filter}) AND bucket = '{bucket}' AND role = '{role}'", "met_part", **kwargs)
        con.execute(f"CREATE OR REPLACE TABLE {dst} AS SELECT * FROM met_part" if i == 0
                    else f"INSERT INTO {dst} SELECT * FROM met_part")
    con.execute("DROP TABLE IF EXISTS met_part")


def eb_spread(con: duckdb.DuckDBPyConnection, table: str, metric: str, default_k: float,
               min_games: float = 0) -> dict:
    """Разброс истинных значений τ² между ячейками (эмпирический Байес, раздел 5), методом моментов.

    τ² = общий разброс оценок − средняя дисперсия внутри ячеек, по ячейкам от min_games игр: у меньших
    дисперсия оценивается ненадёжно. Замер 30.09 (378 809 матчей, BUGLOG №21): τ²_A стабилен от 300 игр
    (0,00027–0,00030), τ²_B падает с ростом ячеек и выходит на 0,0011–0,0015 от 1000 игр.
    Возвращает {"tau2", "k"}: k = σ²/τ² — то же в «играх» (σ² — дисперсия на игру), для журнала и для матчапов.
    Если оценка не вышла — τ² из стартового k (k_a_default / k_b_default).
    """
    sigma_all = con.execute(f"SELECT avg(v{metric} * n) FROM {table} WHERE v{metric} > 0 AND n > 0").fetchone()[0]
    cnt, total_var, within, sigma2 = con.execute(f"""
        SELECT count(*), var_samp({metric}), avg(v{metric}), avg(v{metric} * n) FROM {table}
        WHERE {metric} IS NOT NULL AND v{metric} > 0 AND n >= greatest({min_games}, 1e-9)
    """).fetchone()
    tau2 = (total_var - within) if cnt >= 30 and total_var is not None else None
    if not tau2 or tau2 <= 0 or not sigma2:
        sigma2 = sigma_all or 0.25
        return {"tau2": sigma2 / default_k, "k": default_k, "estimated": False}
    return {"tau2": tau2, "k": sigma2 / tau2, "estimated": True}


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


def smooth(con: duckdb.DuckDBPyConnection, src: str, dst: str, eb: dict, prior_sql: str | None = None,
           prior: str | None = None) -> None:
    """Стягивание к более общему уровню по собственной дисперсии ячейки: θ* = (τ²·θ + v·θ_prior) / (τ² + v).

    Вес ячейки τ²/(τ² + v): чем точнее оценка (меньше v), тем меньше она стягивается. Для B это важно: его точность
    задаёт самая маленькая из четырёх клеток, а не число игр (замер 30.09, BUGLOG №21). Без приора — к нулю.
    """
    if prior_sql:
        join = f"LEFT JOIN ({prior_sql.format(src=src, prior=prior)}) p USING (bucket, role, hero, ctx, y)"
    else:
        join = "LEFT JOIN (SELECT NULL AS bucket, NULL AS role, NULL AS hero, NULL AS ctx, NULL AS y, " \
               "NULL::DOUBLE AS pA, NULL::DOUBLE AS pB WHERE false) p USING (bucket, role, hero, ctx, y)"
    ta, tb = eb["A"]["tau2"], eb["B"]["tau2"]
    con.execute(f"""
        CREATE OR REPLACE TABLE {dst} AS
        SELECT s.*,
            CASE WHEN s.A IS NULL THEN coalesce(p.pA, 0) WHEN s.vA IS NULL OR s.vA <= 0 THEN s.A
                 ELSE (s.A * {ta} + coalesce(p.pA, 0) * s.vA) / ({ta} + s.vA) END AS a_s,
            CASE WHEN s.vA > 0 THEN s.vA * {ta} / ({ta} + s.vA) ELSE s.vA END AS va_s,
            CASE WHEN s.B IS NULL THEN coalesce(p.pB, 0) WHEN s.vB IS NULL OR s.vB <= 0 THEN s.B
                 ELSE (s.B * {tb} + coalesce(p.pB, 0) * s.vB) / ({tb} + s.vB) END AS b_s,
            CASE WHEN s.vB > 0 THEN s.vB * {tb} / ({tb} + s.vB) ELSE s.vB END AS vb_s
        FROM {src} s {join}
    """)


# вид контекста → как найти правило механик: (вид в таблице ans, выражение контекста)
RULE_LOOKUP = {
    "L1": [("x", "split_part(t.ctx, ':', 2)"), ("e", "split_part(t.ctx, ':', 1)")],
    "L2": [("x", "t.ctx")],
    "L3": [("m", "t.ctx")],
    "hero": [("e", "t.ctx")],
}


def rank_answers(con: duckdb.DuckDBPyConnection, table: str, level: str, cfg: RecsConfig, eb: dict,
                 only_confident: bool = False) -> list:
    """Топ-N ответов для каждого (bucket, role, hero, ctx): [Y, S, A*, B*, уверенность, n, правило или 'exp'].

    Кандидаты — механически отвечают контексту (правило) или входят в топ по A* («по опыту игроков»).
    """
    sd_a, sd_b = con.execute(f"SELECT stddev_samp(a_s), stddev_samp(b_s) FROM {table}").fetchone()
    sd_a, sd_b = sd_a or 1.0, sd_b or 1.0
    joins, rules, broads = [], [], []
    for i, (kind, expr) in enumerate(RULE_LOOKUP[level]):
        joins.append(f"LEFT JOIN ans1 r{i} ON r{i}.kind = '{kind}' AND r{i}.ctx = {expr} AND r{i}.y = t.y")
        rules.append(f"r{i}.rule")
        broads.append(f"r{i}.broad")
    tau_a = eb["A"]["tau2"]  # уверенность низкая, если вес ячейки в оценке A меньше 50% (v > τ², спека 5)
    # сначала ответы по механике (DECISIONS №18); с mechanic_first_min_a — только если против врага его берут чаще
    first_key = ""
    if cfg.mechanic_first:
        if cfg.mechanic_first_min_a is None:
            first_key = "rule IS NULL, "
        else:
            # берут чаще (A > порога) или помогает (B значимо > 0): иначе настоящий ответ по эффекту
            # (Skadi против Heart в искусственных данных: A −0,5, B +9,8) уходил за популярный, но бесполезный
            works = (f" AND NOT (b_s IS NOT NULL AND b_s - {cfg.z90} * sqrt(greatest(coalesce(vb_s, 0), 0)) > 0)"
                     if cfg.mechanic_first_b_significant else "")
            first_key = f"(rule IS NULL OR (a_s <= {cfg.mechanic_first_min_a}{works})), "
    confident = "AND conf <> 'low'" if only_confident else ""
    return con.execute(f"""
        WITH t0 AS (SELECT t.*, coalesce({", ".join(rules)}) AS rule0, coalesce({", ".join(broads)}) AS broad0
                    FROM {table} t {" ".join(joins)}),
        -- ответ на широкое правило — «по механике», только если A значимо больше нуля (DECISIONS №19)
        t AS (SELECT * EXCLUDE (rule0, broad0),
                     CASE WHEN broad0 AND NOT (a_s - {cfg.z90} * sqrt(greatest(coalesce(va_s, 0), 0)) > 0) THEN NULL
                          ELSE rule0 END AS rule
              FROM t0),
        s AS (
            SELECT *, a_s / {sd_a} + {cfg.lam} * coalesce(b_s, 0) / {sd_b} AS S,
                   CASE WHEN vA IS NULL OR vA > {tau_a} THEN 'low' WHEN n >= {cfg.conf_high} THEN 'high'
                        WHEN n >= {cfg.conf_mid} THEN 'mid' ELSE 'low' END AS conf,
                   row_number() OVER (PARTITION BY bucket, role, hero, ctx ORDER BY round(a_s, 9) DESC, y) AS ra
            FROM t WHERE a_s IS NOT NULL
        ),
        f AS (
            SELECT * FROM s
            WHERE (rule IS NOT NULL OR ra <= {cfg.adaptation_top})
              AND NOT (b_s IS NOT NULL AND vb_s IS NOT NULL AND (b_s + {cfg.z90} * sqrt(greatest(vb_s, 0))) * 100 < -{cfg.veto_b})
              {confident}
        ),
        r AS (SELECT *, row_number() OVER (PARTITION BY bucket, role, hero, ctx
                                           ORDER BY {first_key}round(S, 9) DESC, y) AS rs FROM f)
        SELECT bucket, role, hero, ctx, y, S, a_s, b_s, conf, n, rule FROM r WHERE rs <= {cfg.top_n}
        ORDER BY bucket, role, hero, ctx, rs
    """).fetchall()


def nest_answers(out: dict, group: str, rows: list, flags: list) -> None:
    """Строки из rank_answers → out[bucket][group][role][hero][ctx] = [[id Y, S, A п.п., B п.п., conf, игр, источник]].

    conf — номер в CONF_CODES, источник — номер в meta.flags («exp» или правило механик), формат — meta.row_format.
    """
    index = {f: i for i, f in enumerate(flags)}
    for bucket, role, hero, ctx, y, s, a, b, conf, n, rule in rows:
        cell = out.setdefault(bucket, {}).setdefault(group, {}).setdefault(role, {}).setdefault(str(hero), {})
        cell.setdefault(ctx, []).append([int(y), round(s, 2), round(a * 100, 1), round((b or 0.0) * 100, 1),
                                         CONF_CODES.index(conf), int(round(n)), index[rule or EXPERIENCE_FLAG]])


# ---------- угрозы (спека 7: threat) ----------
def threat_block(con: duckdb.DuckDBPyConnection, group: str, k_b: float, out: dict) -> None:
    """Для героя E: доли ролей по нетворсу, сила в лейте, матчапы героев H против него — по суммам all_tot и all_mu.

    late: [WR(45+) − WR(<25) в п.п., игр 45+, игр <25] (для турбо — крайние страты турбо).
    vs: {H: [ΔWR(H против E) − WR(H) в п.п., стянутая к нулю с силой k_B, число игр]}.
    """
    for bucket, hero, role, share in con.execute("""
        WITH rw AS (SELECT bucket, hero, role, sum(n) AS n FROM all_tot GROUP BY bucket, hero, role)
        SELECT bucket, hero, role, n / sum(n) OVER (PARTITION BY bucket, hero) FROM rw ORDER BY 1, 2, 3
    """).fetchall():
        cell = out.setdefault(bucket, {}).setdefault(group, {}).setdefault(str(hero), {})
        cell.setdefault("roles", {})[role] = round(share, 3)
    for bucket, hero, late, n_late, n_early in con.execute("""
        SELECT bucket, hero,
               sum(w) FILTER (WHERE stratum = 3) / nullif(sum(n) FILTER (WHERE stratum = 3), 0)
               - sum(w) FILTER (WHERE stratum = 0) / nullif(sum(n) FILTER (WHERE stratum = 0), 0),
               sum(n) FILTER (WHERE stratum = 3), sum(n) FILTER (WHERE stratum = 0)
        FROM all_tot GROUP BY ALL
    """).fetchall():
        if late is not None:
            out[bucket][group][str(hero)]["late"] = [round(late * 100, 2), int(round(n_late)), int(round(n_early))]
    for bucket, enemy, hero, delta, n in con.execute(f"""
        WITH hw AS (SELECT bucket, hero, sum(w) / sum(n) AS wr FROM all_tot GROUP BY ALL),
        mu AS (SELECT bucket, hero, enemy, sum(w) / sum(n) AS wr, sum(n) AS n FROM all_mu GROUP BY ALL)
        SELECT mu.bucket, mu.enemy, mu.hero, (mu.wr - hw.wr) * mu.n / (mu.n + {k_b}), mu.n
        FROM mu JOIN hw USING (bucket, hero) ORDER BY 1, 2, 3
    """).fetchall():
        cell = out.setdefault(bucket, {}).setdefault(group, {}).setdefault(str(enemy), {})
        cell.setdefault("vs", {})[str(hero)] = [round(delta * 100, 2), int(round(n))]


# ---------- поставщики сумм: из игр (с нуля) или из хранилища дневных сумм (tools/recs_state.py) ----------
class PgCounts:
    """Суммы прямо из таблицы игр pg (расчёт с нуля); rank_filter — ранговая группа или 'true'."""

    def __init__(self, con: duckdb.DuckDBPyConnection, rank_filter: str = "true"):
        self.con, self.flt = con, rank_filter

    def parts(self) -> list:
        return self.con.execute(f"SELECT DISTINCT bucket, role FROM pg WHERE {self.flt} ORDER BY ALL").fetchall()

    def counts(self, level: str, who: str, bucket: str, role: str, prefix: str) -> None:
        count_tables(self.con, level, f"({self.flt}) AND bucket = '{bucket}' AND role = '{role}'", prefix, who)

    def overall(self) -> None:
        """all_tot(bucket, role, hero, stratum, n, w), all_gy(bucket, role, hero, y, n), all_mu(bucket, hero, enemy, n, w)."""
        g = f"(SELECT * FROM pg WHERE {self.flt})"
        self.con.execute(f"""CREATE OR REPLACE TABLE all_tot AS SELECT bucket, role, hero, stratum, sum(wt) AS n,
                             sum(wt * win::INT) AS w FROM {g} GROUP BY ALL""")
        self.con.execute(f"""CREATE OR REPLACE TABLE all_gy AS SELECT p.bucket, p.role, p.hero, o.y, sum(p.wt) AS n
                             FROM {g} p JOIN owned_y o USING (match_id, is_radiant, hero) GROUP BY ALL""")
        self.con.execute(f"""CREATE OR REPLACE TABLE all_mu AS SELECT g.bucket, g.hero, e.hero AS enemy, sum(g.wt) AS n,
                             sum(g.wt * g.win::INT) AS w FROM {g} g
                             JOIN pg e ON e.match_id = g.match_id AND e.is_radiant <> g.is_radiant GROUP BY ALL""")


def level_from_counts(con: duckdb.DuckDBPyConnection, provider, level: str, dst: str, cfg: RecsConfig,
                      who: str = "hero", min_ctx: float = 0.0) -> None:
    """Метрики уровня по кускам «режим × роль» (память меньше в ~6 раз, DECISIONS №16): суммы от поставщика → формулы.
    Промежуточные предметы, которые обычно доделывают (DECISIONS №14), выпадают из Y здесь (для расчёта с нуля их уже
    нет в дереве предметов, для дневных сумм — решение по сумме всех дней)."""
    for i, (bucket, role) in enumerate(provider.parts()):
        provider.counts(level, who, bucket, role, "cnt")
        for t in ("cnt_gy", "cnt_gxy"):
            con.execute(f"DELETE FROM {t} WHERE y IN (SELECT y FROM component_share "
                        f"WHERE share < {cfg.component_final_share})")
        metrics_from_counts(con, "met_part", "cnt", cfg.min_buy_share, min_ctx)
        con.execute(f"CREATE OR REPLACE TABLE {dst} AS SELECT * FROM met_part" if i == 0
                    else f"INSERT INTO {dst} SELECT * FROM met_part")
    for t in ("met_part", "cnt_tot", "cnt_gx", "cnt_gy", "cnt_gxy"):
        con.execute(f"DROP TABLE IF EXISTS {t}")


# ---------- главное ----------
LEVELS = ("L3", "L2", "L1", "hero")
CLASS_LEVELS = ("L3", "L2", "hero")


def setup_connection(con: duckdb.DuckDBPyConnection | None, cfg: RecsConfig) -> duckdb.DuckDBPyConnection:
    con = con or duckdb.connect()
    # память: на 378 809 матчах без ограничения было 11,4 ГБ (замер 30.09), у раннера GitHub 16 ГБ
    con.execute(f"SET memory_limit = '{cfg.duckdb_memory_limit}'")
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"SET threads = {cfg.duckdb_threads}")  # память растёт с числом потоков (BUGLOG №22)
    con.execute(f"SET temp_directory = '{(Path(tempfile.gettempdir()) / 'dotatimer_duckdb').as_posix()}'")
    return con


def item_meta(items: dict, abilities: dict, hero_abilities: dict, heroes: dict, cfg: RecsConfig) -> tuple:
    roles = {v["name"].removeprefix("npc_dota_hero_"): tuple(v.get("roles") or ()) for v in heroes.values()}
    tagger = MechanicsTagger(load_mechanics_config(), items, abilities, hero_abilities, roles)
    return build_item_meta(items, tagger, heroes, cfg), [EXPERIENCE_FLAG] + [r.id for r in tagger.rules]


def compute(matches_glob: str, ranks_glob: str | None, items: dict, abilities: dict, hero_abilities: dict,
            heroes: dict, cfg: RecsConfig, log=print, where: str = "true", con=None) -> dict:
    """Расчёт с нуля по всем матчам (эталон для инкрементального расчёта tools/recs_state.py)."""
    meta, flags = item_meta(items, abilities, hero_abilities, heroes, cfg)
    con = setup_connection(con, cfg)
    t0 = time.time()
    prepare(con, matches_glob, ranks_glob, meta, heroes, cfg, where)
    total, rows, ranked, first, last = con.execute("""
        SELECT count(DISTINCT match_id), count(*), count(DISTINCT match_id) FILTER (WHERE rank_group IS NOT NULL),
               min(start_time), max(start_time) FROM pg
    """).fetchone()
    info = {"matches": total, "ranked": ranked, "first_start": first, "last_start": last,
            "by_bucket": dict(con.execute("SELECT bucket, count(DISTINCT match_id) FROM pg GROUP BY ALL").fetchall()),
            "patch": con.execute("SELECT mode(patch) FROM pg").fetchone()[0]}
    log(f"матчей {total}, строк {rows}, с рангом {ranked} ({ranked / max(total, 1):.0%}), "
        f"подготовка {time.time() - t0:.0f} с")
    # ранговые группы — только если включены (DECISIONS №17)
    groups = {"all": "true", **({g: f"rank_group = '{g}'" for g in cfg.rank_groups} if cfg.store_rank_groups else {})}
    return finish(con, {g: PgCounts(con, flt) for g, flt in groups.items()}, meta, flags, cfg, info, log, t0)


def finish(con: duckdb.DuckDBPyConnection, providers: dict, meta: ItemMeta, flags: list, cfg: RecsConfig, info: dict,
           log=print, t0: float | None = None) -> dict:
    """Общая часть расчёта по суммам: метрики уровней, стягивание, ответы, угрозы, покупки. Таблицы hero_cls,
    component_share и справочные (static_tables) уже есть; providers — {ранговая группа: поставщик сумм}."""
    t0 = t0 or time.time()
    total = info["matches"]
    result = {"meta": {"matches": total, "by_bucket": info["by_bucket"],
                       "ranked_share": round(info["ranked"] / max(total, 1), 3),
                       "patch": info["patch"], "first_start": info["first_start"], "last_start": info["last_start"],
                       "generated_at": int(time.time()), "half_life_days": cfg.half_life_days, "k": {},
                       "row_format": ROW_FORMAT, "conf_codes": CONF_CODES, "flags": flags,
                       "broad_rules": meta.broad_rules,
                       "hero_class": {str(h): [c, r] for h, c, r in con.execute("SELECT * FROM hero_cls").fetchall()}},
              "item": {}, "item_hero": {}, "hero": {}, "item_class": {}, "hero_class": {}, "threat": {}, "buys": {}}
    for group, provider in providers.items():
        for lvl in cfg_levels(cfg):
            level_from_counts(con, provider, lvl, f"met_{lvl}", cfg, min_ctx=cfg.conf_mid if lvl == "L1" else 0)
        eb = {"A": eb_spread(con, "met_L2", "A", cfg.k_a_default, cfg.eb_min_games),
              "B": eb_spread(con, "met_L2", "B", cfg.k_b_default, cfg.eb_min_games)}
        k = {m: eb[m]["k"] for m in eb}
        result["meta"]["k"][group] = {m: {"tau2": round(e["tau2"], 7), "k": round(e["k"], 1),
                                          "estimated": e["estimated"]} for m, e in eb.items()}
        smooth(con, "met_L2", "s_L2", eb, PRIOR_L2, "met_L3")
        nest_answers(result["item"], group, rank_answers(con, "s_L2", "L2", cfg, eb), flags)
        if "L1" in cfg_levels(cfg):
            smooth(con, "met_L1", "s_L1", eb, PRIOR_L1, "s_L2")
            nest_answers(result["item_hero"], group, rank_answers(con, "s_L1", "L1", cfg, eb, only_confident=True), flags)
        smooth(con, "met_hero", "s_hero", eb)
        nest_answers(result["hero"], group, rank_answers(con, "s_hero", "hero", cfg, eb), flags)
        # запасной вариант: класс героя (атрибут × типичная роль)
        for lvl in CLASS_LEVELS:
            level_from_counts(con, provider, lvl, f"met_c{lvl}", cfg, who="cls")
        smooth(con, "met_cL2", "s_cL2", eb, PRIOR_L2, "met_cL3")
        smooth(con, "met_chero", "s_chero", eb)
        nest_answers(result["item_class"], group, rank_answers(con, "s_cL2", "L2", cfg, eb), flags)
        nest_answers(result["hero_class"], group, rank_answers(con, "s_chero", "hero", cfg, eb), flags)
        provider.overall()
        threat_block(con, group, k["B"], result["threat"])
        cells = {lvl: con.execute(f"SELECT count(*) FROM met_{lvl}").fetchone()[0] for lvl in cfg_levels(cfg)}
        log(f"[{group}] ячеек: " + ", ".join(f"{lvl} {c}" for lvl, c in cells.items())
            + f"; k_A={k['A']:.0f}, k_B={k['B']:.0f}; {time.time() - t0:.0f} с")
    providers["all"].overall()
    # что герой H вообще покупает в роли (≥ min_buy_share игр): отсев в программе (спека 6.4) и сверка 8.1
    for bucket, role, hero, ys in con.execute(f"""
        WITH t AS (SELECT bucket, role, hero, sum(n) AS n FROM all_tot GROUP BY ALL),
        o AS (SELECT bucket, role, hero, y, sum(n) AS k FROM all_gy
              WHERE y NOT IN (SELECT y FROM component_share WHERE share < {cfg.component_final_share}) GROUP BY ALL)
        SELECT o.bucket, o.role, o.hero, list(o.y ORDER BY o.y) FROM o JOIN t USING (bucket, role, hero)
        WHERE o.k >= {cfg.min_buy_share} * t.n GROUP BY ALL
    """).fetchall():
        result["buys"].setdefault(bucket, {}).setdefault(role, {})[str(hero)] = [int(y) for y in ys]
    result["meta"]["components"] = {  # доля «заканчивают с ним самим»; ниже порога — не ответ (DECISIONS №14)
        meta.names.get(int(y), str(y)): [round(share, 3), int(players), share >= cfg.component_final_share]
        for y, share, players in con.execute("SELECT y, share, players FROM component_share ORDER BY share, y").fetchall()}
    result["meta"]["seconds"] = round(time.time() - t0)
    return result


def cfg_levels(cfg: RecsConfig) -> tuple:
    """Уровни контекста, которые считаем (L1 — по решению о нём, recs_config: levels)."""
    return tuple(lvl for lvl in LEVELS if lvl in cfg.levels)


def load_cache(cache: Path, name: str):
    return json.loads((cache / f"{name}.json").read_text(encoding="utf-8"))["data"]


def load_refs(cache: Path) -> tuple:
    """Справочники OpenDota из кеша программы: предметы, способности, способности героев, герои."""
    return (load_cache(cache, "items"), load_cache(cache, "abilities"), load_cache(cache, "hero_abilities"),
            load_cache(cache, "heroes"))


def raw_globs(raw: Path) -> tuple[str, str | None]:
    ranks = str(raw / "ranks-*.parquet") if list(raw.glob("ranks-*.parquet")) else None
    return str(raw / "matches-*.parquet"), ranks


def write_recs(result: dict, out: Path, cfg: RecsConfig) -> None:
    """recs.zip по героям; роли, в которых героя играют реже min_role_share, не пишутся (DECISIONS №17)."""
    write_recs_zip(result, out, cfg.min_role_share)


def main() -> None:
    parser = argparse.ArgumentParser(description="Расчёт советов по сырым матчам")
    parser.add_argument("--raw", default="raw", help="папка с matches-*.parquet и ranks-*.parquet")
    parser.add_argument("--out", default="recs.zip")
    parser.add_argument("--cache", default=str(Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "cache"))
    args = parser.parse_args()
    matches, ranks = raw_globs(Path(args.raw))
    cfg = load_config()
    result = compute(matches, ranks, *load_refs(Path(args.cache)), cfg)
    write_recs(result, Path(args.out), cfg)
    print(f"{args.out}: {Path(args.out).stat().st_size // 1024} КБ, матчей {result['meta']['matches']}, "
          f"{result['meta']['seconds']} с")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # вывод в файл на Windows иначе в cp1251 (BUGLOG №39)
    main()
