import json
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from apps.answerkeys.models import AnswerKey, AnswerKeyVersion
from apps.exams.models import Examination
from apps.exams.services import create_layout
from apps.papers.models import Paper
from apps.papers.services import add_file, get_or_create_batch
from apps.processing.tests import test_processing as pt
from apps.results.models import EvaluationRun, Result
from apps.results.services import stale_reasons, summary

SAMPLES = Path(settings.BASE_DIR) / "samples"
Q, sheet = pt.Q, pt.sheet
KEY = {q: "ABCD"[q % 4] for q in range(1, Q + 1)}  # matches sheet() default answers


def answers_with_wrong(n_wrong: int) -> dict:
    """Sheet answers where the first n questions are deliberately wrong."""
    ans = {q: [q % 4] for q in range(1, Q + 1)}
    for q in range(1, n_wrong + 1):
        ans[q] = [(q + 1) % 4]
    return ans


@override_settings(MCQ_BACKGROUND_PROCESSING=False)
class ResultTests(pt.ProcessingTests):
    """Reuses the processing fixtures (generated sheet layout + 20-question key)."""

    def test_verified_paper_is_marked_automatically(self):
        paper = self.add(sheet(answers=answers_with_wrong(3)))
        self.process()
        r = Result.objects.get(paper=paper)
        self.assertEqual((r.correct, r.incorrect, r.unanswered, r.multiple), (17, 3, 0, 0))
        self.assertEqual((r.obtained, r.percentage, r.passed, r.rank), (D("17.00"), D("85.00"), True, 1))
        self.assertEqual(r.key_version, self.exam.answer_key_version)

    def test_paper_needing_review_is_not_marked_until_confirmed(self):
        paper = self.add(sheet(fill_level={5: 185}))
        self.process()
        self.assertFalse(Result.objects.exists())
        self.client.post(reverse("processing:review", args=[paper.uid]),
                         {"action": "confirm", "roll_number": "1234", "q_5": "B"})
        r = Result.objects.get(paper=paper)
        self.assertEqual(r.correct, Q)  # Q5 key is "B" (5 % 4 = 1)

    def test_ranking_uses_competition_ranks(self):
        self.add(sheet(roll="1001", answers=answers_with_wrong(2)), "a.png")  # 18
        self.add(sheet(roll="1002", answers=answers_with_wrong(0)), "b.png")  # 20
        self.add(sheet(roll="1003", answers=answers_with_wrong(2)), "c.png")  # 18
        self.add(sheet(roll="1004", answers=answers_with_wrong(5)), "d.png")  # 15
        self.process()
        ranks = {r.roll_number: (r.obtained, r.rank) for r in Result.objects.all()}
        self.assertEqual(ranks, {"1002": (D("20.00"), 1), "1001": (D("18.00"), 2), "1003": (D("18.00"), 2),
                                 "1004": (D("15.00"), 4)})
        s = summary(self.exam)
        self.assertEqual((s["evaluated"], s["highest"], s["lowest"], s["average"]),
                         (4, D("20.00"), D("15.00"), D("17.75")))

    def test_excluded_paper_leaves_the_ranking(self):
        a = self.add(sheet(roll="1001", answers=answers_with_wrong(0)), "a.png")
        self.add(sheet(roll="1002", answers=answers_with_wrong(4)), "b.png")
        self.process()
        self.client.post(reverse("papers:set_excluded", args=[a.uid]), {"exclude": "1"})
        r = Result.objects.get(roll_number="1002")
        self.assertEqual(r.rank, 1)
        self.assertEqual(summary(self.exam)["evaluated"], 1)

    def test_negative_marking_end_to_end(self):
        Examination.objects.filter(pk=self.exam.pk).update(negative_marking=True, negative_marks=D("0.25"))
        self.exam.refresh_from_db()
        answers = answers_with_wrong(4)
        answers[10] = []  # one blank
        self.add(sheet(answers=answers))
        self.process()
        r = Result.objects.get()
        # 15 correct − 4 × 0.25 = 14.00
        self.assertEqual((r.correct, r.incorrect, r.unanswered, r.obtained), (15, 4, 1, D("14.00")))

    def test_correction_on_verified_paper_is_remarked_with_same_key(self):
        paper = self.add(sheet())
        self.process()
        url = reverse("processing:review", args=[paper.uid])
        self.client.post(url, {"action": "save", "roll_number": "1234", "q_1": "A"})  # change a correct answer
        r = Result.objects.get()
        self.assertEqual((stale_reasons(r), r.correct, r.incorrect), ([], Q - 1, 1))

    def test_paper_sent_back_to_review_has_stale_result(self):
        paper = self.add(sheet())
        self.process()
        Paper.objects.filter(pk=paper.pk).update(status=Paper.Status.NEEDS_REVIEW)
        r = Result.objects.get()
        self.assertIn("paper needs review", stale_reasons(r))

    def test_key_change_preserves_results_and_requires_explicit_reevaluation(self):
        self.add(sheet())
        self.process()
        old = Result.objects.get()
        self.assertEqual(old.correct, Q)
        # New verified key version where Q1 is different.
        new_key = AnswerKeyVersion.objects.create(
            key=self.exam.answer_key_version.key, number=2, num_questions=Q, status="verified",
            answers={**{str(q): a for q, a in KEY.items()}, "1": "D"})
        self.client.post(reverse("exams:assign_key", args=[self.exam.uid]), {"version": new_key.pk})
        old.refresh_from_db()
        self.assertEqual(old.correct, Q)  # unchanged
        self.assertIn("answer key changed", stale_reasons(old))
        self.assertIsNone(old.rank)
        # A newly verified paper is NOT silently marked with the new key while old results exist.
        self.add(sheet(roll="9999", answers=answers_with_wrong(1)), "late.png")
        self.process()
        self.assertEqual(Result.objects.count(), 1)
        # Explicit re-evaluation.
        self.client.post(reverse("results:evaluate", args=[self.exam.uid]))
        run = EvaluationRun.objects.get()
        self.assertEqual((run.papers_evaluated, run.key_version), (2, new_key))
        old.refresh_from_db()
        self.assertEqual((old.correct, old.key_version, stale_reasons(old)), (Q - 1, new_key, []))
        self.assertEqual(len(run.snapshot), 2)
        # A second run keeps the first in history.
        self.client.post(reverse("results:evaluate", args=[self.exam.uid]))
        self.assertEqual(EvaluationRun.objects.count(), 2)

    def test_rules_change_marks_results_stale(self):
        self.add(sheet())
        self.process()
        data = {"name": "E", "subject": "S", "class_section": "C", "exam_date": "2026-11-16",
                "marks_per_question": "2", "negative_marks": "0", "multiple_mark_policy": "zero",
                "passing_percentage": "40"}
        resp = self.client.post(reverse("exams:edit", args=[self.exam.uid]), data, follow=True)
        self.assertContains(resp, "Marking rules changed")
        r = Result.objects.get()
        self.assertIn("marking rules changed", stale_reasons(r))
        self.client.post(reverse("results:evaluate", args=[self.exam.uid]))
        r.refresh_from_db()
        self.assertEqual((r.obtained, r.max_marks), (D("40.00"), D("40.00")))

    def test_pages_show_results(self):
        paper = self.add(sheet())
        self.process()
        page = self.client.get(self.exam.get_absolute_url())
        self.assertContains(page, 'id="results"')
        self.assertContains(page, "Pass")
        self.assertContains(self.client.get(paper.get_absolute_url()), "outcome-grid")
        self.assertContains(self.client.get(reverse("processing:review", args=[paper.uid])), "outcome-grid")

    def test_real_sample_paper_marked_with_real_key(self):
        blank, student = SAMPLES / "blank_answer_sheet.pdf", SAMPLES / "student_022.pdf"
        key_truth = SAMPLES / "ground_truth" / "official_answer_key.json"
        if not (blank.exists() and student.exists() and key_truth.exists()):
            self.skipTest("private sample papers not available")
        key_answers = json.loads(key_truth.read_text(encoding="utf-8"))["answers"]
        layout = create_layout(name="Real", code="", option_labels="ABCD", roll_digits=5, question_order="column",
                               uploaded=SimpleUploadedFile("blank.pdf", blank.read_bytes()), user=self.user)
        version = AnswerKeyVersion.objects.create(key=AnswerKey.objects.create(title="Official"), number=1,
                                                  num_questions=50, status="verified", answers=key_answers)
        exam = Examination.objects.create(name="Real", subject="S", class_section="C", exam_date="2026-11-16",
                                          sheet_layout=layout, num_questions=50, answer_key_version=version,
                                          passing_percentage=D("40"))
        add_file(exam, get_or_create_batch(exam, None, 1, self.user),
                 SimpleUploadedFile("student.pdf", student.read_bytes()))
        self.client.post(reverse("processing:start", args=[exam.uid]))
        paper = Paper.objects.get(exam=exam)
        self.assertFalse(Result.objects.filter(paper=paper).exists())  # Q50 uncertain → not marked yet
        self.client.post(reverse("processing:review", args=[paper.uid]),
                         {"action": "confirm", "roll_number": paper.roll_number, "q_50": "B"})
        r = Result.objects.get(paper=paper)
        # Hand-checked against the official key: 42 correct, 5 incorrect, 2 unanswered, 1 multiple.
        self.assertEqual((r.correct, r.incorrect, r.unanswered, r.multiple), (42, 5, 2, 1))
        self.assertEqual((r.obtained, r.percentage, r.passed), (D("42.00"), D("84.00"), True))
        wrong = sorted(q["q"] for q in r.questions if q["outcome"] == "incorrect")
        self.assertEqual(wrong, [8, 9, 11, 44, 48])


# Only run the result tests in this module (the processing tests run in their own module).
for _name in [n for n in dir(pt.ProcessingTests) if n.startswith("test_")]:
    if _name not in ResultTests.__dict__:
        setattr(ResultTests, _name, None)
