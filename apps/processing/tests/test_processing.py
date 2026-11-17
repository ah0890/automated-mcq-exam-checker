import os
import shutil
import stat
import tempfile
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.answerkeys.models import AnswerKey, AnswerKeyVersion
from apps.exams.models import Examination
from apps.exams.services import create_layout
from apps.papers.models import Paper
from apps.papers.services import add_file, get_or_create_batch
from apps.processing import worker
from apps.processing.models import Correction, ProcessingJob
from omr.tests.sheetgen import draw_sheet

SAMPLES = Path(settings.BASE_DIR) / "samples"
Q = 20


def png(img) -> bytes:
    return cv2.imencode(".png", img)[1].tobytes()


def sheet(answers=None, roll="1234", fill_level=None):
    answers = answers if answers is not None else {q: [q % 4] for q in range(1, Q + 1)}
    return draw_sheet(questions=Q, options=4, blocks=2, roll_digits=4, answers=answers, roll=roll,
                      fill_level=fill_level)


@override_settings(MCQ_BACKGROUND_PROCESSING=False)
class ProcessingTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(DATA_DIR=self.tmp)
        self.override.enable()
        self.user = get_user_model().objects.create_user("teacher", password="a-strong-test-pass-91")
        self.client.force_login(self.user)
        blank = SimpleUploadedFile("blank.png", png(draw_sheet(questions=Q, options=4, blocks=2, roll_digits=4)))
        layout = create_layout(name="L", code="", option_labels="ABCD", roll_digits=4, question_order="column",
                               uploaded=blank, user=self.user)
        key = AnswerKey.objects.create(title="K")
        version = AnswerKeyVersion.objects.create(key=key, number=1, num_questions=Q, status="verified",
                                                  answers={str(q): "ABCD"[q % 4] for q in range(1, Q + 1)})
        self.exam = Examination.objects.create(name="E", subject="S", class_section="C", exam_date="2026-11-16",
                                               sheet_layout=layout, num_questions=Q, answer_key_version=version)
        self.batch = get_or_create_batch(self.exam, None, 1, self.user)

    def tearDown(self):
        self.override.disable()
        for root, _, files in os.walk(self.tmp):
            for f in files:
                os.chmod(os.path.join(root, f), stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, img, name="p.png"):
        add_file(self.exam, self.batch, SimpleUploadedFile(name, png(img)))
        return Paper.objects.filter(exam=self.exam).order_by("-sequence").first()

    def process(self):
        resp = self.client.post(reverse("processing:start", args=[self.exam.uid]))
        self.assertEqual(resp.status_code, 302)

    # --- reading -------------------------------------------------------------------------
    def test_clean_paper_is_read_and_processed(self):
        paper = self.add(sheet())
        self.process()
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.PROCESSED)
        self.assertEqual(paper.roll_number, "1234")
        self.assertEqual(paper.roll_source, "omr")
        self.assertEqual(paper.answers["1"]["value"], "B")
        self.assertEqual(paper.answer_counts["answered"], Q)
        job = ProcessingJob.objects.get()
        self.assertEqual((job.status, job.done, job.succeeded), ("done", 1, 1))

    def test_faint_mark_sends_paper_to_review(self):
        paper = self.add(sheet(fill_level={5: 185}))
        self.process()
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.NEEDS_REVIEW)
        self.assertEqual(paper.uncertain_questions, [5])
        self.assertIsNone(paper.answers["5"]["value"])

    def test_multiple_and_blank_are_recorded_without_review(self):
        answers = {q: [q % 4] for q in range(1, Q + 1)}
        answers[2], answers[3] = [], [0, 1]
        paper = self.add(sheet(answers=answers))
        self.process()
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.PROCESSED)
        self.assertEqual(paper.answers["2"]["status"], "blank")
        self.assertEqual((paper.answers["3"]["status"], paper.answers["3"]["value"]), ("multiple", "AB"))

    def test_unreadable_roll_number_needs_review(self):
        img = sheet(roll="1234")
        # Erase the third roll digit column's mark.
        img[230 - 15:230 + 34 * 10, 820 + 2 * 40 - 13:820 + 2 * 40 + 13] = 255
        for d in range(10):
            cv2.circle(img, (820 + 2 * 40, 230 + d * 34), 11, 0, 2)
        paper = self.add(img)
        self.process()
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.NEEDS_REVIEW)
        self.assertEqual(paper.roll_number, "")
        self.assertIn("roll_unreadable", [f["code"] for f in paper.review_flags])

    def test_duplicate_roll_numbers_flag_both_papers(self):
        p1 = self.add(sheet(roll="5555"), "a.png")
        p2 = self.add(sheet(roll="5555", answers={q: [(q + 1) % 4] for q in range(1, Q + 1)}), "b.png")
        self.process()
        p1.refresh_from_db()
        p2.refresh_from_db()
        for p in (p1, p2):
            self.assertEqual(p.status, Paper.Status.NEEDS_REVIEW)
            self.assertIn("duplicate_roll", [f["code"] for f in p.review_flags])

    def test_non_sheet_page_fails_cleanly_and_batch_continues(self):
        bad = self.add(np.full((1754, 1240), 255, np.uint8), "blank-page.png")
        good = self.add(sheet(), "good.png")
        self.process()
        bad.refresh_from_db()
        good.refresh_from_db()
        self.assertEqual((bad.status, bad.error_code), (Paper.Status.FAILED, "markers_not_found"))
        self.assertEqual(good.status, Paper.Status.PROCESSED)
        job = ProcessingJob.objects.get()
        self.assertEqual((job.succeeded, job.failed), (1, 1))

    # --- job behaviour ----------------------------------------------------------------------
    def test_processing_requires_verified_key(self):
        self.exam.answer_key_version = None
        self.exam.save()
        self.add(sheet())
        self.process()
        self.assertFalse(ProcessingJob.objects.exists())

    def test_processed_papers_are_not_read_twice(self):
        self.add(sheet())
        self.process()
        first = Paper.objects.get().processed_at
        self.process()  # nothing waiting → no job
        self.assertEqual(ProcessingJob.objects.count(), 1)
        self.assertEqual(Paper.objects.get().processed_at, first)

    def test_interrupted_job_resumes(self):
        p1 = self.add(sheet(roll="1111"), "a.png")
        p2 = self.add(sheet(roll="2222"), "b.png")
        job = worker.enqueue(self.exam, self.user)
        # Simulate a crash: job left RUNNING, first paper mid-processing, no heartbeat for a while.
        ProcessingJob.objects.filter(pk=job.pk).update(status="running",
                                                       heartbeat_at=timezone.now() - timedelta(minutes=10))
        Paper.objects.filter(pk=p1.pk).update(status=Paper.Status.PROCESSING)
        worker.run_pending()
        job.refresh_from_db()
        self.assertEqual(job.status, "done")
        self.assertEqual(set(Paper.objects.values_list("status", flat=True)), {Paper.Status.PROCESSED})
        self.assertEqual(sorted(Paper.objects.values_list("roll_number", flat=True)), ["1111", "2222"])

    def test_cancel_returns_unprocessed_papers(self):
        self.add(sheet(roll="1111"), "a.png")
        self.add(sheet(roll="2222"), "b.png")
        job = worker.enqueue(self.exam, self.user)
        worker.request_cancel(job)
        worker.run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(set(Paper.objects.values_list("status", flat=True)), {Paper.Status.UPLOADED})

    def test_status_endpoint(self):
        self.add(sheet())
        self.process()
        data = self.client.get(reverse("processing:status", args=[self.exam.uid])).json()
        self.assertEqual(data["job"]["status"], "done")
        self.assertEqual(data["stats"]["processed"], 1)

    # --- review ----------------------------------------------------------------------------
    def test_review_corrects_uncertain_answer_and_confirms(self):
        paper = self.add(sheet(fill_level={5: 185}))
        self.process()
        url = reverse("processing:review", args=[Paper.objects.get().uid])
        page = self.client.get(url)
        self.assertContains(page, "Q5")
        # Confirming with the uncertain answer unresolved is refused.
        resp = self.client.post(url, {"action": "confirm", "roll_number": "1234"})
        self.assertContains(resp, "still needs checking")
        resp = self.client.post(url, {"action": "confirm", "roll_number": "1234", "q_5": "B"})
        self.assertEqual(resp.status_code, 302)
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.PROCESSED)
        self.assertEqual(paper.answers["5"], {**paper.answers["5"], "value": "B", "status": "answered",
                                              "source": "teacher"})
        c = Correction.objects.get()
        self.assertEqual((c.field, c.old_value, c.new_value, c.user), ("q5", "Uncertain", "B", self.user))
        self.assertIsNotNone(paper.reviewed_at)

    def test_teacher_roll_number_resolves_unreadable_roll(self):
        img = sheet(roll="1234")
        img[230 - 15:230 + 34 * 10, 820 + 2 * 40 - 13:820 + 2 * 40 + 13] = 255
        paper = self.add(img)
        self.process()
        url = reverse("processing:review", args=[paper.uid])
        self.client.post(url, {"action": "confirm", "roll_number": "1234"})
        paper.refresh_from_db()
        self.assertEqual((paper.status, paper.roll_number, paper.roll_source),
                         (Paper.Status.PROCESSED, "1234", "teacher"))

    def test_reprocessing_keeps_teacher_corrections(self):
        paper = self.add(sheet(fill_level={5: 185}))
        self.process()
        self.client.post(reverse("processing:review", args=[paper.uid]),
                         {"action": "confirm", "roll_number": "1234", "q_5": "C", "q_6": "-"})
        self.client.post(reverse("processing:reprocess", args=[self.exam.uid]), {"papers": [str(paper.uid)]})
        paper.refresh_from_db()
        self.assertEqual(paper.answers["5"]["value"], "C")
        self.assertEqual(paper.answers["6"]["status"], "blank")
        self.assertEqual(paper.answers["6"]["source"], "teacher")
        self.assertEqual(ProcessingJob.objects.count(), 2)

    def test_crops_and_pages_render(self):
        paper = self.add(sheet(fill_level={5: 185}))
        self.process()
        for url in [reverse("processing:question_image", args=[paper.uid, 5]),
                    reverse("processing:roll_image", args=[paper.uid]),
                    reverse("processing:processed_image", args=[paper.uid])]:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
        self.assertEqual(self.client.get(reverse("processing:question_image", args=[paper.uid, 99])).status_code,
                         404)
        for url in [reverse("processing:center"), reverse("processing:queue"), self.exam.get_absolute_url(),
                    paper.get_absolute_url()]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_finalized_exam_blocks_processing_and_review(self):
        paper = self.add(sheet(fill_level={5: 185}))
        self.process()
        Examination.objects.filter(pk=self.exam.pk).update(status=Examination.Status.FINALIZED)
        self.client.post(reverse("processing:review", args=[paper.uid]),
                         {"action": "confirm", "roll_number": "9999", "q_5": "A"})
        paper.refresh_from_db()
        self.assertEqual(paper.roll_number, "1234")
        with self.assertRaises(worker.ProcessingError):
            worker.enqueue(Examination.objects.get(pk=self.exam.pk), self.user, reprocess=True)

    # --- real sample --------------------------------------------------------------------------
    def test_real_sample_paper_end_to_end(self):
        blank, student = SAMPLES / "blank_answer_sheet.pdf", SAMPLES / "student_022.pdf"
        truth_file = SAMPLES / "ground_truth" / "student_022.json"
        if not (blank.exists() and student.exists() and truth_file.exists()):
            self.skipTest("private sample papers not available")
        import json

        truth = json.loads(truth_file.read_text(encoding="utf-8"))
        layout = create_layout(name="Real", code="", option_labels="ABCD", roll_digits=5, question_order="column",
                               uploaded=SimpleUploadedFile("blank.pdf", blank.read_bytes()), user=self.user)
        key = AnswerKey.objects.create(title="Real key")
        version = AnswerKeyVersion.objects.create(key=key, number=1, num_questions=50, status="verified",
                                                  answers={str(q): "A" for q in range(1, 51)})
        exam = Examination.objects.create(name="Real", subject="S", class_section="C", exam_date="2026-11-16",
                                          sheet_layout=layout, num_questions=50, answer_key_version=version)
        add_file(exam, get_or_create_batch(exam, None, 1, self.user),
                 SimpleUploadedFile("student.pdf", student.read_bytes()))
        self.client.post(reverse("processing:start", args=[exam.uid]))
        paper = Paper.objects.get(exam=exam)
        self.assertEqual(paper.roll_number, truth["roll_number"])
        for q, expected in truth["answers"].items():
            got = paper.answers[q]
            if expected == "BLANK":
                self.assertEqual(got["status"], "blank", q)
            elif expected.startswith("MULTIPLE:"):
                self.assertEqual((got["status"], got["value"]), ("multiple", expected.split(":")[1]), q)
            elif expected.startswith("UNCERTAIN:"):
                self.assertEqual(got["status"], "uncertain", q)
            else:
                self.assertEqual((got["status"], got["value"]), ("answered", expected), q)
        self.assertEqual(paper.status, Paper.Status.NEEDS_REVIEW)
