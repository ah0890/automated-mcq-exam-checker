from django.conf import settings
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

from apps.answerkeys.models import AnswerKey, AnswerKeyVersion
from apps.exams.models import Examination, SheetLayout
from apps.papers.models import Paper
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


def _is_uuid(value) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def dashboard(request):
    checks = run_system_checks()
    healthy = all(c.ok for c in checks)
    now = timezone.localtime()

    exams = Examination.objects.select_related("answer_key_version__key", "sheet_layout").annotate(
        paper_count=Count("papers", filter=~Q(papers__status="excluded")))
    counts = {
        "exams": exams.count(),
        "layouts": SheetLayout.objects.count(),
        "verified_keys": AnswerKeyVersion.objects.filter(status=AnswerKeyVersion.Status.VERIFIED)
                                                .values("key").distinct().count(),
        "draft_keys": AnswerKey.objects.filter(versions__status=AnswerKeyVersion.Status.DRAFT).distinct().count(),
        "students": Student.objects.filter(is_active=True).count(),
        "ready": exams.filter(answer_key_version__isnull=False, status=Examination.Status.ACTIVE).count(),
        "papers": Paper.objects.exclude(status=Paper.Status.EXCLUDED).count(),
        "to_review": Paper.objects.filter(status__in=[Paper.Status.NEEDS_REVIEW, Paper.Status.FAILED]).count(),
    }
    from apps.results.analytics import class_stats

    latest_results = []
    for exam in exams.filter(results__isnull=False).distinct()[:5]:
        latest_results.append({"exam": exam, "stats": class_stats(exam)})
    upload_exams = list(exams.filter(status=Examination.Status.ACTIVE))
    # The examination in focus: chosen in the URL (e.g. after setup or checking),
    # otherwise the most recent one with papers.
    focus = exams.filter(uid=request.GET.get("exam")).first() if _is_uuid(request.GET.get("exam")) else None
    if focus is None:
        focus = next((e for e in exams if e.paper_count), None)
    # Upload defaults to the focused examination, else the newest ready one without papers.
    default_exam = (focus if focus and focus in upload_exams else None) or \
        next((e for e in upload_exams if e.answer_key_version_id and not e.paper_count), None) or \
        next((e for e in upload_exams if e.answer_key_version_id), None)

    focus_results = None
    if focus and focus.answer_key_version_id:
        from apps.processing.models import ProcessingJob
        from apps.results.analytics import class_rows

        rows = class_rows(focus)
        focus_results = {
            "exam": focus, "rows": rows, "stats": class_stats(focus, rows),
            "running": ProcessingJob.objects.filter(exam=focus, status__in=["queued", "running"]).exists(),
        }

    # Every item reflects real application state.
    has_ready_exam = exams.filter(answer_key_version__isnull=False).exists()
    setup = [
        {"title": "System components ready", "state": "done" if healthy else "fail",
         "desc": "OCR, image processing and storage are available." if healthy
         else "Some components need attention — see System health."},
        {"title": "Set up the examination", "state": "done" if has_ready_exam else "todo",
         "desc": "Upload the blank answer sheet and the official answer key, then confirm the key.",
         "url": "exams:quick_setup"},
        {"title": "Upload the students' papers", "state": "done" if counts["papers"] else "todo",
         "desc": "On this dashboard — they are checked automatically.", "anchor": "#upload"},
        {"title": "Review doubtful papers", "state": "done" if counts["papers"] and not counts["to_review"] else "todo",
         "desc": "Only papers with faint marks or unreadable roll numbers need you.", "url": "processing:queue"},
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
        "upload_exams": upload_exams,
        "latest_results": [r for r in latest_results if not focus or r["exam"].pk != focus.pk],
        "focus_results": focus_results,
        "default_upload_exam": str(default_exam.uid) if default_exam else "",
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
