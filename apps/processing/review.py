"""Teacher review of recognised papers."""

from __future__ import annotations

import re
from functools import lru_cache

import cv2
import numpy as np
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.files import data_path
from apps.papers.models import Paper

from .engine import check_roll_conflicts
from .models import Correction

BLANK, INVALID = "-", "*"  # form values for "no answer" and "multiple/invalid"
ROLL_RE = re.compile(r"^[0-9A-Za-z\-/]{1,20}$")


def display_value(entry: dict) -> str:
    status = entry.get("status")
    if status == "answered":
        return entry.get("value") or ""
    if status == "multiple":
        return f"Multiple ({entry.get('value') or '?'})"
    if status == "blank":
        return "Blank"
    return "Uncertain"


def _encode(entry: dict) -> str:
    return {"answered": entry.get("value") or "", "blank": BLANK, "multiple": INVALID}.get(entry.get("status"), "")


@transaction.atomic
def save_review(paper: Paper, post, user, confirm: bool) -> list[str]:
    """Apply corrections from the review form. Returns a list of problems
    (empty when saved/confirmed successfully)."""
    paper = Paper.objects.select_for_update().select_related("exam").get(pk=paper.pk)
    labels = paper.exam.option_labels
    problems: list[str] = []
    answers = {q: dict(a) for q, a in paper.answers.items()}

    for q, entry in answers.items():
        raw = post.get(f"q_{q}")
        if raw is None:
            continue
        raw = raw.strip().upper()
        if raw == _encode(entry):
            continue
        if raw == BLANK:
            new = {"value": None, "status": "blank"}
        elif raw == INVALID:
            new = {"value": entry.get("value") or entry.get("candidates") or None, "status": "multiple"}
        elif len(raw) == 1 and raw in labels:
            new = {"value": raw, "status": "answered"}
        elif raw == "":
            continue  # left unresolved
        else:
            problems.append(f"Question {q}: invalid choice.")
            continue
        Correction.objects.create(paper=paper, field=f"q{q}", old_value=display_value(entry)[:40],
                                  new_value=display_value(new)[:40], user=user)
        answers[q] = {**entry, **new, "source": "teacher"}

    roll = (post.get("roll_number") or "").strip()
    if roll != paper.roll_number:
        if roll and not ROLL_RE.match(roll):
            problems.append("The roll number may only contain letters, digits, '-' or '/'.")
        else:
            Correction.objects.create(paper=paper, field="roll_number", old_value=paper.roll_number,
                                      new_value=roll, user=user)
            paper.roll_number = roll
            paper.roll_source = "teacher" if roll else ""

    if problems:
        transaction.set_rollback(True)
        return problems

    paper.answers = answers
    uncertain = sorted(int(q) for q, a in answers.items() if a.get("status") == "uncertain")
    flags = [f for f in paper.review_flags if f["code"] not in ("uncertain_answers", "roll_unreadable")]
    if uncertain:
        flags.append({"code": "uncertain_answers", "blocking": True,
                      "message": f"{len(uncertain)} answer{'s still need' if len(uncertain) != 1 else ' still needs'} checking: "
                                 + ", ".join(f"Q{q}" for q in uncertain) + "."})
    has_roll_grid = bool(paper.reading.get("roll_digits"))
    if has_roll_grid and not paper.roll_number:
        flags.append({"code": "roll_unreadable", "blocking": True, "message": "Roll number is missing."})
    if confirm:
        flags = [f for f in flags if f["code"] != "orientation"]  # teacher has looked at the page
    paper.review_flags = flags
    paper.save()
    check_roll_conflicts(paper)
    paper.refresh_from_db()

    if confirm:
        blocking = [f["message"] for f in paper.review_flags if f.get("blocking")]
        if blocking:
            return blocking
        paper.status = Paper.Status.PROCESSED
        paper.reviewed_at = timezone.now()
        paper.reviewed_by = user
        paper.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
    elif paper.status == Paper.Status.PROCESSED and any(f.get("blocking") for f in paper.review_flags):
        paper.status = Paper.Status.NEEDS_REVIEW
        paper.save(update_fields=["status", "updated_at"])
    from apps.results.services import auto_evaluate

    auto_evaluate(paper)
    return []


# --- image crops for the review screen -------------------------------------------------------

@lru_cache(maxsize=8)
def _template(layout_pk: int, template_dir: str):
    from omr import SheetTemplate

    return SheetTemplate.load(data_path(template_dir))


def template_for(paper: Paper):
    layout = paper.exam.sheet_layout
    return _template(layout.pk, layout.template_dir)


def _load_processed(paper: Paper):
    if not paper.processed_image:
        raise ValidationError("This paper has not been processed.")
    img = cv2.imread(str(data_path(paper.processed_image)), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValidationError("The processed image is missing.")
    return img


def question_crop(paper: Paper, q: int) -> bytes:
    tpl = template_for(paper)
    if not 1 <= q <= min(tpl.capacity, paper.exam.num_questions):
        raise ValidationError("No such question.")
    img = cv2.cvtColor(_load_processed(paper), cv2.COLOR_GRAY2BGR)
    row = tpl.questions[q - 1]
    r = tpl.answer_radius
    entry = paper.answers.get(str(q), {})
    chosen = set(entry.get("value") or "") if entry.get("status") in ("answered", "multiple") else set()
    candidates = set(entry.get("candidates") or "")
    for label, (x, y) in zip(tpl.option_labels, row):
        if label in chosen:
            cv2.circle(img, (int(x), int(y)), int(r + 5), (94, 183, 18), 3)  # green: selected
        elif label in candidates:
            cv2.circle(img, (int(x), int(y)), int(r + 5), (11, 144, 247), 3)  # orange: faint/uncertain
    xs = [p[0] for p in row]
    y = row[0][1]
    x0, x1 = int(min(xs) - r * 4.5), int(max(xs) + r * 2)
    y0, y1 = int(y - r * 1.7), int(y + r * 1.7)
    crop = img[max(0, y0):y1, max(0, x0):x1]
    crop = cv2.resize(crop, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    return cv2.imencode(".png", crop)[1].tobytes()


def roll_crop(paper: Paper) -> bytes:
    tpl = template_for(paper)
    if not tpl.roll_columns:
        raise ValidationError("This sheet has no roll-number grid.")
    img = cv2.cvtColor(_load_processed(paper), cv2.COLOR_GRAY2BGR)
    pts = np.array([p for col in tpl.roll_columns for p in col])
    r = tpl.roll_radius
    digits = paper.reading.get("roll_digits", [])
    for col, d in zip(tpl.roll_columns, digits):
        if d.get("status") == "answered" and d.get("value") is not None:
            x, y = col[int(d["value"])]
            cv2.circle(img, (int(x), int(y)), int(r + 4), (94, 183, 18), 3)
        else:
            for digit in d.get("candidates", ""):
                x, y = col[int(digit)]
                cv2.circle(img, (int(x), int(y)), int(r + 4), (11, 144, 247), 3)
    x0, x1 = int(pts[:, 0].min() - r * 5), int(pts[:, 0].max() + r * 3)
    y0, y1 = int(pts[:, 1].min() - r * 7), int(pts[:, 1].max() + r * 2.5)  # include written digits above
    crop = img[max(0, y0):y1, max(0, x0):x1]
    return cv2.imencode(".png", crop)[1].tobytes()


def review_rows(paper: Paper) -> list[dict]:
    rows = []
    for q in range(1, paper.exam.num_questions + 1):
        entry = paper.answers.get(str(q), {})
        rows.append({"q": q, "entry": entry, "current": _encode(entry), "display": display_value(entry),
                     "uncertain": entry.get("status") == "uncertain",
                     "teacher": entry.get("source") == "teacher"})
    return rows
