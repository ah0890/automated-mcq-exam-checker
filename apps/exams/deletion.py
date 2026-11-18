"""Permanent deletion of examinations.

Deleting an examination removes everything recorded for it: uploaded papers
(original files and processed images), readings, manual corrections, results,
evaluation history, processing jobs and finalization events. Optionally the
answer key and the answer-sheet layout are removed as well, but only when no
other examination uses them.

Database rows are removed in one transaction; the files are removed only
after that transaction commits, so a failure never leaves records pointing at
missing files.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.files import data_path

from .models import Examination, SheetLayout

log = logging.getLogger("apps.exams")


@dataclass
class DeletionSummary:
    examinations: int = 0
    papers: int = 0
    results: int = 0
    layouts_removed: int = 0
    keys_removed: int = 0
    folders: list[str] = field(default_factory=list)


def deletion_blockers(exam: Examination, user) -> list[str]:
    """Reasons this user cannot delete this examination right now."""
    reasons = []
    if exam.processing_jobs.filter(status__in=["queued", "running"]).exists():
        reasons.append("Papers are being checked right now. Stop processing first.")
    if exam.is_finalized and not user.is_superuser:
        reasons.append("Results are finalized. Only an administrator can delete a finalized examination.")
    return reasons


def impact(exam: Examination) -> dict:
    """What deleting the examination would remove (shown before confirming)."""
    return {"papers": exam.papers.count(), "files": exam.source_files.count(), "results": exam.results.count()}


def _remove_tree(rel: str) -> None:
    """Remove a DATA_DIR folder, including read-only original files."""
    path = data_path(rel)
    if not path.exists():
        return

    def make_writable_and_retry(func, p, *_):
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        func(p)

    try:
        shutil.rmtree(path, onexc=make_writable_and_retry)
    except OSError:
        log.exception("Could not remove folder %s", rel)


def _layout_unused(layout: SheetLayout) -> bool:
    return not layout.examinations.exists()


def _key_unused(key) -> bool:
    from apps.results.models import EvaluationRun, Result

    versions = key.versions.all()
    return not (Examination.objects.filter(answer_key_version__in=versions).exists()
                or Result.objects.filter(key_version__in=versions).exists()
                or EvaluationRun.objects.filter(key_version__in=versions).exists())


def delete_examinations(exams, user, *, remove_unused_setup: bool = False) -> DeletionSummary:
    """Permanently delete examinations and all their data. Raises ValidationError if any is blocked."""
    from apps.papers.models import Paper, SourceFile, UploadBatch
    from apps.processing.models import ProcessingJob
    from apps.results.models import EvaluationRun, FinalizationEvent, Result

    summary = DeletionSummary()
    folders: list[str] = []
    with transaction.atomic():
        exams = list(Examination.objects.select_for_update().filter(pk__in=[e.pk for e in exams])
                     .select_related("sheet_layout", "answer_key_version__key"))
        for exam in exams:
            blockers = deletion_blockers(exam, user)
            if blockers:
                raise ValidationError(f"“{exam.name}” cannot be deleted: {' '.join(blockers)}")

        layouts = {e.sheet_layout for e in exams}
        keys = {e.answer_key_version.key for e in exams if e.answer_key_version_id}
        for exam in exams:
            summary.papers += exam.papers.count()
            summary.results += exam.results.count()
            # Children first: these relations are PROTECT so nothing disappears by accident elsewhere.
            Result.objects.filter(exam=exam).delete()
            EvaluationRun.objects.filter(exam=exam).delete()
            FinalizationEvent.objects.filter(exam=exam).delete()
            ProcessingJob.objects.filter(exam=exam).delete()
            Paper.objects.filter(exam=exam).delete()  # cascades to corrections
            SourceFile.objects.filter(exam=exam).delete()
            UploadBatch.objects.filter(exam=exam).delete()
            folders.append(exam.storage_dir)
            log.info("Examination %s deleted by %s", exam.uid, user.get_username())
            exam.delete()
            summary.examinations += 1

        if remove_unused_setup:
            for key in keys:
                if _key_unused(key):
                    folders.append(f"answer_key_library/{key.uid.hex}")
                    key.delete()  # cascades to its versions
                    summary.keys_removed += 1
            for layout in layouts:
                if _layout_unused(layout):
                    folders.append(f"sheet_layouts/{layout.uid.hex}")
                    layout.delete()
                    summary.layouts_removed += 1

        summary.folders = folders
        transaction.on_commit(lambda: [_remove_tree(f) for f in folders])
    return summary


def delete_layout(layout: SheetLayout, user) -> None:
    """Permanently delete an answer-sheet layout that no examination uses."""
    with transaction.atomic():
        if not _layout_unused(layout):
            raise ValidationError("This sheet layout is used by an examination. Delete the examination first.")
        folder = f"sheet_layouts/{layout.uid.hex}"
        log.info("Sheet layout %s deleted by %s", layout.uid, user.get_username())
        layout.delete()
        transaction.on_commit(lambda: _remove_tree(folder))


def delete_answer_key(key, user) -> None:
    """Permanently delete an answer key (all versions) that no examination or result uses."""
    with transaction.atomic():
        if not _key_unused(key):
            raise ValidationError("This answer key is used by an examination or its results. "
                                  "Delete the examination first.")
        folder = f"answer_key_library/{key.uid.hex}"
        log.info("Answer key %s deleted by %s", key.uid, user.get_username())
        key.delete()  # cascades to its versions
        transaction.on_commit(lambda: _remove_tree(folder))
