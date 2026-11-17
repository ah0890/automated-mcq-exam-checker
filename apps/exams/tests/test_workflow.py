"""End-to-end Milestone 2 workflow through the web interface:
sheet layout → answer key upload → verification → examination → key attachment."""

import io
import json
import os
import shutil
import stat
import tempfile
from decimal import Decimal

import cv2
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.answerkeys import services as key_services
from apps.answerkeys.models import AnswerKey, AnswerKeyVersion
from apps.exams.models import Examination, SheetLayout
from apps.students.models import Student
from omr.tests.sheetgen import draw_sheet

ANSWERS = "ABCDDCBAAB" * 2  # 20 questions


def blank_sheet_png() -> bytes:
    img = draw_sheet(questions=20, options=4, blocks=2, roll_digits=4)
    return cv2.imencode(".png", img)[1].tobytes()


def key_xlsx(answers=ANSWERS) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Question", "Answer"])
    for i, a in enumerate(answers, start=1):
        ws.append([f"Q{i}", a])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class WorkflowBase(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(DATA_DIR=self.tmp)
        self.override.enable()
        self.user = get_user_model().objects.create_user("teacher", password="a-strong-test-pass-91")
        self.client.force_login(self.user)

    def tearDown(self):
        self.override.disable()
        for root, _, files in os.walk(self.tmp):
            for f in files:
                os.chmod(os.path.join(root, f), stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def create_layout(self):
        resp = self.client.post(reverse("exams:layout_create"), {
            "name": "Test sheet", "code": "T-1", "option_labels": "ABCD", "roll_digits": 4,
            "question_order": "column", "blank_sheet": SimpleUploadedFile("blank.png", blank_sheet_png()),
        })
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["form"].errors)
        return SheetLayout.objects.get()

    def upload_key(self, answers=ANSWERS):
        resp = self.client.post(reverse("answerkeys:upload"), {
            "title": "Test key", "subject": "Science", "option_labels": "ABCD", "num_questions": len(answers),
            "document": SimpleUploadedFile("key.xlsx", key_xlsx(answers)),
        })
        self.assertEqual(resp.status_code, 302)
        return AnswerKeyVersion.objects.latest("created_at")

    def verify_key(self, version, answers=ANSWERS):
        data = {f"q_{i}": a for i, a in enumerate(answers, start=1)}
        data["action"] = "verify"
        return self.client.post(reverse("answerkeys:verify", args=[version.uid]), data)

    def create_exam(self, layout, n=20, **extra):
        data = {"name": "Unit test", "subject": "Science", "class_section": "Grade 10 - B", "exam_date": "2026-11-16",
                "sheet_layout": layout.pk, "num_questions": n, "marks_per_question": "1",
                "negative_marks": "0", "multiple_mark_policy": "zero", "passing_percentage": "40"}
        data.update(extra)
        return self.client.post(reverse("exams:create"), data)


class SheetLayouts(WorkflowBase):
    def test_blank_sheet_is_calibrated(self):
        layout = self.create_layout()
        self.assertEqual(layout.capacity, 20)
        self.assertEqual(layout.roll_digits, 4)
        self.assertEqual(layout.load_template().capacity, 20)
        self.assertEqual(self.client.get(reverse("exams:layout_preview", args=[layout.uid])).status_code, 200)

    def test_wrong_description_is_reported(self):
        resp = self.client.post(reverse("exams:layout_create"), {
            "name": "Wrong", "option_labels": "ABCDE", "roll_digits": 4, "question_order": "column",
            "blank_sheet": SimpleUploadedFile("blank.png", blank_sheet_png())})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SheetLayout.objects.exists())
        self.assertContains(resp, "could not be calibrated")

    def test_exam_creation_requires_a_layout(self):
        resp = self.client.get(reverse("exams:create"))
        self.assertRedirects(resp, reverse("exams:layout_create"))


class AnswerKeyLifecycle(WorkflowBase):
    def test_upload_extract_verify_export(self):
        version = self.upload_key()
        self.assertEqual(version.status, AnswerKeyVersion.Status.DRAFT)
        self.assertEqual(version.extraction["found"], 20)
        page = self.client.get(reverse("answerkeys:verify", args=[version.uid]))
        self.assertContains(page, "Verify answer key")

        self.assertEqual(self.verify_key(version).status_code, 302)
        version.refresh_from_db()
        self.assertTrue(version.is_verified)
        self.assertEqual("".join(a for _, a in version.ordered_answers()), ANSWERS)

        exported = json.loads(self.client.get(reverse("answerkeys:export", args=[version.uid, "json"])).content)
        self.assertEqual(exported["answers"]["1"], "A")
        xlsx = self.client.get(reverse("answerkeys:export", args=[version.uid, "xlsx"]))
        self.assertEqual(xlsx.status_code, 200)

    def test_incomplete_key_cannot_be_verified(self):
        version = self.upload_key()
        data = {f"q_{i}": a for i, a in enumerate(ANSWERS[:-1], start=1)}
        data["action"] = "verify"
        resp = self.client.post(reverse("answerkeys:verify", args=[version.uid]), data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Question 20 has no answer")
        version.refresh_from_db()
        self.assertFalse(version.is_verified)

    def test_verified_version_is_immutable(self):
        version = self.upload_key()
        self.verify_key(version)
        version.refresh_from_db()
        version.answers["1"] = "D"
        with self.assertRaises(ValidationError):
            version.save()
        with self.assertRaises(ValidationError):
            version.delete()

    def test_revision_creates_new_version_and_keeps_old(self):
        v1 = self.upload_key()
        self.verify_key(v1)
        self.client.post(reverse("answerkeys:revise", args=[v1.uid]))
        v2 = AnswerKeyVersion.objects.get(key=v1.key, number=2)
        changed = "D" + ANSWERS[1:]
        self.verify_key(v2, changed)
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual(v1.answers["1"], "A")
        self.assertEqual(v2.answers["1"], "D")
        self.assertEqual(v1.key.latest_verified, v2)

    def test_discard_draft_removes_key(self):
        version = self.upload_key()
        self.client.post(reverse("answerkeys:verify", args=[version.uid]), {"action": "discard"})
        self.assertFalse(AnswerKey.objects.exists())

    def test_ocr_keys_need_explicit_comparison(self):
        version = self.upload_key()
        AnswerKeyVersion.objects.filter(pk=version.pk).update(source_kind="png")
        data = {f"q_{i}": a for i, a in enumerate(ANSWERS, start=1)}
        data["action"] = "verify"
        resp = self.client.post(reverse("answerkeys:verify", args=[version.uid]), data)
        self.assertContains(resp, "Confirm that you compared every answer")
        data["confirm_compared"] = "1"
        self.assertEqual(self.client.post(reverse("answerkeys:verify", args=[version.uid]), data).status_code, 302)


class Examinations(WorkflowBase):
    def test_full_setup_flow(self):
        layout = self.create_layout()
        version = self.upload_key()
        self.verify_key(version)
        resp = self.create_exam(layout)
        exam = Examination.objects.get()
        self.assertRedirects(resp, exam.get_absolute_url())
        self.assertEqual(exam.stage, "needs_key")
        self.assertEqual(exam.max_marks, Decimal("20"))
        self.assertEqual(exam.pass_marks, Decimal("8.00"))

        resp = self.client.post(reverse("exams:assign_key", args=[exam.uid]), {"version": version.pk})
        exam.refresh_from_db()
        self.assertEqual(exam.answer_key_version, version)
        self.assertEqual(exam.stage, "ready_for_papers")
        dashboard = self.client.get(reverse("core:dashboard"))
        self.assertContains(dashboard, "Unit test")
        self.assertEqual(dashboard.context["counts"]["ready"], 1)

    def test_question_count_cannot_exceed_sheet(self):
        layout = self.create_layout()
        resp = self.create_exam(layout, n=21)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Examination.objects.exists())

    def test_negative_marking_requires_value(self):
        layout = self.create_layout()
        resp = self.create_exam(layout, negative_marking="on", negative_marks="0")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Examination.objects.exists())

    def test_draft_or_mismatched_key_cannot_be_attached(self):
        layout = self.create_layout()
        self.create_exam(layout)
        exam = Examination.objects.get()
        draft = self.upload_key()
        self.client.post(reverse("exams:assign_key", args=[exam.uid]), {"version": draft.pk})
        exam.refresh_from_db()
        self.assertIsNone(exam.answer_key_version)

        short = self.upload_key(ANSWERS[:10])
        self.verify_key(short, ANSWERS[:10])
        self.client.post(reverse("exams:assign_key", args=[exam.uid]), {"version": short.pk})
        exam.refresh_from_db()
        self.assertIsNone(exam.answer_key_version)

    def test_key_in_use_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        layout = self.create_layout()
        version = self.upload_key()
        self.verify_key(version)
        self.create_exam(layout)
        exam = Examination.objects.get()
        self.client.post(reverse("exams:assign_key", args=[exam.uid]), {"version": version.pk})
        with self.assertRaises((ProtectedError, ValidationError)):
            version.key.delete()


class Roster(WorkflowBase):
    def test_csv_import_creates_and_updates(self):
        csv = b"Roll No,Name\n10001,First Student\n10002,Second Student\n10002,Dup\n,Missing\n"
        resp = self.client.post(reverse("students:import"), {
            "class_section": "Grade 10 - B", "update_existing": "on",
            "roster": SimpleUploadedFile("list.csv", csv)})
        self.assertEqual(resp.status_code, 200)
        report = resp.context["report"]
        self.assertEqual((report.created, len(report.errors)), (2, 2))
        csv2 = b"Roll No,Name\n10001,First Student Renamed\n"
        self.client.post(reverse("students:import"), {
            "class_section": "Grade 10 - B", "update_existing": "on", "roster": SimpleUploadedFile("l.csv", csv2)})
        self.assertEqual(Student.objects.get(roll_number="10001").name, "First Student Renamed")

    def test_missing_headers_rejected(self):
        resp = self.client.post(reverse("students:import"), {
            "class_section": "X", "roster": SimpleUploadedFile("l.csv", b"a,b\n1,2\n")})
        self.assertContains(resp, "Could not find header columns")


class PagesRender(WorkflowBase):
    def test_all_pages_render(self):
        layout = self.create_layout()
        version = self.upload_key()
        self.verify_key(version)
        self.create_exam(layout)
        exam = Examination.objects.get()
        for url in [reverse("core:dashboard"), reverse("exams:list"), reverse("exams:create"),
                    reverse("exams:layouts"), layout.get_absolute_url(), exam.get_absolute_url(),
                    reverse("exams:edit", args=[exam.uid]), reverse("answerkeys:library"),
                    reverse("answerkeys:upload"), reverse("answerkeys:manual"), version.key.get_absolute_url(),
                    reverse("students:list"), reverse("students:create"), reverse("students:import")]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_files_require_login(self):
        layout = self.create_layout()
        self.client.logout()
        resp = self.client.get(reverse("exams:layout_preview", args=[layout.uid]))
        self.assertEqual(resp.status_code, 302)
