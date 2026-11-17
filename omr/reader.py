"""Reading an answer sheet against a calibrated template.

The reader measures how much darker each bubble is than the same bubble on
the blank reference sheet (this cancels the printed letter and outline) and
classifies each question. Anything that is not clearly one answer or clearly
blank is reported as UNCERTAIN — the reader never guesses.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from .alignment import align_page
from .errors import OMRError
from .imaging import normalise_illumination
from .template import SheetTemplate

ANSWERED = "answered"
BLANK = "blank"
MULTIPLE = "multiple"
UNCERTAIN = "uncertain"

MIN_MATCH_SCORE = 0.45  # below this the page is probably not this sheet design


@dataclass(frozen=True)
class ReadingThresholds:
    """Fill-level thresholds (0 = untouched, 1 = solid black).

    Provisional values derived from the supplied sample sheet: firmly filled
    bubbles measured ≈0.65–0.69, untouched ≈0.00 and a faint mark 0.28.
    """

    marked: float = 0.45
    empty: float = 0.15


@dataclass
class BubbleRead:
    number: int  # question number (1-based) or digit position (1-based)
    status: str
    value: str | None  # selected label/digit when ANSWERED; labels joined when MULTIPLE
    candidates: str  # options above the empty threshold, strongest first
    fills: list[float]
    confidence: float  # 0..1, how far the decision is from the thresholds


@dataclass
class SheetReading:
    answers: list[BubbleRead]
    roll_digits: list[BubbleRead] = field(default_factory=list)
    alignment: dict = field(default_factory=dict)

    @property
    def roll_number(self) -> str | None:
        if not self.roll_digits or any(d.status != ANSWERED for d in self.roll_digits):
            return None
        return "".join(d.value for d in self.roll_digits)

    @property
    def needs_review(self) -> bool:
        roll_unreadable = bool(self.roll_digits) and self.roll_number is None
        return roll_unreadable or any(a.status == UNCERTAIN for a in self.answers)

    def to_dict(self) -> dict:
        return {
            "roll_number": self.roll_number,
            "roll_digits": [asdict(d) for d in self.roll_digits],
            "answers": [asdict(a) for a in self.answers],
            "alignment": self.alignment,
        }


def read_sheet(page: np.ndarray, template: SheetTemplate, thresholds: ReadingThresholds = ReadingThresholds(),
               num_questions: int | None = None) -> tuple[SheetReading, np.ndarray]:
    """Read one page. Returns the reading and the aligned page image."""
    if template.reference is None:
        raise OMRError("template_reference_missing", "Template has no reference image.")
    n = num_questions or template.capacity
    if n > template.capacity:
        raise OMRError("too_many_questions", f"The sheet only has {template.capacity} questions.")

    ref = template.reference
    aligned, diag = align_page(normalise_illumination(page), ref, np.float32(template.frame_markers))
    if diag["match_score"] < MIN_MATCH_SCORE:
        raise OMRError("template_mismatch", "The page does not match the answer-sheet template.")

    labels = template.option_labels
    answers = []
    for i, centres in enumerate(template.questions[:n]):
        fills = _measure_group(aligned, ref, centres, template.answer_radius)
        answers.append(_classify(i + 1, fills, labels, thresholds))

    roll = []
    for i, centres in enumerate(template.roll_columns):
        fills = _measure_group(aligned, ref, centres, template.roll_radius)
        roll.append(_classify(i + 1, fills, "0123456789", thresholds))

    return SheetReading(answers=answers, roll_digits=roll, alignment=diag), aligned


def _measure_group(img, ref, centres, radius):
    """Fill level for each bubble in a group, after a small local shift search
    that compensates for residual misalignment (paper curl, scanner skew)."""
    pts = np.array(centres, dtype=np.float32)
    pad = radius * 1.4
    x0, y0 = int(pts[:, 0].min() - pad), int(pts[:, 1].min() - pad)
    x1, y1 = int(pts[:, 0].max() + pad), int(pts[:, 1].max() + pad)
    search = max(2, int(radius * 0.45))
    dx = dy = 0
    H, W = img.shape
    if x0 - search >= 0 and y0 - search >= 0 and x1 + search < W and y1 + search < H:
        tmpl = ref[y0:y1, x0:x1]
        area = img[y0 - search : y1 + search, x0 - search : x1 + search]
        res = cv2.matchTemplate(area, tmpl, cv2.TM_CCOEFF_NORMED)
        _, _, _, (bx, by) = cv2.minMaxLoc(res)
        dx, dy = bx - search, by - search
    return [_fill(img, ref, x + dx, y + dy, x, y, radius) for x, y in centres]


def _disk_darkness(img, cx, cy, r):
    x, y, rr = int(round(cx)), int(round(cy)), max(2, int(r))
    patch = img[y - rr : y + rr + 1, x - rr : x + rr + 1].astype(np.float32)
    if patch.shape != (2 * rr + 1, 2 * rr + 1):
        return 0.0
    yy, xx = np.ogrid[-rr : rr + 1, -rr : rr + 1]
    return 1.0 - float(patch[xx * xx + yy * yy <= rr * rr].mean()) / 255.0


def _fill(img, ref, x, y, rx, ry, radius):
    inner = radius * 0.75  # stay inside the printed outline
    return round(max(0.0, _disk_darkness(img, x, y, inner) - _disk_darkness(ref, rx, ry, inner)), 4)


def _classify(number, fills, labels, t: ReadingThresholds) -> BubbleRead:
    order = sorted(range(len(fills)), key=lambda k: -fills[k])
    marked = [k for k in order if fills[k] >= t.marked]
    ambiguous = [k for k in order if t.empty <= fills[k] < t.marked]
    candidates = "".join(labels[k] for k in order if fills[k] >= t.empty)

    def margin(v, lo, hi):
        return max(0.0, min(1.0, (v - lo) / (hi - lo)))

    if len(marked) >= 2:
        status, value = MULTIPLE, "".join(labels[k] for k in sorted(marked))
        conf = min(margin(fills[k], t.empty, t.marked) for k in marked)
    elif len(marked) == 1 and not ambiguous:
        status, value = ANSWERED, labels[marked[0]]
        runner_up = fills[order[1]] if len(order) > 1 else 0.0
        conf = min(margin(fills[marked[0]], t.empty, t.marked), 1 - margin(runner_up, 0.0, t.empty) * 0.5)
    elif not marked and not ambiguous:
        status, value = BLANK, None
        conf = 1 - margin(fills[order[0]], 0.0, t.empty) * 0.5
    else:
        status, value, conf = UNCERTAIN, None, 0.0
    return BubbleRead(number, status, value, candidates, [round(f, 3) for f in fills], round(conf, 3))
