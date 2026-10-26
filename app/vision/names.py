"""Какой герой выделен: по надписи с именем над портретом в нижней панели.

Надпись — заглавные буквы с тенью на меняющемся фоне: белые или золотистые (у выделенного врага и иногда у своего
героя имя золотое — видео автора 16.10, BUGLOG №55). Берём почти белые пиксели в полосе высотой с текст и сравниваем
с именами кандидатов (10 героев матча), нарисованными похожим шрифтом; не нашлось — то же по золотистым пикселям
со строгими порогами. Из 10 вариантов выбрать правильный надёжно.
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
# Золотые надписи (BUGLOG №55): красный ярче 160, R ≥ G ≥ B, синий от 90, разница R − B от 25 до 110 (замер по
# видео 16.10: буквы ≈ B 137, G 163, R 194). Пороги строже белых: на 230 кадрах двух видео ошибки (WINDRANGER →
# Bloodseeker) — оценка ≤ 0.32 и отрыв ≤ 0.17, верные — оценка ≥ 0.38 и отрыв ≥ 0.35; пороги — посередине разрыва
# 26.10: синий от 60, R − B до 150 — бывает золото насыщеннее: B 111, G 185, R 237 (R − B 126; logs/vision_debug 23–24.10:
# Leshrac, Phantom Lancer, Rubick, враг Invoker — «не узнан» всю катку, вместо имени бралась стрелка уровня, BUGLOG №64)
GOLD_R_MIN, GOLD_B_MIN, GOLD_RB_MIN, GOLD_RB_MAX = 160, 60, 25, 150
GOLD_MIN_SCORE = 0.35
GOLD_MIN_MARGIN = 0.25
# сдвиг широкой панели меньше этого (при высоте 1080) — то же место, что обычная рамка
MIN_SHIFT_REF = 4
RENDER_W, RENDER_H = 800, 64
RENDER_FONT, RENDER_PT, RENDER_SPACING = "Segoe UI", 24, 135


def white_mask(bgr: np.ndarray) -> np.ndarray:
    mn = bgr.min(axis=2).astype(int)
    mx = bgr.max(axis=2).astype(int)
    return ((mn > WHITE_MIN) & (mx - mn < WHITE_SPREAD)).astype(np.uint8) * 255


def gold_mask(bgr: np.ndarray) -> np.ndarray:
    b, g, r = (bgr[:, :, i].astype(int) for i in range(3))
    return ((r > GOLD_R_MIN) & (r >= g) & (g >= b) & (b > GOLD_B_MIN)
            & (r - b > GOLD_RB_MIN) & (r - b < GOLD_RB_MAX)).astype(np.uint8) * 255


def _crop_to_content(mask: np.ndarray) -> np.ndarray | None:
    ys, xs = np.nonzero(mask)
    if len(xs) < 10:
        return None
    return mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def text_mask(bgr: np.ndarray, scale: float, mask_fn=white_mask) -> np.ndarray | None:
    """Бинарная картинка надписи, обрезанная по краям букв. None, если надписи нет."""
    clean = _text_pixels(bgr, scale, mask_fn)
    return None if clean is None else _crop_to_content(clean)


def text_span(bgr: np.ndarray, scale: float, mask_fn=white_mask) -> tuple[int, int] | None:
    """Левый и правый край надписи по горизонтали. None, если надписи нет."""
    clean = _text_pixels(bgr, scale, mask_fn)
    if clean is None or (clean > 0).sum() < 10:
        return None
    cols = np.nonzero(clean.sum(axis=0))[0]
    return int(cols[0]), int(cols[-1])


def _text_pixels(bgr: np.ndarray, scale: float, mask_fn) -> np.ndarray | None:
    """Пиксели надписи на картинке того же размера: полоса высотой с текст, без точек и оторванных кусков."""
    mask = mask_fn(bgr)
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
    return clean


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
        """Сначала белые буквы, не нашлось — золотые (строже: на них ложные совпадения чаще)."""
        return (self._match(bgr, scale, white_mask, MIN_SCORE, MIN_MARGIN)
                or self._match(bgr, scale, gold_mask, GOLD_MIN_SCORE, GOLD_MIN_MARGIN))

    def find(self, strip: np.ndarray, scale: float, box_x: int, box_w: int, center: float) -> tuple[str | None, int]:
        """Имя в полосе, расширенной влево, и сдвиг панели влево в пикселях (BUGLOG №60).

        box_x, box_w — где в полосе обычная рамка имени; center — где в полосе центр надписи при обычной панели.
        Сдвиг меряется всегда, по центру надписи: короткое имя (ZEUS) у раздвинутой панели целиком влезает и в обычную
        рамку — имя узнаётся, а предметы читались бы со старого места; длинное обрезается (WITCH DOCTOR → Warlock).
        Рамка ставится так, чтобы надпись была в ней на обычном месте; не нашлось — обычная рамка, как раньше.
        """
        for mask_fn, min_score, min_margin in ((white_mask, MIN_SCORE, MIN_MARGIN),
                                               (gold_mask, GOLD_MIN_SCORE, GOLD_MIN_MARGIN)):
            span = text_span(strip, scale, mask_fn)
            if span is None:
                continue
            shift = round(center - (span[0] + span[1]) / 2)
            if shift < MIN_SHIFT_REF * scale:
                shift = 0  # обычное место (или надпись правее — значит, это не имя: проверит рамка)
            if shift > box_x:
                continue  # за краем полосы
            hero = self._match(strip[:, box_x - shift:box_x - shift + box_w], scale, mask_fn, min_score, min_margin)
            if hero is None and span[1] - span[0] + 1 > box_w - 2 * TEXT_HEIGHT_REF * scale:
                # длинное имя (PHANTOM ASSASSIN, 143 px) шире рамки без краёв — рамка его обрезает: берём надпись
                # целиком по её краям (24.10 свой Phantom Assassin «не узнан» всю катку, BUGLOG №64)
                hero = self._match(strip[:, span[0]:span[1] + 1], scale, mask_fn, min_score, min_margin)
            if hero:
                return hero, shift
        return self.match(strip[:, box_x:box_x + box_w], scale), 0

    def _match(self, bgr: np.ndarray, scale: float, mask_fn, min_score: float, min_margin: float) -> str | None:
        text = text_mask(bgr, scale, mask_fn)
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
        if not scored or scored[0][0] < min_score:
            return None
        if len(scored) > 1 and scored[0][0] - scored[1][0] < min_margin:
            return None
        return scored[0][1]
