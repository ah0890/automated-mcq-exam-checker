"""Security checks across the whole application."""

import re
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse

PUBLIC = {"login"}

# Every endpoint that changes state must refuse GET.
POST_ONLY = [
    ("logout", []), ("answerkeys:revise", ["uuid"]), ("exams:assign_key", ["uuid"]), ("papers:upload", ["uuid"]),
    ("papers:set_excluded", ["uuid"]), ("processing:start", ["uuid"]), ("processing:reprocess", ["uuid"]),
    ("processing:cancel", ["uuid"]), ("results:evaluate", ["uuid"]), ("results:finalize", ["uuid"]),
    ("results:reopen", ["uuid"]), ("reports:backup_exam", ["uuid"]), ("reports:backup_full", []),
    ("reports:restore", []), ("exams:layout_delete", ["uuid"]), ("answerkeys:delete", ["uuid"]),
]


def all_url_names(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for p in resolver.url_patterns:
        if isinstance(p, URLResolver):
            ns = f"{prefix}{p.namespace}:" if p.namespace else prefix
            yield from all_url_names(p, ns)
        elif isinstance(p, URLPattern) and p.name:
            yield prefix + p.name, p


def sample_args(pattern: URLPattern):
    args = []
    for name, conv in pattern.pattern.converters.items():
        kind = type(conv).__name__
        args.append(uuid.uuid4() if kind == "UUIDConverter" else 1 if kind == "IntConverter" else "x")
    return args


class AccessControl(TestCase):
    def test_every_page_requires_sign_in(self):
        checked = 0
        for name, pattern in all_url_names():
            if name in PUBLIC or name.startswith("admin:"):
                continue
            url = reverse(name, args=sample_args(pattern))
            resp = self.client.get(url)
            with self.subTest(url=name):
                self.assertIn(resp.status_code, (302, 405), f"{name} answered {resp.status_code} without login")
                if resp.status_code == 302:
                    self.assertTrue(resp["Location"].startswith(reverse("login")), name)
            checked += 1
        self.assertGreater(checked, 50)

    def test_admin_requires_staff(self):
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_state_changing_endpoints_refuse_get(self):
        user = get_user_model().objects.create_superuser("admin", password="admin-pass-12345")
        self.client.force_login(user)
        for name, kinds in POST_ONLY:
            url = reverse(name, args=[uuid.uuid4() if k == "uuid" else k for k in kinds])
            with self.subTest(url=name):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_csrf_token_is_enforced(self):
        user = get_user_model().objects.create_user("teacher", password="a-strong-test-pass-91")
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        resp = client.post(reverse("logout"))
        self.assertEqual(resp.status_code, 403)

    def test_security_headers(self):
        resp = self.client.get(reverse("login"))
        csp = resp.headers.get("Content-Security-Policy", "")
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "same-origin")
        cookie = resp.cookies.get("csrftoken")
        self.assertEqual(cookie["samesite"], "Lax")

    def test_no_inline_javascript_in_templates(self):
        offenders = []
        for path in Path(settings.BASE_DIR, "templates").rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"\son[a-z]+\s*=", text) or re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)", text):
                offenders.append(path.name)
        self.assertEqual(offenders, [])


@override_settings(LOGIN_MAX_FAILURES=3, LOGIN_LOCKOUT_SECONDS=600)
class LoginThrottling(TestCase):
    def setUp(self):
        cache.clear()
        get_user_model().objects.create_user("teacher", password="a-strong-test-pass-91")

    def login(self, password):
        return self.client.post(reverse("login"), {"username": "teacher", "password": password})

    def test_lockout_after_repeated_failures_even_with_right_password(self):
        for _ in range(3):
            self.assertContains(self.login("wrong"), "incorrect")
        resp = self.login("a-strong-test-pass-91")
        self.assertContains(resp, "Too many unsuccessful sign-in attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_success_resets_counter(self):
        self.login("wrong")
        self.login("wrong")
        self.assertEqual(self.login("a-strong-test-pass-91").status_code, 302)
        self.client.logout()
        self.login("wrong")
        self.assertEqual(self.login("a-strong-test-pass-91").status_code, 302)


@override_settings(MCQ_BACKGROUND_PROCESSING=False)
class LogHygiene(TestCase):
    """Application logs must not contain student data (roll numbers, answers, names)."""

    def test_processing_logs_contain_no_student_data(self):
        import os
        import shutil
        import stat
        import tempfile

        import cv2
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.answerkeys.models import AnswerKey, AnswerKeyVersion
        from apps.exams.models import Examination
        from apps.exams.services import create_layout
        from apps.papers.services import add_file, get_or_create_batch
        from apps.processing import worker
        from omr.tests.sheetgen import draw_sheet

        tmp = tempfile.mkdtemp()
        try:
            with self.settings(DATA_DIR=tmp), self.assertLogs("apps", level="INFO") as logs:
                user = get_user_model().objects.create_user("teacher", password="a-strong-test-pass-91")
                png = lambda img: SimpleUploadedFile("s.png", cv2.imencode(".png", img)[1].tobytes())  # noqa: E731
                layout = create_layout(name="L", code="", option_labels="ABCD", roll_digits=4, question_order="column",
                                       uploaded=png(draw_sheet(questions=20, options=4, blocks=2, roll_digits=4)),
                                       user=user)
                version = AnswerKeyVersion.objects.create(key=AnswerKey.objects.create(title="K"), number=1,
                                                          num_questions=20, status="verified",
                                                          answers={str(q): "ABCD"[q % 4] for q in range(1, 21)})
                exam = Examination.objects.create(name="E", subject="S", class_section="C", exam_date="2026-11-16",
                                                  sheet_layout=layout, num_questions=20, answer_key_version=version)
                sheet = draw_sheet(questions=20, options=4, blocks=2, roll_digits=4, roll="4815",
                                   answers={q: [q % 4] for q in range(1, 21)})
                add_file(exam, get_or_create_batch(exam, None, 1, user), png(sheet))
                worker.enqueue(exam, user)
                worker.run_pending()
            text = " | ".join(logs.output)
            self.assertIn("finished", text)
            self.assertNotIn("4815", text)
        finally:
            for root, _, files in os.walk(tmp):
                for f in files:
                    os.chmod(os.path.join(root, f), stat.S_IWRITE | stat.S_IREAD)
            shutil.rmtree(tmp, ignore_errors=True)
