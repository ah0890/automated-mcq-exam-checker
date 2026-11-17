"""Draws simple OMR sheets for unit-testing the engine's geometry and
classification logic. These are test fixtures only — they are never used as
evidence of recognition accuracy on real papers."""

from __future__ import annotations

import cv2
import numpy as np

W, H = 1240, 1754  # ~150 dpi A4


def draw_sheet(*, questions: int, options: int, blocks: int, roll_digits: int,
               answers: dict[int, list[int]] | None = None, roll: str | None = None,
               fill_level: dict[int, int] | None = None, order: str = "column") -> np.ndarray:
    """answers: question -> option indexes to fill. fill_level: question -> gray value (0 = black)."""
    img = np.full((H, W), 255, np.uint8)
    for x, y in [(60, 60), (W - 60, 60), (60, H - 60), (W - 60, H - 60)]:
        cv2.rectangle(img, (x - 22, y - 22), (x + 22, y + 22), 0, -1)
    cv2.putText(img, "TEST SHEET", (420, 140), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 3)

    r, dx, dy = 15, 44, 40
    roll_pos = [[(820 + c * 40, 230 + d * 34) for d in range(10)] for c in range(roll_digits)]
    for c, col in enumerate(roll_pos):
        for d, (x, y) in enumerate(col):
            cv2.circle(img, (x, y), 11, 0, 2)
            if roll and int(roll[c]) == d:
                cv2.circle(img, (x, y), 9, 20, -1)

    per_block = -(-questions // blocks)
    block_w = (W - 240) // blocks
    pos = {}
    for q in range(questions):
        if order == "column":
            b, row = divmod(q, per_block)
        else:
            row, b = divmod(q, blocks)
        for o in range(options):
            pos[(q + 1, o)] = (180 + b * block_w + o * dx, 700 + row * dy)
    for (q, o), (x, y) in pos.items():
        cv2.circle(img, (x, y), r, 0, 2)
        if answers and o in answers.get(q, []):
            shade = (fill_level or {}).get(q, 25)
            cv2.circle(img, (x, y), r - 3, shade, -1)
    return img
