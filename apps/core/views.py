from django.conf import settings
from django.shortcuts import render
from django.utils import timezone

from apps.answerkeys.models import AnswerKey, AnswerKeyVersion
from apps.exams.models import Examination, SheetLayout
from apps.students.models import Student

from .system_checks import run_system_checks

WORKFLOW = [
    ("Create examination", "journal-plus"),
    ("Upload answer key", "file-earmark-arrow-up"),
    ("Verify key", "check2-circle"),
    ("Upload papers", "cloud-arrow-up"),
    ("Process papers", "cpu"),
    ("Review uncertain", "patch-question"),
    ("Finalize results", "lock"),
    ("View results", "trophy"),
]


def _greeting(now):
    if now.hour < 12:
        return "Good morning"
    if now.hour < 17:
        return "Good afternoon"
    return "Good evening"


def dashboard(request):
    checks = run_system_checks()
    healthy = all(c.ok for c in checks)
    now = timezone.localtime()

    exams = Examination.objects.select_related("answer_key_version__key", "sheet_layout")
    counts = {
        "exams": exams.count(),
        "layouts": SheetLayout.objects.count(),
        "verified_keys": AnswerKeyVersion.objects.filter(status=AnswerKeyVersion.Status.VERIFIED)
                                                .values("key").distinct().count(),
        "draft_keys": AnswerKey.objects.filter(versions__status=AnswerKeyVersion.Status.DRAFT).distinct().count(),
        "students": Student.objects.filter(is_active=True).count(),
        "ready": exams.filter(answer_key_version__isnull=False, status=Examination.Status.ACTIVE).count(),
    }

    # Every item reflects real application state.
    setup = [
        {"title": "System components ready", "state": "done" if healthy else "fail",
         "desc": "OCR, image processing and storage are available." if healthy
         else "Some components need attention — see System health."},
        {"title": "Add your answer-sheet layout", "state": "done" if counts["layouts"] else "todo",
         "desc": "Upload a blank sheet once so bubbles can be located.", "url": "exams:layout_create"},
        {"title": "Verify an answer key", "state": "done" if counts["verified_keys"] else "todo",
         "desc": "Upload the official key and check every answer.", "url": "answerkeys:upload"},
        {"title": "Create an examination", "state": "done" if counts["exams"] else "todo",
         "desc": "Name, class, question count and marking rules.", "url": "exams:create"},
        {"title": "Attach the key to the examination", "state": "done" if counts["ready"] else "todo",
         "desc": "The examination is then ready for student papers."},
        {"title": "Import the class list", "state": "done" if counts["students"] else "optional",
         "desc": "Optional — shows student names next to roll numbers.", "url": "students:import"},
    ]
    required = [s for s in setup if s["state"] != "optional"]

    return render(request, "core/dashboard.html", {
        "checks": checks,
        "healthy": healthy,
        "greeting": _greeting(now),
        "display_name": request.user.first_name or request.user.get_username(),
        "today": now,
        "workflow": WORKFLOW,
        "setup": setup,
        "setup_done": sum(1 for s in required if s["state"] == "done"),
        "setup_total": len(required),
        "counts": counts,
        "recent_exams": exams[:6],
        "ready_exams": exams.filter(answer_key_version__isnull=False, status=Examination.Status.ACTIVE)[:5],
        "data_dir": settings.DATA_DIR,
    })


def system_settings(request):
    checks = run_system_checks()
    return render(request, "core/settings.html", {
        "checks": checks,
        "healthy": all(c.ok for c in checks),
        "data_dir": settings.DATA_DIR,
        "database": settings.DATABASES["default"]["NAME"],
        "tesseract": settings.TESSERACT_CMD,
        "max_upload_mb": settings.MCQ_MAX_UPLOAD_MB,
        "time_zone": settings.TIME_ZONE,
        "session_hours": settings.SESSION_COOKIE_AGE // 3600,
    })
