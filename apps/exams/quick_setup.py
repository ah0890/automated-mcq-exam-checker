"""One-step examination setup from the dashboard.

The teacher supplies the blank answer sheet, the official answer key and a few
details. The sheet is calibrated (roll-number grid detected automatically),
the key is extracted, and the examination is created with the number of
questions found in the key. The key still has to be verified by the teacher
before it is attached — results are never produced from an unverified key.
"""

from __future__ import annotations

import hashlib

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.answerkeys import services as key_services
from omr.template import AUTO_ROLL

from .models import Examination, SheetLayout
from .services import create_layout


def _sha(uploaded) -> str:
    h = hashlib.sha256()
    for chunk in uploaded.chunks():
        h.update(chunk)
    uploaded.seek(0)
    return h.hexdigest()


@transaction.atomic
def quick_setup(*, name, subject, class_section, exam_date, marks_per_question, negative_marking, negative_marks,
                passing_percentage, multiple_mark_policy, option_labels, blank_sheet, answer_key, user):
    """Returns (examination, draft answer-key version)."""
    # Reuse a layout calibrated from the very same blank sheet.
    layout = SheetLayout.objects.filter(blank_sha256=_sha(blank_sheet), option_labels=option_labels).first()
    if layout is None:
        layout = create_layout(name=f"{name} — answer sheet", code="", option_labels=option_labels,
                               roll_digits=AUTO_ROLL, question_order="column", uploaded=blank_sheet, user=user)

    version = key_services.create_from_upload(
        title=f"{name} — answer key", subject=subject, num_questions=None, option_labels=option_labels,
        uploaded=answer_key, user=user)
    n = version.num_questions
    if n > layout.capacity:
        raise ValidationError(f"The answer key has {n} questions but the answer sheet only has {layout.capacity}. "
                              "Check that the right files were chosen.")

    exam = Examination(name=name, subject=subject, class_section=class_section, exam_date=exam_date,
                       sheet_layout=layout, num_questions=n, marks_per_question=marks_per_question,
                       negative_marking=negative_marking, negative_marks=negative_marks or 0,
                       multiple_mark_policy=multiple_mark_policy, passing_percentage=passing_percentage,
                       created_by=user)
    exam.full_clean()
    exam.save()
    return exam, version
