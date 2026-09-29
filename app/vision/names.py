"""Какой герой выделен: по надписи с именем над портретом в нижней панели.

Надпись — белые заглавные буквы с тенью на меняющемся фоне. Берём только почти белые
пиксели в полосе высотой с текст и сравниваем с именами кандидатов (10 героев матча),
нарисованными похожим шрифтом. Из 10 вариантов выбрать правильный надёжно.
"""
import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

WHITE_MIN = 185  # все каналы ярче этого
WHITE_SPREAD = 45  # и почти без цвета
TEXT_HEIGHT_REF = 9  # высота заглавных букв при высоте экрана 1080
MIN_COMPONENT_AREA = 3
BLUR_SIGMA = 1.0
# Пороги по скриншотам автора: верные ответы 0.23–0.81, отрыв от второго места ≥ 0.15
MIN_SCORE = 0.2
MIN_MARGIN = 0.1
RENDER_W, RENDER_H = 800, 64
RENDER_FONT, RENDER_PT, RENDER_SPACING = "Segoe UI", 24, 135


def white_mask(bgr: np.ndarray) -> np.ndarray:
    mn = bgr.min(axis=2).astype(int)
    mx = bgr.max(axis=2).astype(int)
    return ((mn > WHITE_MIN) & (mx - mn < WHITE_SPREAD)).astype(np.uint8) * 255


def _crop_to_content(mask: np.ndarray) -> np.ndarray | None:
    ys, xs = np.nonzero(mask)
    if len(xs) < 10:
        return None
    return mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def text_mask(bgr: np.ndarray, scale: float) -> np.ndarray | None:
    """Бинарная картинка надписи, обрезанная по краям букв. None, если надписи нет."""
    mask = white_mask(bgr)
    # полоса высотой с текст, где больше всего белых пикселей (отсекает светлые края панели)
    band = max(3, round(TEXT_HEIGHT_REF * scale))
    rows = (mask > 0).sum(axis=1).astype(float)
    if rows.sum() == 0:
        return None
    sums = np.convolve(rows, np.ones(band), mode="valid")
    top = int(sums.argmax())
    band_mask = np.zeros_like(mask)
    band_mask[top:top + band] = mask[top:top + band]
    # убираем одиночные светлые точки
    count, labels, stats, _ = cv2.connectedComponentsWithStats(band_mask, connectivity=8)
    clean = np.zeros_like(band_mask)
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] >= MIN_COMPONENT_AREA:
            clean[labels == i] = 255
    # отсекаем компоненты, оторванные от основной надписи (дальше двух высот букв)
    cols = np.nonzero(clean.sum(axis=0))[0]
    if len(cols) == 0:
        return None
    gaps = np.diff(cols)
    limit = band * 2
    groups, start = [], cols[0]
    for prev, cur, gap in zip(cols[:-1], cols[1:], gaps):
        if gap > limit:
            groups.append((start, prev))
            start = cur
    groups.append((start, cols[-1]))
    widest = max(groups, key=lambda g: clean[:, g[0]:g[1] + 1].sum())
    clean[:, :widest[0]] = 0
    clean[:, widest[1] + 1:] = 0
    return _crop_to_content(clean)


def render_name(text: str) -> np.ndarray | None:
    """Имя героя, нарисованное заглавными похожим шрифтом (нужен QGuiApplication)."""
    img = QImage(RENDER_W, RENDER_H, QImage.Format_Grayscale8)
    img.fill(0)
    painter = QPainter(img)
    font = QFont(RENDER_FONT, RENDER_PT)
    font.setBold(True)
    font.setLetterSpacing(QFont.PercentageSpacing, RENDER_SPACING)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(img.rect(), Qt.AlignLeft | Qt.AlignVCenter, text.upper())
    painter.end()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(RENDER_H, img.bytesPerLine())[:, :RENDER_W]
    _, binary = cv2.threshold(arr.copy(), 128, 255, cv2.THRESH_BINARY)
    return _crop_to_content(binary)


class NameMatcher:
    def __init__(self, candidates: dict[str, str]):
        """candidates: ключ героя → имя как в игре ("zuus" → "Zeus")."""
        self.templates = {key: t for key, name in candidates.items() if (t := render_name(name)) is not None}

    def match(self, bgr: np.ndarray, scale: float) -> str | None:
        text = text_mask(bgr, scale)
        if text is None:
            return None
        h, w = text.shape
        # лёгкое размытие: буквы высотой ~9 пикселей, сдвиг на пиксель не должен ронять оценку
        text_blur = cv2.GaussianBlur(text.astype(np.float32), (0, 0), BLUR_SIGMA)
        scored = []
        for key, tmpl in self.templates.items():
            resized = cv2.resize(tmpl, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)
            resized = cv2.GaussianBlur(resized, (0, 0), BLUR_SIGMA)
            ncc = float(cv2.matchTemplate(text_blur, resized, cv2.TM_CCOEFF_NORMED)[0, 0])
            ratio = (w / h) / (tmpl.shape[1] / tmpl.shape[0])
            scored.append((ncc * min(ratio, 1 / ratio) ** 2, key))  # штраф за непохожую длину надписи
        scored.sort(reverse=True)
        if not scored or scored[0][0] < MIN_SCORE:
            return None
        if len(scored) > 1 and scored[0][0] - scored[1][0] < MIN_MARGIN:
            return None
        return scored[0][1]
