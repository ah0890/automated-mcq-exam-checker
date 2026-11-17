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

from apps.answerkeys.models import AnswerKey, AnswerKeyVersion
from apps.core.files import data_path
from apps.exams.models import Examination, SheetLayout
from apps.papers.models import Paper, SourceFile

SAMPLES = Path(settings.BASE_DIR) / "samples"


def pdf_pages(labels) -> bytes:
    """A PDF whose pages differ visibly (so their fingerprints differ)."""
    doc = pymupdf.open()
    for label in labels:
        page = doc.new_page()
        page.insert_text((72, 120), f"Answer sheet {label}", fontsize=36)
        page.draw_rect(pymupdf.Rect(72, 200, 72 + 20 * (hash(label) % 20 + 1), 260), fill=(0, 0, 0))
    return doc.tobytes()


def png_page(label: str) -> bytes:
    img = np.full((1100, 800), 255, np.uint8)
    cv2.putText(img, label, (60, 200), cv2.FONT_HERSHEY_SIMPLEX, 3, 0, 6)
    return cv2.imencode(".png", img)[1].tobytes()


class UploadTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(DATA_DIR=self.tmp)
        self.override.enable()
        self.user = get_user_model().objects.create_user("teacher", password="a-strong-test-pass-91")
        self.client.force_login(self.user)
        layout = SheetLayout.objects.create(name="L", option_labels="ABCD", capacity=50, blank_path="x",
                                            blank_name="x", blank_sha256="0" * 64, template_dir="x")
        key = AnswerKey.objects.create(title="K")
        version = AnswerKeyVersion.objects.create(key=key, number=1, num_questions=10,
                                                  answers={str(i): "A" for i in range(1, 11)})
        self.exam = Examination.objects.create(name="E", subject="S", class_section="C", exam_date="2026-11-16",
                                               sheet_layout=layout, num_questions=10)
        self.version = version

    def tearDown(self):
        self.override.disable()
        for root, _, files in os.walk(self.tmp):
            for f in files:
                os.chmod(os.path.join(root, f), stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def upload(self, *files, ppp=1, ajax=True, exam=None):
        exam = exam or self.exam
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}
        return self.client.post(reverse("papers:upload", args=[exam.uid]),
                                {"files": [SimpleUploadedFile(n, d) for n, d in files], "pages_per_paper": ppp},
                                **headers)

    def test_pdf_with_whole_class_is_split_one_paper_per_page(self):
        resp = self.upload(("class.pdf", pdf_pages(["a", "b", "c"])))
        result = resp.json()["results"][0]
        self.assertEqual((result["status"], result["papers_added"]), ("added", 3))
        papers = list(Paper.objects.filter(exam=self.exam).order_by("sequence"))
        self.assertEqual([(p.sequence, p.first_page) for p in papers], [(1, 1), (2, 2), (3, 3)])
        self.assertEqual({p.status for p in papers}, {Paper.Status.UPLOADED})

    def test_grouping_pages_per_student_flags_incomplete_last_paper(self):
        resp = self.upload(("class.pdf", pdf_pages(["a", "b", "c", "d", "e"])), ppp=2)
        result = resp.json()["results"][0]
        self.assertEqual(result["papers_added"], 3)
        last = Paper.objects.filter(exam=self.exam).order_by("sequence").last()
        self.assertEqual((last.first_page, last.page_count, last.is_incomplete), (5, 1, True))
        self.assertTrue(result["warnings"])

    def test_identical_file_is_skipped(self):
        data = pdf_pages(["a", "b"])
        self.upload(("first.pdf", data))
        result = self.upload(("copy.pdf", data)).json()["results"][0]
        self.assertEqual(result["status"], "duplicate_file")
        self.assertIn("first.pdf", result["error"])
        self.assertEqual(Paper.objects.count(), 2)
        self.assertEqual(SourceFile.objects.count(), 1)

    def test_same_page_inside_another_file_is_skipped(self):
        self.upload(("batch1.pdf", pdf_pages(["a", "b"])))
        result = self.upload(("batch2.pdf", pdf_pages(["b", "c"]))).json()["results"][0]
        self.assertEqual(result["papers_added"], 1)
        self.assertEqual(len(result["skipped_pages"]), 1)
        self.assertEqual(Paper.objects.count(), 3)

    def test_images_are_one_paper_each(self):
        resp = self.upload(("s1.png", png_page("ONE")), ("s2.jpg", cv2.imencode(".jpg", cv2.imdecode(
            np.frombuffer(png_page("TWO"), np.uint8), 0))[1].tobytes()))
        self.assertEqual([r["papers_added"] for r in resp.json()["results"]], [1, 1])
        kinds = set(SourceFile.objects.values_list("kind", flat=True))
        self.assertEqual(kinds, {"png", "jpg"})

    def test_bad_files_are_rejected_without_stopping_the_batch(self):
        resp = self.upload(("notes.pdf", b"this is not a pdf"), ("ok.pdf", pdf_pages(["a"])))
        results = resp.json()["results"]
        self.assertEqual(results[0]["status"], "rejected")
        self.assertEqual(results[1]["status"], "added")

    def test_originals_are_stored_read_only_in_the_exam_folder(self):
        self.upload(("class.pdf", pdf_pages(["a"])))
        src = SourceFile.objects.get()
        self.assertTrue(src.path.startswith(f"examinations/{self.exam.uid.hex}/original_papers/"))
        self.assertFalse(os.access(data_path(src.path), os.W_OK))
        self.assertEqual(src.original_name, "class.pdf")

    def test_finalized_exam_rejects_uploads(self):
        Examination.objects.filter(pk=self.exam.pk).update(status=Examination.Status.FINALIZED)
        result = self.upload(("a.pdf", pdf_pages(["a"]))).json()["results"][0]
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(Paper.objects.exists())

    def test_form_post_without_javascript(self):
        resp = self.upload(("a.pdf", pdf_pages(["a", "b"])), ajax=False)
        self.assertRedirects(resp, self.exam.get_absolute_url() + "#papers", fetch_redirect_response=False)
        self.assertEqual(Paper.objects.count(), 2)

    def test_exam_stage_and_pages(self):
        self.exam.answer_key_version = self.version
        self.exam.save()
        self.assertEqual(self.exam.stage, "ready_for_papers")
        self.upload(("a.pdf", pdf_pages(["a"])))
        self.assertEqual(self.exam.stage, "ready_to_process")
        page = self.client.get(self.exam.get_absolute_url())
        self.assertContains(page, "Paper 1")
        dash = self.client.get(reverse("core:dashboard"))
        self.assertEqual(dash.context["counts"]["papers"], 1)

    def test_paper_viewer_and_images(self):
        self.upload(("class.pdf", pdf_pages(["a", "b"])))
        paper = Paper.objects.get(sequence=2)
        self.assertEqual(self.client.get(paper.get_absolute_url()).status_code, 200)
        img = self.client.get(reverse("papers:page_image", args=[paper.uid, 2]))
        self.assertEqual((img.status_code, img["Content-Type"]), (200, "image/png"))
        self.assertEqual(self.client.get(reverse("papers:page_image", args=[paper.uid, 1])).status_code, 404)
        self.assertEqual(self.client.get(reverse("papers:source", args=[paper.uid])).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("papers:page_image", args=[paper.uid, 2])).status_code, 302)

    def test_exclude_and_restore(self):
        self.upload(("a.pdf", pdf_pages(["a"])))
        paper = Paper.objects.get()
        self.client.post(reverse("papers:set_excluded", args=[paper.uid]), {"exclude": "1"})
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.EXCLUDED)
        self.assertEqual(self.exam.active_papers().count(), 0)
        self.client.post(reverse("papers:set_excluded", args=[paper.uid]), {"exclude": "0"})
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.UPLOADED)

    def test_upload_pages_render(self):
        self.assertEqual(self.client.get(reverse("papers:upload_page")).status_code, 200)
        self.assertContains(self.client.get(reverse("core:dashboard")), "data-uploader")

    def test_sequence_numbers_continue_across_batches(self):
        self.upload(("a.pdf", pdf_pages(["a", "b"])))
        self.upload(("b.pdf", pdf_pages(["c"])))
        self.assertEqual(list(Paper.objects.order_by("sequence").values_list("sequence", flat=True)), [1, 2, 3])

    def test_real_sample_paper(self):
        sample = SAMPLES / "student_022.pdf"
        if not sample.exists():
            self.skipTest("private sample papers not available")
        data = sample.read_bytes()
        first = self.upload(("student_022.pdf", data)).json()["results"][0]
        self.assertEqual((first["status"], first["papers_added"]), ("added", 1))
        again = self.upload(("renamed.pdf", data)).json()["results"][0]
        self.assertEqual(again["status"], "duplicate_file")
