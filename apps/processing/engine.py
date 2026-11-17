"""Reading one paper with the OMR engine and recording the outcome.

The engine never guesses: anything the OMR reader cannot classify with
confidence is stored as "uncertain" and the paper is sent to manual review.
Values entered by the teacher are preserved when a paper is reprocessed.
"""

from __future__ import annotations

import logging

import cv2
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.files import data_path
from apps.papers.models import Paper
from omr import OMRError, ReadingThresholds, read_sheet
from omr.imaging import load_page
from omr.reader import ANSWERED, MULTIPLE, UNCERTAIN

log = logging.getLogger("apps.processing")

ORIENTATION_MARGIN_MIN = 0.05


def thresholds() -> ReadingThresholds:
    return ReadingThresholds(marked=getattr(settings, "OMR_MARKED_THRESHOLD", 0.45),
                             empty=getattr(settings, "OMR_EMPTY_THRESHOLD", 0.15))


def _flag(code, message, blocking=True):
    return {"code": code, "message": message, "blocking": blocking}


def process_paper(paper: Paper, template) -> str:
    """Read one paper and store the result. Returns the new status."""
    exam = paper.exam
    try:
        page = load_page(data_path(paper.source.path), paper.first_page)
        reading, aligned = read_sheet(page, template, thresholds(), num_questions=exam.num_questions)
    except OMRError as exc:
        return _store_failure(paper, exc.code, exc.message)
    except Exception:  # unexpected: keep the batch going, record the failure
        log.exception("Unexpected error processing paper %s", paper.uid)
        return _store_failure(paper, "internal_error", "An unexpected error occurred while reading this paper.")

    rel_image = f"{exam.storage_dir}/processed_papers/{paper.uid.hex}.png"
    target = data_path(rel_image)
    target.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(target), aligned, [cv2.IMWRITE_PNG_COMPRESSION, 6])

    previous = paper.answers or {}
    answers = {}
    for a in reading.answers:
        q = str(a.number)
        if previous.get(q, {}).get("source") == "teacher":
            answers[q] = previous[q]  # teacher decisions survive reprocessing
            continue
        value = a.value if a.status in (ANSWERED, MULTIPLE) else None
        answers[q] = {"value": value, "status": a.status, "source": "omr", "confidence": a.confidence,
                      "candidates": a.candidates}

    flags = []
    if paper.roll_source == "teacher" and paper.roll_number:
        roll = paper.roll_number
        roll_source = "teacher"
    else:
        roll = reading.roll_number or ""
        roll_source = "omr" if roll else ""
        if template.roll_columns and not roll:
            unreadable = [str(d.number) for d in reading.roll_digits if d.status != ANSWERED]
            flags.append(_flag("roll_unreadable",
                               f"Roll number could not be read (digit position {', '.join(unreadable)})."))

    uncertain = sorted(int(q) for q, a in answers.items() if a["status"] == UNCERTAIN)
    if uncertain:
        flags.append(_flag("uncertain_answers",
                           f"{len(uncertain)} answer{'s need' if len(uncertain) != 1 else ' needs'} checking: "
                           + ", ".join(f"Q{q}" for q in uncertain) + "."))
    align = reading.alignment
    if align.get("orientation_margin", 1) < ORIENTATION_MARGIN_MIN:
        flags.append(_flag("orientation", "Page orientation is doubtful — check the page is the right way up."))
    if align.get("markers_seen", 4) < 4:
        flags.append(_flag("marker_reconstructed", "One corner marker was missing and was reconstructed.",
                           blocking=False))
    if paper.is_incomplete:
        flags.append(_flag("incomplete", "This paper has fewer pages than expected.", blocking=False))

    with transaction.atomic():
        paper.reading = reading.to_dict()
        paper.answers = answers
        paper.roll_number = roll
        paper.roll_source = roll_source
        paper.processed_image = rel_image
        paper.error_code = ""
        paper.processed_at = timezone.now()
        paper.review_flags = flags
        paper.status = Paper.Status.NEEDS_REVIEW if any(f["blocking"] for f in flags) else Paper.Status.PROCESSED
        paper.status_note = ""
        paper.save()
        check_roll_conflicts(paper)
        paper.refresh_from_db()
    _after_change(paper)
    return paper.status


def _after_change(paper: Paper) -> None:
    """Mark the paper if it is verified; keep class ranks current."""
    from apps.results.services import auto_evaluate

    auto_evaluate(paper)


def _store_failure(paper: Paper, code: str, message: str) -> str:
    paper.status = Paper.Status.FAILED
    paper.error_code = code
    paper.status_note = message[:255]
    paper.review_flags = [_flag(code, message)]
    paper.processed_at = timezone.now()
    paper.save(update_fields=["status", "error_code", "status_note", "review_flags", "processed_at", "updated_at"])
    return paper.status


def check_roll_conflicts(paper: Paper) -> None:
    """Flag every active paper that shares a roll number with another one; clear
    the flag where the conflict no longer exists."""
    exam_papers = Paper.objects.filter(exam=paper.exam).exclude(status=Paper.Status.EXCLUDED)
    by_roll: dict[str, list[Paper]] = {}
    for p in exam_papers.exclude(roll_number=""):
        by_roll.setdefault(p.roll_number, []).append(p)
    for p in exam_papers.exclude(status__in=[Paper.Status.UPLOADED, Paper.Status.QUEUED, Paper.Status.FAILED]):
        others = [o.sequence for o in by_roll.get(p.roll_number, []) if o.pk != p.pk] if p.roll_number else []
        flags = [f for f in p.review_flags if f["code"] != "duplicate_roll"]
        if others:
            flags.append(_flag("duplicate_roll", f"Roll number {p.roll_number} also appears on paper "
                                                 + ", ".join(str(s) for s in others) + "."))
        if flags != p.review_flags:
            p.review_flags = flags
            blocking = any(f["blocking"] for f in flags) or bool(p.uncertain_questions)
            if p.status in (Paper.Status.PROCESSED, Paper.Status.NEEDS_REVIEW):
                p.status = Paper.Status.NEEDS_REVIEW if blocking else Paper.Status.PROCESSED
            p.save(update_fields=["review_flags", "status", "updated_at"])
