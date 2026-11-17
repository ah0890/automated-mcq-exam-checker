import re

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.exams.models import Examination
from apps.papers.models import Paper

from . import backup, excel, pdf

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BACKUP_NAME = re.compile(r"^(exam|full-backup)-[a-z0-9\-]+\.zip$")


def _name(exam, kind, ext):
    return f"{slugify(exam.name)[:50] or 'examination'}-{kind}-{timezone.localdate():%Y%m%d}.{ext}"


def _download(data: bytes, filename: str, content_type: str, inline=False) -> HttpResponse:
    resp = HttpResponse(data, content_type=content_type)
    resp["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{filename}"'
    resp["Cache-Control"] = "private, no-store"
    return resp


def _exam(uid):
    exam = get_object_or_404(Examination.objects.select_related("answer_key_version__key", "sheet_layout"), uid=uid)
    if not exam.answer_key_version_id:
        raise Http404("This examination has no answer key yet.")
    return exam


def index(request):
    exams = (Examination.objects.filter(answer_key_version__isnull=False)
             .annotate(papers_total=Count("papers", filter=~Q(papers__status=Paper.Status.EXCLUDED)),
                       marked=Count("results", distinct=True))
             .select_related("answer_key_version__key"))
    return render(request, "reports/index.html", {"exams": exams, "backups": backup.list_backups(),
                                                  "all_exams": Examination.objects.all()})


def class_excel(request, exam_uid):
    exam = _exam(exam_uid)
    return _download(excel.class_workbook(exam), _name(exam, "class-results", "xlsx"), XLSX)


def questions_excel(request, exam_uid):
    exam = _exam(exam_uid)
    return _download(excel.question_workbook(exam), _name(exam, "question-analysis", "xlsx"), XLSX)


def errors_excel(request, exam_uid):
    exam = get_object_or_404(Examination, uid=exam_uid)
    return _download(excel.error_workbook(exam), _name(exam, "processing-report", "xlsx"), XLSX)


def cards_pdf(request, exam_uid):
    exam = _exam(exam_uid)
    papers = None
    if request.GET.get("paper"):
        papers = list(Paper.objects.filter(exam=exam, uid=request.GET["paper"]))
        if not papers:
            raise Http404
    data = pdf.result_cards(exam, papers)
    kind = f"result-card-{papers[0].roll_number or papers[0].sequence}" if papers else "result-cards"
    return _download(data, _name(exam, kind, "pdf"), "application/pdf", inline=request.GET.get("view") == "1")


def summary_pdf(request, exam_uid):
    exam = _exam(exam_uid)
    return _download(pdf.summary_pdf(exam), _name(exam, "summary", "pdf"), "application/pdf",
                     inline=request.GET.get("view") == "1")


# --- backups ------------------------------------------------------------------------------------

@require_POST
def backup_exam(request, exam_uid):
    exam = get_object_or_404(Examination, uid=exam_uid)
    path = backup.backup_exam(exam, request.user)
    messages.success(request, f"Backup created: {path.name}")
    return redirect("reports:index")


@require_POST
def backup_full(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only an administrator can create a full backup.")
    path = backup.full_backup()
    messages.success(request, f"Full backup created: {path.name}")
    return redirect("reports:index")


def download_backup(request, name):
    if not BACKUP_NAME.match(name):
        raise Http404
    path = backup.backups_dir() / name
    if not path.exists():
        raise Http404
    if name.startswith("full-") and not request.user.is_superuser:
        raise PermissionDenied
    return FileResponse(open(path, "rb"), as_attachment=True, filename=name)


@require_POST
def restore(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only an administrator can restore backups.")
    upload = request.FILES.get("archive")
    if not upload:
        messages.error(request, "Choose an examination backup (.zip) to restore.")
        return redirect("reports:index")
    try:
        exam = backup.restore_exam(upload)
    except ValidationError as exc:
        messages.error(request, "Restore failed: " + " ".join(exc.messages))
        return redirect("reports:index")
    messages.success(request, f"Examination “{exam.name}” restored with all its papers, answers and results.")
    return redirect(exam.get_absolute_url())
