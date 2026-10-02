"""Инкрементальный расчёт советов (решение автора 02.10, DECISIONS №23): суммы по играм хранятся, после сбора
к ним прибавляются суммы только по новым матчам — из новых файлов сырья, без повторов (уже посчитанные номера матчей
хранятся). Файлы сырья после выкладки не меняются, поэтому вычитать ничего не нужно.

Почему это даёт ровно то же, что расчёт с нуля (tools/compute_recs.py: compute): формулы A и B зависят только от
сумм весов игр и побед по стратам (count_tables), а вес по давности показательный — в суммах хранится вес
2^((start_time − epoch)/T½), и одно умножение на 2^((epoch − newest)/T½) в конце даёт вес 0,5^((newest − start_time)/T½).
Сырые матчи остаются (DECISIONS №1): хранилище можно пересобрать с нуля в любой момент.

Хранилище (папка; на GitHub — state.tar.gz в релизе «recs-state»):
  manifest.json            — эпоха, отпечаток справочников и настроек, посчитанные файлы сырья, сведения о матчах;
  total/<таблица>.parquet  — суммы: tot, gy, comp, gx_<уровень>, gxy_<уровень> (без L1 — state_config);
  seen.parquet             — номера уже посчитанных матчей (повторы между запусками сбора не считаются дважды).
Пересборка с нуля — если изменились справочники или настройки расчёта или файл сырья пропал / изменился.

Запуск: python tools/recs_state.py --raw raw --state state --out recs.zip [--release data-raw] [--cache …]
"""
import argparse
import dataclasses
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
VERSION = 2
# Сколько файлов сырья обрабатывать за раз: при пересборке с нуля память ограничена куском, а не всем сырьём
# (один день ≈ 20 частей по 15 мин ≈ 240 тыс. матчей — замер 02.10).
FILES_PER_BATCH = 20
PARQUET = "(FORMAT parquet, COMPRESSION zstd)"  # zstd на 14% меньше snappy (замер 02.10: день 325 → 280 МБ)
KEYS = {"tot": ["bucket", "role", "hero", "stratum"], "gy": ["bucket", "role", "hero", "y", "stratum"],
        "gx": ["bucket", "role", "hero", "ctx", "stratum"], "gxy": ["bucket", "role", "hero", "ctx", "y", "stratum"],
        "comp": ["y"]}
VALUES = {"tot": ["n", "w", "c"], "gy": ["n", "w"], "gx": ["n", "w"], "gxy": ["n", "w"], "comp": ["itself", "players"]}


def state_config(cfg: RecsConfig) -> RecsConfig:
    """Настройки для хранилища: без L1 (X у героя E). Один день L1 — 55 млн строк против 13 млн у остальных уровней
    вместе (замер 02.10), итог L1 рос бы без предела; по решению автора L1 либо убирается, либо считается отдельно
    по окну 14 дней — в обоих случаях не в хранилище сумм (DECISIONS №23)."""
    return dataclasses.replace(cfg, levels=[lvl for lvl in cfg.levels if lvl != "L1"])


def table_names(levels: tuple) -> list:
    return ["tot", "gy", "comp"] + [f"{kind}_{lvl}" for lvl in levels for kind in ("gx", "gxy")]


def kind_of(table: str) -> str:
    return table.split("_")[0]


def fingerprint(meta, cfg: RecsConfig, levels: tuple) -> str:
    """Отпечаток всего, от чего зависят суммы: изменился — пересборка с нуля."""
    data = {"expand": meta.expand, "key_items": sorted(meta.key_items), "mechanic_of": meta.mechanic_of,
            "components": sorted(meta.components), "edges": cfg.duration_edges, "half_life": cfg.half_life_days,
            "levels": list(levels), "version": VERSION}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def day_start(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def raw_files(raw: Path) -> dict:
    return {p.name: p.stat().st_size for p in sorted(raw.glob("matches-*.parquet"))}


def add_to_total(con: duckdb.DuckDBPyConnection, state: Path, table: str, new_table: str) -> None:
    """total = total + суммы новых матчей (по ключам)."""
    kind = kind_of(table)
    keys, vals = ", ".join(KEYS[kind]), VALUES[kind]
    total = state / "total" / f"{table}.parquet"
    parts = [f"SELECT {keys}, {', '.join(vals)} FROM {new_table}"]
    if total.exists():
        parts.append(f"SELECT {keys}, {', '.join(vals)} FROM read_parquet('{total.as_posix()}')")
    order = "y" if kind == "comp" else "bucket, role"
    tmp = total.with_suffix(".tmp.parquet")
    total.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"""COPY (SELECT {keys}, {', '.join(f'sum({v}) AS {v}' for v in vals)}
                          FROM ({' UNION ALL '.join(parts)}) GROUP BY ALL ORDER BY {order}) TO '{tmp.as_posix()}' {PARQUET}""")
    tmp.replace(total)


def add_batch(con: duckdb.DuckDBPyConnection, batch: Path, state: Path, manifest: dict, meta, heroes,
              cfg: RecsConfig, levels: tuple) -> int:
    """Прибавить к хранилищу суммы по матчам из файлов папки batch, которых ещё нет в seen. Возвращает число матчей."""
    glob = (batch / "matches-*.parquet").as_posix()
    if manifest.get("epoch") is None:  # эпоха — начало самого раннего дня первой порции (дальше не меняется)
        first = duckdb.connect().execute(f"SELECT min(start_time) FROM read_parquet('{glob}')").fetchone()[0]
        manifest["epoch"] = int(first) // DAY * DAY
    seen = state / "seen.parquet"
    where = f"match_id NOT IN (SELECT match_id FROM read_parquet('{seen.as_posix()}'))" if seen.exists() else "true"
    prepare(con, glob, None, meta, heroes, cfg, where=where, epoch=manifest["epoch"], drop_components=False)
    matches, first, last = con.execute("SELECT count(DISTINCT match_id), min(start_time), max(start_time) FROM pg").fetchone()
    if not matches:
        return 0
    for i, lvl in enumerate(levels):
        count_tables(con, lvl, "true", "d", "hero")
        if i == 0:
            add_to_total(con, state, "tot", "d_tot")
            add_to_total(con, state, "gy", "d_gy")
        add_to_total(con, state, f"gx_{lvl}", "d_gx")
        add_to_total(con, state, f"gxy_{lvl}", "d_gxy")
    add_to_total(con, state, "comp", "(SELECT y, itself, players FROM component_share)")
    old = f"UNION ALL SELECT match_id FROM read_parquet('{seen.as_posix()}')" if seen.exists() else ""
    tmp = seen.with_suffix(".tmp.parquet")
    con.execute(f"COPY (SELECT DISTINCT match_id FROM (SELECT match_id FROM pg {old}) ORDER BY match_id) "
                f"TO '{tmp.as_posix()}' {PARQUET}")
    tmp.replace(seen)
    info = manifest.setdefault("info", {"matches": 0, "ranked": 0, "by_bucket": {}, "patches": {}, "first": first,
                                        "last": last})
    info["matches"] += matches
    info["first"], info["last"] = min(info["first"], first), max(info["last"], last)
    for k, v in con.execute("SELECT bucket, count(DISTINCT match_id) FROM pg GROUP BY ALL").fetchall():
        info["by_bucket"][k] = info["by_bucket"].get(k, 0) + v
    for k, v in con.execute("SELECT patch, count(*) FROM pg GROUP BY ALL").fetchall():
        info["patches"][str(k)] = info["patches"].get(str(k), 0) + v
    return matches


def update(raw: Path, state: Path, refs: tuple, cfg: RecsConfig, log=print, listing: dict | None = None,
           fetch=None) -> dict:
    """Довести хранилище до текущего сырья: прибавить суммы по новым файлам. Возвращает manifest.

    listing — {файл: размер} всего сырья (по умолчанию — файлы в папке raw); fetch(имена) — докачать в raw только
    новые файлы (на GitHub — из релиза data-raw, чтобы не качать всё сырьё).
    """
    cfg = state_config(cfg)
    items, abilities, hero_abilities, heroes = refs
    meta, _flags = item_meta(items, abilities, hero_abilities, heroes, cfg)
    levels = cfg_levels(cfg)
    stamp = fingerprint(meta, cfg, levels)
    path = state / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    files = listing if listing is not None else raw_files(raw)
    fetch = fetch or (lambda names: None)
    done = manifest.get("files", {})
    rebuild = (manifest.get("version") != VERSION or manifest.get("fingerprint") != stamp
               or any(files.get(f) != size for f, size in done.items()))  # файл пропал или изменился
    if rebuild:
        log(f"пересборка хранилища с нуля: файлов сырья {len(files)}")
        shutil.rmtree(state, ignore_errors=True)
        manifest, done = {"version": VERSION, "fingerprint": stamp, "files": {}}, {}
    new = sorted(f for f in files if f not in done)
    con = setup_connection(None, cfg)
    t0 = time.time()
    state.mkdir(parents=True, exist_ok=True)
    added = 0
    for i in range(0, len(new), FILES_PER_BATCH):
        names = new[i:i + FILES_PER_BATCH]
        fetch(names)
        batch = state.parent / f"{state.name}.batch"
        shutil.rmtree(batch, ignore_errors=True)
        batch.mkdir(parents=True)
        for name in names:
            shutil.copyfile(raw / name, batch / name)
        added += add_batch(con, batch, state, manifest, meta, heroes, cfg, levels)
        shutil.rmtree(batch, ignore_errors=True)
        manifest["files"].update({name: files[name] for name in names})
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    manifest["updated_seconds"] = round(time.time() - t0)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"хранилище: новых файлов {len(new)}, новых матчей {added}, всего матчей {manifest.get('info', {}).get('matches', 0)}, "
        f"{time.time() - t0:.0f} с")
    return manifest


class StateCounts:
    """Суммы из хранилища: итог по всем матчам, умноженный на 2^((epoch − newest)/T½) — веса как в расчёте с нуля."""

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
    """Советы по хранилищу сумм — те же, что compute() по всем матчам."""
    cfg = state_config(cfg)
    if cfg.store_rank_groups:
        raise ValueError("ранговые группы в хранилище дневных сумм не поддерживаются (DECISIONS №17: не храним)")
    items, abilities, hero_abilities, heroes = refs
    meta, flags = item_meta(items, abilities, hero_abilities, heroes, cfg)
    manifest = json.loads((state / "manifest.json").read_text(encoding="utf-8"))
    info0 = manifest["info"]
    newest = info0["last"]
    scale = 2.0 ** ((manifest["epoch"] - newest) / (cfg.half_life_days * DAY))
    con = setup_connection(None, cfg)
    t0 = time.time()
    static_tables(con, meta, heroes)
    total = state / "total"
    hero_classes(con, f"SELECT hero, role, sum(c) AS c FROM read_parquet('{(total / 'tot.parquet').as_posix()}') GROUP BY ALL")
    con.execute(f"""CREATE OR REPLACE TABLE component_share AS SELECT y, itself / players AS share, players, itself
                    FROM read_parquet('{(total / 'comp.parquet').as_posix()}')""")
    patches = info0["patches"]
    patch = max(sorted(patches), key=lambda k: patches[k]) if patches else None
    info = {"matches": info0["matches"], "ranked": info0["ranked"], "first_start": info0["first"], "last_start": newest,
            "by_bucket": info0["by_bucket"],
            "patch": int(patch) if patch is not None and patch.lstrip("-").isdigit() else patch}
    log(f"хранилище: матчей {info['matches']}")
    return finish(con, {"all": StateCounts(con, state, scale)}, meta, flags, cfg, info, log, t0)


def release_fetcher(raw: Path, repo: str, release: str, log=print) -> tuple[dict, callable]:
    """Список файлов матчей в релизе и функция, которая докачивает выбранные (только отсутствующие или другого размера)."""
    import urllib.request
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/tags/{release}",
                                     headers={"User-Agent": "DotaTimer-recs", "Accept": "application/vnd.github+json"})
    if os.environ.get("GH_TOKEN"):
        request.add_header("Authorization", f"Bearer {os.environ['GH_TOKEN']}")
    with urllib.request.urlopen(request, timeout=60) as response:
        assets = {a["name"]: a for a in json.loads(response.read())["assets"]
                  if a["name"].startswith("matches-") and a["name"].endswith(".parquet")}
    raw.mkdir(parents=True, exist_ok=True)

    def fetch(names) -> None:
        todo = [n for n in names if not (raw / n).exists() or (raw / n).stat().st_size != assets[n]["size"]]
        for n in todo:
            urllib.request.urlretrieve(assets[n]["browser_download_url"], raw / n)
        if todo:
            log(f"скачано файлов сырья: {len(todo)}, {sum(assets[n]['size'] for n in todo) / 2**20:.0f} МБ")

    return {n: a["size"] for n, a in assets.items()}, fetch


def main() -> None:
    parser = argparse.ArgumentParser(description="Инкрементальный расчёт советов по хранилищу сумм")
    parser.add_argument("--release", help="брать сырьё из релиза (например data-raw), докачивая только нужное")
    parser.add_argument("--raw", default="raw")
    parser.add_argument("--state", default="state")
    parser.add_argument("--out", default="recs.zip")
    parser.add_argument("--cache", default=str(Path(os.environ.get("APPDATA", ".")) / "DotaTimer" / "cache"))
    args = parser.parse_args()
    cfg = load_config()
    refs = load_refs(Path(args.cache))
    t0 = time.time()
    listing, fetch = (release_fetcher(Path(args.raw), os.environ.get("GITHUB_REPOSITORY", "chehlao1989-oss/DotaTimer"),
                                      args.release) if args.release else (None, None))
    manifest = update(Path(args.raw), Path(args.state), refs, cfg, listing=listing, fetch=fetch)
    t1 = time.time()
    result = compute_from_state(Path(args.state), refs, cfg)
    size = sum(f.stat().st_size for f in Path(args.state).rglob("*") if f.is_file())
    print(f"замер: хранилище обновлено за {manifest['updated_seconds']} с, советы по нему — {time.time() - t1:.0f} с; "
          f"размер хранилища {size / 2**20:.0f} МБ")
    write_recs(result, Path(args.out), cfg)
    print(f"{args.out}: {Path(args.out).stat().st_size // 1024} КБ, матчей {result['meta']['matches']}, "
          f"всего {time.time() - t0:.0f} с")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # вывод в файл на Windows иначе в cp1251 (BUGLOG №39)
    main()
