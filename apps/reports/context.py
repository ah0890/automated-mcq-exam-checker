"""Data shared by every report, gathered once from stored records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone

from apps.exams.models import Examination
from apps.results import analytics


@dataclass
class ReportData:
    exam: Examination
    generated_at: datetime
    rows: list[dict]
    stats: dict
    questions: list[dict]
    difficulty: dict
    distribution: list[dict]
    finalized: bool
    details: list[tuple[str, str]] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return "FINAL" if self.finalized else "PROVISIONAL"


def money(v) -> str:
    """Format a Decimal without trailing zeros (42.00 → 42, 5.25 → 5.25)."""
    if v is None:
        return "—"
    s = f"{v:.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def gather(exam: Examination) -> ReportData:
    rows = analytics.class_rows(exam)
    stats = analytics.class_stats(exam, rows)
    questions = analytics.question_analysis(exam) if exam.answer_key_version_id else []
    current = [r["result"] for r in rows if r["result"]]
    key = exam.answer_key_version
    details = [
        ("Examination", exam.name),
        ("Subject", exam.subject),
        ("Class / section", exam.class_section),
        ("Examination date", exam.exam_date.strftime("%d %B %Y")),
        ("Exam ID", exam.exam_code or "—"),
        ("Questions", str(exam.num_questions)),
        ("Marks per question", money(exam.marks_per_question)),
        ("Negative marking", f"−{money(exam.negative_marks)} per wrong answer" if exam.negative_marking else "None"),
        ("Multiple marked answers", exam.get_multiple_mark_policy_display()),
        ("Maximum marks", money(exam.max_marks)),
        ("Pass mark", f"{money(exam.pass_marks)} ({money(exam.passing_percentage)}%)"),
        ("Answer key", f"{key.key.title} (version {key.number})" if key else "Not attached"),
        ("Results status", "Finalized " + timezone.localtime(exam.finalized_at).strftime("%d %b %Y %H:%M")
         if exam.is_finalized and exam.finalized_at else "Provisional — not finalized"),
    ]
    return ReportData(exam=exam, generated_at=timezone.localtime(), rows=rows, stats=stats, questions=questions,
                      difficulty=analytics.difficulty_summary(questions), distribution=analytics.distribution(current),
                      finalized=exam.is_finalized, details=details)
