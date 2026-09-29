"""Распознавание 10 героев по верхней панели (один скриншот после начала игры).

Портреты в верхней панели видны всем игрокам, это обычная видимая информация.
"""
import logging
from dataclasses import dataclass

import numpy as np

from app.vision.geometry import HudLayout
from app.vision.matcher import IconLibrary, Match

log = logging.getLogger(__name__)

# Пороги уверенности: подобраны по скриншотам автора (верные ответы 0.52–0.86, отрыв от второго ≥ 0.2)
MIN_SCORE = 0.40
MIN_MARGIN = 0.08


@dataclass(frozen=True)
class TopbarResult:
    radiant: tuple[Match, ...]
    dire: tuple[Match, ...]

    @staticmethod
    def confident(match: Match) -> bool:
        return match.key is not None and match.score >= MIN_SCORE and match.score - match.runner_up >= MIN_MARGIN

    def heroes(self, side: str) -> list[str | None]:
        """Имена героев стороны ("radiant"/"dire"); None там, где не уверены."""
        matches = self.radiant if side == "radiant" else self.dire
        return [m.key if self.confident(m) else None for m in matches]

    def side_of(self, hero: str) -> str | None:
        if hero in self.heroes("radiant"):
            return "radiant"
        if hero in self.heroes("dire"):
            return "dire"
        return None


def recognize_topbar(frame: np.ndarray, layout: HudLayout, heroes: IconLibrary) -> TopbarResult:
    """frame — весь экран (BGR)."""
    matches = []
    for rect in layout.topbar_slots():
        crop = frame[rect.y:rect.y + rect.h, rect.x:rect.x + rect.w]
        matches.append(heroes.match(crop))
    result = TopbarResult(tuple(matches[:5]), tuple(matches[5:]))
    log.info("Верхняя панель: %s", [(m.key, round(m.score, 2)) for m in matches])
    return result


def enemy_side(own_team: str | None) -> str | None:
    """Своя команда из GSI (player.team_name) → сторона врагов."""
    return {"radiant": "dire", "dire": "radiant"}.get(own_team or "")
