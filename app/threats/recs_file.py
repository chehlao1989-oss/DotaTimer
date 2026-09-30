"""Файл готовых советов recs.zip (docs/RECOMMENDER_SPEC.md, раздел 7; DECISIONS №16).

Советы разложены в архиве по героям, чтобы программа держала в памяти только своего героя:
  meta.json          — патч, число матчей, формат строк (row_format, conf_codes, flags), класс героя и т.п.;
  threat.json        — угрозы: роли врагов, сила в лейте, матчапы;
  class.json         — запасной вариант по классу героя (item_class, hero_class);
  heroes/<id>.json   — {таблица: {режим: {ранговая группа: {роль: {контекст: [строки]}}}}} для таблиц item, item_hero, hero;
                       плюс "buys": {режим: {роль: [номера предметов, которые герой покупает ≥ 3% игр]}} (отсев, спека 6.4).
Целый файл в памяти занимает сотни мегабайт (замер 30.09: 8,5 МБ архив → +300 МБ), кусок одного героя — единицы.
"""
import json
import zipfile
from pathlib import Path

HERO_TABLES = ("item", "item_hero", "hero")
CLASS_TABLES = ("item_class", "hero_class")


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def write_recs_zip(result: dict, path: Path, min_role_share: float = 0.0) -> None:
    """Разложить результат расчёта по героям. Роли, в которых героя играют реже min_role_share, не пишутся."""
    per_hero: dict = {}
    threat = result.get("threat", {})
    for table in HERO_TABLES:
        for bucket, groups in result.get(table, {}).items():
            for group, roles in groups.items():
                for role, heroes in roles.items():
                    for hero, contexts in heroes.items():
                        share = threat.get(bucket, {}).get("all", {}).get(hero, {}).get("roles", {}).get(role, 0.0)
                        if share < min_role_share:
                            continue
                        (per_hero.setdefault(hero, {}).setdefault(table, {}).setdefault(bucket, {})
                         .setdefault(group, {})[role]) = contexts
    for bucket, roles in result.get("buys", {}).items():
        for role, heroes in roles.items():
            for hero, ys in heroes.items():
                per_hero.setdefault(hero, {}).setdefault("buys", {}).setdefault(bucket, {})[role] = ys
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("meta.json", _dump({**result["meta"], "min_role_share": min_role_share}))
        archive.writestr("threat.json", _dump(threat))
        archive.writestr("class.json", _dump({t: result.get(t, {}) for t in CLASS_TABLES}))
        for hero, tables in sorted(per_hero.items(), key=lambda kv: int(kv[0])):
            archive.writestr(f"heroes/{hero}.json", _dump(tables))


def load_meta(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("meta.json"))


def load_hero(path: Path, hero_id: int) -> dict | None:
    """Советы для одного героя (моего): {таблица: {режим: {группа: {роль: {контекст: [строки]}}}}} или None."""
    with zipfile.ZipFile(path) as archive:
        name = f"heroes/{hero_id}.json"
        if name not in archive.namelist():
            return None
        return json.loads(archive.read(name))


def load_all(path: Path) -> dict:
    """Весь архив в прежнем виде {таблица: {режим: {группа: {роль: {герой: …}}}}} — для проверочных инструментов."""
    with zipfile.ZipFile(path) as archive:
        result = {"meta": json.loads(archive.read("meta.json")), "threat": json.loads(archive.read("threat.json")),
                  **json.loads(archive.read("class.json"))}
        for table in (*HERO_TABLES, "buys"):
            result.setdefault(table, {})
        for name in archive.namelist():
            if not name.startswith("heroes/"):
                continue
            hero = name.removeprefix("heroes/").removesuffix(".json")
            for table, buckets in json.loads(archive.read(name)).items():
                if table == "buys":
                    for bucket, roles in buckets.items():
                        for role, ys in roles.items():
                            result["buys"].setdefault(bucket, {}).setdefault(role, {})[hero] = ys
                    continue
                for bucket, groups in buckets.items():
                    for group, roles in groups.items():
                        for role, contexts in roles.items():
                            (result[table].setdefault(bucket, {}).setdefault(group, {})
                             .setdefault(role, {})[hero]) = contexts
    return result
