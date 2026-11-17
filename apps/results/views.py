from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.exams.models import Examination
from evaluation import EvaluationError

from . import services


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
    return redirect(exam.get_absolute_url() + "#results")
