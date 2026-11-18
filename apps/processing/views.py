from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.previews import image_file_response
from apps.exams.models import Examination
from apps.papers.models import Paper
from apps.papers.services import paper_stats

from . import review, worker
from .models import Correction, ProcessingJob


def _stale(paper):
    from apps.results.services import stale_reasons

    r = getattr(paper, "result", None)
    return stale_reasons(r) if r else []


def _png(data: bytes) -> HttpResponse:
    resp = HttpResponse(data, content_type="image/png")
    resp["Cache-Control"] = "private, no-cache"
    return resp


# --- processing -------------------------------------------------------------------------

@require_POST
def start(request, exam_uid):
    exam = get_object_or_404(Examination, uid=exam_uid)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest"
    try:
        job = worker.enqueue(exam, request.user)
    except worker.ProcessingError as exc:
        if wants_json:
            active = exam.processing_jobs.filter(status__in=[ProcessingJob.Status.QUEUED,
                                                              ProcessingJob.Status.RUNNING]).exists()
            return JsonResponse({"started": False, "running": active, "error": str(exc)})
        messages.error(request, str(exc))
    else:
        worker.ensure_worker()
        if wants_json:
            return JsonResponse({"started": True, "running": True, "job": job.as_dict()})
        messages.success(request, f"Processing {job.total} paper{'s' if job.total != 1 else ''}. "
                                  "You can leave this page — progress is saved paper by paper.")
    return redirect(exam.get_absolute_url() + "#processing")


@require_POST
def reprocess(request, exam_uid):
    exam = get_object_or_404(Examination, uid=exam_uid)
    uids = request.POST.getlist("papers")
    papers = list(Paper.objects.filter(exam=exam, uid__in=uids)) if uids else None
    if uids and not papers:
        messages.error(request, "Select at least one paper to reprocess.")
        return redirect(exam.get_absolute_url() + "#papers")
    try:
        job = worker.enqueue(exam, request.user, papers=papers, reprocess=True)
    except worker.ProcessingError as exc:
        messages.error(request, str(exc))
    else:
        worker.ensure_worker()
        messages.success(request, f"Reprocessing {job.total} paper{'s' if job.total != 1 else ''}. "
                                  "Your manual corrections are kept.")
    return redirect(exam.get_absolute_url() + "#processing")


@require_POST
def cancel(request, exam_uid):
    exam = get_object_or_404(Examination, uid=exam_uid)
    job = exam.processing_jobs.filter(status__in=[ProcessingJob.Status.QUEUED, ProcessingJob.Status.RUNNING]).first()
    if job:
        worker.request_cancel(job)
        messages.info(request, "Stopping after the current paper. Processed papers are kept.")
    return redirect(exam.get_absolute_url() + "#processing")


def status(request, exam_uid):
    """Polled by the workspace while processing runs; also resumes interrupted work."""
    exam = get_object_or_404(Examination, uid=exam_uid)
    worker.ensure_worker()
    job = exam.processing_jobs.first()
    return JsonResponse({"job": job.as_dict() if job else None, "stats": paper_stats(exam)})


def center(request):
    worker.ensure_worker()
    jobs = ProcessingJob.objects.select_related("exam", "created_by")[:30]
    exams = (Examination.objects.filter(status=Examination.Status.ACTIVE)
             .annotate(total=Count("papers", filter=~Q(papers__status=Paper.Status.EXCLUDED)),
                       awaiting=Count("papers", filter=Q(papers__status__in=[Paper.Status.UPLOADED,
                                                                             Paper.Status.QUEUED])),
                       review=Count("papers", filter=Q(papers__status=Paper.Status.NEEDS_REVIEW)),
                       failed=Count("papers", filter=Q(papers__status=Paper.Status.FAILED)),
                       processed=Count("papers", filter=Q(papers__status=Paper.Status.PROCESSED)))
             .filter(total__gt=0))
    return render(request, "processing/center.html", {"jobs": jobs, "exams": exams})


# --- manual verification ------------------------------------------------------------------

def queue(request):
    papers = (Paper.objects.filter(status__in=[Paper.Status.NEEDS_REVIEW, Paper.Status.FAILED])
              .select_related("exam", "source").order_by("exam__exam_date", "exam_id", "sequence"))
    exam_uid = request.GET.get("exam")
    if exam_uid:
        papers = papers.filter(exam__uid=exam_uid)
    return render(request, "processing/queue.html", {"papers": papers, "exam_uid": exam_uid})


def review_paper(request, uid):
    paper = get_object_or_404(Paper.objects.select_related("exam", "exam__sheet_layout", "source"), uid=uid)
    if paper.status in (Paper.Status.UPLOADED, Paper.Status.QUEUED, Paper.Status.PROCESSING):
        messages.info(request, "This paper has not been processed yet.")
        return redirect(paper.get_absolute_url())
    if paper.status == Paper.Status.FAILED:
        return render(request, "processing/failed.html", {"paper": paper, "exam": paper.exam})

    problems = []
    if request.method == "POST":
        if paper.exam.is_finalized:
            messages.error(request, "This examination is finalized; results can no longer be changed.")
            return redirect(request.path)
        confirm = request.POST.get("action") == "confirm"
        problems = review.save_review(paper, request.POST, request.user, confirm)
        if not problems:
            if confirm:
                messages.success(request, f"Paper {paper.sequence} verified.")
                nxt = (Paper.objects.filter(exam=paper.exam, status=Paper.Status.NEEDS_REVIEW)
                       .exclude(pk=paper.pk).order_by("sequence").first())
                return redirect("processing:review", nxt.uid) if nxt else redirect(paper.exam.get_absolute_url())
            messages.success(request, "Corrections saved.")
            return redirect(request.path)
        paper.refresh_from_db()

    rows = review.review_rows(paper)
    return render(request, "processing/review.html", {
        "paper": paper, "exam": paper.exam, "rows": rows, "problems": problems,
        "flagged": [r for r in rows if r["uncertain"] or r["teacher"] or r["entry"].get("status") == "multiple"],
        "labels": list(paper.exam.option_labels),
        "has_roll_grid": bool(paper.reading.get("roll_digits")),
        "corrections": Correction.objects.filter(paper=paper).select_related("user")[:50],
        "remaining": Paper.objects.filter(exam=paper.exam, status=Paper.Status.NEEDS_REVIEW).count(),
        "student": paper.student,
        "result_stale": _stale(paper),
    })


def processed_image(request, uid):
    paper = get_object_or_404(Paper, uid=uid)
    if not paper.processed_image:
        raise Http404
    return image_file_response(paper.processed_image)


def question_image(request, uid, q):
    paper = get_object_or_404(Paper.objects.select_related("exam__sheet_layout"), uid=uid)
    try:
        return _png(review.question_crop(paper, q))
    except ValidationError:
        raise Http404


def roll_image(request, uid):
    paper = get_object_or_404(Paper.objects.select_related("exam__sheet_layout"), uid=uid)
    try:
        return _png(review.roll_crop(paper))
    except ValidationError:
        raise Http404
