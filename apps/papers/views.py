import uuid

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.previews import download_response, png_response
from apps.exams.models import Examination

from . import services
from .models import Paper


def _wants_json(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or \
        "application/json" in request.headers.get("accept", "")


def upload_page(request):
    """Stand-alone upload page (sidebar: Upload Student Papers)."""
    exams = Examination.objects.filter(status=Examination.Status.ACTIVE).select_related("answer_key_version")
    selected = request.GET.get("exam")
    return render(request, "papers/upload.html", {"exams": exams, "selected": selected})


@require_POST
def upload(request, exam_uid):
    exam = get_object_or_404(Examination, uid=exam_uid)
    try:
        per = max(1, min(int(request.POST.get("pages_per_paper") or 1), 20))
    except ValueError:
        per = 1
    try:
        batch_uid = uuid.UUID(request.POST["batch"]) if request.POST.get("batch") else None
    except ValueError:
        batch_uid = None
    files = request.FILES.getlist("files")
    if not files:
        if _wants_json(request):
            return JsonResponse({"error": "No file received."}, status=400)
        messages.error(request, "Choose at least one file to upload.")
        return redirect(exam.get_absolute_url() + "#papers")

    batch = services.get_or_create_batch(exam, batch_uid, per, request.user)
    outcomes = [services.add_file(exam, batch, f) for f in files]

    if _wants_json(request):
        return JsonResponse({"results": [o.as_dict() for o in outcomes],
                             "stats": services.paper_stats(exam)})
    added = sum(o.papers_added for o in outcomes)
    problems = [f"{o.name}: {o.error}" for o in outcomes if o.error]
    skipped = [f"{o.name}: {s}" for o in outcomes for s in o.skipped_pages]
    if added:
        messages.success(request, f"{added} paper{'s' if added != 1 else ''} added from {len(files)} file(s).")
    for p in (problems + skipped)[:10]:
        messages.warning(request, p)
    return redirect(exam.get_absolute_url() + "#papers")


def detail(request, uid):
    paper = get_object_or_404(Paper.objects.select_related("exam", "source"), uid=uid)
    siblings = Paper.objects.filter(exam=paper.exam).order_by("sequence")
    prev_paper = siblings.filter(sequence__lt=paper.sequence).order_by("-sequence").first()
    next_paper = siblings.filter(sequence__gt=paper.sequence).first()
    from apps.results.services import stale_reasons

    result = getattr(paper, "result", None)
    return render(request, "papers/detail.html", {"paper": paper, "exam": paper.exam,
                                                  "prev_paper": prev_paper, "next_paper": next_paper,
                                                  "result_stale": stale_reasons(result) if result else []})


def page_image(request, uid, page):
    paper = get_object_or_404(Paper.objects.select_related("source"), uid=uid)
    if page not in paper.pages:
        raise Http404
    width = 600 if request.GET.get("size") == "thumb" else 1400
    return png_response(paper.source.path, paper.source.kind, page, width=width)


def source_download(request, uid):
    paper = get_object_or_404(Paper.objects.select_related("source"), uid=uid)
    return download_response(paper.source.path, paper.source.original_name)


@require_POST
def set_excluded(request, uid):
    paper = get_object_or_404(Paper.objects.select_related("exam"), uid=uid)
    if paper.exam.is_finalized:
        messages.error(request, "This examination is finalized.")
        return redirect(paper.get_absolute_url())
    exclude = request.POST.get("exclude") == "1"
    if exclude:
        if paper.status == Paper.Status.PROCESSING:
            messages.error(request, "This paper is being processed; try again when processing finishes.")
            return redirect(paper.get_absolute_url())
        paper.status = Paper.Status.EXCLUDED
        paper.status_note = (request.POST.get("reason") or "Excluded by teacher").strip()[:255]
        messages.info(request, f"Paper {paper.sequence} excluded. It stays stored and can be restored.")
    elif paper.status == Paper.Status.EXCLUDED:
        if paper.processed_at and paper.answers:
            blocking = any(f.get("blocking") for f in paper.review_flags) or bool(paper.uncertain_questions)
            paper.status = Paper.Status.NEEDS_REVIEW if blocking and not paper.reviewed_at else Paper.Status.PROCESSED
        else:
            paper.status = Paper.Status.UPLOADED
        paper.status_note = ""
        messages.success(request, f"Paper {paper.sequence} restored.")
    paper.save(update_fields=["status", "status_note", "updated_at"])
    from apps.processing.engine import check_roll_conflicts
    from apps.results.services import auto_evaluate

    check_roll_conflicts(paper)
    auto_evaluate(paper)
    return redirect(paper.get_absolute_url())
