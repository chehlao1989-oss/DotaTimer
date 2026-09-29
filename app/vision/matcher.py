"""Сравнение кусочков экрана с эталонными иконками (OpenCV, template matching).

Эталон уменьшается до размера ячейки на экране пользователя, от него берётся
внутренняя часть (без краёв, где рамки и цифры), и она ищется внутри ячейки.
Оценка — нормированная корреляция: 1.0 = полное совпадение.
"""
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

INNER = 0.84  # доля эталона по центру, которая сравнивается (края часто перекрыты рамкой)


@dataclass(frozen=True)
class Match:
    key: str | None
    score: float
    runner_up: float = 0.0  # оценка второго места: чем больше разрыв, тем увереннее


def load_image(path: Path) -> np.ndarray | None:
    """cv2.imread не понимает кириллицу в пути на Windows, поэтому читаем байты сами."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 3 and img.shape[2] == 4:  # прозрачность → на тёмный фон, как в игре
        alpha = img[:, :, 3:4].astype(np.float32) / 255
        img = (img[:, :, :3].astype(np.float32) * alpha).astype(np.uint8)
    elif img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return img


class IconLibrary:
    """Набор эталонов, подогнанных под размер ячейки (w, h) на экране."""

    def __init__(self, images: dict[str, np.ndarray], cell_w: int, cell_h: int):
        self.cell_w, self.cell_h = cell_w, cell_h
        iw, ih = max(4, round(cell_w * INNER)), max(4, round(cell_h * INNER))
        x0, y0 = (cell_w - iw) // 2, (cell_h - ih) // 2
        self.templates: dict[str, np.ndarray] = {}
        for key, img in images.items():
            resized = cv2.resize(img, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
            self.templates[key] = resized[y0:y0 + ih, x0:x0 + iw]

    @classmethod
    def from_files(cls, files: dict[str, Path], cell_w: int, cell_h: int) -> "IconLibrary":
        images = {}
        for key, path in files.items():
            img = load_image(path)
            if img is not None:
                images[key] = img
        log.info("Эталонов загружено: %d из %d", len(images), len(files))
        return cls(images, cell_w, cell_h)

    def match(self, crop: np.ndarray, candidates=None) -> Match:
        """Лучший эталон для ячейки crop (BGR). candidates ограничивает выбор."""
        if crop.shape[0] != self.cell_h or crop.shape[1] != self.cell_w:
            crop = cv2.resize(crop, (self.cell_w, self.cell_h), interpolation=cv2.INTER_AREA)
        keys = self.templates.keys() if candidates is None else [k for k in candidates if k in self.templates]
        best_key, best, second = None, -1.0, -1.0
        for key in keys:
            score = float(cv2.matchTemplate(crop, self.templates[key], cv2.TM_CCOEFF_NORMED).max())
            if score > best:
                best_key, best, second = key, score, best
            elif score > second:
                second = score
        return Match(best_key, best, max(second, 0.0))


def is_empty_cell(crop: np.ndarray, max_brightness: float = 40, max_std: float = 12) -> bool:
    """Пустая ячейка: тёмная и почти однотонная."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(gray.mean()) < max_brightness and float(gray.std()) < max_std
