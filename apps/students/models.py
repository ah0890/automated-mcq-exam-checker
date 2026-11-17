import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

roll_validator = RegexValidator(r"^[0-9A-Za-z\-/]{1,20}$", "Use letters, digits, '-' or '/' (max 20).")


class Student(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    roll_number = models.CharField(max_length=20, validators=[roll_validator])
    name = models.CharField(max_length=150)
    class_section = models.CharField("Class / section", max_length=60)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["class_section", "roll_number"]
        constraints = [
            models.UniqueConstraint(fields=["class_section", "roll_number"], name="unique_roll_per_class"),
        ]

    def __str__(self):
        return f"{self.roll_number} — {self.name}"
