from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.exams.models import Examination
from apps.papers.models import Paper
from evaluation import EvaluationError

from . import analytics, services
from .models import FinalizationEvent


@require_POST
def evaluate(request, exam_uid):
    exam = get_object_or_404(Examination, uid=exam_uid)
    try:
        run = services.evaluate_exam(exam, request.user)
    except EvaluationError as exc:
        messages.error(request, str(exc))
    else:
        msg = f"{run.papers_evaluated} paper{'s' if run.papers_evaluated != 1 else ''} evaluated with " \
              f"“{run.key_version.key.title}” v{run.key_version.number}."
        if run.skipped:
            msg += f" {len(run.skipped)} not evaluated yet (awaiting processing or review)."
        messages.success(request, msg)
    return redirect(request.POST.get("next") or exam.get_absolute_url() + "#results")


def index(request, view="results"):
    exams = (Examination.objects.filter(answer_key_version__isnull=False)
             .annotate(papers_total=Count("papers", filter=~Q(papers__status=Paper.Status.EXCLUDED)))
             .filter(papers_total__gt=0).select_related("answer_key_version__key"))
    rows = []
    for exam in exams:
        stats = analytics.class_stats(exam)
        rows.append({"exam": exam, "stats": stats})
    return render(request, "results/index.html", {
        "rows": rows, "view": view, "subjects": analytics.subject_performance() if view == "results" else [],
    })


def class_results(request, exam_uid):
    exam = get_object_or_404(Examination.objects.select_related("answer_key_version__key", "finalized_by"),
                             uid=exam_uid)
    rows = analytics.class_rows(exam)
    stats = analytics.class_stats(exam, rows)
    done = [row["result"] for row in rows if row["result"]]
    chart = {
        "distribution": analytics.distribution(done),
        "passFail": {"passed": stats["passed"], "failed": stats["failed"], "pending": stats["pending"]},
        "students": [{"label": row["roll"] or f"Paper {row['paper'].sequence}",
                      "name": row["name"], "marks": float(row["result"].obtained),
                      "pct": float(row["result"].percentage), "passed": row["result"].passed}
                     for row in sorted((r for r in rows if r["result"]), key=lambda r: -r["result"].obtained)],
        "passMarks": float(exam.pass_marks),
        "maxMarks": float(exam.max_marks),
    }
    return render(request, "results/class.html", {
        "exam": exam, "rows": rows, "stats": stats, "chart": chart,
        "blockers": services.finalize_blockers(exam) if not exam.is_finalized else [],
        "stale": services.summary(exam)["stale"],
        "events": FinalizationEvent.objects.filter(exam=exam).select_related("user")[:10],
    })


def question_analysis(request, exam_uid):
    exam = get_object_or_404(Examination.objects.select_related("answer_key_version__key"), uid=exam_uid)
    stats = analytics.question_analysis(exam) if exam.answer_key_version_id else []
    summary = analytics.difficulty_summary(stats)
    students = stats[0]["students"] if stats else 0
    chart = {"questions": [{"q": s["q"], "correct": float(s["correct_pct"]), "incorrect": float(s["incorrect_pct"]),
                            "unanswered": s["unanswered"], "students": s["students"]} for s in stats]}
    return render(request, "results/questions.html", {"exam": exam, "stats": stats, "summary": summary,
                                                      "students": students, "chart": chart,
                                                      "stale": services.summary(exam)["stale"]})


@require_POST
def finalize(request, exam_uid):
    exam = get_object_or_404(Examination, uid=exam_uid)
    try:
        services.finalize(exam, request.user, request.POST.get("note", ""))
    except EvaluationError as exc:
        messages.error(request, f"Results cannot be finalized yet. {exc}")
    else:
        messages.success(request, "Results finalized. The examination is now locked: papers, answers, the key "
                                  "and marking rules can no longer be changed.")
    return redirect("results:class", exam.uid)


@require_POST
def reopen(request, exam_uid):
    if not request.user.is_superuser:
        raise PermissionDenied("Only an administrator can reopen finalized results.")
    exam = get_object_or_404(Examination, uid=exam_uid)
    try:
        services.reopen(exam, request.user, request.POST.get("note", ""))
    except EvaluationError as exc:
        messages.error(request, str(exc))
    else:
        messages.warning(request, "Results reopened. Changes are possible again; finalize when done.")
    return redirect("results:class", exam.uid)
