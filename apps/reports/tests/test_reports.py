import io
import os
import stat
import zipfile
from pathlib import Path

import pymupdf
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from openpyxl import load_workbook

from apps.core.files import data_path
from apps.exams.models import Examination
from apps.papers.models import Paper, SourceFile, UploadBatch
from apps.processing.models import Correction, ProcessingJob
from apps.reports import backup
from apps.results.models import EvaluationRun, FinalizationEvent, Result
from apps.results.tests import test_analytics as ta


def pdf_text(data: bytes) -> tuple[int, str]:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return doc.page_count, "\n".join(p.get_text() for p in doc)


@override_settings(MCQ_BACKGROUND_PROCESSING=False)
class ReportTests(ta.AnalyticsTests):
    def setUp(self):
        super().setUp()
        self.build_class()  # 4 marked students (ranks 1, 2, 2, 4) + 1 paper needing review
        self.pending = Paper.objects.get(roll_number="1005")

    def get(self, name, **query):
        url = reverse(f"reports:{name}", args=[self.exam.uid])
        if query:
            url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, name)
        return resp.content

    # --- Excel ------------------------------------------------------------------------------
    def test_class_result_sheet(self):
        wb = load_workbook(io.BytesIO(self.get("class_excel")))
        self.assertEqual(wb.sheetnames, ["Class results", "Question analysis", "Student answers",
                                         "Examination details"])
        ws = wb["Class results"]
        self.assertEqual(ws["A1"].value, "Class Result Sheet")
        self.assertIn("PROVISIONAL", ws["A3"].value)
        header = [c.value for c in ws[5]][:11]
        self.assertEqual(header, ["Rank", "Roll No.", "Student Name", "Correct", "Incorrect", "Unanswered",
                                  "Multiple", "Marks", "Max", "Percentage", "Status"])
        body = [[c.value for c in row][:11] for row in ws.iter_rows(min_row=6, max_row=10)]
        self.assertEqual([(r[0], r[1], r[7], r[10]) for r in body[:4]],
                         [(1, "1001", 20.0, "Pass"), (2, "1002", 18.0, "Pass"), (2, "1003", 18.0, "Pass"),
                          (4, "1004", 16.0, "Pass")])
        self.assertEqual(body[0][2], "First Student")
        self.assertTrue(body[4][10].startswith("Pending"))
        answers = wb["Student answers"]
        self.assertEqual(answers.cell(row=6, column=4).value, "B")  # key for Q1

    def test_question_analysis_workbook(self):
        ws = load_workbook(io.BytesIO(self.get("questions_excel")))["Question analysis"]
        q1 = [c.value for c in ws[6]]
        self.assertEqual(q1[:9], [1, "B", 1, 3, 0, 0, 0.25, "D", 2])
        self.assertEqual(q1[-1], "High error rate")

    def test_processing_report_lists_issues_and_corrections(self):
        self.client.post(reverse("processing:review", args=[self.pending.uid]),
                         {"action": "save", "roll_number": "1005", "q_7": "D"})
        wb = load_workbook(io.BytesIO(self.get("errors_excel")))
        corrections = [[c.value for c in row] for row in wb["Manual corrections"].iter_rows(min_row=6)]
        self.assertEqual(corrections[0][1:6], [self.pending.sequence, "1005", "Q7", "Uncertain", "D"])

    # --- PDF -----------------------------------------------------------------------------------
    def test_result_cards_one_page_per_student(self):
        pages, text = pdf_text(self.get("cards_pdf"))
        self.assertEqual(pages, 5)
        for expected in ["Student Result Card", "First Student", "1001", "20 / 20", "PASS", "PROVISIONAL",
                         "Result pending"]:
            self.assertIn(expected, text)

    def test_single_result_card(self):
        paper = Paper.objects.get(roll_number="1004")
        pages, text = pdf_text(self.get("cards_pdf", paper=paper.uid))
        self.assertEqual(pages, 1)
        self.assertIn("1004", text)
        self.assertIn("16 / 20", text)
        self.assertNotIn("1001", text)

    def test_summary_pdf(self):
        pages, text = pdf_text(self.get("summary_pdf"))
        self.assertGreaterEqual(pages, 2)
        for expected in ["Examination Summary", "Class statistics", "Marks distribution", "Question-wise analysis",
                         "Generated", "18"]:
            self.assertIn(expected, text)

    def test_final_reports_drop_provisional_label(self):
        self.client.post(reverse("processing:review", args=[self.pending.uid]),
                         {"action": "confirm", "roll_number": "1005", "q_7": "D"})
        self.client.post(reverse("results:finalize", args=[self.exam.uid]))
        _, text = pdf_text(self.get("summary_pdf"))
        self.assertIn("Results FINAL", text)
        self.assertNotIn("PROVISIONAL", text)

    def test_reports_page(self):
        resp = self.client.get(reverse("reports:index"))
        self.assertContains(resp, reverse("reports:class_excel", args=[self.exam.uid]))
        self.assertNotContains(resp, reverse("reports:backup_full"))  # teacher is not an administrator

    # --- backup & restore --------------------------------------------------------------------------
    def counts(self):
        return {m.__name__: m.objects.filter(**f).count() for m, f in [
            (Paper, {"exam__uid": self.exam.uid}), (SourceFile, {"exam__uid": self.exam.uid}),
            (Result, {"exam__uid": self.exam.uid}), (Correction, {"paper__exam__uid": self.exam.uid}),
            (ProcessingJob, {"exam__uid": self.exam.uid})]}

    def wipe_exam(self):
        uid = self.exam.uid
        Result.objects.filter(exam__uid=uid).delete()
        EvaluationRun.objects.filter(exam__uid=uid).delete()
        FinalizationEvent.objects.filter(exam__uid=uid).delete()
        Correction.objects.filter(paper__exam__uid=uid).delete()
        ProcessingJob.objects.filter(exam__uid=uid).delete()
        Paper.objects.filter(exam__uid=uid).delete()
        SourceFile.objects.filter(exam__uid=uid).delete()
        UploadBatch.objects.filter(exam__uid=uid).delete()
        folder = data_path(self.exam.storage_dir)
        for root, _, files in os.walk(folder):
            for f in files:
                os.chmod(os.path.join(root, f), stat.S_IWRITE | stat.S_IREAD)
        import shutil

        shutil.rmtree(folder)
        Examination.objects.filter(uid=uid).delete()

    def test_backup_and_restore_round_trip(self):
        self.client.post(reverse("processing:review", args=[self.pending.uid]),
                         {"action": "confirm", "roll_number": "1005", "q_7": "D"})
        before = self.counts()
        ranks_before = sorted(Result.objects.values_list("roll_number", "obtained", "rank"))
        resp = self.client.post(reverse("reports:backup_exam", args=[self.exam.uid]))
        self.assertEqual(resp.status_code, 302)
        archive = sorted(backup.backups_dir().glob("exam-*.zip"))[-1]
        with zipfile.ZipFile(archive) as zf:
            manifest = backup.read_manifest(zf)
            self.assertEqual(len(manifest["records"]["Paper"]), 5)
            self.assertTrue(all(n.startswith("files/") or n == "manifest.json" for n in zf.namelist()))

        self.wipe_exam()
        self.assertFalse(Examination.objects.filter(uid=self.exam.uid).exists())
        restored = backup.restore_exam(str(archive))
        self.assertEqual(restored.uid, self.exam.uid)
        self.assertEqual(self.counts(), before)
        self.assertEqual(sorted(Result.objects.values_list("roll_number", "obtained", "rank")), ranks_before)
        paper = Paper.objects.get(roll_number="1001")
        self.assertTrue(data_path(paper.source.path).exists())
        self.assertFalse(os.access(data_path(paper.source.path), os.W_OK))  # originals read-only again
        self.assertEqual(self.client.get(reverse("processing:question_image", args=[paper.uid, 1])).status_code, 200)
        job = ProcessingJob.objects.filter(exam=restored).first()
        self.assertTrue(set(job.paper_ids) <= set(Paper.objects.filter(exam=restored).values_list("pk", flat=True)))

    def test_restore_refuses_existing_exam(self):
        path = backup.backup_exam(self.exam)
        with self.assertRaises(ValidationError) as ctx:
            backup.restore_exam(str(path))
        self.assertIn("already exists", " ".join(ctx.exception.messages))

    def test_restore_rejects_tampered_or_unsafe_archives(self):
        path = backup.backup_exam(self.exam)
        self.wipe_exam()
        tampered = Path(path).with_name("exam-tampered.zip")
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tampered, "w") as dst:
            first = True
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename.startswith("files/") and first:
                    data += b"x"
                    first = False
                dst.writestr(item, data)
        with self.assertRaises(ValidationError):
            backup.restore_exam(str(tampered))
        self.assertFalse(Examination.objects.filter(uid=self.exam.uid).exists())
        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as zf:
            zf.writestr("../evil.txt", "x")
        with self.assertRaises(ValidationError):
            backup.restore_exam(unsafe)

    def test_full_backup_admin_only(self):
        self.assertEqual(self.client.post(reverse("reports:backup_full")).status_code, 403)
        admin = get_user_model().objects.create_superuser("admin", password="admin-pass-12345")
        self.client.force_login(admin)
        self.client.post(reverse("reports:backup_full"))
        archive = sorted(backup.backups_dir().glob("full-backup-*.zip"))[-1]
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        self.assertIn("database.sql", names)
        self.assertTrue(any(n.startswith("data/examinations/") for n in names))
        self.assertFalse(any(n.startswith("data/backups/") for n in names))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("reports:download_backup", args=[archive.name])).status_code, 403)

    def test_backup_download_name_is_validated(self):
        self.assertEqual(self.client.get(reverse("reports:download_backup", args=["..%2Fdb.sqlite3"])).status_code,
                         404)


for _name in [n for n in dir(ta.AnalyticsTests) if n.startswith("test_")]:
    if _name not in ReportTests.__dict__:
        setattr(ReportTests, _name, None)
