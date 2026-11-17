"""Answer Key Library.

An AnswerKey is a named entry in the library; every change creates a new
AnswerKeyVersion. A version is a DRAFT until the teacher verifies it, after
which it is immutable. Examinations reference a specific verified version,
so editing a key never silently changes results that used an earlier one.
"""

import uuid
from collections import Counter

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse


class AnswerKey(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    title = models.CharField(max_length=150)
    subject = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("answerkeys:detail", args=[self.uid])

    @property
    def latest(self):
        return self.versions.order_by("-number").first()

    @property
    def latest_verified(self):
        return self.versions.filter(status=AnswerKeyVersion.Status.VERIFIED).order_by("-number").first()

    @property
    def open_draft(self):
        return self.versions.filter(status=AnswerKeyVersion.Status.DRAFT).order_by("-number").first()


class AnswerKeyVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Awaiting verification"
        VERIFIED = "verified", "Verified"

    class Source(models.TextChoices):
        UPLOAD = "upload", "Uploaded document"
        MANUAL = "manual", "Entered manually"
        REVISION = "revision", "Revision of earlier version"

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    key = models.ForeignKey(AnswerKey, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    num_questions = models.PositiveSmallIntegerField()
    option_labels = models.CharField(max_length=8, default="ABCD")
    answers = models.JSONField(default=dict, help_text='{"1": "A", "2": "C", ...}')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.UPLOAD)

    source_path = models.CharField(max_length=255, blank=True)
    source_name = models.CharField(max_length=160, blank=True)
    source_kind = models.CharField(max_length=10, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True)
    extraction = models.JSONField(default=dict, blank=True)

    change_note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["key", "-number"]
        constraints = [models.UniqueConstraint(fields=["key", "number"], name="unique_key_version")]

    def __str__(self):
        return f"{self.key.title} · v{self.number}"

    def get_absolute_url(self):
        return reverse("answerkeys:detail", args=[self.key.uid]) + f"?v={self.number}"

    # --- integrity ---------------------------------------------------------------
    def save(self, *args, **kwargs):
        if self.pk:
            stored = AnswerKeyVersion.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored == self.Status.VERIFIED:
                raise ValidationError("A verified answer-key version cannot be modified. Create a new version.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.VERIFIED:
            raise ValidationError("Verified answer-key versions are kept permanently.")
        return super().delete(*args, **kwargs)

    @property
    def is_verified(self):
        return self.status == self.Status.VERIFIED

    def answer_for(self, q: int) -> str | None:
        return self.answers.get(str(q))

    def ordered_answers(self) -> list[tuple[int, str | None]]:
        return [(q, self.answers.get(str(q))) for q in range(1, self.num_questions + 1)]

    def missing_questions(self) -> list[int]:
        return [q for q, a in self.ordered_answers() if not a]

    def distribution(self) -> list[tuple[str, int]]:
        counts = Counter(self.answers.values())
        return [(label, counts.get(label, 0)) for label in self.option_labels]

    def validate_complete(self) -> None:
        errors = []
        for q, a in self.ordered_answers():
            if not a:
                errors.append(f"Question {q} has no answer.")
            elif a not in self.option_labels:
                errors.append(f"Question {q} has an invalid answer “{a}”.")
        extra = [k for k in self.answers if not k.isdigit() or not 1 <= int(k) <= self.num_questions]
        if extra:
            errors.append("Answers exist for questions outside the key.")
        if errors:
            raise ValidationError(errors)
