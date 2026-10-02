"""Эталон «какие предметы контрят героя» со страниц «<Hero>/Counters» вики Dota 2 (Fandom).

Это не источник советов, а эталон для проверки рекомендателя: сравниваем,
насколько наши контр-предметы похожи на то, что пишет вики сообщества.

Что берётся со страницы героя:
  - раздел «Bad against...», подраздел «Items»: предметы, которые хороши против героя
    (в порядке страницы). Предметом-контрой считается предмет в начале пункта списка
    («{{I|Silver Edge}} breaks ...»); предметы, упомянутые дальше в тексте пункта,
    складываются отдельно в items_mentioned_in_text;
  - там же герои из {{hero label|...}} (counter_heroes) и из подраздела «Others»
    через {{H|...}} (counter_heroes_other).

Названия предметов переводятся во внутренние ключи по кешу OpenDota
(%APPDATA%\\DotaTimer\\cache\\items.json), список героев берётся из heroes.json там же.
Что не удалось сопоставить, попадает в unmapped (не угадываем).

Сеть: MediaWiki API Fandom, пачками по 10 страниц, пауза между запросами ≥1.5 с,
повтор с нарастающей паузой на 429/5xx. На 402/403 или капче скрипт останавливается.

Запуск: python tools/fetch_wiki_counters.py
Результат: data/benchmark_counters.json
"""
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "benchmark_counters.json"

API_URL = "https://dota2.fandom.com/api.php"
PAGE_URL = "https://dota2.fandom.com/wiki/"
USER_AGENT = "DotaTimer-benchmark/1.0 (github.com/chehlao1989-oss/DotaTimer)"
REQUEST_PAUSE_SEC = 1.5
BATCH_SIZE = 10
MAX_RETRIES = 5

# Шаблоны вики, которыми обозначают предмет
ITEM_TEMPLATES = {"i", "item", "item id", "item label", "item icon", "itemlabel", "il"}
# Шаблоны героя
HERO_LABEL_TEMPLATES = {"hero label", "herolabel"}
HERO_TEMPLATES = {"h", "hero", "hero id", "hero icon"}

# Связки, которые допустимы между предметами в начале пункта: «X and Y», «X / Y», «X's»
CONNECTOR_RE = re.compile(r"^(\s+|,|/|&|\(|\)|:|\band/or\b|\band\b|\bor\b|'s\b|’s\b|')", re.IGNORECASE)
# Имена героев на вики, если они отличаются от OpenDota: {имя в OpenDota: [имена на вики]}
WIKI_ALT_NAMES = {"Outworld Devourer": ["Outworld Destroyer"]}
PATCH_RE =re.compile(r"\b7\.(\d{2})[a-z]?\b")


class BlockedError(Exception):
    """Сайт не пускает (402/403/капча): дальше не идём и ничего не обходим."""


def app_cache_dir() -> Path:
    """Папка кеша программы в %APPDATA%\\DotaTimer\\cache."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "DotaTimer" / "cache"


def normalize_name(name: str) -> str:
    """Название для сравнения: без регистра, с обычным апострофом и одиночными пробелами."""
    name = name.replace("’", "'").replace("_", " ")
    return re.sub(r"\s+", " ", name).strip().lower()


def load_heroes() -> list[dict]:
    """Герои из кеша OpenDota: ключ (npc-имя без префикса), id и имя как в игре."""
    data = json.loads((app_cache_dir() / "heroes.json").read_text(encoding="utf-8"))["data"]
    heroes = []
    for hero_id, h in data.items():
        heroes.append({
            "key": h["name"].replace("npc_dota_hero_", ""),
            "id": int(hero_id),
            "name": h["localized_name"],
        })
    heroes.sort(key=lambda h: h["name"])
    return heroes


def load_item_map() -> tuple[dict[str, str], dict[str, list[str]]]:
    """Словарь «название → ключ предмета». Рецепты пропускаются.

    При одинаковых названиях (Dagon 1–5 и т.п.) берётся ключ без цифрового хвоста,
    иначе самый короткий; все такие случаи возвращаются вторым значением для отчёта.
    """
    data = json.loads((app_cache_dir() / "items.json").read_text(encoding="utf-8"))["data"]
    by_name: dict[str, list[str]] = {}
    for key, item in data.items():
        dname = item.get("dname")
        if not dname or key.startswith("recipe_"):
            continue
        by_name.setdefault(normalize_name(dname), []).append(key)
    result, dupes = {}, {}
    for name, keys in by_name.items():
        keys.sort(key=lambda k: (bool(re.search(r"_\d+$", k)), len(k), k))
        result[name] = keys[0]
        if len(keys) > 1:
            dupes[name] = keys
    return result, dupes


def api_get(params: dict) -> dict:
    """Один запрос к API с паузой и повторами. На блокировку бросает BlockedError."""
    query = urllib.parse.urlencode({**params, "format": "json"})
    delay = 5.0
    for attempt in range(MAX_RETRIES):
        time.sleep(REQUEST_PAUSE_SEC)
        req = urllib.request.Request(
            f"{API_URL}?{query}",
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
        except urllib.error.HTTPError as e:
            if e.code in (402, 403):
                raise BlockedError(f"HTTP {e.code} на {query}") from e
            if e.code == 429 or e.code >= 500:
                wait = float(e.headers.get("Retry-After") or delay)
                print(f"  HTTP {e.code}, жду {wait:.0f} с (попытка {attempt + 1})")
                time.sleep(wait)
                delay *= 2
                continue
            raise
        except urllib.error.URLError as e:
            print(f"  сеть: {e.reason}, жду {delay:.0f} с")
            time.sleep(delay)
            delay *= 2
            continue
        text = raw.decode("utf-8", errors="replace")
        if "captcha" in text[:2000].lower() and not text.lstrip().startswith("{"):
            raise BlockedError("похоже на капчу вместо ответа API")
        return json.loads(text)
    raise RuntimeError(f"не удалось получить ответ после {MAX_RETRIES} попыток: {query}")


def fetch_pages(titles: list[str]) -> dict[str, dict]:
    """Тексты страниц пачкой. Возвращает {запрошенное название: {title, revid, timestamp, text}}."""
    resp = api_get({
        "action": "query",
        "prop": "revisions",
        "rvprop": "ids|timestamp|content",
        "rvslots": "main",
        "redirects": 1,
        "titles": "|".join(titles),
    })
    q = resp.get("query", {})
    # Цепочка: запрошенное → нормализованное → после перенаправления
    alias = {t: t for t in titles}
    for n in q.get("normalized", []):
        for k, v in alias.items():
            if v == n["from"]:
                alias[k] = n["to"]
    for r in q.get("redirects", []):
        for k, v in alias.items():
            if v == r["from"]:
                alias[k] = r["to"]
    pages_by_title = {}
    for page in q.get("pages", {}).values():
        if "missing" in page or not page.get("revisions"):
            continue
        rev = page["revisions"][0]
        text = rev.get("slots", {}).get("main", {}).get("*") or rev.get("*", "")
        pages_by_title[page["title"]] = {
            "title": page["title"],
            "revid": rev.get("revid"),
            "timestamp": rev.get("timestamp"),
            "text": text,
        }
    return {t: pages_by_title[alias[t]] for t in titles if alias[t] in pages_by_title}


def fetch_last_non_redirect(title: str) -> dict | None:
    """Последняя версия страницы из истории, которая не является перенаправлением."""
    resp = api_get({
        "action": "query",
        "prop": "revisions",
        "rvprop": "ids|timestamp|content",
        "rvslots": "main",
        "rvlimit": 20,
        "titles": title,
    })
    for page in resp.get("query", {}).get("pages", {}).values():
        for rev in page.get("revisions", []):
            text = rev.get("slots", {}).get("main", {}).get("*") or rev.get("*", "")
            if text and not text.lstrip().upper().startswith("#REDIRECT"):
                return {
                    "title": page["title"],
                    "revid": rev.get("revid"),
                    "timestamp": rev.get("timestamp"),
                    "text": text,
                }
    return None


def split_sections(text: str, level: int) -> list[tuple[str, str]]:
    """Делит текст на разделы заданного уровня: [(заголовок, тело)]. Текст до первого заголовка — с пустым заголовком."""
    eq = "=" * level
    pattern = re.compile(rf"^{eq}\s*([^=].*?)\s*{eq}\s*$", re.MULTILINE)
    parts, last_title, last_pos = [], "", 0
    for m in pattern.finditer(text):
        parts.append((last_title, text[last_pos:m.start()]))
        last_title, last_pos = m.group(1), m.end()
    parts.append((last_title, text[last_pos:]))
    return parts


def parse_template(body: str) -> tuple[str, str]:
    """Имя шаблона и первый позиционный параметр: «I|Force Staff|30px» → ('i', 'Force Staff')."""
    parts = [p.strip() for p in body.split("|")]
    name = parts[0].lower()
    arg = ""
    for p in parts[1:]:
        if "=" not in p and p:
            arg = p
            break
    return name, arg


def find_templates(text: str, names: set[str]) -> list[str]:
    """Все аргументы шаблонов с указанными именами, в порядке текста."""
    found = []
    for m in re.finditer(r"\{\{([^{}]+)\}\}", text):
        name, arg = parse_template(m.group(1))
        if name in names and arg:
            found.append(arg)
    return found


def lead_items(line: str, item_map: dict[str, str]) -> list[str]:
    """Предметы в начале пункта списка (подлежащее фразы), до первого обычного слова.

    Вики-ссылка [[...]] считается предметом, только если такое название есть в item_map
    (иначе это механика вроде [[break]], и разбор останавливается).
    """
    rest = line.lstrip("*#: ").strip()
    items = []
    while rest:
        m = re.match(r"\{\{([^{}]+)\}\}", rest)
        if m:
            name, arg = parse_template(m.group(1))
            if name in ITEM_TEMPLATES and arg:
                items.append(arg)
            elif name not in ITEM_TEMPLATES:
                break
            rest = rest[m.end():]
            continue
        m = re.match(r"\[\[([^\]|]+)(\|[^\]]*)?\]\]", rest)
        if m:
            if normalize_name(m.group(1)) not in item_map:
                break
            items.append(m.group(1).strip())
            rest = rest[m.end():]
            continue
        m = CONNECTOR_RE.match(rest)
        if m and m.end() > 0:
            rest = rest[m.end():]
            continue
        break
    return items


def all_item_mentions(line: str) -> list[str]:
    """Все предметы в строке: шаблоны предметов и вики-ссылки."""
    found = []
    for m in re.finditer(r"\{\{([^{}]+)\}\}|\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", line):
        if m.group(1):
            name, arg = parse_template(m.group(1))
            if name in ITEM_TEMPLATES and arg:
                found.append(arg)
        else:
            found.append(m.group(2).strip())
    return found


def unique(seq: list[str]) -> list[str]:
    """Убирает повторы, сохраняя порядок."""
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def parse_counters(text: str, item_map: dict[str, str], hero_map: dict[str, str]) -> dict:
    """Разбор страницы: контр-предметы и контр-герои из раздела «Bad against...»."""
    bad = None
    sections = split_sections(text, 2)
    for title, body in sections:
        if title.lower().startswith("bad against"):
            bad = body
            break
    result = {
        "counter_items": [], "items_mentioned_in_text": [], "counter_heroes": [],
        "counter_heroes_other": [], "unmapped": [], "problems": [],
    }
    if bad is None and "border=red" in sections[0][1]:
        # Заголовок раздела потеряли при правке, но красные карточки героев стоят до «Good against»
        bad = sections[0][1]
        result["problems"].append("заголовок Bad against потерян, раздел взят по красным карточкам")
    if bad is None:
        result["problems"].append("нет раздела Bad against")
        return result

    unmapped = []

    def map_hero(name: str) -> str | None:
        key = hero_map.get(normalize_name(name))
        if key is None:
            unmapped.append(f"hero:{name}")
        return key

    for name in find_templates(bad, HERO_LABEL_TEMPLATES):
        key = map_hero(name)
        if key:
            result["counter_heroes"].append(key)

    items_body = None
    for title, body in split_sections(bad, 3):
        low = title.lower()
        if low.startswith("item"):
            items_body = body
        elif low.startswith("other"):
            for name in find_templates(body, HERO_TEMPLATES):
                key = map_hero(name)
                if key:
                    result["counter_heroes_other"].append(key)
    if items_body is None:
        result["problems"].append("нет подраздела Items")

    lead, mentioned = [], []
    for line in (items_body or "").splitlines():
        s = line.strip()
        if not s.startswith("*"):
            continue
        is_top = not s.startswith("**")
        found_lead = lead_items(s, item_map) if is_top else []
        if is_top and not found_lead:
            # Пункт начинается с текста («Buying {{I|X}} ...»): берём первый предмет пункта
            first = [n for n in all_item_mentions(s) if normalize_name(n) in item_map][:1]
            found_lead = first
        lead.extend(found_lead)
        mentioned.extend(n for n in all_item_mentions(s) if n not in found_lead)

    for name in lead:
        key = item_map.get(normalize_name(name))
        if key:
            result["counter_items"].append(key)
        else:
            unmapped.append(name)
    for name in mentioned:
        key = item_map.get(normalize_name(name))
        if key:
            result["items_mentioned_in_text"].append(key)
        # Упоминания без сопоставления (способности, механики) не шумят в unmapped

    for k in ("counter_items", "counter_heroes", "counter_heroes_other", "unmapped"):
        result[k] = unique(result[k] if k != "unmapped" else unmapped)
    result["items_mentioned_in_text"] = [
        k for k in unique(result["items_mentioned_in_text"]) if k not in result["counter_items"]
    ]
    return result


def patches_mentioned(text: str) -> list[str]:
    """Номера патчей 7.xx, упомянутые в тексте страницы (признак устаревшего текста)."""
    return sorted({f"7.{m}" for m in PATCH_RE.findall(text)}, key=lambda p: int(p.split(".")[1]))


def main() -> int:
    heroes = load_heroes()
    item_map, item_dupes = load_item_map()
    hero_map = {normalize_name(h["name"]): h["key"] for h in heroes}
    for h in heroes:
        for alt in WIKI_ALT_NAMES.get(h["name"], []):
            hero_map.setdefault(normalize_name(alt), h["key"])

    out_heroes, problems = {}, []
    try:
        for i in range(0, len(heroes), BATCH_SIZE):
            batch = heroes[i:i + BATCH_SIZE]
            titles = [f"{h['name']}/Counters" for h in batch]
            print(f"[{i + 1}-{i + len(batch)}/{len(heroes)}] {', '.join(h['name'] for h in batch)}")
            pages = fetch_pages(titles)
            # Если на вики герой называется иначе, пробуем запасные названия
            missing = [f"{alt}/Counters" for h, t in zip(batch, titles) if t not in pages
                       for alt in WIKI_ALT_NAMES.get(h["name"], [])]
            alt_pages = fetch_pages(missing) if missing else {}
            for h, title in zip(batch, titles):
                page = pages.get(title)
                for alt in WIKI_ALT_NAMES.get(h["name"], []):
                    page = page or alt_pages.get(f"{alt}/Counters")
                if page is None:
                    problems.append(f"{h['name']}: страница {title} не найдена")
                    continue
                warning = None
                target_hero = hero_map.get(normalize_name(page["title"].split("/")[0]))
                if page["title"] != title and target_hero and target_hero != h["key"]:
                    # Перенаправление на страницу ДРУГОГО героя (порча вики): берём
                    # последнюю нормальную версию исходной страницы из её истории
                    warning = f"{title} перенаправлена на {page['title']}; взята последняя версия без перенаправления"
                    page = fetch_last_non_redirect(title)
                    if page is None:
                        problems.append(f"{h['name']}: {warning.split(';')[0]}, нормальной версии не нашлось")
                        continue
                elif page["title"] != title and target_hero is None:
                    # Страницу переименовали (на вики была волна «событийных» переименований)
                    warning = f"страница переименована: {title} → {page['title']}"
                if warning:
                    problems.append(f"{h['name']}: {warning}")
                parsed = parse_counters(page["text"], item_map, hero_map)
                entry = {
                    "hero_id": h["id"],
                    "name": h["name"],
                    "page": PAGE_URL + urllib.parse.quote(page["title"].replace(" ", "_")),
                    "revid": page["revid"],
                    "last_edited": page["timestamp"],
                    "patches_mentioned": patches_mentioned(page["text"]),
                    **{k: v for k, v in parsed.items() if k != "problems"},
                }
                if page["title"] != title:
                    entry["redirected_from"] = title
                if warning:
                    entry["wiki_warning"] = warning
                out_heroes[h["key"]] = entry
                for p in parsed["problems"]:
                    problems.append(f"{h['name']}: {p}")
    except BlockedError as e:
        print(f"ОСТАНОВКА: сайт блокирует запросы ({e}). Ничего не обходим.")
        problems.append(f"блокировка: {e}")

    doc = {
        "note": "Эталон для проверки рекомендателя: какие предметы вики сообщества считает контрой героя. Не источник советов.",
        "source": "Dota 2 Fandom wiki, <Hero>/Counters pages",
        "license": "CC BY-SA 3.0 (community wiki content), attribution: Dota 2 Wiki contributors",
        "fetched_at": datetime.date.today().isoformat(),
        "item_name_duplicates": item_dupes,
        "problems": problems,
        "heroes": out_heroes,
    }
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Готово: {len(out_heroes)} героев → {OUT_PATH}")
    if problems:
        print("Проблемы:")
        for p in problems:
            print("  -", p)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # вывод в файл на Windows иначе в cp1251 (BUGLOG №39)
    sys.exit(main())
