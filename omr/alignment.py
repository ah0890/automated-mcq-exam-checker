"""Registration of a page onto the template frame using the four corner markers."""

from __future__ import annotations

import cv2
import numpy as np

from .errors import OMRError

ROTATIONS = (None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE)
ROTATION_DEGREES = (0, 90, 180, 270)


def find_corner_markers(gray: np.ndarray) -> tuple[np.ndarray, int]:
    """Locate the solid square marker nearest each page corner.

    Returns the centres ordered TL, TR, BL, BR and the number of markers that
    were actually seen (a single missing marker, e.g. a cropped corner, is
    reconstructed from the other three).
    """
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    short = min(h, w)
    min_area, max_area = (short * 0.02) ** 2, (short * 0.08) ** 2
    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        if not min_area <= area <= max_area:
            continue
        (cx, cy), (rw, rh), _ = cv2.minAreaRect(c)
        if rw == 0 or rh == 0 or not 0.8 < rw / rh < 1.25:
            continue
        if area / (rw * rh) < 0.88:  # must be a filled square, not an outline
            continue
        if cv2.contourArea(cv2.convexHull(c)) * 0.95 > area:
            continue
        if _ink_ratio(bw, c) < 0.9:  # hollow boxes (e.g. digit write-in boxes) are not markers
            continue
        candidates.append((cx, cy))

    corners = [(0, 0), (w, 0), (0, h), (w, h)]
    picked: list[tuple[float, float] | None] = [None] * 4
    for i, (X, Y) in enumerate(corners):
        if not candidates:
            break
        best = min(candidates, key=lambda c: (c[0] - X) ** 2 + (c[1] - Y) ** 2)
        if np.hypot(best[0] - X, best[1] - Y) < 0.3 * max(h, w):
            picked[i] = best

    seen = sum(p is not None for p in picked)
    if seen < 3 or len({p for p in picked if p is not None}) < seen:
        raise OMRError("markers_not_found", "Corner markers could not be located on the page.")
    if seen == 3:
        i = picked.index(None)
        P = [np.array(p) if p is not None else None for p in picked]
        # Parallelogram completion: TL + BR = TR + BL
        a, b, c = {0: (1, 2, 3), 1: (0, 3, 2), 2: (0, 3, 1), 3: (1, 2, 0)}[i]
        picked[i] = tuple(P[a] + P[b] - P[c])
    return np.float32(picked), seen


def _ink_ratio(bw: np.ndarray, contour: np.ndarray) -> float:
    x, y, w, h = cv2.boundingRect(contour)
    mask = np.zeros((h, w), np.uint8)
    cv2.drawContours(mask, [contour - (x, y)], -1, 255, -1)
    inside = mask > 0
    return float((bw[y : y + h, x : x + w][inside] > 0).mean()) if inside.any() else 0.0


def _shape_matches(markers: np.ndarray, frame_markers: np.ndarray, tolerance: float = 0.12) -> bool:
    """The detected marker quadrilateral must have the sheet's proportions."""
    def ratios(m):
        top, bottom = np.linalg.norm(m[1] - m[0]), np.linalg.norm(m[3] - m[2])
        left, right = np.linalg.norm(m[2] - m[0]), np.linalg.norm(m[3] - m[1])
        return (top + bottom) / (left + right), top / bottom, left / right
    a, b = ratios(markers), ratios(frame_markers)
    return abs(a[0] / b[0] - 1) < tolerance and all(0.8 < r < 1.25 for r in a[1:])


def warp_to_frame(gray: np.ndarray, markers: np.ndarray, frame_markers: np.ndarray, frame_size: tuple[int, int]) -> np.ndarray:
    H = cv2.getPerspectiveTransform(markers, frame_markers)
    return cv2.warpPerspective(gray, H, frame_size, flags=cv2.INTER_AREA, borderValue=255)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Normalised cross-correlation of two equally-sized images (downsampled)."""
    sa = cv2.resize(a, (a.shape[1] // 4, a.shape[0] // 4), interpolation=cv2.INTER_AREA).astype(np.float32)
    sb = cv2.resize(b, (sa.shape[1], sa.shape[0]), interpolation=cv2.INTER_AREA).astype(np.float32)
    sa -= sa.mean()
    sb -= sb.mean()
    denom = float(np.sqrt((sa * sa).sum() * (sb * sb).sum()))
    return float((sa * sb).sum() / denom) if denom else 0.0


def align_page(gray: np.ndarray, reference: np.ndarray, frame_markers: np.ndarray) -> tuple[np.ndarray, dict]:
    """Try all four page orientations and keep the one that best matches the
    blank reference sheet. Returns the aligned image and diagnostics."""
    h, w = reference.shape
    tried = []
    for rot, deg in zip(ROTATIONS, ROTATION_DEGREES):
        img = gray if rot is None else cv2.rotate(gray, rot)
        if (img.shape[0] >= img.shape[1]) != (h >= w):
            continue  # orientation cannot match the sheet's portrait/landscape shape
        try:
            markers, seen = find_corner_markers(img)
        except OMRError:
            continue
        if not _shape_matches(markers, frame_markers):
            continue
        aligned = warp_to_frame(img, markers, frame_markers, (w, h))
        tried.append((similarity(aligned, reference), deg, seen, aligned))

    if not tried:
        raise OMRError("markers_not_found", "Corner markers could not be located on the page.")
    tried.sort(key=lambda t: -t[0])
    score, deg, seen, aligned = tried[0]
    runner_up = tried[1][0] if len(tried) > 1 else -1.0
    return aligned, {
        "rotation": deg,
        "match_score": round(score, 4),
        "orientation_margin": round(score - runner_up, 4),
        "markers_seen": seen,
    }
