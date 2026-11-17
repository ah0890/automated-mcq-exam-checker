"""Sheet templates: learnt once from an image of the blank answer sheet.

Calibration is purely raster-based (it works on a scanned blank sheet as
well as a digital PDF). Bubbles are detected, grouped into answer blocks and
a roll-number grid, and the result is validated against the expected layout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .alignment import find_corner_markers
from .errors import OMRError
from .imaging import normalise_illumination

TEMPLATE_VERSION = 1


@dataclass(frozen=True)
class LayoutSpec:
    """What the teacher tells us about the sheet; detection must agree with it."""

    options_per_question: int = 4
    option_labels: str = "ABCD"
    roll_digits: int | None = None  # None = no roll-number bubble grid
    question_order: str = "column"  # questions numbered down each block, then across

    def __post_init__(self):
        if not 2 <= self.options_per_question <= 8:
            raise ValueError("options_per_question must be between 2 and 8")
        if len(self.option_labels) != self.options_per_question:
            raise ValueError("option_labels must have one label per option")
        if self.question_order not in {"column", "row"}:
            raise ValueError("question_order must be 'column' or 'row'")


@dataclass
class SheetTemplate:
    frame_size: tuple[int, int]  # (width, height) of the template frame in px
    frame_markers: list[list[float]]  # TL, TR, BL, BR centres in the frame
    option_labels: str
    answer_radius: float
    questions: list[list[list[float]]]  # questions[q][option] = [x, y]
    roll_radius: float | None = None
    roll_columns: list[list[list[float]]] = field(default_factory=list)  # [digit position][0..9] = [x, y]
    reference: np.ndarray | None = None  # normalised blank sheet in the frame
    version: int = TEMPLATE_VERSION

    @property
    def capacity(self) -> int:
        return len(self.questions)

    # --- persistence -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "frame_size": list(self.frame_size),
            "frame_markers": self.frame_markers,
            "option_labels": self.option_labels,
            "answer_radius": self.answer_radius,
            "questions": self.questions,
            "roll_radius": self.roll_radius,
            "roll_columns": self.roll_columns,
        }

    def save(self, directory: str | Path) -> None:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        (d / "template.json").write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        if self.reference is not None:
            cv2.imwrite(str(d / "reference.png"), self.reference)

    @classmethod
    def load(cls, directory: str | Path) -> "SheetTemplate":
        d = Path(directory)
        data = json.loads((d / "template.json").read_text(encoding="utf-8"))
        if data.get("version") != TEMPLATE_VERSION:
            raise OMRError("template_version", "Template was created by an incompatible version.")
        ref = cv2.imread(str(d / "reference.png"), cv2.IMREAD_GRAYSCALE)
        if ref is None:
            raise OMRError("template_reference_missing", "Template reference image is missing.")
        return cls(
            frame_size=tuple(data["frame_size"]),
            frame_markers=data["frame_markers"],
            option_labels=data["option_labels"],
            answer_radius=data["answer_radius"],
            questions=data["questions"],
            roll_radius=data["roll_radius"],
            roll_columns=data["roll_columns"],
            reference=ref,
        )


# --- calibration -----------------------------------------------------------------

def build_template(blank: np.ndarray, spec: LayoutSpec) -> SheetTemplate:
    """Create a template from an upright image of the blank answer sheet."""
    norm = normalise_illumination(blank)
    markers, seen = find_corner_markers(norm)
    if seen < 4:
        raise OMRError("calibration_markers", "All four corner markers must be visible on the blank sheet.")

    circles = _detect_circles(norm)
    if len(circles) < spec.options_per_question * 2:
        raise OMRError("calibration_no_bubbles", "Too few answer bubbles were detected on the blank sheet.")

    groups = _cluster_by_size(circles)
    roll_group = answer_group = None
    roll_cols: list = []
    if spec.roll_digits:
        for g in groups:
            cols = _roll_grid(g, spec.roll_digits)
            if cols:
                roll_group, roll_cols = g, cols
                break
        if roll_group is None:
            raise OMRError(
                "calibration_roll_grid",
                f"A roll-number grid with {spec.roll_digits} digit columns of 0–9 bubbles was not found.",
            )

    best_questions: list = []
    for g in groups:
        if g is roll_group:
            continue
        qs = _answer_grid(g, spec)
        if len(qs) > len(best_questions):
            best_questions, answer_group = qs, g
    if not best_questions:
        raise OMRError(
            "calibration_answer_grid",
            f"No answer grid with {spec.options_per_question} options per question was found.",
        )

    h, w = norm.shape
    return SheetTemplate(
        frame_size=(w, h),
        frame_markers=markers.tolist(),
        option_labels=spec.option_labels,
        answer_radius=float(np.median([c[2] for c in answer_group])),
        questions=best_questions,
        roll_radius=float(np.median([c[2] for c in roll_group])) if roll_group else None,
        roll_columns=roll_cols,
        reference=norm,
    )


def _detect_circles(gray: np.ndarray) -> list[tuple[float, float, float]]:
    """Return (x, y, radius) of printed bubble outlines."""
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    contours, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    min_d, max_d = w * 0.012, w * 0.06
    found = []
    for c in contours:
        area = cv2.contourArea(c)
        perim = cv2.arcLength(c, True)
        if perim == 0 or area == 0:
            continue
        x, y, bw_, bh = cv2.boundingRect(c)
        d = (bw_ + bh) / 2
        if not min_d <= d <= max_d or not 0.85 < bw_ / bh < 1.18:
            continue
        if 4 * np.pi * area / (perim * perim) < 0.85:  # squares score ~0.785
            continue
        found.append((x + bw_ / 2, y + bh / 2, d / 2))
    # A printed ring yields an outer and inner contour: keep the outer one.
    found.sort(key=lambda c: -c[2])
    kept: list[tuple[float, float, float]] = []
    for c in found:
        if all(np.hypot(c[0] - k[0], c[1] - k[1]) > 0.5 * k[2] for k in kept):
            kept.append(c)
    return kept


def _cluster_by_size(circles):
    circles = sorted(circles, key=lambda c: c[2])
    groups, current = [], [circles[0]]
    for c in circles[1:]:
        if c[2] > current[-1][2] * 1.12:
            groups.append(current)
            current = []
        current.append(c)
    groups.append(current)
    return sorted(groups, key=len, reverse=True)


def _cluster_1d(values, tol):
    order = sorted(values)
    clusters, cur = [], [order[0]]
    for v in order[1:]:
        if v - cur[-1] > tol:
            clusters.append(cur)
            cur = []
        cur.append(v)
    clusters.append(cur)
    return [float(np.mean(c)) for c in clusters]


def _nearest(centres, v):
    return int(np.argmin([abs(c - v) for c in centres]))


def _columns(group, r):
    """Group circles into vertical columns (by x). Returns [(x, members)]."""
    xs = _cluster_1d([c[0] for c in group], r)
    cols = {i: [] for i in range(len(xs))}
    for c in group:
        cols[_nearest(xs, c[0])].append(c)
    return [(xs[i], cols[i]) for i in range(len(xs))]


def _roll_grid(group, digits):
    """Find `digits` adjacent columns of exactly ten vertically aligned bubbles.
    Stray round shapes (e.g. a letter 'O' in a heading) are ignored."""
    if len(group) < digits * 10:
        return None
    r = np.median([c[2] for c in group])
    cols = [(x, m) for x, m in _columns(group, r) if len(m) == 10]
    if len(cols) != digits:
        return None
    members = [c for _, m in cols for c in m]
    ys = _cluster_1d([c[1] for c in members], r)
    if len(ys) != 10:
        return None
    xs = [x for x, _ in cols]
    grid = [[None] * 10 for _ in range(digits)]
    for x, y, _ in members:
        grid[_nearest(xs, x)][_nearest(ys, y)] = [round(x, 2), round(y, 2)]
    if any(cell is None for col in grid for cell in col):
        return None
    return grid


def _answer_grid(group, spec: LayoutSpec):
    n = spec.options_per_question
    r = np.median([c[2] for c in group])
    # A real option column holds many bubbles; isolated round shapes are noise.
    cols = [(x, m) for x, m in _columns(group, r) if len(m) >= 2]
    group = [c for _, m in cols for c in m]
    xs = [x for x, _ in cols]
    if not xs or len(xs) % n:
        return []
    blocks = [xs[i : i + n] for i in range(0, len(xs), n)]
    rows_by_block = []
    for bx in blocks:
        members = [c for c in group if bx[0] - r <= c[0] <= bx[-1] + r]
        ys = _cluster_1d([c[1] for c in members], r)
        rows = []
        for y in ys:
            row = [None] * n
            for c in members:
                if abs(c[1] - y) <= r:
                    row[_nearest(bx, c[0])] = [round(c[0], 2), round(c[1], 2)]
            if any(cell is None for cell in row):
                return []  # incomplete row → layout does not match the spec
            rows.append(row)
        rows_by_block.append(rows)

    if spec.question_order == "column":
        return [row for rows in rows_by_block for row in rows]
    depth = max(len(rows) for rows in rows_by_block)
    return [rows[i] for i in range(depth) for rows in rows_by_block if i < len(rows)]
