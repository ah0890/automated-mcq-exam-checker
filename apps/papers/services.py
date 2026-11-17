"""Adding uploaded files to an examination as individual student papers."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

import cv2
import numpy as np
import pymupdf
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from apps.core.files import IMAGE_KINDS, JPEG, PDF, PNG, clean_filename, store_bytes
from apps.exams.models import Examination

from .models import Paper, SourceFile, UploadBatch

log = logging.getLogger("apps.papers")

PAPER_FORMATS = {PDF, PNG, JPEG}
FINGERPRINT_WIDTH = 400


@dataclass
class FileOutcome:
    name: str
    status: str  # added | duplicate_file | rejected
    papers_added: int = 0
    skipped_pages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self):
        return {"name": self.name, "status": self.status, "papers_added": self.papers_added,
                "skipped_pages": self.skipped_pages, "warnings": self.warnings, "error": self.error}


def page_fingerprints(data: bytes, kind: str) -> list[str]:
    """A hash per page of a small grayscale rendering. Identical scans give
    identical hashes regardless of the file they are in (PDF page or image)."""
    prints = []
    if kind == PDF:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                zoom = FINGERPRINT_WIDTH / page.rect.width
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
                prints.append(_digest(np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)
                                      [:, : pix.width]))
    elif kind in IMAGE_KINDS:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValidationError("The image could not be decoded.")
        f = FINGERPRINT_WIDTH / img.shape[1]
        prints.append(_digest(cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)))
    return prints


def _digest(arr: np.ndarray) -> str:
    # Quantise to absorb tiny re-encoding differences of the same scan.
    return hashlib.sha256((arr // 16).astype(np.uint8).tobytes() + bytes(str(arr.shape), "ascii")).hexdigest()


def get_or_create_batch(exam: Examination, batch_uid, pages_per_paper: int, user) -> UploadBatch:
    if batch_uid:
        batch = UploadBatch.objects.filter(uid=batch_uid, exam=exam).first()
        if batch:
            return batch
        return UploadBatch.objects.create(uid=batch_uid, exam=exam, pages_per_paper=pages_per_paper,
                                          uploaded_by=user)
    return UploadBatch.objects.create(exam=exam, pages_per_paper=pages_per_paper, uploaded_by=user)


def add_file(exam: Examination, batch: UploadBatch, uploaded) -> FileOutcome:
    """Validate, store and split one uploaded file. Never raises for bad input;
    problems are reported in the outcome so a batch continues past them."""
    name = clean_filename(uploaded.name)
    if exam.is_finalized:
        return FileOutcome(name, "rejected", error="This examination is finalized; no more papers can be added.")
    limit = settings.MCQ_MAX_UPLOAD_MB * 1024 * 1024
    if uploaded.size > limit:
        return FileOutcome(name, "rejected", error=f"Larger than the {settings.MCQ_MAX_UPLOAD_MB} MB limit.")
    data = b"".join(uploaded.chunks())
    if not data:
        return FileOutcome(name, "rejected", error="The file is empty.")

    sha = hashlib.sha256(data).hexdigest()
    existing = SourceFile.objects.filter(exam=exam, sha256=sha).first()
    if existing:
        return FileOutcome(name, "duplicate_file",
                           error=f"Already uploaded as “{existing.original_name}” — skipped.")

    try:
        stored = store_bytes(data, name, f"{exam.storage_dir}/original_papers", PAPER_FORMATS)
    except ValidationError as exc:
        return FileOutcome(name, "rejected", error=" ".join(exc.messages))

    try:
        prints = page_fingerprints(data, stored.kind)
    except Exception:
        log.exception("Could not render uploaded file for exam %s", exam.uid)
        _discard(stored)
        return FileOutcome(name, "rejected", error="The file could not be rendered.")

    per = batch.pages_per_paper if stored.kind == PDF else 1
    outcome = FileOutcome(name, "added")
    with transaction.atomic():
        Examination.objects.select_for_update().filter(pk=exam.pk).exists()  # serialise sequence numbers
        source = SourceFile.objects.create(
            exam=exam, batch=batch, original_name=name, path=stored.relative_path, kind=stored.kind,
            size=stored.size, sha256=stored.sha256, page_count=len(prints))
        known = dict(Paper.objects.filter(exam=exam).values_list("fingerprint", "sequence"))
        next_seq = (Paper.objects.filter(exam=exam).aggregate(m=Max("sequence"))["m"] or 0) + 1
        for start in range(0, len(prints), per):
            group = prints[start:start + per]
            fp = group[0]  # the answer page is the first page of each paper
            label = f"page {start + 1}" if len(prints) > 1 else "the sheet"
            if fp in known:
                outcome.skipped_pages.append(f"{label.capitalize()} is already uploaded as paper {known[fp]}.")
                continue
            incomplete = len(group) < per
            Paper.objects.create(exam=exam, source=source, sequence=next_seq, first_page=start + 1,
                                 page_count=len(group), is_incomplete=incomplete, fingerprint=fp,
                                 status_note="Fewer pages than expected." if incomplete else "")
            if incomplete:
                outcome.warnings.append(f"The last paper has {len(group)} of {per} pages.")
            known[fp] = next_seq
            next_seq += 1
            outcome.papers_added += 1
        if not outcome.papers_added:
            outcome.status = "duplicate_file"
    if not outcome.papers_added:
        # Every page was a duplicate; keep the records consistent by removing the unused source.
        with transaction.atomic():
            source.delete()
        _discard(stored)
    log.info("Exam %s: file added (%s pages, %s papers)", exam.uid, len(prints), outcome.papers_added)
    return outcome


def _discard(stored):
    from apps.core.files import discard

    discard(stored)


def paper_stats(exam: Examination) -> dict:
    papers = Paper.objects.filter(exam=exam)
    counts = {s: 0 for s, _ in Paper.Status.choices}
    for status in papers.values_list("status", flat=True):
        counts[status] += 1
    active = sum(v for k, v in counts.items() if k != Paper.Status.EXCLUDED)
    return {
        "total": active,
        "files": SourceFile.objects.filter(exam=exam).count(),
        "awaiting": counts[Paper.Status.UPLOADED] + counts[Paper.Status.QUEUED],
        "startable": counts[Paper.Status.UPLOADED] + counts[Paper.Status.FAILED],
        "to_review": counts[Paper.Status.NEEDS_REVIEW] + counts[Paper.Status.FAILED],
        "read": counts[Paper.Status.PROCESSED] + counts[Paper.Status.NEEDS_REVIEW],
        "processing": counts[Paper.Status.PROCESSING],
        "processed": counts[Paper.Status.PROCESSED],
        "needs_review": counts[Paper.Status.NEEDS_REVIEW],
        "failed": counts[Paper.Status.FAILED],
        "excluded": counts[Paper.Status.EXCLUDED],
        "incomplete": papers.filter(is_incomplete=True).exclude(status=Paper.Status.EXCLUDED).count(),
    }
