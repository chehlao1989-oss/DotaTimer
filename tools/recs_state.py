"""Инкрементальный расчёт советов (решение автора 02.10): суммы по дням хранятся, после сбора пересчитываются
только дни, в которые пришли новые матчи; общий итог = старый итог − старые суммы дня + новые суммы дня.

Почему это даёт ровно то же, что расчёт с нуля (tools/compute_recs.py: compute): формулы A и B зависят только от
сумм весов игр и побед по стратам (count_tables), а вес по давности показательный — в днях хранится вес
2^((start_time − epoch)/T½), и одно умножение на 2^((epoch − newest)/T½) в конце даёт вес 0,5^((newest − start_time)/T½).
Сырые матчи остаются (DECISIONS №1): хранилище можно пересобрать с нуля в любой момент.

Хранилище (папка или релиз «recs-state»):
  manifest.json            — эпоха, отпечаток справочников и настроек, дни (матчей, ранги, патчи, время), файлы сырья;
  total/<таблица>.parquet  — суммы по всем дням: tot, gy, comp, gx_<уровень>, gxy_<уровень>;
  days/<день>/<таблица>.parquet — суммы последних KEEP_DAYS дней (их ещё может дополнить следующий сбор).
Если меняются справочники или настройки расчёта, или новые матчи пришли в «замороженный» день, — пересборка с нуля.

Запуск: python tools/recs_state.py --raw raw --state state --out recs.zip [--cache …]
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compute_recs import (RecsConfig, cfg_levels, count_tables, finish, hero_classes, item_meta,  # noqa: E402
                                load_config, load_refs, prepare, setup_connection, static_tables, write_recs)

DAY = 86400
# Дни, которые ещё может дополнить следующий сбор: окна сбора — последние сутки (DECISIONS №6), запуск идёт до 5 ч,
# поэтому матчи дня D приходят до D+2 (оценка). Старше — «заморожены»: их суммы уже в итоге, отдельно не храним.
KEEP_DAYS = 3
VERSION = 1
KEYS = {"tot": ["bucket", "role", "hero", "stratum"], "gy": ["bucket", "role", "hero", "y", "stratum"],
        "gx": ["bucket", "role", "hero", "ctx", "stratum"], "gxy": ["bucket", "role", "hero", "ctx", "y", "stratum"],
        "comp": ["y"]}
VALUES = {"tot": ["n", "w", "c"], "gy": ["n", "w"], "gx": ["n", "w"], "gxy": ["n", "w"], "comp": ["itself", "players"]}


def table_names(levels: tuple) -> list:
    return ["tot", "gy", "comp"] + [f"{kind}_{lvl}" for lvl in levels for kind in ("gx", "gxy")]


def kind_of(table: str) -> str:
    return table.split("_")[0]


def fingerprint(meta, cfg: RecsConfig, levels: tuple) -> str:
    """Отпечаток всего, от чего зависят дневные суммы: изменился — пересборка с нуля."""
    data = {"expand": meta.expand, "key_items": sorted(meta.key_items), "mechanic_of": meta.mechanic_of,
            "components": sorted(meta.components), "edges": cfg.duration_edges, "half_life": cfg.half_life_days,
            "levels": list(levels), "version": VERSION}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def day_start(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def raw_files(raw: Path) -> dict:
    return {p.name: p.stat().st_size for p in sorted(raw.glob("matches-*.parquet"))}


def file_days(path: Path) -> list:
    lo, hi = duckdb.connect().execute(f"SELECT min(start_time), max(start_time) FROM read_parquet('{path.as_posix()}')"
                                      ).fetchone()
    if lo is None:
        return []
    return [day_of(t) for t in range(day_start(day_of(lo)), int(hi) + 1, DAY)]


def build_day(con: duckdb.DuckDBPyConnection, raw: Path, ranks_glob: str | None, day: str, out: Path, meta, heroes,
              cfg: RecsConfig, epoch: float, levels: tuple) -> dict:
    """Суммы одного дня игр → out/<таблица>.parquet. Возвращает сведения о дне (матчей, ранги, патчи, время)."""
    lo = day_start(day)
    prepare(con, (raw / "matches-*.parquet").as_posix(), ranks_glob, meta, heroes, cfg,
            where=f"start_time >= {lo} AND start_time < {lo + DAY}", epoch=epoch, drop_components=False)
    out.mkdir(parents=True, exist_ok=True)
    for i, lvl in enumerate(levels):
        count_tables(con, lvl, "true", "d", "hero")
        if i == 0:
            for kind in ("tot", "gy"):
                con.execute(f"COPY (SELECT * FROM d_{kind} ORDER BY bucket, role) TO '{(out / f'{kind}.parquet').as_posix()}'")
        for kind in ("gx", "gxy"):
            con.execute(f"COPY (SELECT * FROM d_{kind} ORDER BY bucket, role) TO '{(out / f'{kind}_{lvl}.parquet').as_posix()}'")
    con.execute(f"COPY (SELECT y, itself, players FROM component_share) TO '{(out / 'comp.parquet').as_posix()}'")
    matches, ranked, first, last = con.execute(
        "SELECT count(DISTINCT match_id), count(DISTINCT match_id) FILTER (WHERE rank_group IS NOT NULL), "
        "min(start_time), max(start_time) FROM pg").fetchone()
    return {"matches": matches, "ranked": ranked, "first": first, "last": last,
            "by_bucket": dict(con.execute("SELECT bucket, count(DISTINCT match_id) FROM pg GROUP BY ALL").fetchall()),
            "patches": {str(k): v for k, v in con.execute("SELECT patch, count(*) FROM pg GROUP BY ALL").fetchall()}}


def merge(con: duckdb.DuckDBPyConnection, state: Path, table: str, old: Path | None, new: Path | None) -> None:
    """total = total − старые суммы дня + новые. Строки, где суммы обнулились, убираются (вес любой игры ≥ 1:
    эпоха не позже самого раннего дня)."""
    kind = kind_of(table)
    keys, vals = ", ".join(KEYS[kind]), VALUES[kind]
    total = state / "total" / f"{table}.parquet"
    parts = []
    if total.exists():
        parts.append(f"SELECT {keys}, {', '.join(vals)} FROM read_parquet('{total.as_posix()}')")
    if old is not None and (old / f"{table}.parquet").exists():
        parts.append(f"SELECT {keys}, {', '.join(f'-{v}' for v in vals)} FROM read_parquet('{(old / f'{table}.parquet').as_posix()}')")
    if new is not None and (new / f"{table}.parquet").exists():
        parts.append(f"SELECT {keys}, {', '.join(vals)} FROM read_parquet('{(new / f'{table}.parquet').as_posix()}')")
    if not parts:
        return
    alive = "sum(players) <> 0" if kind == "comp" else "abs(sum(n)) > 0.5"
    order = "y" if kind == "comp" else "bucket, role"
    tmp = total.with_suffix(".tmp.parquet")
    total.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"""COPY (SELECT {keys}, {', '.join(f'sum({v}) AS {v}' for v in vals)}
                          FROM ({' UNION ALL '.join(parts)}) GROUP BY ALL HAVING {alive} ORDER BY {order})
                    TO '{tmp.as_posix()}'""")
    tmp.replace(total)


def update(raw: Path, state: Path, refs: tuple, cfg: RecsConfig, log=print) -> dict:
    """Довести хранилище до текущего сырья: пересчитать дни с новыми файлами, обновить итог. Возвращает manifest."""
    items, abilities, hero_abilities, heroes = refs
    meta, _flags = item_meta(items, abilities, hero_abilities, heroes, cfg)
    levels = cfg_levels(cfg)
    stamp = fingerprint(meta, cfg, levels)
    path = state / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    files = raw_files(raw)
    days_of = {f: manifest.get("files", {}).get(f, {}).get("days") for f in files}
    changed = [f for f in files if manifest.get("files", {}).get(f, {}).get("size") != files[f]]
    for f in changed:
        days_of[f] = file_days(raw / f)
    all_days = sorted({d for ds in days_of.values() for d in ds or []})
    touched = sorted({d for f in changed for d in days_of[f]})
    rebuild = (manifest.get("version") != VERSION or manifest.get("fingerprint") != stamp or not all_days
               or set(manifest.get("files", {})) - set(files)  # пропал файл сырья
               or any(d in manifest.get("days", {}) and not (state / "days" / d).exists() for d in touched)
               or min(all_days) < manifest.get("epoch_day", "9999"))
    if rebuild:
        log(f"пересборка хранилища с нуля: дней {len(all_days)}")
        shutil.rmtree(state, ignore_errors=True)
        manifest = {"version": VERSION, "fingerprint": stamp, "epoch_day": all_days[0] if all_days else None, "days": {}}
        touched = all_days
    epoch = day_start(manifest["epoch_day"])
    ranks = list(raw.glob("ranks-*.parquet"))
    ranks_glob = (raw / "ranks-*.parquet").as_posix() if ranks else None
    con = setup_connection(None, cfg)
    t0 = time.time()
    for day in touched:
        t1 = time.time()
        new_dir = state / "days" / f"{day}.new"
        shutil.rmtree(new_dir, ignore_errors=True)
        info = build_day(con, raw, ranks_glob, day, new_dir, meta, heroes, cfg, epoch, levels)
        old_dir = state / "days" / day
        for table in table_names(levels):
            merge(con, state, table, old_dir if old_dir.exists() else None, new_dir)
        shutil.rmtree(old_dir, ignore_errors=True)
        new_dir.replace(old_dir)
        manifest["days"][day] = info
        log(f"день {day}: матчей {info['matches']}, {time.time() - t1:.0f} с")
    newest = max(manifest["days"])
    for day in list(manifest["days"]):  # заморозить старые дни: их суммы уже в итоге
        if day_start(day) < day_start(newest) - (KEEP_DAYS - 1) * DAY:
            shutil.rmtree(state / "days" / day, ignore_errors=True)
    manifest["files"] = {f: {"size": files[f], "days": days_of[f]} for f in files}
    manifest["updated_seconds"] = round(time.time() - t0)
    state.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"хранилище: дней пересчитано {len(touched)}, всего дней {len(manifest['days'])}, {time.time() - t0:.0f} с")
    return manifest


class StateCounts:
    """Суммы из хранилища: итог по всем дням, умноженный на 2^((epoch − newest)/T½) — веса как в расчёте с нуля."""

    def __init__(self, con: duckdb.DuckDBPyConnection, state: Path, scale: float):
        self.con, self.total, self.scale = con, state / "total", scale

    def _src(self, table: str) -> str:
        return f"read_parquet('{(self.total / f'{table}.parquet').as_posix()}')"

    def parts(self) -> list:
        return self.con.execute(f"SELECT DISTINCT bucket, role FROM {self._src('tot')} ORDER BY ALL").fetchall()

    def counts(self, level: str, who: str, bucket: str, role: str, prefix: str) -> None:
        hero = "hc.cls" if who == "cls" else "t.hero"
        join = "JOIN hero_cls hc ON hc.hero = t.hero" if who == "cls" else ""
        flt = f"WHERE t.bucket = '{bucket}' AND t.role = '{role}'"
        for kind, table in (("tot", "tot"), ("gx", f"gx_{level}"), ("gy", "gy"), ("gxy", f"gxy_{level}")):
            extra = ", ".join(f"t.{k}" for k in KEYS[kind] if k not in ("bucket", "role", "hero", "stratum"))
            keys = f"t.bucket, t.role, {hero} AS hero{', ' + extra if extra else ''}, t.stratum"
            self.con.execute(f"""CREATE OR REPLACE TABLE {prefix}_{kind} AS
                SELECT {keys}, sum(t.n) * {self.scale} AS n, sum(t.w) * {self.scale} AS w
                FROM {self._src(table)} t {join} {flt} GROUP BY ALL""")

    def overall(self) -> None:
        s = self.scale
        self.con.execute(f"""CREATE OR REPLACE TABLE all_tot AS SELECT bucket, role, hero, stratum, sum(n) * {s} AS n,
                             sum(w) * {s} AS w FROM {self._src('tot')} GROUP BY ALL""")
        self.con.execute(f"""CREATE OR REPLACE TABLE all_gy AS SELECT bucket, role, hero, y, sum(n) * {s} AS n
                             FROM {self._src('gy')} GROUP BY ALL""")
        self.con.execute(f"""CREATE OR REPLACE TABLE all_mu AS SELECT bucket, hero, CAST(ctx AS SMALLINT) AS enemy,
                             sum(n) * {s} AS n, sum(w) * {s} AS w FROM {self._src('gx_hero')} GROUP BY ALL""")


def compute_from_state(state: Path, refs: tuple, cfg: RecsConfig, log=print) -> dict:
    """Советы по хранилищу дневных сумм — те же, что compute() по всем матчам."""
    if cfg.store_rank_groups:
        raise ValueError("ранговые группы в хранилище дневных сумм не поддерживаются (DECISIONS №17: не храним)")
    items, abilities, hero_abilities, heroes = refs
    meta, flags = item_meta(items, abilities, hero_abilities, heroes, cfg)
    manifest = json.loads((state / "manifest.json").read_text(encoding="utf-8"))
    days = manifest["days"].values()
    newest = max(d["last"] for d in days)
    scale = 2.0 ** ((day_start(manifest["epoch_day"]) - newest) / (cfg.half_life_days * DAY))
    con = setup_connection(None, cfg)
    t0 = time.time()
    static_tables(con, meta, heroes)
    total = state / "total"
    hero_classes(con, f"SELECT hero, role, sum(c) AS c FROM read_parquet('{(total / 'tot.parquet').as_posix()}') GROUP BY ALL")
    con.execute(f"""CREATE OR REPLACE TABLE component_share AS SELECT y, itself / players AS share, players, itself
                    FROM read_parquet('{(total / 'comp.parquet').as_posix()}')""")
    patches = {}
    by_bucket = {}
    for d in days:
        for k, v in d["patches"].items():
            patches[k] = patches.get(k, 0) + v
        for k, v in d["by_bucket"].items():
            by_bucket[k] = by_bucket.get(k, 0) + v
    patch = max(sorted(patches), key=lambda k: patches[k]) if patches else None
    info = {"matches": sum(d["matches"] for d in days), "ranked": sum(d["ranked"] for d in days),
            "first_start": min(d["first"] for d in days), "last_start": newest, "by_bucket": by_bucket,
            "patch": int(patch) if patch is not None and patch.lstrip("-").isdigit() else patch}
    log(f"хранилище: матчей {info['matches']}, дней {len(manifest['days'])}")
    return finish(con, {"all": StateCounts(con, state, scale)}, meta, flags, cfg, info, log, t0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Инкрементальный расчёт советов по дневным суммам")
    parser.add_argument("--raw", default="raw")
    parser.add_argument("--state", default="state")
    parser.add_argument("--out", default="recs.zip")
    parser.add_argument("--cache", default=str(Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "cache"))
    args = parser.parse_args()
    cfg = load_config()
    refs = load_refs(Path(args.cache))
    t0 = time.time()
    update(Path(args.raw), Path(args.state), refs, cfg)
    result = compute_from_state(Path(args.state), refs, cfg)
    write_recs(result, Path(args.out), cfg)
    print(f"{args.out}: {Path(args.out).stat().st_size // 1024} КБ, матчей {result['meta']['matches']}, "
          f"всего {time.time() - t0:.0f} с")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # вывод в файл на Windows иначе в cp1251 (BUGLOG №39)
    main()
