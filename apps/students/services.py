"""Class roster import from Excel or CSV."""

import csv
import io
import re
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.files import CSV, XLSX, sniff_kind

from .models import Student, roll_validator

ROLL_HEADERS = re.compile(r"^(roll|roll\s*no\.?|roll\s*number|reg(istration)?\s*(no\.?|number)?|student\s*id|id)$", re.I)
NAME_HEADERS = re.compile(r"^(name|student\s*name|full\s*name)$", re.I)
CLASS_HEADERS = re.compile(r"^(class|section|class\s*/\s*section|class\s*section|grade)$", re.I)


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)


def _rows(data: bytes, name: str) -> list[list[str]]:
    kind = sniff_kind(data[:16], name)
    if kind == "zip" or name.lower().endswith(".xlsx"):
        from openpyxl import load_workbook

        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise ValidationError("The Excel file could not be opened.") from exc
        ws = wb.worksheets[0]
        rows = [["" if v is None else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)).strip()
                 for v in row] for row in ws.iter_rows(values_only=True)]
        wb.close()
        return rows
    if kind == CSV or name.lower().endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("CSV files must be UTF-8 encoded.") from exc
        return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text))]
    raise ValidationError("Upload an Excel (.xlsx) or CSV file.")


@transaction.atomic
def import_roster(uploaded, default_class: str, update_existing: bool) -> ImportReport:
    if uploaded.size > 5 * 1024 * 1024:
        raise ValidationError("Class lists are limited to 5 MB.")
    rows = _rows(b"".join(uploaded.chunks()), uploaded.name)
    header_idx = next((i for i, r in enumerate(rows[:20])
                       if any(ROLL_HEADERS.match(c) for c in r) and any(NAME_HEADERS.match(c) for c in r)), None)
    if header_idx is None:
        raise ValidationError("Could not find header columns for roll number and name "
                              "(e.g. “Roll No” and “Name”).")
    header = rows[header_idx]
    roll_col = next(i for i, c in enumerate(header) if ROLL_HEADERS.match(c))
    name_col = next(i for i, c in enumerate(header) if NAME_HEADERS.match(c))
    class_col = next((i for i, c in enumerate(header) if CLASS_HEADERS.match(c)), None)

    report = ImportReport()
    seen = set()
    for line_no, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not any(row):
            continue
        cell = lambda i: row[i].strip() if i is not None and i < len(row) else ""  # noqa: E731
        roll, name = cell(roll_col), re.sub(r"\s+", " ", cell(name_col))
        class_section = cell(class_col) or default_class
        if not roll or not name:
            report.errors.append(f"Row {line_no}: roll number and name are required.")
            continue
        try:
            roll_validator(roll)
        except ValidationError:
            report.errors.append(f"Row {line_no}: roll number “{roll}” has invalid characters.")
            continue
        if (class_section, roll) in seen:
            report.errors.append(f"Row {line_no}: roll number {roll} appears more than once.")
            continue
        seen.add((class_section, roll))
        student, created = Student.objects.get_or_create(
            class_section=class_section[:60], roll_number=roll, defaults={"name": name[:150]})
        if created:
            report.created += 1
        elif update_existing and student.name != name[:150]:
            student.name = name[:150]
            student.save(update_fields=["name", "updated_at"])
            report.updated += 1
        else:
            report.unchanged += 1
    return report
