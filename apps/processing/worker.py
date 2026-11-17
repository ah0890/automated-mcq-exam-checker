"""Database-backed, resumable processing.

A ProcessingJob lists the papers to read. The worker takes queued jobs one at
a time and commits each paper individually, so closing the application loses
at most the paper being read at that moment. Interrupted jobs are resumed
automatically the next time the worker starts (from the web app or the
`process_papers` command). Papers already processed are never read twice
unless the teacher explicitly asks to reprocess them.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, connection, transaction
from django.db.models import F
from django.utils import timezone

from apps.exams.models import Examination
from apps.papers.models import Paper

from .engine import process_paper
from .models import ProcessingJob

log = logging.getLogger("apps.processing")

_thread: threading.Thread | None = None
_lock = threading.Lock()
STALE_AFTER = timedelta(minutes=2)


class ProcessingError(Exception):
    pass


# --- queueing ------------------------------------------------------------------------

@transaction.atomic
def enqueue(exam: Examination, user, papers=None, reprocess=False) -> ProcessingJob:
    """Queue papers for reading. Without `papers`, every paper awaiting
    processing (and any that previously failed) is queued."""
    if exam.is_finalized:
        raise ProcessingError("This examination is finalized.")
    if not exam.answer_key_version_id:
        raise ProcessingError("Attach a verified answer key before processing papers.")
    active = exam.processing_jobs.filter(status__in=[ProcessingJob.Status.QUEUED, ProcessingJob.Status.RUNNING])
    if active.exists():
        raise ProcessingError("Processing is already running for this examination.")

    qs = Paper.objects.filter(exam=exam).exclude(status__in=[Paper.Status.EXCLUDED, Paper.Status.PROCESSING])
    if papers is not None:
        qs = qs.filter(pk__in=[p.pk for p in papers])
    elif not reprocess:
        qs = qs.filter(status__in=[Paper.Status.UPLOADED, Paper.Status.FAILED, Paper.Status.QUEUED])
    ids = list(qs.order_by("sequence").values_list("pk", flat=True))
    if not ids:
        raise ProcessingError("There are no papers waiting to be processed.")
    Paper.objects.filter(pk__in=ids).update(status=Paper.Status.QUEUED, updated_at=timezone.now())
    job = ProcessingJob.objects.create(exam=exam, paper_ids=ids, total=len(ids), created_by=user,
                                       is_reprocess=reprocess or papers is not None)
    log.info("Queued %s papers for exam %s (job %s)", len(ids), exam.uid, job.uid)
    return job


def request_cancel(job: ProcessingJob) -> None:
    ProcessingJob.objects.filter(pk=job.pk, status__in=[ProcessingJob.Status.QUEUED,
                                                         ProcessingJob.Status.RUNNING]).update(cancel_requested=True)


# --- running -------------------------------------------------------------------------

def recover_interrupted() -> int:
    """Return stale running jobs to the queue (the process that ran them is gone)."""
    stale = timezone.now() - STALE_AFTER
    jobs = ProcessingJob.objects.filter(status=ProcessingJob.Status.RUNNING, heartbeat_at__lt=stale)
    n = 0
    for job in jobs:
        Paper.objects.filter(pk__in=job.paper_ids, status=Paper.Status.PROCESSING).update(status=Paper.Status.QUEUED)
        job.status = ProcessingJob.Status.QUEUED
        job.save(update_fields=["status"])
        log.warning("Resuming interrupted job %s", job.uid)
        n += 1
    return n


def run_job(job: ProcessingJob) -> ProcessingJob:
    job.status = ProcessingJob.Status.RUNNING
    job.started_at = job.started_at or timezone.now()
    job.heartbeat_at = timezone.now()
    job.save(update_fields=["status", "started_at", "heartbeat_at"])
    template = job.exam.sheet_layout.load_template()

    for pid in job.paper_ids:
        job.refresh_from_db(fields=["cancel_requested"])
        if job.cancel_requested:
            Paper.objects.filter(pk__in=job.paper_ids, status=Paper.Status.QUEUED).update(
                status=Paper.Status.UPLOADED)
            job.status = ProcessingJob.Status.CANCELLED
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "finished_at"])
            log.info("Job %s cancelled", job.uid)
            return job
        # Claim the paper atomically; skip anything not queued (idempotent resume).
        claimed = Paper.objects.filter(pk=pid, status=Paper.Status.QUEUED).update(status=Paper.Status.PROCESSING)
        if not claimed:
            continue
        paper = Paper.objects.select_related("exam", "source").get(pk=pid)
        status = process_paper(paper, template)
        counter = {Paper.Status.PROCESSED: "succeeded", Paper.Status.NEEDS_REVIEW: "needs_review"}.get(status, "failed")
        ProcessingJob.objects.filter(pk=job.pk).update(done=F("done") + 1, heartbeat_at=timezone.now(),
                                                       **{counter: F(counter) + 1})

    job.refresh_from_db()
    job.status = ProcessingJob.Status.DONE
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at"])
    log.info("Job %s finished: %s processed, %s review, %s failed in %.1fs", job.uid, job.succeeded,
             job.needs_review, job.failed, job.seconds or 0)
    return job


def run_pending() -> int:
    """Run queued jobs until none remain. Returns the number of jobs run."""
    recover_interrupted()
    n = 0
    while True:
        job = ProcessingJob.objects.filter(status=ProcessingJob.Status.QUEUED).order_by("created_at").first()
        if not job:
            return n
        run_job(job)
        n += 1


# --- background thread ---------------------------------------------------------------------

def ensure_worker() -> None:
    """Start the in-process background worker if there is queued work."""
    global _thread
    if not getattr(settings, "MCQ_BACKGROUND_PROCESSING", True):
        run_pending()  # synchronous mode (tests, command line)
        return
    with _lock:
        if _thread and _thread.is_alive():
            return
        recover_interrupted()
        if not ProcessingJob.objects.filter(status=ProcessingJob.Status.QUEUED).exists():
            return
        _thread = threading.Thread(target=_thread_main, name="paper-processing", daemon=True)
        _thread.start()


def _thread_main():
    try:
        close_old_connections()
        run_pending()
    except Exception:
        log.exception("Background processing stopped unexpectedly")
    finally:
        connection.close()
