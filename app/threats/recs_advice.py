"""Угрозы и советы в игре из готового файла советов recs.zip (docs/RECOMMENDER_SPEC.md, разделы 6–7).

Заменяет старую схему (роли Valve, винрейты OpenDota, stats.json), которую автор отверг 29.09.
- Угрозы: для каждого врага из recs.zip → threat.json — доли ролей по нетворсу, сила в лейте, матчап
  против моего героя. Оценка — веса из data/threats.json → threat_score.
- Советы: из куска моего героя (recs.zip → heroes/<id>.json). Если у врага замечен предмет X — сначала ответы на X
  (связка «E с X», иначе «любой враг с X»), потом ответы на самого героя E. Порядок строк уже в файле:
  сначала по механике, потом «по опыту игроков» (DECISIONS №18).
- Отсев в программе (спека 6.4–6.7): уже куплено (сам предмет или его доделка) — галочка; два слота: «сейчас»
  (укладывается в фазу игры по стоимости) и «потом» (лучший вообще); бонус предмету, который в топ-3 против двух
  угроз; гистерезис — совет меняется, только если новый S больше старого на hysteresis.
"""
import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.threats.recommend import RecItem, ThreatRecommendation, ThreatInput
from app.threats.scoring import Threat

log = logging.getLogger(__name__)

TIER_MECHANIC, TIER_HERO, TIER_ITEM = "mechanic", "hero", "item"
EXPERIENCE_FLAG = "exp"


@dataclass(frozen=True)
class AdviceConfig:
    phase_base_cost: int  # «сейчас»: предмет стоит не больше base + per_min × минуты игры
    phase_cost_per_min: int
    multi_threat_bonus: float  # спека 6.6
    hysteresis: float  # спека 6.7, в единицах S
    top_for_bonus: int  # в топ-N против двух угроз — бонус (спека 6.6: топ-3)


def advice_config(raw: dict) -> AdviceConfig:
    r = raw["recs_advice"]
    return AdviceConfig(r["phase_base_cost"], r["phase_cost_per_min"], r["multi_threat_bonus"], r["hysteresis"],
                        r["top_for_bonus"])


class RecsSource:
    """Чтение recs.zip: meta и угрозы — при открытии, кусок героя — по запросу (в памяти только свой)."""

    def __init__(self, path: Path):
        self.path = path
        with zipfile.ZipFile(path) as archive:
            self.meta = json.loads(archive.read("meta.json"))
            self.threat = json.loads(archive.read("threat.json"))
        self._hero_id: int | None = None
        self._hero: dict | None = None

    @classmethod
    def open(cls, path: Path) -> "RecsSource | None":
        try:
            return cls(path)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            log.warning("Файл советов %s не прочитан", path, exc_info=True)
            return None

    def hero(self, hero_id: int) -> dict:
        if hero_id != self._hero_id:
            with zipfile.ZipFile(self.path) as archive:
                name = f"heroes/{hero_id}.json"
                self._hero = json.loads(archive.read(name)) if name in archive.namelist() else {}
            self._hero_id = hero_id
        return self._hero or {}

    def main_role(self, hero_id: int) -> str | None:
        cls = self.meta.get("hero_class", {}).get(str(hero_id))
        return cls[1] if cls else None

    def decode(self, row: list) -> tuple[int, float, float, float, str, int, str]:
        """[номер предмета, S, A, B, уверенность, игр, источник] → уверенность и источник словами."""
        return (row[0], row[1], row[2], row[3], self.meta["conf_codes"][row[4]], row[5], self.meta["flags"][row[6]])


# ---------- угрозы ----------
def rank_threats(source: RecsSource, enemies: list, my_hero_id: int | None, bucket: str, cfg,
                 describe) -> list[Threat]:
    """Враги по убыванию опасности. describe(reasons: dict) → строка причины для карточки (тексты в ru.py)."""
    table = source.threat.get(bucket, {}).get("all", {})
    threats = []
    for hero in enemies:
        entry = table.get(str(hero.id), {})
        roles = entry.get("roles") or {}
        farm = None
        if roles:  # ожидаемое место по нетворсу: кор 1–2, тройка 3, саппорт 4–5 (DECISIONS №9)
            farm = 1.5 * roles.get("core", 0) + 3.0 * roles.get("offlane", 0) + 4.5 * roles.get("support", 0)
        late = entry.get("late")
        late_pp = late[0] if late and min(late[1], late[2]) >= cfg.min_length_games else None
        vs = (entry.get("vs") or {}).get(str(my_hero_id)) if my_hero_id is not None else None
        matchup_pp = vs[0] if vs and vs[1] >= cfg.min_pair_games else None  # < 0 — мой герой против него хуже
        farming = roles.get("core", 0) + roles.get("offlane", 0)  # доля игр, где герой фармит
        score = 0.0
        if farm is not None:
            score += cfg.w_farm * (3.0 - farm)
        if late_pp is not None:
            # сила в лейте опасна у фармящих: у саппорта она поднимала его в угрозы (Lion, 30.09)
            score += cfg.w_late * late_pp * farming
        if matchup_pp is not None:
            score += cfg.w_matchup * (-matchup_pp)
        reasons = {}
        if roles:
            # роль — в которой героя играют чаще всего (Alchemist: кор 49%, саппорт 37% — «кор», а не «саппорт»)
            main = max(roles, key=roles.get)
            reasons["role"] = "carry" if main == "core" and farm is not None and farm <= cfg.carry_rank else main
        if late_pp is not None and late_pp >= cfg.late_notable:
            reasons["late"] = late_pp
        if matchup_pp is not None and -matchup_pp >= cfg.matchup_notable:
            reasons["matchup"] = matchup_pp
        threats.append(Threat(hero, round(score, 2), reasons.get("role", "core"), None, note=describe(reasons)))
    return sorted(threats, key=lambda t: -t.score)


# ---------- советы ----------
class RecsRecommender:
    def __init__(self, source: RecsSource, items: dict, config: AdviceConfig):
        """items: ключ → Item (из GameData): стоимость, состав, номер."""
        self.source = source
        self.items = items
        self.config = config
        self.bucket = "normal"
        self.role_mode = "auto"
        self.by_id = {i.id: k for k, i in items.items()}
        self._closure: dict[str, set[str]] = {}
        self._previous: dict[str, tuple[str, float, frozenset]] = {}  # угроза → (совет, его S, предметы угрозы)

    def reset(self) -> None:
        self._previous.clear()

    def _components(self, key: str) -> set[str]:
        if key not in self._closure:
            out, stack = set(), list(self.items[key].components) if key in self.items else []
            while stack:
                c = stack.pop()
                if c not in out:
                    out.add(c)
                    stack.extend(self.items[c].components if c in self.items else ())
            self._closure[key] = out
        return self._closure[key]

    def _owned(self, item: str, my_items: set[str]) -> bool:
        """Есть сам предмет или его доделка (Orchid засчитан, если собран Bloodthorn)."""
        return item in my_items or any(item in self._components(z) for z in my_items)

    def _role(self, hero_id: int, slice_: dict) -> str | None:
        roles = slice_.get("hero", {}).get(self.bucket, {}).get("all", {})
        wanted = self.role_mode if self.role_mode in ("core", "offlane", "support") else self.source.main_role(hero_id)
        if wanted in roles:
            return wanted
        return max(roles, key=lambda r: len(roles[r]), default=None)

    def _rows(self, slice_: dict, role: str, threat: ThreatInput) -> list[tuple]:
        """Строки ответов против угрозы: сначала на её предметы, потом на героя. Повторы убраны."""
        def table(name: str) -> dict:
            return slice_.get(name, {}).get(self.bucket, {}).get("all", {}).get(role, {})
        ordered, seen = [], set()
        for x in sorted(threat.items):
            item = self.items.get(x)
            if item is None:
                continue
            rows = table("item_hero").get(f"{threat.hero_id}:{item.id}") or table("item").get(str(item.id)) or []
            ordered += [(r, TIER_ITEM) for r in rows]
        ordered += [(r, TIER_HERO) for r in table("hero").get(str(threat.hero_id), [])]
        out = []
        for row, tier in ordered:
            key = self.by_id.get(row[0])
            if key and key not in seen:
                seen.add(key)
                out.append((key, self.source.decode(row), tier))
        return out

    def recommend(self, my_hero_id: int | None, threats: list[ThreatInput], my_items: set[str],
                  game_time: int, my_gold: int | None = None) -> list[ThreatRecommendation]:
        if my_hero_id is None:
            return []
        slice_ = self.source.hero(my_hero_id)
        role = self._role(my_hero_id, slice_)
        if role is None:
            log.info("В файле советов нет героя %s", my_hero_id)
            return []
        cfg = self.config
        minutes = max(0, game_time) / 60
        budget = cfg.phase_base_cost + cfg.phase_cost_per_min * minutes
        per_threat = {t.hero: self._rows(slice_, role, t) for t in threats}
        # бонус за предмет в топ-N против двух и больше угроз (спека 6.6)
        tops: dict[str, int] = {}
        for rows in per_threat.values():
            for key, *_ in [r for r in rows if not self._owned(r[0], my_items)][:cfg.top_for_bonus]:
                tops[key] = tops.get(key, 0) + 1
        result = []
        for threat in threats:
            rows = per_threat[threat.hero]
            bought = [(k, d, t) for k, d, t in rows if self._owned(k, my_items)]
            fresh = [(k, d, t) for k, d, t in rows if not self._owned(k, my_items)]
            score = {k: d[1] + (cfg.multi_threat_bonus if tops.get(k, 0) >= 2 else 0.0) for k, d, _ in fresh}
            now = [r for r in fresh if self.items.get(r[0]) is None or self.items[r[0]].cost <= budget]
            first = now[0] if now else (fresh[0] if fresh else None)
            first = self._hysteresis(threat.hero, first, fresh, score, threat.items)
            chosen = [first] if first else []
            later = next((r for r in fresh if first is None or r[0] != first[0]), None)
            if later is not None:
                chosen.append(later)  # «потом»: лучший, кроме «сейчас» (спека 6.5)
            items = [RecItem(k, 0.0, "bought", None, 0, None if d[6] == EXPERIENCE_FLAG else d[6], bought=True)
                     for k, d, _ in bought[:1]]
            for index, (key, d, tier) in enumerate(chosen):
                changed = index == 0 and threat.hero in self._previous and self._previous[threat.hero][0] != key
                cost = self.items[key].cost if key in self.items else 0
                items.append(RecItem(key, round(score[key], 2), TIER_MECHANIC if d[6] != EXPERIENCE_FLAG else tier,
                                     d[2], d[5], None if d[6] == EXPERIENCE_FLAG else d[6], changed=changed,
                                     shared=max(1, tops.get(key, 1)),
                                     affordable=my_gold is not None and my_gold >= cost))
            if first:
                self._previous[threat.hero] = (first[0], score[first[0]], threat.items)
            result.append(ThreatRecommendation(threat.hero, tuple(items)))
        return result

    def counters(self, my_hero_id: int | None, enemy_hero_id: int, item: str, limit: int = 2) -> list[str]:
        """Советы на предмет врага для подсказки «у врага появился X»: ключи предметов."""
        if my_hero_id is None:
            return []
        slice_ = self.source.hero(my_hero_id)
        role = self._role(my_hero_id, slice_)
        if role is None:
            return []
        rows = self._rows(slice_, role, ThreatInput("", enemy_hero_id, frozenset({item})))
        return [k for k, _d, tier in rows if tier == TIER_ITEM][:limit]

    def _hysteresis(self, hero: str, first, fresh, score, items: frozenset):
        """Совет меняется, только если новый S больше прошлого на hysteresis (спека 6.7).

        Если у врага появился новый предмет — совет пересчитывается сразу: это новая угроза, а не колебание S.
        """
        previous = self._previous.get(hero)
        if first is None or previous is None or previous[0] == first[0] or previous[2] != items:
            return first
        still = next((r for r in fresh if r[0] == previous[0]), None)
        if still is not None and score[first[0]] < score[previous[0]] + self.config.hysteresis:
            return still
        return first
