"""Проверка таблицы подсказок v2 (data/item_counters.json) по страницам предметов Dota 2 Wiki.

Для каждой строки таблицы — «предмет врага даёт свойство» и «предмет отвечает на тип угрозы» — на странице
предмета в вики (Fandom, MediaWiki API) ищутся слова-подтверждения из prop_evidence / answer_evidence.
Итог — data/item_counters_checked.json: подтверждено или нет, найденное слово, ссылка, дата правки страницы.
Программа показывает только подтверждённые строки. Сырой текст страниц — в %APPDATA%\\DotaTimer\\cache\\wiki_items\\
(сырые данные храним, DECISIONS №1), чтобы любую строку можно было перепроверить.

Запуск: python tools/check_item_counters.py [--offline] (--offline — только по сохранённым страницам)
"""
import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from fetch_wiki_counters import BlockedError, api_get, app_cache_dir  # noqa: E402

TABLE_PATH = ROOT / "data" / "item_counters.json"
OUT_PATH = ROOT / "data" / "item_counters_checked.json"
BATCH = 10


def raw_dir() -> Path:
    folder = app_cache_dir() / "wiki_items"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def item_titles() -> dict[str, str]:
    """Ключ предмета → название на вики (название в игре из кеша OpenDota)."""
    data = json.loads((app_cache_dir() / "items.json").read_text(encoding="utf-8"))["data"]
    return {k: v["dname"] for k, v in data.items() if v.get("dname")}


def fetch_pages(titles: list[str]) -> dict[str, dict]:
    """Название → {text, rev}; страницы сохраняются в кеш."""
    out = {}
    for start in range(0, len(titles), BATCH):
        batch = titles[start:start + BATCH]
        reply = api_get({"action": "query", "prop": "revisions", "rvprop": "content|timestamp", "rvslots": "main",
                         "titles": "|".join(batch), "redirects": "1", "formatversion": "2"})
        query = reply.get("query", {})
        back = {r["to"]: r["from"] for r in query.get("redirects", [])}
        for page in query.get("pages", []):
            if page.get("missing") or not page.get("revisions"):
                continue
            title = back.get(page["title"], page["title"])
            rev = page["revisions"][0]
            entry = {"title": page["title"], "text": rev["slots"]["main"]["content"], "rev": rev["timestamp"]}
            out[title] = entry
            (raw_dir() / f"{urllib.parse.quote(title, safe='')}.json").write_text(
                json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        print(f"  страниц: {min(start + BATCH, len(titles))} из {len(titles)}")
    return out


def cached_pages(titles: list[str]) -> dict[str, dict]:
    out = {}
    for title in titles:
        path = raw_dir() / f"{urllib.parse.quote(title, safe='')}.json"
        if path.is_file():
            out[title] = json.loads(path.read_text(encoding="utf-8"))
    return out


TECH_FIELD = re.compile(r"\|\s*(title|image|modifier|intern|sound\d*|lore|name)\s*=[^|\n]*", re.IGNORECASE)


def page_body(text: str, title: str) -> str:
    """Текст страницы без названия самого предмета и технических полей (имя, картинка, модификатор, звук):
    иначе «ethereal» у Ethereal Blade и «dominat» в имени модификатора Helm of the Overlord подтверждали
    свойство одним названием (сверка 20 строк, 16.10)."""
    body = TECH_FIELD.sub(" ", text)
    return re.sub(re.escape(title), " ", body, flags=re.IGNORECASE)


def find_evidence(text: str, groups: list[list[str]]) -> str | None:
    """Первое найденное слово из каждой группы через « + »; None — какой-то группы нет на странице."""
    low = " ".join(text.lower().split())
    found = []
    for group in groups:
        word = next((w for w in group if w in low), None)
        if word is None:
            return None
        found.append(word)
    return " + ".join(found)


def check(table: dict, pages: dict[str, dict], titles: dict[str, str]) -> dict:
    wiki = table["wiki"]

    def row(key: str, groups: list[list[str]]) -> dict:
        title = titles.get(key)
        page = pages.get(title) if title else None
        if page is None:
            return {"verified": False, "why": "нет страницы на вики" if title else "нет предмета в справочнике"}
        evidence = find_evidence(page_body(page["text"], page["title"]), groups)
        result = {"verified": evidence is not None, "source": wiki + urllib.parse.quote(page["title"].replace(" ", "_")),
                  "rev": page["rev"]}
        result.update({"evidence": evidence} if evidence else {"why": "на странице нет подтверждения"})
        return result

    threat = {key: {prop: row(key, table["prop_evidence"][prop]) for prop in spec["props"]}
              for key, spec in table["threat_items"].items()}
    counters = {answer: {key: row(key, table["answer_evidence"][answer]) for key in keys}
                for answer, keys in table["counters"].items()}
    return {"note": "Создано tools/check_item_counters.py по data/item_counters.json — руками не править.",
            "checked_patch": table["checked_patch"], "license": table["license"],
            "threat_items": threat, "counters": counters}


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка таблицы подсказок по Dota 2 Wiki")
    parser.add_argument("--offline", action="store_true", help="только по сохранённым страницам")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # BUGLOG №39
    table = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    titles = item_titles()
    keys = set(table["threat_items"]) | {k for keys in table["counters"].values() for k in keys}
    wanted = sorted({titles[k] for k in keys if k in titles})
    try:
        pages = cached_pages(wanted) if args.offline else fetch_pages(wanted)
    except BlockedError as error:
        sys.exit(f"Вики не пускает: {error}. Ничего не обходим, повторить позже.")
    result = check(table, pages, titles)
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    rows = [r for item in result["threat_items"].values() for r in item.values()]
    rows += [r for answer in result["counters"].values() for r in answer.values()]
    bad = [(k, p, r.get("why")) for k, item in result["threat_items"].items() for p, r in item.items() if not r["verified"]]
    bad += [(k, a, r.get("why")) for a, items in result["counters"].items() for k, r in items.items() if not r["verified"]]
    print(f"Строк: {len(rows)}, подтверждено: {len(rows) - len(bad)}, нет: {len(bad)}")
    for key, what, why in bad:
        print(f"  НЕТ {key} — {what}: {why}")


if __name__ == "__main__":
    main()
