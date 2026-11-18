import io
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.core.previews import download_response, page_count, png_response

from . import services
from .forms import KeyManualForm, KeyUploadForm, clean_note, parse_answer_grid
from .models import AnswerKey, AnswerKeyVersion


def library(request):
    query = (request.GET.get("q") or "").strip()
    keys = (AnswerKey.objects.annotate(version_count=Count("versions", distinct=True),
                                       latest_number=Max("versions__number"),
                                       exam_count=Count("versions__examinations", distinct=True))
            .order_by("-updated_at"))
    if query:
        keys = keys.filter(Q(title__icontains=query) | Q(subject__icontains=query))
    rows = []
    for key in keys:
        latest = key.latest
        rows.append({"key": key, "latest": latest, "verified": key.latest_verified, "draft": key.open_draft})
    return render(request, "answerkeys/library.html", {"rows": rows, "query": query,
                                                        "total": AnswerKey.objects.count()})


def upload(request):
    form = KeyUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            version = services.create_from_upload(
                title=d["title"], subject=d["subject"], num_questions=d["num_questions"],
                option_labels=d["option_labels"], uploaded=d["document"], user=request.user)
        except ValidationError as exc:
            form.add_error("document", exc)
        else:
            found = version.extraction.get("found", 0)
            messages.success(request, f"Document read: {found} of {version.num_questions} answers found. "
                                      "Check every answer against the document, then verify the key.")
            return redirect("answerkeys:verify", version.uid)
    return render(request, "answerkeys/upload.html", {"form": form, "mode": "upload"})


def manual(request):
    form = KeyManualForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        version = services.create_manual(title=d["title"], subject=d["subject"], num_questions=d["num_questions"],
                                         option_labels=d["option_labels"], user=request.user)
        return redirect("answerkeys:verify", version.uid)
    return render(request, "answerkeys/upload.html", {"form": form, "mode": "manual"})


def verify(request, uid):
    version = get_object_or_404(AnswerKeyVersion.objects.select_related("key"), uid=uid)
    if version.is_verified:
        return redirect(version.get_absolute_url())
    errors = []
    answers = dict(version.answers)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "discard":
            title = version.key.title
            services.discard_draft(version)
            messages.info(request, f"Draft of “{title}” discarded.")
            return redirect("answerkeys:library")
        answers, errors = parse_answer_grid(request.POST, version.num_questions, version.option_labels)
        if not errors and action == "save":
            services.save_draft(version, answers)
            messages.success(request, "Draft saved. It cannot be used for marking until it is verified.")
            return redirect("answerkeys:verify", version.uid)
        if not errors and action == "verify":
            if version.source_kind in ("png", "jpg") or "OCR" in version.extraction.get("method", ""):
                if not request.POST.get("confirm_compared"):
                    errors.append("Confirm that you compared every answer with the document.")
            if not errors:
                try:
                    services.verify(version, answers, request.user, clean_note(request.POST.get("note")))
                except ValidationError as exc:
                    errors.extend(exc.messages)
                else:
                    messages.success(request, f"“{version.key.title}” version {version.number} verified and saved "
                                              "to the Answer Key Library.")
                    exam = _attach_to_pending_exam(request, version)
                    if exam:
                        return redirect(f"{reverse('core:dashboard')}?exam={exam.uid}#upload")
                    return redirect(version.get_absolute_url())

    extraction = version.extraction or {}
    extracted = extraction.get("extracted", {})
    conflicts = extraction.get("conflicts", {})
    rows = []
    for q in range(1, version.num_questions + 1):
        key = str(q)
        value = answers.get(key, "")
        flag = None
        if key in conflicts:
            flag = "conflict"
        elif version.source == AnswerKeyVersion.Source.UPLOAD and key not in extracted:
            flag = "missing"
        elif extracted and extracted.get(key) and value and extracted.get(key) != value:
            flag = "edited"
        rows.append({"q": q, "value": value, "flag": flag, "options": conflicts.get(key, [])})
    pages = page_count(version.source_path, version.source_kind) if version.source_path else 0
    return render(request, "answerkeys/verify.html", {
        "version": version, "key": version.key, "rows": rows, "errors": errors, "extraction": extraction,
        "labels": list(version.option_labels), "pages": range(1, pages + 1),
        "needs_confirmation": version.source_kind in ("png", "jpg") or "OCR" in extraction.get("method", ""),
        "filled": sum(1 for r in rows if r["value"]),
    })


def _attach_to_pending_exam(request, version):
    """Finish a dashboard quick setup: attach the newly verified key to its examination."""
    from apps.exams.models import Examination
    from apps.exams.services import assign_key

    uid = request.session.pop("quick_setup_exam", None)
    exam = Examination.objects.filter(uid=uid, answer_key_version__isnull=True).first() if uid else None
    if not exam:
        return None
    version.refresh_from_db()  # the service verified a fresh copy
    try:
        assign_key(exam, version, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return None
    messages.success(request, f"“{exam.name}” is ready. Upload the students' answer sheets below — they are "
                              "checked automatically.")
    return exam


def detail(request, uid):
    key = get_object_or_404(AnswerKey, uid=uid)
    versions = list(key.versions.select_related("created_by", "verified_by").order_by("-number"))
    if not versions:
        messages.error(request, "This key has no versions.")
        return redirect("answerkeys:library")
    selected = versions[0]
    if request.GET.get("v", "").isdigit():
        selected = next((v for v in versions if v.number == int(request.GET["v"])), selected)
    exams = selected.examinations.all() if selected.is_verified else []
    return render(request, "answerkeys/detail.html", {
        "key": key, "versions": versions, "selected": selected, "exams": exams,
        "all_exams": [e for v in versions for e in v.examinations.all()],
        "draft": key.open_draft,
    })


@require_POST
def delete(request, uid):
    from apps.exams.deletion import delete_answer_key

    key = get_object_or_404(AnswerKey, uid=uid)
    try:
        delete_answer_key(key, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect(key.get_absolute_url())
    messages.success(request, f"Answer key “{key.title}” deleted.")
    return redirect("answerkeys:library")


@require_POST
def revise(request, uid):
    version = get_object_or_404(AnswerKeyVersion, uid=uid)
    draft = services.start_revision(version, request.user)
    messages.info(request, f"Editing as version {draft.number}. Version {version.number} stays unchanged, and "
                           "examinations keep using it until you switch them.")
    return redirect("answerkeys:verify", draft.uid)


def export(request, uid, fmt):
    version = get_object_or_404(AnswerKeyVersion.objects.select_related("key"), uid=uid)
    base = f"{slugify(version.key.title) or 'answer-key'}-v{version.number}"
    if fmt == "json":
        payload = {
            "title": version.key.title, "subject": version.key.subject, "version": version.number,
            "status": version.status, "total_questions": version.num_questions,
            "options": version.option_labels,
            "verified_at": version.verified_at.isoformat() if version.verified_at else None,
            "exported_at": timezone.now().isoformat(),
            "answers": {str(q): a for q, a in version.ordered_answers() if a},
        }
        resp = HttpResponse(json.dumps(payload, indent=2), content_type="application/json")
        resp["Content-Disposition"] = f'attachment; filename="{base}.json"'
        return resp
    if fmt == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        wb = Workbook()
        ws = wb.active
        ws.title = "Answer key"
        ws.append([version.key.title])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append([f"Version {version.number} · {version.get_status_display()} · {version.num_questions} questions"])
        ws.append([])
        ws.append(["Question", "Answer"])
        for cell in ws[4]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="13294B")
            cell.alignment = Alignment(horizontal="center")
        for q, a in version.ordered_answers():
            ws.append([q, a or ""])
        ws.column_dimensions["A"].width = 12
        ws.column_dimensions["B"].width = 12
        buf = io.BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{base}.xlsx"'
        return resp
    return HttpResponse(status=404)


def source_preview(request, uid, page):
    version = get_object_or_404(AnswerKeyVersion, uid=uid)
    if not version.source_path:
        return HttpResponse(status=404)
    return png_response(version.source_path, version.source_kind, page)


def source_download(request, uid):
    version = get_object_or_404(AnswerKeyVersion, uid=uid)
    if not version.source_path:
        return HttpResponse(status=404)
    return download_response(version.source_path, version.source_name or f"answer-key.{version.source_kind}")
