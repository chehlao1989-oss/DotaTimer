"""Окно сырых матчей: один новый уникальный матч заменяет один самый старый (DECISIONS №32).

Набор публикуется отдельным релизом. window.json пишется последним и служит отметкой готовности.
Предыдущие наборы удаляются только после успешной публикации советов по новому набору.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREFIX = "data-window-"
STAGE = "data-stage-"
CHUNK_MATCHES = 100_000  # технический размер части, не игровое значение


def gh(*args, binary=False):
    """Вызвать GitHub CLI, не выводя содержимое авторизации."""
    result = subprocess.run(["gh", *args], check=True, capture_output=True)
    return result.stdout if binary else result.stdout.decode("utf-8")


def repo_name():
    return os.environ.get("GITHUB_REPOSITORY", "chehlao1989-oss/DotaTimer")


def releases(repo):
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"))
    return [release for page in pages for release in page]


def assets(repo, tag):
    release = json.loads(gh("api", f"repos/{repo}/releases/tags/{tag}"))
    return json.loads(gh("api", "--paginate", "--slurp",
                         f"repos/{repo}/releases/{release['id']}/assets?per_page=100"))


def flat_assets(repo, tag):
    return [asset for page in assets(repo, tag) for asset in page]


def asset_bytes(repo, asset):
    # Репозиторий публичный: загрузка вложений не должна расходовать REST-лимит
    # GITHUB_TOKEN (1000 запросов/ч на репозиторий). Токен в этот запрос не передаём.
    request = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "DotaTimer-storage"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def resolve(repo, tag="data-raw"):
    """Выбрать только законченный набор; для пробных релизов маршрутизация не применяется."""
    if tag != "data-raw":
        return tag
    pinned = os.environ.get("RAW_SNAPSHOT")
    if pinned:
        return pinned
    candidates = sorted((r for r in releases(repo) if r["tag_name"].startswith(PREFIX)),
                        key=lambda r: int(r["tag_name"][len(PREFIX):]), reverse=True)
    for release in candidates:
        if any(a["name"] == "window.json" for a in release["assets"]):
            return release["tag_name"]
    return tag


def manifest(repo, tag, listing=None):
    listing = listing if listing is not None else flat_assets(repo, tag)
    marker = next((a for a in listing if a["name"] == "window.json"), None)
    return json.loads(asset_bytes(repo, marker)) if marker else None


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(repo, tag, destination, include_control=False):
    """Скачать закреплённый набор и сверить файлы по описи; ошибка не подменяет его старым."""
    destination.mkdir(parents=True, exist_ok=True)
    listing = flat_assets(repo, tag)
    record = manifest(repo, tag, listing)
    expected = record["files"] if record else None
    downloaded = 0
    for asset in listing:
        name = asset["name"]
        if Path(name).name != name:
            raise ValueError("Недопустимое имя файла в релизе")
        data_file = name.startswith(("matches-", "ranks-")) and name.endswith(".parquet")
        control = include_control and (name.startswith("run-") or name == "next_seq.txt")
        if not (data_file or control):
            continue
        if expected is not None and name not in expected:
            raise ValueError(f"Файл вне описи: {name}")
        path = destination / name
        if not path.exists() or path.stat().st_size != asset["size"]:
            path.write_bytes(asset_bytes(repo, asset))
        downloaded += 1
        if downloaded % 50 == 0:
            print(f"сырьё {tag}: проверено файлов {downloaded}", flush=True)
        if path.stat().st_size != asset["size"] or (expected and digest(path) != expected[name]["sha256"]):
            raise ValueError(f"Повреждён файл {name}")
    if expected is not None:
        missing = set(expected) - {p.name for p in destination.iterdir()}
        # Управляющие файлы для расчёта не нужны.
        missing = {n for n in missing if include_control or n.endswith(".parquet")}
        if missing:
            raise ValueError(f"Отсутствуют файлы: {sorted(missing)}")
    return record


def rotate(old, incoming, out, tag, capacity=None):
    """Собрать окно, считая match_id, сохраняя все строки игроков и порядок start_time, match_id."""
    import duckdb
    import pyarrow.parquet as pq

    if out.exists() and any(out.iterdir()):
        raise ValueError("Каталог нового окна должен быть пустым")
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(out / "rotation.duckdb"))
    try:
        con.execute("SET memory_limit='2GB'")
        con.execute("SET threads=2")
        for name, directory in (("old", old), ("incoming", incoming)):
            paths = [str(p) for p in sorted(directory.glob("matches-*.parquet"))]
            if not paths:
                raise ValueError(f"Нет матчей: {directory}")
            con.from_parquet(paths).create_view(name + "_rows")
            # Повторная выгрузка одной партии не должна удваивать игроков.
            con.execute(f"CREATE TABLE {name}_data AS SELECT DISTINCT * FROM {name}_rows")
            invalid = con.execute(f"SELECT count(*) FROM (SELECT match_id FROM {name}_data "
                                  "GROUP BY match_id HAVING count(*) != 10 OR count(DISTINCT hero_id) != 10 "
                                  "OR count(DISTINCT start_time) != 1)").fetchone()[0]
            if invalid:
                raise ValueError(f"Неполные или противоречивые матчи в {name}: {invalid}")
        old_count = con.execute("SELECT count(DISTINCT match_id) FROM old_data").fetchone()[0]
        capacity = old_count if capacity is None else capacity
        if capacity != old_count or capacity <= 0:
            raise ValueError("Размер предыдущего окна не совпадает с зафиксированным объёмом")
        con.execute("CREATE TABLE fresh AS SELECT i.* FROM incoming_data i ANTI JOIN "
                    "(SELECT DISTINCT match_id FROM old_data) o USING(match_id)")
        added = con.execute("SELECT count(DISTINCT match_id) FROM fresh").fetchone()[0]
        if added > capacity:
            raise ValueError("Новая партия больше окна: требуется отдельное решение об объёме")
        con.execute("CREATE TABLE retained AS SELECT match_id, min(start_time) AS start_time FROM old_data "
                    "GROUP BY match_id ORDER BY start_time DESC, match_id DESC LIMIT ?", [capacity - added])
        con.execute("CREATE VIEW selected AS SELECT o.* FROM old_data o JOIN retained USING(match_id) "
                    "UNION ALL SELECT * FROM fresh")
        con.execute("CREATE TABLE chunks AS SELECT match_id, (row_number() OVER "
                    "(ORDER BY min(start_time), match_id)-1) // ? AS part FROM selected GROUP BY match_id",
                    [CHUNK_MATCHES])
        parts = con.execute("SELECT DISTINCT part FROM chunks ORDER BY part").fetchall()
        for (part,) in parts:
            path = out / f"matches-{tag}-p{part:03d}.parquet"
            con.sql("SELECT s.* FROM selected s JOIN chunks c USING(match_id) WHERE c.part = ? "
                    "ORDER BY s.start_time, s.match_id, s.is_radiant, s.hero_id", params=[part]).write_parquet(
                        str(path), compression="zstd")
        rank_paths = [str(p) for directory in (old, incoming) for p in directory.glob("ranks-*.parquet")]
        if rank_paths:
            con.from_parquet(rank_paths, union_by_name=True).create_view("ranks")
            con.sql("SELECT r.* FROM ranks r SEMI JOIN chunks USING(match_id) "
                    "QUALIFY row_number() OVER (PARTITION BY match_id ORDER BY num_rank_tier DESC, "
                    "avg_rank_tier DESC) = 1").write_parquet(str(out / f"ranks-{tag}.parquet"), compression="zstd")
        actual = sum(pq.read_metadata(p).num_rows for p in out.glob("matches-*.parquet"))
        if actual != capacity * 10:
            raise ValueError("Проверка количества строк нового окна не пройдена")
        return {"capacity": capacity, "matches": capacity, "added": added, "removed": added,
                "previous_matches": old_count}
    finally:
        con.close()
        (out / "rotation.duckdb").unlink(missing_ok=True)


def create_release(repo, tag):
    gh("release", "create", tag, "--repo", repo, "--target", "main", "--prerelease",
       "--title", tag, "--notes", "Сырые матчи DotaTimer. DECISIONS №32: замена старых матчей новыми 1:1.")


def publish(repo, stage, raw, work):
    """Записать и проверить новый набор; маркер готовности — последняя запись."""
    base = resolve(repo)
    old = work / "old"
    previous = download(repo, base, old)
    run_files = sorted(raw.glob("run-*.json"))
    if len(run_files) != 1:
        raise ValueError("Не найден единственный отчёт нового сбора")
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    if not run.get("all_saved"):
        raise ValueError("Сбор не полностью сохранён: замена запрещена")
    tag = PREFIX + stage.removeprefix(STAGE)
    out = work / "out"
    record = rotate(old, raw, out, run["tag"], previous["capacity"] if previous else None)
    record.update({"version": 1, "tag": tag, "parent": base, "stage": stage,
                   "run": run, "generation": int(tag[len(PREFIX):])})
    import shutil
    for path in [*run_files, raw / "next_seq.txt"]:
        if not path.exists():
            raise ValueError(f"Нет управляющего файла {path.name}")
        shutil.copyfile(path, out / path.name)
    files = [p for p in out.iterdir() if p.suffix in (".parquet", ".json", ".txt")]
    record["files"] = {p.name: {"size": p.stat().st_size, "sha256": digest(p)} for p in files}
    create_release(repo, tag)
    for path in files:
        gh("release", "upload", tag, str(path), "--repo", repo)
    remote = {a["name"]: a for a in flat_assets(repo, tag)}
    if set(remote) != set(record["files"]):
        raise ValueError("Список выгруженных файлов не совпадает с новым набором")
    for name, info in record["files"].items():
        if remote[name]["size"] != info["size"]:
            raise ValueError(f"Неверный размер выгруженного файла {name}")
        remote_digest = remote[name].get("digest")
        if remote_digest != "sha256:" + info["sha256"]:
            if hashlib.sha256(asset_bytes(repo, remote[name])).hexdigest() != info["sha256"]:
                raise ValueError(f"Контрольная сумма не совпала: {name}")
    marker = out / "window.json"
    marker.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    gh("release", "upload", tag, str(marker), "--repo", repo)
    # Отчёт следующего шага должен читать уже новое окно, а не закреплённое перед сбором.
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write(f"RAW_SNAPSHOT={tag}\n")
    print(f"окно: {record['matches']} матчей; новых {record['added']}, удалено из окна {record['removed']}; {tag}")


def cleanup(repo, snapshot):
    """Удалить прошлые наборы только после расчёта по закреплённому новому окну."""
    record = manifest(repo, snapshot)
    if not record or record["matches"] != record["capacity"] or record["added"] != record["removed"]:
        raise ValueError("Нет проверенного окна для очистки")
    for release in releases(repo):
        tag = release["tag_name"]
        older_window = tag.startswith(PREFIX) and int(tag[len(PREFIX):]) < record["generation"]
        older_stage = tag == record["stage"]
        if tag == "data-raw" or older_window or older_stage:
            gh("release", "delete", tag, "--repo", repo, "--yes")
            print(f"Удалён прежний набор: {tag}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["resolve", "download", "publish", "cleanup"])
    parser.add_argument("--release", default="data-raw")
    parser.add_argument("--raw", type=Path, default=Path("raw"))
    parser.add_argument("--work", type=Path, default=Path("rotation"))
    args = parser.parse_args()
    repo = repo_name()
    if args.command == "resolve":
        print(resolve(repo, args.release))
    elif args.command == "download":
        download(repo, resolve(repo, args.release), args.raw, include_control=True)
    elif args.command == "publish":
        publish(repo, args.release, args.raw, args.work)
    else:
        cleanup(repo, args.release)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
