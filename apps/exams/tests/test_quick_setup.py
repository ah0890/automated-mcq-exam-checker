"""Dashboard-first workflow: quick setup → confirm key → upload on the dashboard →
automatic checking → results on the dashboard."""

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.exams.models import Examination, SheetLayout
from apps.exams.tests.test_workflow import ANSWERS, blank_sheet_png, key_xlsx
from apps.papers.models import Paper
from apps.results.models import Result
from omr import load_pages
from omr.tests.sheetgen import draw_sheet

SAMPLES = Path(settings.BASE_DIR) / "samples"


def png(img) -> bytes:
    return cv2.imencode(".png", img)[1].tobytes()


@override_settings(MCQ_BACKGROUND_PROCESSING=False)
class QuickSetupFlow(TestCase):
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

    def setup_exam(self, blank: bytes, blank_name: str, key: bytes, key_name: str):
        resp = self.client.post(reverse("exams:quick_setup"), {
            "name": "Class test", "subject": "Science", "class_section": "Grade 10 - B", "exam_date": "2026-11-16",
            "option_labels": "ABCD", "marks_per_question": "1", "passing_percentage": "40",
            "negative_marks": "0", "multiple_mark_policy": "zero",
            "blank_sheet": SimpleUploadedFile(blank_name, blank), "answer_key": SimpleUploadedFile(key_name, key)})
        self.assertEqual(resp.status_code, 302, resp.context and resp.context["form"].errors)
        exam = Examination.objects.get()
        self.assertIsNone(exam.answer_key_version)  # never used before the teacher verifies it
        version_url = resp["Location"]
        version = exam.sheet_layout and __import__("apps.answerkeys.models", fromlist=["x"]).AnswerKeyVersion.objects.get()
        data = {f"q_{q}": a for q, a in version.answers.items()}
        data["action"] = "verify"
        resp = self.client.post(version_url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"?exam={exam.uid}", resp["Location"])
        exam.refresh_from_db()
        self.assertEqual(exam.answer_key_version, version)
        return exam

    def upload_and_check(self, exam, files):
        for name, data in files:
            r = self.client.post(reverse("papers:upload", args=[exam.uid]), {"files": [SimpleUploadedFile(name, data)]},
                                 HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(r.json()["results"][0]["status"], "added")
        r = self.client.post(reverse("processing:start", args=[exam.uid]), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTrue(r.json()["started"])
        return self.client.get(reverse("processing:status", args=[exam.uid])).json()

    def test_generated_class_end_to_end(self):
        exam = self.setup_exam(blank_sheet_png(), "blank.png", key_xlsx(), "key.xlsx")
        layout = SheetLayout.objects.get()
        self.assertEqual((layout.capacity, layout.roll_digits), (20, 4))  # roll grid detected automatically
        self.assertEqual(exam.num_questions, 20)  # taken from the key

        # Dashboard shows the uploader for this exam with automatic checking enabled.
        dash = self.client.get(reverse("core:dashboard") + f"?exam={exam.uid}")
        self.assertContains(dash, "data-auto-check")
        self.assertContains(dash, f"Results — {exam.name}")

        papers = []
        for i, roll in enumerate(["1001", "1002", "1003"]):
            answers = {q: ["ABCD".index(ANSWERS[q - 1])] for q in range(1, 21)}
            for q in range(1, i * 3 + 1):  # 0, 3 and 6 wrong answers
                answers[q] = [("ABCD".index(ANSWERS[q - 1]) + 1) % 4]
            papers.append((f"s{roll}.png", png(draw_sheet(questions=20, options=4, blocks=2, roll_digits=4,
                                                         answers=answers, roll=roll))))
        status = self.upload_and_check(exam, papers)
        self.assertEqual((status["job"]["status"], status["stats"]["processed"]), ("done", 3))

        dash = self.client.get(reverse("core:dashboard") + f"?exam={exam.uid}")
        rows = dash.context["focus_results"]["rows"]
        self.assertEqual([(r["roll"], r["rank"], str(r["result"].obtained)) for r in rows],
                         [("1001", 1, "20.00"), ("1002", 2, "17.00"), ("1003", 3, "14.00")])
        self.assertContains(dash, "1003")

    def test_answer_key_larger_than_sheet_is_rejected(self):
        resp = self.client.post(reverse("exams:quick_setup"), {
            "name": "X", "subject": "S", "class_section": "C", "exam_date": "2026-11-16", "option_labels": "ABCD",
            "marks_per_question": "1", "passing_percentage": "40", "negative_marks": "0",
            "multiple_mark_policy": "zero", "blank_sheet": SimpleUploadedFile("b.png", blank_sheet_png()),
            "answer_key": SimpleUploadedFile("k.xlsx", key_xlsx(ANSWERS + "ABCD"))})
        self.assertContains(resp, "only has 20")
        self.assertFalse(Examination.objects.exists())

    def test_real_samples_dashboard_flow(self):
        files = ["blank_answer_sheet.pdf", "official_answer_key.pdf", "student_022.pdf",
                 "ground_truth/student_022.json"]
        if not all((SAMPLES / f).exists() for f in files):
            self.skipTest("private sample papers not available")
        exam = self.setup_exam((SAMPLES / files[0]).read_bytes(), "blank.pdf",
                               (SAMPLES / files[1]).read_bytes(), "key.pdf")
        self.assertEqual((exam.num_questions, exam.sheet_layout.roll_digits), (50, 5))

        # A second, distinct scan of the same student's sheet stands in for another upload.
        page = load_pages(SAMPLES / files[2])[0]
        h, w = page.shape
        rotated = cv2.warpAffine(page, cv2.getRotationMatrix2D((w / 2, h / 2), 2.5, 0.97), (w, h), borderValue=255)
        doc = pymupdf.open()
        p = doc.new_page(width=595.3, height=841.9)
        p.insert_image(p.rect, stream=cv2.imencode(".jpg", rotated)[1].tobytes())
        status = self.upload_and_check(exam, [("student.pdf", (SAMPLES / files[2]).read_bytes()),
                                              ("scan.pdf", doc.tobytes())])
        self.assertEqual(status["job"]["status"], "done")
        # Both copies need review: faint Q50 (and the same roll number twice).
        self.assertEqual(status["stats"]["needs_review"], 2)
        truth = json.loads((SAMPLES / files[3]).read_text(encoding="utf-8"))
        for paper in Paper.objects.filter(exam=exam):
            self.assertEqual(paper.roll_number, truth["roll_number"])
        dash = self.client.get(reverse("core:dashboard") + f"?exam={exam.uid}")
        self.assertEqual(dash.context["focus_results"]["stats"]["needs_review"], 2)
        self.assertContains(dash, "Review 2 papers")
        self.assertFalse(Result.objects.exists())
