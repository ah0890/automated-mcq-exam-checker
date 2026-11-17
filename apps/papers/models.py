"""Uploaded student answer sheets.

SourceFile — one uploaded file, stored read-only and fingerprinted (SHA-256).
Paper      — one student's answer sheet: a group of consecutive pages within a
             source file. Papers are never deleted; they can be excluded.
"""

import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.exams.models import Examination


class UploadBatch(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    exam = models.ForeignKey(Examination, on_delete=models.PROTECT, related_name="upload_batches")
    pages_per_paper = models.PositiveSmallIntegerField(default=1)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class SourceFile(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    exam = models.ForeignKey(Examination, on_delete=models.PROTECT, related_name="source_files")
    batch = models.ForeignKey(UploadBatch, on_delete=models.PROTECT, related_name="files")
    original_name = models.CharField(max_length=160)
    path = models.CharField(max_length=255)
    kind = models.CharField(max_length=10)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    page_count = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [models.UniqueConstraint(fields=["exam", "sha256"], name="unique_file_per_exam")]

    def __str__(self):
        return self.original_name


class Paper(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Awaiting processing"
        PROCESSING = "processing", "Processing"
        PROCESSED = "processed", "Processed"
        NEEDS_REVIEW = "needs_review", "Needs review"
        FAILED = "failed", "Processing error"
        EXCLUDED = "excluded", "Excluded"

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    exam = models.ForeignKey(Examination, on_delete=models.PROTECT, related_name="papers")
    source = models.ForeignKey(SourceFile, on_delete=models.PROTECT, related_name="papers")
    sequence = models.PositiveIntegerField(help_text="Paper number within the examination, in upload order.")
    first_page = models.PositiveIntegerField(help_text="1-based page number within the source file.")
    page_count = models.PositiveSmallIntegerField(default=1)
    is_incomplete = models.BooleanField(default=False, help_text="Fewer pages than expected per paper.")
    fingerprint = models.CharField(max_length=64, db_index=True,
                                   help_text="Hash of the rendered answer page, for duplicate detection.")
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.UPLOADED, db_index=True)
    status_note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["exam", "sequence"]
        constraints = [models.UniqueConstraint(fields=["exam", "sequence"], name="unique_paper_sequence")]

    def __str__(self):
        return f"Paper {self.sequence}"

    def get_absolute_url(self):
        return reverse("papers:detail", args=[self.uid])

    @property
    def pages(self) -> range:
        return range(self.first_page, self.first_page + self.page_count)

    @property
    def page_label(self) -> str:
        if self.source.page_count == 1:
            return "Single page"
        if self.page_count == 1:
            return f"Page {self.first_page} of {self.source.page_count}"
        return f"Pages {self.first_page}–{self.first_page + self.page_count - 1} of {self.source.page_count}"

    @property
    def status_css(self) -> str:
        return {
            self.Status.UPLOADED: "pill-neutral",
            self.Status.PROCESSING: "pill-warning",
            self.Status.PROCESSED: "pill-success",
            self.Status.NEEDS_REVIEW: "pill-warning",
            self.Status.FAILED: "pill-danger",
            self.Status.EXCLUDED: "pill-neutral",
        }.get(self.status, "pill-neutral")
