import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse

from apps.answerkeys.models import AnswerKeyVersion


class SheetLayout(models.Model):
    """An answer-sheet design, calibrated once from an image of the blank sheet."""

    class QuestionOrder(models.TextChoices):
        COLUMN = "column", "Down each column, then across"
        ROW = "row", "Across each row, then down"

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=120)
    code = models.CharField("Printed template ID", max_length=80, blank=True,
                            help_text="Optional identifier printed on the sheet, e.g. OMR-GKBS001-A4-50Q-V1.")
    option_labels = models.CharField(max_length=8, default="ABCD")
    roll_digits = models.PositiveSmallIntegerField(null=True, blank=True,
                                                   validators=[MinValueValidator(1), MaxValueValidator(12)])
    question_order = models.CharField(max_length=10, choices=QuestionOrder.choices, default=QuestionOrder.COLUMN)
    capacity = models.PositiveSmallIntegerField(help_text="Number of question rows detected on the sheet.")
    blank_path = models.CharField(max_length=255)
    blank_name = models.CharField(max_length=160)
    blank_sha256 = models.CharField(max_length=64)
    template_dir = models.CharField(max_length=255)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.capacity} questions, {self.option_labels})"

    def get_absolute_url(self):
        return reverse("exams:layout_detail", args=[self.uid])

    @property
    def options_per_question(self):
        return len(self.option_labels)

    def load_template(self):
        from omr import SheetTemplate

        from apps.core.files import data_path

        return SheetTemplate.load(data_path(self.template_dir))


class Examination(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        FINALIZED = "finalized", "Finalized"

    class MultipleMarkPolicy(models.TextChoices):
        ZERO = "zero", "Score zero (no negative marking)"
        INCORRECT = "incorrect", "Treat as incorrect (negative marking applies)"

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField("Examination name", max_length=150)
    subject = models.CharField(max_length=100)
    class_section = models.CharField("Class / section", max_length=60)
    exam_date = models.DateField("Examination date")
    exam_code = models.CharField("Exam ID", max_length=40, blank=True,
                                 help_text="Optional code printed on the answer sheet, e.g. GKBS-001.")

    sheet_layout = models.ForeignKey(SheetLayout, on_delete=models.PROTECT, related_name="examinations")
    num_questions = models.PositiveSmallIntegerField("Number of MCQs",
                                                     validators=[MinValueValidator(1), MaxValueValidator(500)])
    marks_per_question = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("1"),
                                             validators=[MinValueValidator(Decimal("0.01"))])
    negative_marking = models.BooleanField(default=False)
    negative_marks = models.DecimalField("Marks deducted per wrong answer", max_digits=6, decimal_places=2,
                                         default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))])
    multiple_mark_policy = models.CharField("Multiple marked answers", max_length=10,
                                            choices=MultipleMarkPolicy.choices, default=MultipleMarkPolicy.ZERO)
    passing_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("40"),
                                             validators=[MinValueValidator(Decimal("0")),
                                                         MaxValueValidator(Decimal("100"))])

    answer_key_version = models.ForeignKey(AnswerKeyVersion, null=True, blank=True, on_delete=models.PROTECT,
                                           related_name="examinations")
    key_assigned_at = models.DateTimeField(null=True, blank=True)
    key_assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="+")

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-exam_date", "-created_at"]

    def __str__(self):
        return f"{self.name} — {self.class_section}"

    def get_absolute_url(self):
        return reverse("exams:detail", args=[self.uid])

    # --- derived ----------------------------------------------------------------
    @property
    def option_labels(self):
        return self.sheet_layout.option_labels

    @property
    def max_marks(self) -> Decimal:
        return self.marks_per_question * self.num_questions

    @property
    def pass_marks(self) -> Decimal:
        return (self.max_marks * self.passing_percentage / Decimal(100)).quantize(Decimal("0.01"))

    @property
    def is_finalized(self):
        return self.status == self.Status.FINALIZED

    @property
    def storage_dir(self) -> str:
        return f"examinations/{self.uid.hex}"

    @property
    def stage(self) -> str:
        """Where the examination is in the workflow (drives the next action)."""
        if self.is_finalized:
            return "finalized"
        if not self.answer_key_version_id:
            return "needs_key"
        return "ready_for_papers"

    @property
    def newer_key_available(self):
        if not self.answer_key_version_id:
            return None
        latest = self.answer_key_version.key.latest_verified
        return latest if latest and latest.number > self.answer_key_version.number else None

    # --- validation ---------------------------------------------------------------
    def clean(self):
        errors = {}
        if self.sheet_layout_id and self.num_questions and self.num_questions > self.sheet_layout.capacity:
            errors["num_questions"] = (f"The selected answer sheet has {self.sheet_layout.capacity} questions; "
                                       f"this examination cannot have more.")
        if self.negative_marking and (self.negative_marks is None or self.negative_marks <= 0):
            errors["negative_marks"] = "Enter the marks deducted per wrong answer, or turn negative marking off."
        if not self.negative_marking:
            self.negative_marks = Decimal("0")
        if errors:
            raise ValidationError(errors)

    def check_key_compatible(self, version: AnswerKeyVersion) -> None:
        if not version.is_verified:
            raise ValidationError("Only a verified answer key can be used for an examination.")
        if version.num_questions != self.num_questions:
            raise ValidationError(f"The key has {version.num_questions} questions but the examination has "
                                  f"{self.num_questions}.")
        used = set(version.answers.values())
        if not used <= set(self.option_labels):
            raise ValidationError(f"The key uses options outside {self.option_labels}.")
