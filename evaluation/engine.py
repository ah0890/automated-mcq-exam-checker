from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Hashable, Iterable, Mapping

TWO_PLACES = Decimal("0.01")

CORRECT, INCORRECT, UNANSWERED, MULTIPLE = "correct", "incorrect", "unanswered", "multiple"


class EvaluationError(ValueError):
    pass


@dataclass(frozen=True)
class MarkingRules:
    num_questions: int
    marks_per_question: Decimal = Decimal("1")
    negative_marking: bool = False
    negative_marks: Decimal = Decimal("0")
    multiple_mark_policy: str = "zero"  # "zero" | "incorrect"
    passing_percentage: Decimal = Decimal("40")

    def __post_init__(self):
        for name in ("marks_per_question", "negative_marks", "passing_percentage"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                object.__setattr__(self, name, Decimal(str(value)))
        if self.num_questions < 1:
            raise EvaluationError("An examination needs at least one question.")
        if self.marks_per_question <= 0:
            raise EvaluationError("Marks per question must be positive.")
        if self.negative_marks < 0:
            raise EvaluationError("Negative marks cannot be below zero.")
        if not Decimal("0") <= self.passing_percentage <= Decimal("100"):
            raise EvaluationError("Passing percentage must be between 0 and 100.")
        if self.multiple_mark_policy not in ("zero", "incorrect"):
            raise EvaluationError("Unknown multiple-mark policy.")

    @property
    def penalty(self) -> Decimal:
        return self.negative_marks if self.negative_marking else Decimal("0")

    @property
    def max_marks(self) -> Decimal:
        return self.marks_per_question * self.num_questions

    def as_dict(self) -> dict:
        return {"num_questions": self.num_questions, "marks_per_question": str(self.marks_per_question),
                "negative_marking": self.negative_marking, "negative_marks": str(self.negative_marks),
                "multiple_mark_policy": self.multiple_mark_policy,
                "passing_percentage": str(self.passing_percentage)}


@dataclass(frozen=True)
class Response:
    """A student's final response to one question.

    status: "answered" (value = one option), "blank", "multiple" (value = the
    options marked) or "uncertain" (not yet resolved — cannot be evaluated).
    """

    status: str
    value: str | None = None


@dataclass
class QuestionResult:
    question: int
    key: str
    response: str | None
    outcome: str
    marks: Decimal


@dataclass
class PaperResult:
    correct: int
    incorrect: int
    unanswered: int
    multiple: int
    obtained: Decimal
    max_marks: Decimal
    percentage: Decimal
    passed: bool
    questions: list[QuestionResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("obtained", "max_marks", "percentage"):
            d[k] = str(d[k])
        d["questions"] = [{**q, "marks": str(q["marks"])} for q in d["questions"]]
        return d


def evaluate(responses: Mapping[int, Response], key: Mapping[int, str], rules: MarkingRules) -> PaperResult:
    """Mark one paper.

    Obtained marks = correct × marks per question − incorrect × negative marks
    (negative marking only when enabled). Unanswered questions score zero.
    Multiple marks score zero, or count as incorrect under the "incorrect"
    policy. The total is not floored at zero, exactly as the formula states.
    """
    n = rules.num_questions
    missing_key = [q for q in range(1, n + 1) if not key.get(q)]
    if missing_key:
        raise EvaluationError(f"The answer key has no answer for question(s) {missing_key[:10]}.")
    unresolved = [q for q in range(1, n + 1) if responses.get(q, Response("uncertain")).status == "uncertain"]
    if unresolved:
        raise EvaluationError(f"Question(s) {unresolved[:10]} are not resolved; review the paper first.")

    questions: list[QuestionResult] = []
    counts = {CORRECT: 0, INCORRECT: 0, UNANSWERED: 0, MULTIPLE: 0}
    for q in range(1, n + 1):
        r = responses[q]
        k = key[q]
        if r.status == "blank":
            outcome, marks = UNANSWERED, Decimal("0")
        elif r.status == "multiple":
            if rules.multiple_mark_policy == "incorrect":
                outcome, marks = INCORRECT, -rules.penalty
            else:
                outcome, marks = MULTIPLE, Decimal("0")
        elif r.status == "answered":
            if not r.value or len(r.value) != 1:
                raise EvaluationError(f"Question {q}: an answered response must have exactly one option.")
            if r.value == k:
                outcome, marks = CORRECT, rules.marks_per_question
            else:
                outcome, marks = INCORRECT, -rules.penalty
        else:
            raise EvaluationError(f"Question {q}: unknown response status “{r.status}”.")
        counts[outcome] += 1
        questions.append(QuestionResult(q, k, r.value, outcome, marks))

    obtained = sum((qr.marks for qr in questions), Decimal("0"))
    percentage = (obtained / rules.max_marks * 100).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    passed = obtained / rules.max_marks * 100 >= rules.passing_percentage
    return PaperResult(correct=counts[CORRECT], incorrect=counts[INCORRECT], unanswered=counts[UNANSWERED],
                       multiple=counts[MULTIPLE], obtained=obtained.quantize(TWO_PLACES), max_marks=rules.max_marks,
                       percentage=percentage, passed=passed, questions=questions)


def competition_ranks(scores: Iterable[tuple[Hashable, Decimal]]) -> dict[Hashable, int]:
    """Standard competition ranking ("1224"): equal marks share a rank and the
    next rank skips accordingly."""
    ordered = sorted(scores, key=lambda s: s[1], reverse=True)
    ranks: dict[Hashable, int] = {}
    previous = None
    rank = 0
    for position, (ident, score) in enumerate(ordered, start=1):
        if score != previous:
            rank = position
            previous = score
        ranks[ident] = rank
    return ranks
