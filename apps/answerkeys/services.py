import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.files import CSV, DOCX, JSON, JPEG, PDF, PNG, XLSX, discard, store_upload

from .extraction import extract
from .models import AnswerKey, AnswerKeyVersion

log = logging.getLogger("apps.answerkeys")

KEY_FORMATS = {PDF, PNG, JPEG, XLSX, DOCX, JSON, CSV}


def _next_number(key: AnswerKey) -> int:
    last = key.versions.order_by("-number").values_list("number", flat=True).first()
    return (last or 0) + 1


@transaction.atomic
def create_from_upload(*, title, subject, num_questions, option_labels, uploaded, user) -> AnswerKeyVersion:
    key = AnswerKey.objects.create(title=title, subject=subject, created_by=user)
    stored = store_upload(uploaded, f"answer_key_library/{key.uid.hex}", KEY_FORMATS)
    try:
        result = extract(stored.path.read_bytes(), stored.kind, option_labels, expected=num_questions,
                         tesseract_cmd=settings.TESSERACT_CMD)
    except Exception:
        discard(stored)
        log.exception("Answer-key extraction failed for key %s", key.uid)
        raise ValidationError("The document could not be read. Check that it is a valid answer key, "
                              "or enter the answers manually.")
    total = num_questions or result.suggested_total or (max(result.answers) if result.answers else 0)
    if not total:
        discard(stored)
        raise ValidationError("No answers were found and the number of questions is unknown. "
                              "Enter the number of questions and try again, or enter the key manually.")
    answers = {str(q): a for q, a in result.answers.items() if 1 <= q <= total}
    version = AnswerKeyVersion.objects.create(
        key=key, number=1, num_questions=total, option_labels=option_labels, answers=answers,
        source=AnswerKeyVersion.Source.UPLOAD, source_path=stored.relative_path, source_name=stored.original_name,
        source_kind=stored.kind, source_sha256=stored.sha256, created_by=user,
        extraction={
            "method": result.method,
            "warnings": result.warnings,
            "conflicts": {str(q): v for q, v in result.conflicts.items()},
            "unconfirmed": result.details.get("unconfirmed", []),
            "found": len(answers),
            "suggested_total": result.suggested_total,
            "extracted": answers,
        },
    )
    log.info("Answer key %s created from %s (%s answers found)", key.uid, stored.kind, len(answers))
    return version


@transaction.atomic
def create_manual(*, title, subject, num_questions, option_labels, user) -> AnswerKeyVersion:
    key = AnswerKey.objects.create(title=title, subject=subject, created_by=user)
    return AnswerKeyVersion.objects.create(key=key, number=1, num_questions=num_questions,
                                           option_labels=option_labels, answers={},
                                           source=AnswerKeyVersion.Source.MANUAL, created_by=user)


@transaction.atomic
def start_revision(version: AnswerKeyVersion, user) -> AnswerKeyVersion:
    """Open a new draft based on a verified version (the original stays unchanged)."""
    key = AnswerKey.objects.select_for_update().get(pk=version.key_id)
    draft = key.open_draft
    if draft:
        return draft
    return AnswerKeyVersion.objects.create(
        key=key, number=_next_number(key), num_questions=version.num_questions,
        option_labels=version.option_labels, answers=dict(version.answers),
        source=AnswerKeyVersion.Source.REVISION, source_path=version.source_path, source_name=version.source_name,
        source_kind=version.source_kind, source_sha256=version.source_sha256, created_by=user,
        extraction={"based_on": version.number},
    )


@transaction.atomic
def verify(version: AnswerKeyVersion, answers: dict[str, str], user, note: str = "") -> AnswerKeyVersion:
    version = AnswerKeyVersion.objects.select_for_update().get(pk=version.pk)
    if version.is_verified:
        raise ValidationError("This version is already verified.")
    version.answers = {str(q): a for q, a in answers.items() if a}
    version.validate_complete()
    version.status = AnswerKeyVersion.Status.VERIFIED
    version.verified_by = user
    version.verified_at = timezone.now()
    version.change_note = note[:255]
    version.save()
    AnswerKey.objects.filter(pk=version.key_id).update(updated_at=timezone.now())
    log.info("Answer key %s version %s verified", version.key.uid, version.number)
    return version


def save_draft(version: AnswerKeyVersion, answers: dict[str, str]) -> None:
    if version.is_verified:
        raise ValidationError("This version is already verified.")
    version.answers = {str(q): a for q, a in answers.items() if a}
    version.save(update_fields=["answers"])


def discard_draft(version: AnswerKeyVersion) -> None:
    key = version.key
    version.delete()
    if not key.versions.exists():
        key.delete()
