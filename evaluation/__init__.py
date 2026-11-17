"""Evaluation engine.

Pure Python (no Django, no OCR): takes a student's final responses, the
verified answer key and the examination's marking rules, and computes the
result with exact decimal arithmetic. It refuses to evaluate responses that
are still uncertain.
"""

from .engine import (
    EvaluationError,
    MarkingRules,
    PaperResult,
    QuestionResult,
    Response,
    competition_ranks,
    evaluate,
)

__all__ = ["EvaluationError", "MarkingRules", "PaperResult", "QuestionResult", "Response", "competition_ranks",
           "evaluate"]
