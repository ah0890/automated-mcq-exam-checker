"""Permanent deletion of examinations and all their data."""

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.answerkeys.models import AnswerKey, AnswerKeyVersion
from apps.core.files import data_path
from apps.exams.models import Examination, SheetLayout
from apps.papers.models import Paper, SourceFile, UploadBatch
from apps.processing.models import Correction, ProcessingJob
from apps.processing.tests import test_processing as pt
from apps.results.models import EvaluationRun, Result
from apps.results.tests.test_results import answers_with_wrong

sheet = pt.sheet


@override_settings(MCQ_BACKGROUND_PROCESSING=False)
class DeletionTests(pt.ProcessingTests):
    """Reuses the processing fixtures (generated sheet layout + verified key + examination)."""

    def checked_exam(self):
        self.add(sheet(roll="1001", answers=answers_with_wrong(2)), "a.png")
        self.add(sheet(roll="1002", answers=answers_with_wrong(0)), "b.png")
        self.process()
        self.client.post(reverse("results:evaluate", args=[self.exam.uid]))
        self.assertEqual(Result.objects.filter(exam=self.exam).count(), 2)

    def delete(self, uids, confirm="DELETE", **extra):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("exams:delete"), {"uid": [str(u) for u in uids], "confirm": confirm,
                                                              **extra})

    def other_exam(self, **kw):
        return Examination.objects.create(name="Other", subject="S", class_section="C", exam_date="2026-11-17",
                                          sheet_layout=self.exam.sheet_layout, num_questions=pt.Q, **kw)

    def test_confirmation_page_lists_what_will_be_deleted(self):
        self.checked_exam()
        resp = self.client.get(reverse("exams:delete"), {"uid": self.exam.uid})
        self.assertContains(resp, "Delete permanently")
        self.assertEqual(resp.context["totals"], {"papers": 2, "files": 2, "results": 2})
        self.assertTrue(Examination.objects.filter(pk=self.exam.pk).exists())  # GET changes nothing

    def test_delete_removes_every_record_and_file(self):
        self.checked_exam()
        paper = Paper.objects.filter(exam=self.exam).first()
        Correction.objects.create(paper=paper, field="q1", old_value="A", new_value="B", user=self.user)
        folder = data_path(self.exam.storage_dir)
        self.assertTrue(any(folder.rglob("*.png")))
        original = data_path(SourceFile.objects.filter(exam=self.exam).first().path)
        self.assertTrue(original.exists())

        resp = self.delete([self.exam.uid])
        self.assertRedirects(resp, reverse("exams:list"))
        self.assertFalse(Examination.objects.exists())
        for model in (Paper, SourceFile, UploadBatch, Result, EvaluationRun, ProcessingJob, Correction):
            self.assertFalse(model.objects.exists(), model.__name__)
        self.assertFalse(folder.exists())  # read-only originals removed too
        # Shared setup is kept unless asked for.
        self.assertTrue(SheetLayout.objects.exists())
        self.assertTrue(AnswerKey.objects.exists())

    def test_wrong_confirmation_word_deletes_nothing(self):
        self.checked_exam()
        resp = self.delete([self.exam.uid], confirm="yes")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Type DELETE to confirm")
        self.assertEqual(Paper.objects.filter(exam=self.exam).count(), 2)

    def test_several_examinations_at_once_other_data_untouched(self):
        a = self.other_exam(answer_key_version=self.exam.answer_key_version)
        b = self.other_exam()
        keep = self.other_exam()
        self.delete([a.uid, b.uid])
        self.assertEqual(set(Examination.objects.values_list("pk", flat=True)), {self.exam.pk, keep.pk})

    def test_remove_unused_key_and_layout(self):
        self.checked_exam()
        layout_dir = data_path(f"sheet_layouts/{self.exam.sheet_layout.uid.hex}")
        self.assertTrue(layout_dir.exists())
        self.delete([self.exam.uid], remove_setup="1")
        self.assertFalse(SheetLayout.objects.exists())
        self.assertFalse(AnswerKey.objects.exists())
        self.assertFalse(AnswerKeyVersion.objects.exists())
        self.assertFalse(layout_dir.exists())

    def test_key_and_layout_in_use_elsewhere_are_kept(self):
        other = self.other_exam(answer_key_version=self.exam.answer_key_version)
        self.delete([self.exam.uid], remove_setup="1")
        self.assertTrue(SheetLayout.objects.filter(pk=other.sheet_layout_id).exists())
        self.assertTrue(AnswerKeyVersion.objects.filter(pk=other.answer_key_version_id).exists())

    def test_refused_while_processing(self):
        ProcessingJob.objects.create(exam=self.exam, status="running", created_by=self.user)
        resp = self.delete([self.exam.uid])
        self.assertContains(resp, "Stop processing first")
        self.assertTrue(Examination.objects.filter(pk=self.exam.pk).exists())

    def test_finalized_examination_needs_an_administrator(self):
        Examination.objects.filter(pk=self.exam.pk).update(status="finalized", finalized_at=timezone.now())
        resp = self.delete([self.exam.uid])
        self.assertContains(resp, "Only an administrator")
        self.assertTrue(Examination.objects.filter(pk=self.exam.pk).exists())

        admin = get_user_model().objects.create_superuser("admin", password="a-strong-test-pass-92")
        self.client.force_login(admin)
        self.delete([self.exam.uid])
        self.assertFalse(Examination.objects.exists())

    def test_no_selection_returns_to_list(self):
        resp = self.client.get(reverse("exams:delete"))
        self.assertRedirects(resp, reverse("exams:list"))
        resp = self.client.get(reverse("exams:delete"), {"uid": "not-a-uuid"})
        self.assertRedirects(resp, reverse("exams:list"))

    def test_list_and_workspace_offer_delete(self):
        self.assertContains(self.client.get(reverse("exams:list")), "Delete selected")
        self.assertContains(self.client.get(self.exam.get_absolute_url()),
                            f"{reverse('exams:delete')}?uid={self.exam.uid}")

    def test_upload_still_works_after_deleting_another_exam(self):
        other = self.other_exam(answer_key_version=self.exam.answer_key_version)
        self.delete([other.uid])
        self.add(sheet(), "c.png")
        self.assertEqual(Paper.objects.filter(exam=self.exam).count(), 1)
