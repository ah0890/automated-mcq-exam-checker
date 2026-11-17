"""Class statistics and question-wise analysis, derived only from stored,
current (non-stale) results of verified papers."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from apps.exams.models import Examination
from apps.papers.models import Paper
from apps.students.models import Student

from .models import Result
from .services import stale_reasons

HIGH_ERROR = Decimal("50")   # ≥ 50 % of students answered incorrectly
MOSTLY_CORRECT = Decimal("80")
TWO = Decimal("0.01")


def _pct(n: int, d: int) -> Decimal:
    return (Decimal(n) * 100 / d).quantize(TWO) if d else Decimal("0")


def current_results(exam: Examination) -> list[Result]:
    results = (Result.objects.filter(exam=exam, paper__status=Paper.Status.PROCESSED)
               .select_related("paper", "exam", "exam__answer_key_version", "key_version"))
    return [r for r in results if not stale_reasons(r)]


def class_rows(exam: Examination) -> list[dict]:
    """One row per active paper — evaluated papers with their result, others as pending."""
    names = dict(Student.objects.filter(class_section=exam.class_section).values_list("roll_number", "name"))
    current = {r.paper_id: r for r in current_results(exam)}
    rows = []
    papers = Paper.objects.filter(exam=exam).exclude(status=Paper.Status.EXCLUDED).order_by("sequence")
    for p in papers:
        r = current.get(p.pk)
        rows.append({
            "paper": p,
            "result": r,
            "rank": r.rank if r else None,
            "roll": p.roll_number,
            "name": names.get(p.roll_number, ""),
            "status": ("pass" if r.passed else "fail") if r else "pending",
            "pending_reason": "" if r else (p.get_status_display() if p.status != Paper.Status.PROCESSED
                                            else "Awaiting re-evaluation"),
        })
    rows.sort(key=lambda row: (row["rank"] is None, row["rank"] or 0, row["paper"].sequence))
    return rows


def class_stats(exam: Examination, rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else class_rows(exam)
    done = [row["result"] for row in rows if row["result"]]
    n = len(done)
    marks = [r.obtained for r in done]
    return {
        "students": len(rows),
        "uploaded": Paper.objects.filter(exam=exam).count(),
        "evaluated": n,
        "pending": len(rows) - n,
        "needs_review": sum(1 for row in rows if row["paper"].status in (Paper.Status.NEEDS_REVIEW,
                                                                         Paper.Status.FAILED)),
        "average": (sum(marks, Decimal("0")) / n).quantize(TWO) if n else None,
        "average_pct": (sum((r.percentage for r in done), Decimal("0")) / n).quantize(TWO) if n else None,
        "highest": max(marks) if n else None,
        "lowest": min(marks) if n else None,
        "passed": sum(1 for r in done if r.passed),
        "failed": sum(1 for r in done if not r.passed),
        "pass_rate": _pct(sum(1 for r in done if r.passed), n) if n else None,
        "max_marks": exam.max_marks,
    }


def distribution(results: list[Result]) -> list[dict]:
    """Percentage bands 0–9, 10–19, …, 90–100 (negative totals fall in the first band)."""
    bands = [{"label": f"{lo}–{lo + 9}" if lo < 90 else "90–100", "lo": lo, "count": 0} for lo in range(0, 100, 10)]
    for r in results:
        idx = min(9, max(0, int(r.percentage // 10)))
        bands[idx]["count"] += 1
    return bands


def question_analysis(exam: Examination) -> list[dict]:
    results = current_results(exam)
    labels = exam.option_labels
    n = len(results)
    stats = []
    for q in range(1, exam.num_questions + 1):
        outcomes = Counter()
        chosen = Counter()
        wrong_choices = Counter()
        key = exam.answer_key_version.answers.get(str(q)) if exam.answer_key_version_id else None
        for r in results:
            item = r.questions[q - 1] if q - 1 < len(r.questions) else None
            if not item:
                continue
            outcomes[item["outcome"]] += 1
            resp = item.get("response")
            if item["outcome"] in ("correct", "incorrect") and resp and len(resp) == 1:
                chosen[resp] += 1
                if item["outcome"] == "incorrect":
                    wrong_choices[resp] += 1
        correct_pct = _pct(outcomes["correct"], n)
        incorrect_pct = _pct(outcomes["incorrect"], n)
        top_wrong = wrong_choices.most_common(1)
        stats.append({
            "q": q, "key": key or "", "students": n,
            "correct": outcomes["correct"], "incorrect": outcomes["incorrect"],
            "unanswered": outcomes["unanswered"], "multiple": outcomes["multiple"],
            "correct_pct": correct_pct, "incorrect_pct": incorrect_pct,
            "top_wrong": top_wrong[0][0] if top_wrong else "",
            "top_wrong_count": top_wrong[0][1] if top_wrong else 0,
            "options": [{"label": lab, "count": chosen.get(lab, 0), "is_key": lab == key} for lab in labels],
            "high_error": n > 0 and incorrect_pct >= HIGH_ERROR,
            "mostly_correct": n > 0 and correct_pct >= MOSTLY_CORRECT,
        })
    return stats


def difficulty_summary(stats: list[dict], limit: int = 5) -> dict:
    answered = [s for s in stats if s["students"]]
    hardest = sorted(answered, key=lambda s: (s["correct_pct"], -s["incorrect"], s["q"]))[:limit]
    easiest = sorted(answered, key=lambda s: (-s["correct_pct"], s["q"]))[:limit]
    return {
        "hardest": hardest,
        "easiest": easiest,
        "high_error": [s for s in answered if s["high_error"]],
        "mostly_correct": [s for s in answered if s["mostly_correct"]],
    }


def subject_performance() -> list[dict]:
    """Average percentage per subject across examinations (current results only)."""
    by_subject: dict[str, list[Decimal]] = {}
    exams = Examination.objects.filter(answer_key_version__isnull=False)
    for exam in exams:
        for r in current_results(exam):
            by_subject.setdefault(exam.subject, []).append(r.percentage)
    return [{"subject": s, "students": len(v), "average_pct": (sum(v, Decimal("0")) / len(v)).quantize(TWO)}
            for s, v in sorted(by_subject.items()) if v]
