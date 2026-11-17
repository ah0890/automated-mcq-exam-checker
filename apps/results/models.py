"""Stored evaluation results.

A Result records exactly what it was computed from (answer-key version,
marking rules and a digest of the paper's final answers). If any of those
change, the result is reported as out of date until it is re-evaluated —
results are never silently recomputed against a different key.
"""

from django.conf import settings
from django.db import models

from apps.answerkeys.models import AnswerKeyVersion
from apps.exams.models import Examination
from apps.papers.models import Paper


class Result(models.Model):
    paper = models.OneToOneField(Paper, on_delete=models.CASCADE, related_name="result")
    exam = models.ForeignKey(Examination, on_delete=models.PROTECT, related_name="results")
    key_version = models.ForeignKey(AnswerKeyVersion, on_delete=models.PROTECT, related_name="results")
    rules = models.JSONField()
    answers_digest = models.CharField(max_length=64)
    roll_number = models.CharField(max_length=20, blank=True)

    correct = models.PositiveSmallIntegerField()
    incorrect = models.PositiveSmallIntegerField()
    unanswered = models.PositiveSmallIntegerField()
    multiple = models.PositiveSmallIntegerField()
    obtained = models.DecimalField(max_digits=9, decimal_places=2)
    max_marks = models.DecimalField(max_digits=9, decimal_places=2)
    percentage = models.DecimalField(max_digits=7, decimal_places=2)
    passed = models.BooleanField()
    rank = models.PositiveIntegerField(null=True, blank=True)
    questions = models.JSONField(default=list)

    evaluated_at = models.DateTimeField(auto_now=True)
    evaluated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")

    class Meta:
        ordering = ["rank", "-obtained"]


class EvaluationRun(models.Model):
    """An explicit evaluation of a whole examination, kept as history."""

    exam = models.ForeignKey(Examination, on_delete=models.PROTECT, related_name="evaluation_runs")
    key_version = models.ForeignKey(AnswerKeyVersion, on_delete=models.PROTECT, related_name="+")
    rules = models.JSONField()
    papers_evaluated = models.PositiveIntegerField(default=0)
    skipped = models.JSONField(default=list, help_text="Papers not evaluated and why.")
    summary = models.JSONField(default=dict)
    snapshot = models.JSONField(default=list, help_text="Per-paper results at the time of this run.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
