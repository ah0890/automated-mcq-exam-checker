from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from apps.exams.models import Examination
from apps.papers.models import Paper
from apps.processing.tests import test_processing as pt
from apps.results import analytics, services
from apps.results.models import FinalizationEvent
from apps.students.models import Student

Q, sheet = pt.Q, pt.sheet


def answers(wrong: dict[int, int] | None = None, blank=(), multiple=()) -> dict:
    """Default sheet answers (= the key) with chosen questions changed to option index."""
    a = {q: [q % 4] for q in range(1, Q + 1)}
    for q, opt in (wrong or {}).items():
        a[q] = [opt]
    for q in blank:
        a[q] = []
    for q in multiple:
        a[q] = [0, 1]
    return a


@override_settings(MCQ_BACKGROUND_PROCESSING=False)
class AnalyticsTests(pt.ProcessingTests):
    def build_class(self):
        # Key for Q1 is "B" (index 1). Wrong answers on Q1: two students choose "D", one "A".
        self.add(sheet(roll="1001", answers=answers()), "a.png")                                  # 20
        self.add(sheet(roll="1002", answers=answers(wrong={1: 3, 2: 0})), "b.png")                 # 18
        self.add(sheet(roll="1003", answers=answers(wrong={1: 3}, blank=(3,))), "c.png")           # 18
        self.add(sheet(roll="1004", answers=answers(wrong={1: 0}, blank=(3, 4), multiple=(5,))), "d.png")  # 16
        self.add(sheet(roll="1005", fill_level={7: 185}), "e.png")                                 # needs review
        Student.objects.create(roll_number="1001", name="First Student", class_section="C")
        self.process()

    def test_class_rows_and_stats(self):
        self.build_class()
        rows = analytics.class_rows(self.exam)
        self.assertEqual([(r["roll"], r["rank"], r["status"]) for r in rows],
                         [("1001", 1, "pass"), ("1002", 2, "pass"), ("1003", 2, "pass"), ("1004", 4, "pass"),
                          ("1005", None, "pending")])
        self.assertEqual(rows[0]["name"], "First Student")
        s = analytics.class_stats(self.exam, rows)
        self.assertEqual((s["students"], s["evaluated"], s["pending"], s["needs_review"]), (5, 4, 1, 1))
        self.assertEqual((s["average"], s["highest"], s["lowest"]), (D("18.00"), D("20.00"), D("16.00")))
        self.assertEqual((s["passed"], s["failed"], s["pass_rate"]), (4, 0, D("100.00")))

    def test_question_analysis(self):
        self.build_class()
        stats = {s["q"]: s for s in analytics.question_analysis(self.exam)}
        q1 = stats[1]
        self.assertEqual((q1["key"], q1["correct"], q1["incorrect"], q1["students"]), ("B", 1, 3, 4))
        self.assertEqual((q1["top_wrong"], q1["top_wrong_count"]), ("D", 2))
        self.assertEqual(q1["correct_pct"], D("25.00"))
        self.assertTrue(q1["high_error"])
        self.assertEqual((stats[3]["unanswered"], stats[5]["multiple"]), (2, 1))
        self.assertTrue(stats[10]["mostly_correct"])
        summary = analytics.difficulty_summary(list(stats.values()))
        self.assertEqual(summary["hardest"][0]["q"], 1)
        self.assertEqual([o["count"] for o in q1["options"]], [1, 1, 0, 2])  # A, B(key), C, D

    def test_distribution_bands(self):
        self.build_class()
        bands = analytics.distribution(analytics.current_results(self.exam))
        self.assertEqual({b["label"]: b["count"] for b in bands if b["count"]}, {"80–89": 1, "90–100": 3})

    def test_finalize_blocked_until_review_done(self):
        self.build_class()
        blockers = services.finalize_blockers(self.exam)
        self.assertTrue(any("need review" in b for b in blockers))
        resp = self.client.post(reverse("results:finalize", args=[self.exam.uid]), follow=True)
        self.assertContains(resp, "cannot be finalized")
        self.exam.refresh_from_db()
        self.assertFalse(self.exam.is_finalized)

    def finalize_class(self):
        self.build_class()
        pending = Paper.objects.get(roll_number="1005")
        self.client.post(reverse("processing:review", args=[pending.uid]),
                         {"action": "confirm", "roll_number": "1005", "q_7": "D"})  # Q7 key is D
        self.assertEqual(services.finalize_blockers(self.exam), [])
        self.client.post(reverse("results:finalize", args=[self.exam.uid]), {"note": "Checked"})
        self.exam.refresh_from_db()

    def test_finalize_locks_everything(self):
        self.finalize_class()
        self.assertTrue(self.exam.is_finalized)
        self.assertEqual(FinalizationEvent.objects.get().action, "finalized")
        paper = Paper.objects.get(roll_number="1002")
        # Upload rejected
        resp = self.client.post(reverse("papers:upload", args=[self.exam.uid]),
                                {"files": [SimpleUploadedFile("x.png", pt.png(sheet(roll="7777")))]},
                                HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.json()["results"][0]["status"], "rejected")
        # Review changes refused
        self.client.post(reverse("processing:review", args=[paper.uid]),
                         {"action": "confirm", "roll_number": "1002", "q_1": "B"})
        paper.refresh_from_db()
        self.assertEqual(paper.result.correct, 18)
        # Exclusion, editing, re-evaluation and key change refused
        self.client.post(reverse("papers:set_excluded", args=[paper.uid]), {"exclude": "1"})
        paper.refresh_from_db()
        self.assertNotEqual(paper.status, Paper.Status.EXCLUDED)
        self.assertRedirects(self.client.get(reverse("exams:edit", args=[self.exam.uid])),
                             self.exam.get_absolute_url())
        self.client.post(reverse("results:evaluate", args=[self.exam.uid]))
        self.assertFalse(self.exam.evaluation_runs.exists())

    def test_reopen_requires_admin_and_reason(self):
        self.finalize_class()
        url = reverse("results:reopen", args=[self.exam.uid])
        self.assertEqual(self.client.post(url, {"note": "x"}).status_code, 403)  # teacher is not superuser
        admin = get_user_model().objects.create_superuser("admin", password="admin-pass-12345")
        self.client.force_login(admin)
        self.client.post(url, {"note": ""})
        self.exam.refresh_from_db()
        self.assertTrue(self.exam.is_finalized)
        self.client.post(url, {"note": "Rescan for 1003"})
        self.exam.refresh_from_db()
        self.assertFalse(self.exam.is_finalized)
        self.assertEqual(list(FinalizationEvent.objects.values_list("action", flat=True)),
                         ["reopened", "finalized"])

    def test_pages_render(self):
        self.build_class()
        for url in [reverse("results:index"), reverse("results:questions_index"),
                    reverse("results:class", args=[self.exam.uid]), reverse("results:questions", args=[self.exam.uid]),
                    reverse("core:dashboard"), self.exam.get_absolute_url()]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        page = self.client.get(reverse("results:class", args=[self.exam.uid]))
        self.assertContains(page, "First Student")
        self.assertContains(page, 'id="chart-distribution"')
        self.assertContains(self.client.get(reverse("results:questions", args=[self.exam.uid])), 'id="chart-questions"')

    def test_subject_performance(self):
        self.build_class()
        perf = analytics.subject_performance()
        self.assertEqual(perf, [{"subject": "S", "students": 4, "average_pct": D("90.00")}])


for _name in [n for n in dir(pt.ProcessingTests) if n.startswith("test_")]:
    if _name not in AnalyticsTests.__dict__:
        setattr(AnalyticsTests, _name, None)
