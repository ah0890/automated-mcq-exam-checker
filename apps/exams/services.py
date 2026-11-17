import logging
import uuid

import cv2
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.files import JPEG, PDF, PNG, data_path, discard, store_upload
from omr import LayoutSpec, OMRError, build_template, load_pages

from .models import Examination, SheetLayout

log = logging.getLogger("apps.exams")


@transaction.atomic
def create_layout(*, name, code, option_labels, roll_digits, question_order, uploaded, user) -> SheetLayout:
    uid = uuid.uuid4()
    folder = f"sheet_layouts/{uid.hex}"
    stored = store_upload(uploaded, folder, {PDF, PNG, JPEG})
    try:
        page = load_pages(stored.path)[0]
        spec = LayoutSpec(options_per_question=len(option_labels), option_labels=option_labels,
                          roll_digits=roll_digits or None, question_order=question_order)
        template = build_template(page, spec)
    except OMRError as exc:
        discard(stored)
        raise ValidationError(f"The blank sheet could not be calibrated: {exc.message}")
    template_dir = f"{folder}/template"
    template.save(data_path(template_dir))
    _write_preview(template, data_path(f"{folder}/preview.png"))
    layout = SheetLayout.objects.create(
        uid=uid, name=name, code=code, option_labels=option_labels, roll_digits=roll_digits or None,
        question_order=question_order, capacity=template.capacity, blank_path=stored.relative_path,
        blank_name=stored.original_name, blank_sha256=stored.sha256, template_dir=template_dir, created_by=user,
    )
    log.info("Sheet layout %s calibrated: %s questions, %s roll digits", uid, template.capacity,
             len(template.roll_columns))
    return layout


def _write_preview(template, path):
    """Draw detected bubbles on the blank sheet so the teacher can confirm calibration."""
    img = cv2.cvtColor(template.reference, cv2.COLOR_GRAY2BGR)
    r = int(template.answer_radius)
    blue, orange = (235, 99, 37), (11, 144, 247)
    for q, row in enumerate(template.questions, start=1):
        for x, y in row:
            cv2.circle(img, (int(x), int(y)), r + 2, blue, 3)
        # Reading-order number to the right of the row (the sheet prints its own on the left).
        xl, yl = row[-1]
        cv2.putText(img, str(q), (int(xl + r * 1.7), int(yl + r * 0.45)), cv2.FONT_HERSHEY_SIMPLEX,
                    r / 24, blue, 2, cv2.LINE_AA)
    if template.roll_columns:
        rr = int(template.roll_radius)
        for col in template.roll_columns:
            for x, y in col:
                cv2.circle(img, (int(x), int(y)), rr + 2, orange, 3)
    for x, y in template.frame_markers:
        cv2.rectangle(img, (int(x - 40), int(y - 40)), (int(x + 40), int(y + 40)), (94, 183, 18), 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img, [cv2.IMWRITE_PNG_COMPRESSION, 6])


@transaction.atomic
def assign_key(exam: Examination, version, user) -> None:
    if exam.is_finalized:
        raise ValidationError("This examination is finalized; its answer key cannot be changed.")
    exam.check_key_compatible(version)
    exam.answer_key_version = version
    exam.key_assigned_at = timezone.now()
    exam.key_assigned_by = user
    exam.save(update_fields=["answer_key_version", "key_assigned_at", "key_assigned_by", "updated_at"])
    from apps.results.services import update_ranks

    update_ranks(exam)  # results from the previous key drop out of the ranking until re-evaluated
    log.info("Examination %s uses answer key %s v%s", exam.uid, version.key.uid, version.number)
