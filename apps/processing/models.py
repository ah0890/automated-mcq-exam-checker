"""Processing jobs (database-backed, resumable) and the correction audit log."""

import uuid

from django.conf import settings
from django.db import models

from apps.exams.models import Examination
from apps.papers.models import Paper


class ProcessingJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    exam = models.ForeignKey(Examination, on_delete=models.PROTECT, related_name="processing_jobs")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    paper_ids = models.JSONField(default=list)
    total = models.PositiveIntegerField(default=0)
    done = models.PositiveIntegerField(default=0)
    succeeded = models.PositiveIntegerField(default=0)
    needs_review = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    is_reprocess = models.BooleanField(default=False)
    cancel_requested = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def percent(self) -> int:
        return int(self.done * 100 / self.total) if self.total else 100

    @property
    def is_active(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)

    @property
    def seconds(self) -> float | None:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    def as_dict(self) -> dict:
        return {"uid": str(self.uid), "status": self.status, "status_label": self.get_status_display(),
                "total": self.total, "done": self.done, "succeeded": self.succeeded,
                "needs_review": self.needs_review, "failed": self.failed, "percent": self.percent,
                "active": self.is_active, "seconds": self.seconds}


class Correction(models.Model):
    """Every manual change to a recognised value is recorded."""

    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="corrections")
    field = models.CharField(max_length=20, help_text='"roll_number" or "q<N>"')
    old_value = models.CharField(max_length=40, blank=True)
    new_value = models.CharField(max_length=40, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
