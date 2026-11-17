"""Acceptance test: the 16-step real-world workflow from the project brief,
driven entirely through the web interface with the user's real sample files
(samples/, git-ignored). Skipped when the private samples are not present."""

import io
import json
import os
import shutil
import stat
import tempfile
from decimal import Decimal as D
from pathlib import Path

import pymupdf
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

SAMPLES = Path(settings.BASE_DIR) / "samples"
FILES = ["blank_answer_sheet.pdf", "official_answer_key.pdf", "student_022.pdf",
         "ground_truth/official_answer_key.json", "ground_truth/student_022.json"]


@override_settings(MCQ_BACKGROUND_PROCESSING=False)
class RealWorldWorkflow(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not all((SAMPLES / f).exists() for f in FILES):
            raise __import__("unittest").SkipTest("private sample papers not available")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(DATA_DIR=self.tmp)
        self.override.enable()
        get_user_model().objects.create_superuser("teacher", password="a-strong-test-pass-91")

    def tearDown(self):
        self.override.disable()
        for root, _, files in os.walk(self.tmp):
            for f in files:
                os.chmod(os.path.join(root, f), stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def upload(self, name):
        return SimpleUploadedFile(Path(name).name, (SAMPLES / name).read_bytes())

    def test_sixteen_step_workflow(self):
        from apps.answerkeys.models import AnswerKeyVersion
        from apps.exams.models import Examination, SheetLayout
        from apps.papers.models import Paper

        key_truth = json.loads((SAMPLES / FILES[3]).read_text(encoding="utf-8"))["answers"]
        paper_truth = json.loads((SAMPLES / FILES[4]).read_text(encoding="utf-8"))

        # 1. Open the application and sign in.
        resp = self.client.post(reverse("login"), {"username": "teacher", "password": "a-strong-test-pass-91"})
        self.assertRedirects(resp, reverse("core:dashboard"))

        # (Set-up once per sheet design) calibrate the blank answer sheet.
        self.client.post(reverse("exams:layout_create"), {
            "name": "GKBS A4 50", "option_labels": "ABCD", "roll_digits": 5, "question_order": "column",
            "blank_sheet": self.upload(FILES[0])})
        layout = SheetLayout.objects.get()
        self.assertEqual(layout.capacity, 50)

        # 2. Create a new examination.
        self.client.post(reverse("exams:create"), {
            "name": "General Knowledge and Basic Science Test", "subject": "General Science",
            "class_section": "Grade 10 - B", "exam_date": "2026-11-16", "exam_code": "GKBS-001",
            "sheet_layout": layout.pk, "num_questions": 50, "marks_per_question": "1", "negative_marks": "0",
            "multiple_mark_policy": "zero", "passing_percentage": "40"})
        exam = Examination.objects.get()

        # 3. Upload the actual answer-key document.
        self.client.post(reverse("answerkeys:upload"), {
            "title": "GKBS-001 official key", "subject": "General Science", "option_labels": "ABCD",
            "num_questions": "", "document": self.upload(FILES[1])})
        version = AnswerKeyVersion.objects.get()
        # 4. Verify the extracted key (it must match the manually read key exactly).
        self.assertEqual(version.answers, key_truth)
        page = self.client.get(reverse("answerkeys:verify", args=[version.uid]))
        self.assertContains(page, "Verify answer key")
        # 5. Save the verified key and attach it to the examination.
        data = {f"q_{q}": a for q, a in version.answers.items()}
        data["action"] = "verify"
        self.client.post(reverse("answerkeys:verify", args=[version.uid]), data)
        self.client.post(reverse("exams:assign_key", args=[exam.uid]), {"version": version.pk})
        exam.refresh_from_db()
        self.assertEqual(exam.stage, "ready_for_papers")

        # 6. Open "Upload Student Papers".
        self.assertContains(self.client.get(reverse("papers:upload_page")), "Upload student papers")
        # 7–8. Upload the class's papers into the selected examination.
        resp = self.client.post(reverse("papers:upload", args=[exam.uid]), {"files": [self.upload(FILES[2])]},
                                HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.json()["results"][0]["papers_added"], 1)

        # 9–10. Start automatic processing; the system reads the responses.
        self.client.post(reverse("processing:start", args=[exam.uid]))
        paper = Paper.objects.get()
        self.assertEqual(paper.roll_number, paper_truth["roll_number"])
        self.assertEqual(paper.status, Paper.Status.NEEDS_REVIEW)  # the faint Q50 mark is not guessed

        # 11. Review the paper with uncertain recognition.
        self.assertEqual(paper.uncertain_questions, [50])
        self.client.post(reverse("processing:review", args=[paper.uid]),
                         {"action": "confirm", "roll_number": paper.roll_number, "q_50": "B"})
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.PROCESSED)

        # 12. Finalize the evaluation.
        self.client.post(reverse("results:finalize", args=[exam.uid]))
        exam.refresh_from_db()
        self.assertTrue(exam.is_finalized)

        # 13–14. Class results dashboard with accurate individual and class results.
        resp = self.client.get(reverse("results:class", args=[exam.uid]))
        roll = paper_truth["roll_number"]  # from the private ground truth, never hard-coded
        self.assertContains(resp, roll)
        result = paper.result
        self.assertEqual((result.correct, result.incorrect, result.unanswered, result.multiple),
                         (42, 5, 2, 1))
        self.assertEqual((result.obtained, result.percentage, result.passed, result.rank),
                         (D("42.00"), D("84.00"), True, 1))

        # 15. Export the complete result sheet to Excel.
        xlsx = self.client.get(reverse("reports:class_excel", args=[exam.uid]))
        ws = load_workbook(io.BytesIO(xlsx.content))["Class results"]
        self.assertEqual([c.value for c in ws[6]][:11], [1, roll, None, 42, 5, 2, 1, 42, 50, 0.84, "Pass"])
        self.assertIn("FINAL", ws["A3"].value)

        # 16. Generate printable individual result cards.
        pdf = self.client.get(reverse("reports:cards_pdf", args=[exam.uid]))
        with pymupdf.open(stream=pdf.content, filetype="pdf") as doc:
            text = doc[0].get_text()
            self.assertEqual(doc.page_count, 1)
        for expected in ("Student Result Card", roll, "42 / 50", "84%", "PASS", "Results FINAL"):
            self.assertIn(expected, text)
