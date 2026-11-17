"""Connects stored papers to the stand-alone evaluation engine."""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal

from django.db import transaction

from apps.exams.models import Examination
from apps.papers.models import Paper
from evaluation import EvaluationError, MarkingRules, Response, competition_ranks, evaluate

from .models import EvaluationRun, Result

log = logging.getLogger("apps.results")


def rules_for(exam: Examination) -> MarkingRules:
    return MarkingRules(num_questions=exam.num_questions, marks_per_question=exam.marks_per_question,
                        negative_marking=exam.negative_marking, negative_marks=exam.negative_marks,
                        multiple_mark_policy=exam.multiple_mark_policy,
                        passing_percentage=exam.passing_percentage)


def key_dict(exam: Examination) -> dict[int, str]:
    return {int(q): a for q, a in exam.answer_key_version.answers.items()}


def responses_for(paper: Paper) -> dict[int, Response]:
    return {int(q): Response(a.get("status", "uncertain"), a.get("value")) for q, a in paper.answers.items()}


def answers_digest(paper: Paper) -> str:
    payload = {q: [a.get("status"), a.get("value")] for q, a in sorted(paper.answers.items(), key=lambda i: int(i[0]))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def stale_reasons(result: Result) -> list[str]:
    exam, paper = result.exam, result.paper
    reasons = []
    if result.key_version_id != exam.answer_key_version_id:
        reasons.append("answer key changed")
    if result.rules != rules_for(exam).as_dict():
        reasons.append("marking rules changed")
    if result.answers_digest != answers_digest(paper):
        reasons.append("answers corrected")
    if paper.status != Paper.Status.PROCESSED:
        reasons.append("paper needs review")
    return reasons


def evaluate_paper(paper: Paper, user=None) -> Result:
    """Evaluate one verified paper against the examination's current key."""
    exam = paper.exam
    if not exam.answer_key_version_id or not exam.answer_key_version.is_verified:
        raise EvaluationError("The examination has no verified answer key.")
    if paper.status != Paper.Status.PROCESSED:
        raise EvaluationError("Only read and verified papers can be evaluated.")
    rules = rules_for(exam)
    outcome = evaluate(responses_for(paper), key_dict(exam), rules)
    data = dict(exam=exam, key_version=exam.answer_key_version, rules=rules.as_dict(),
                answers_digest=answers_digest(paper), roll_number=paper.roll_number,
                correct=outcome.correct, incorrect=outcome.incorrect, unanswered=outcome.unanswered,
                multiple=outcome.multiple, obtained=outcome.obtained, max_marks=outcome.max_marks,
                percentage=outcome.percentage, passed=outcome.passed,
                questions=[{"q": q.question, "key": q.key, "response": q.response, "outcome": q.outcome,
                            "marks": str(q.marks)} for q in outcome.questions],
                evaluated_by=user)
    result, _ = Result.objects.update_or_create(paper=paper, defaults=data)
    return result


def auto_evaluate(paper: Paper) -> Result | None:
    """Called when a paper becomes verified. Marks it with the current key —
    unless the examination's existing results were produced with a different
    key or rules, in which case the teacher must re-evaluate explicitly."""
    exam = paper.exam
    if paper.status != Paper.Status.PROCESSED or not exam.answer_key_version_id or exam.is_finalized:
        update_ranks(exam)
        return None
    current_rules = rules_for(exam).as_dict()
    mismatched = Result.objects.filter(exam=exam).exclude(key_version_id=exam.answer_key_version_id).exists() or \
        any(r != current_rules for r in Result.objects.filter(exam=exam).values_list("rules", flat=True))
    if mismatched:
        update_ranks(exam)
        return None
    try:
        result = evaluate_paper(paper)
    except EvaluationError as exc:
        log.warning("Paper %s not evaluated: %s", paper.uid, exc)
        result = None
    update_ranks(exam)
    return result


def update_ranks(exam: Examination) -> None:
    """Competition ranking over current (non-stale) results of active papers."""
    with transaction.atomic():
        results = list(Result.objects.filter(exam=exam).select_related("paper", "exam"))
        current = [r for r in results if r.paper.status == Paper.Status.PROCESSED and not stale_reasons(r)]
        ranks = competition_ranks([(r.pk, r.obtained) for r in current])
        for r in results:
            new_rank = ranks.get(r.pk)
            if r.rank != new_rank:
                Result.objects.filter(pk=r.pk).update(rank=new_rank)


@transaction.atomic
def evaluate_exam(exam: Examination, user) -> EvaluationRun:
    """Explicit (re-)evaluation of every verified paper. Previous results are
    preserved in the run history."""
    if exam.is_finalized:
        raise EvaluationError("This examination is finalized.")
    if not exam.answer_key_version_id:
        raise EvaluationError("Attach a verified answer key first.")
    papers = Paper.objects.filter(exam=exam).exclude(status=Paper.Status.EXCLUDED).select_related("exam")
    evaluated, skipped = 0, []
    for paper in papers.order_by("sequence"):
        if paper.status != Paper.Status.PROCESSED:
            skipped.append({"paper": paper.sequence, "reason": paper.get_status_display()})
            continue
        try:
            evaluate_paper(paper, user)
            evaluated += 1
        except EvaluationError as exc:
            skipped.append({"paper": paper.sequence, "reason": str(exc)})
    update_ranks(exam)
    current = Result.objects.filter(exam=exam, paper__status=Paper.Status.PROCESSED).select_related("paper")
    run = EvaluationRun.objects.create(
        exam=exam, key_version=exam.answer_key_version, rules=rules_for(exam).as_dict(), papers_evaluated=evaluated,
        skipped=skipped, summary=_json_summary(summary(exam)), created_by=user,
        snapshot=[{"paper": r.paper.sequence, "roll_number": r.roll_number, "correct": r.correct,
                   "incorrect": r.incorrect, "unanswered": r.unanswered, "multiple": r.multiple,
                   "obtained": str(r.obtained), "percentage": str(r.percentage), "passed": r.passed,
                   "rank": r.rank} for r in current.order_by("paper__sequence")])
    log.info("Exam %s evaluated: %s papers (%s skipped)", exam.uid, evaluated, len(skipped))
    return run


def _json_summary(s: dict) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in s.items()}


def summary(exam: Examination) -> dict:
    results = list(Result.objects.filter(exam=exam).exclude(paper__status=Paper.Status.EXCLUDED)
                   .select_related("paper", "exam", "exam__answer_key_version"))
    current = [r for r in results if not stale_reasons(r)]
    active = Paper.objects.filter(exam=exam).exclude(status=Paper.Status.EXCLUDED)
    n = len(current)
    obtained = [r.obtained for r in current]
    return {
        "papers": active.count(),
        "evaluated": n,
        "stale": len(results) - n,
        "pending": active.exclude(pk__in=[r.paper_id for r in current]).count(),
        "passed": sum(1 for r in current if r.passed),
        "failed": sum(1 for r in current if not r.passed),
        "average": (sum(obtained, Decimal("0")) / n).quantize(Decimal("0.01")) if n else None,
        "average_pct": (sum((r.percentage for r in current), Decimal("0")) / n).quantize(Decimal("0.01"))
        if n else None,
        "highest": max(obtained) if n else None,
        "lowest": min(obtained) if n else None,
        "max_marks": exam.max_marks,
    }
