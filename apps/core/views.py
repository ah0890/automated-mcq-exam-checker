from django.conf import settings
from django.shortcuts import render
from django.utils import timezone

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
    hour = now.hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def dashboard(request):
    checks = run_system_checks()
    healthy = all(c.ok for c in checks)
    now = timezone.localtime()
    name = request.user.first_name or request.user.get_username()

    # Every item reflects real application state; nothing is pre-filled.
    setup = [
        {"title": "Administrator account created", "desc": "You are signed in and can manage the application.",
         "state": "done"},
        {"title": "System components ready",
         "desc": "OCR, image processing and storage are available." if healthy
         else "Some components need attention — see System health.",
         "state": "done" if healthy else "fail"},
        {"title": "Create your first examination", "desc": "Name, class, question count and marking rules.",
         "state": "todo", "milestone": 2},
        {"title": "Upload and verify the answer key", "desc": "Extracted answers are checked by you before use.",
         "state": "todo", "milestone": 2},
        {"title": "Upload the class's answer sheets", "desc": "PDF, JPG or PNG — a whole class at once.",
         "state": "todo", "milestone": 3},
    ]

    return render(request, "core/dashboard.html", {
        "checks": checks,
        "healthy": healthy,
        "greeting": _greeting(now),
        "display_name": name,
        "today": now,
        "workflow": WORKFLOW,
        "setup": setup,
        "setup_done": sum(1 for s in setup if s["state"] == "done"),
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
