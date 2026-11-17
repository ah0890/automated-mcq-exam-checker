"""Evaluation engine tests. Every expected value is worked out by hand."""

import unittest
from decimal import Decimal as D

from evaluation import EvaluationError, MarkingRules, Response, competition_ranks, evaluate

KEY10 = dict(enumerate("ABCDABCDAB", start=1))


def responses(spec: str) -> dict:
    """'A'..'D' = answered, '-' = blank, '*' = multiple (AB), '?' = uncertain."""
    out = {}
    for q, ch in enumerate(spec, start=1):
        if ch == "-":
            out[q] = Response("blank")
        elif ch == "*":
            out[q] = Response("multiple", "AB")
        elif ch == "?":
            out[q] = Response("uncertain")
        else:
            out[q] = Response("answered", ch)
    return out


class DefaultMarking(unittest.TestCase):
    rules = MarkingRules(num_questions=10)

    def test_all_correct(self):
        r = evaluate(responses("ABCDABCDAB"), KEY10, self.rules)
        self.assertEqual((r.correct, r.incorrect, r.unanswered, r.multiple), (10, 0, 0, 0))
        self.assertEqual((r.obtained, r.max_marks, r.percentage, r.passed), (D("10.00"), D("10"), D("100.00"), True))

    def test_mixed(self):
        # Q1-6 correct, Q7 wrong, Q8 blank, Q9 multiple, Q10 wrong
        r = evaluate(responses("ABCDABA-*C"), KEY10, self.rules)
        self.assertEqual((r.correct, r.incorrect, r.unanswered, r.multiple), (6, 2, 1, 1))
        self.assertEqual((r.obtained, r.percentage), (D("6.00"), D("60.00")))
        self.assertEqual([q.outcome for q in r.questions][6:],
                         ["incorrect", "unanswered", "multiple", "incorrect"])

    def test_all_blank_scores_zero_and_fails(self):
        r = evaluate(responses("----------"), KEY10, self.rules)
        self.assertEqual((r.unanswered, r.obtained, r.passed), (10, D("0.00"), False))

    def test_pass_boundary_is_inclusive(self):
        # 4/10 = 40% with a 40% pass mark → pass; 3/10 → fail
        self.assertTrue(evaluate(responses("ABCD------"), KEY10, self.rules).passed)
        self.assertFalse(evaluate(responses("ABC-------"), KEY10, self.rules).passed)


class NegativeMarking(unittest.TestCase):
    def test_formula(self):
        rules = MarkingRules(num_questions=10, marks_per_question=D("1"), negative_marking=True,
                             negative_marks=D("0.25"))
        # 6 correct, 3 incorrect, 1 blank → 6 − 0.75 = 5.25
        r = evaluate(responses("ABCDABBBB-"), KEY10, rules)
        self.assertEqual((r.correct, r.incorrect, r.unanswered), (6, 3, 1))
        self.assertEqual((r.obtained, r.percentage), (D("5.25"), D("52.50")))

    def test_precise_decimals(self):
        rules = MarkingRules(num_questions=3, marks_per_question=D("0.1"), negative_marking=True,
                             negative_marks=D("0.05"), passing_percentage=D("50"))
        key = {1: "A", 2: "B", 3: "C"}
        # 0.1 + 0.1 − 0.05 = 0.15 exactly (binary floats would give 0.15000000000000002)
        r = evaluate(responses("ABD"), key, rules)
        self.assertEqual(r.obtained, D("0.15"))
        self.assertEqual(r.percentage, D("50.00"))
        self.assertTrue(r.passed)

    def test_total_can_be_negative_per_formula(self):
        rules = MarkingRules(num_questions=4, negative_marking=True, negative_marks=D("1"))
        r = evaluate(responses("BBBB"), {1: "A", 2: "A", 3: "A", 4: "B"}, rules)
        self.assertEqual(r.obtained, D("-2.00"))  # 1 correct − 3 wrong
        self.assertEqual(r.percentage, D("-50.00"))

    def test_negative_marks_ignored_when_disabled(self):
        rules = MarkingRules(num_questions=10, negative_marking=False, negative_marks=D("1"))
        r = evaluate(responses("BBBBBBBBBB"), KEY10, rules)
        self.assertEqual(r.obtained, D("3.00"))  # Q2, Q6, Q10 are B


class MultipleMarkPolicy(unittest.TestCase):
    def test_zero_policy_does_not_penalise(self):
        rules = MarkingRules(num_questions=10, negative_marking=True, negative_marks=D("0.5"),
                             multiple_mark_policy="zero")
        r = evaluate(responses("ABCDABCD**"), KEY10, rules)
        self.assertEqual((r.multiple, r.incorrect, r.obtained), (2, 0, D("8.00")))

    def test_incorrect_policy_applies_negative_marks(self):
        rules = MarkingRules(num_questions=10, negative_marking=True, negative_marks=D("0.5"),
                             multiple_mark_policy="incorrect")
        r = evaluate(responses("ABCDABCD**"), KEY10, rules)
        self.assertEqual((r.multiple, r.incorrect, r.obtained), (0, 2, D("7.00")))


class Safety(unittest.TestCase):
    def test_uncertain_responses_are_refused(self):
        with self.assertRaises(EvaluationError):
            evaluate(responses("ABCDABCDA?"), KEY10, MarkingRules(num_questions=10))

    def test_missing_response_is_treated_as_unresolved(self):
        r = responses("ABCDABCDAB")
        del r[10]
        with self.assertRaises(EvaluationError):
            evaluate(r, KEY10, MarkingRules(num_questions=10))

    def test_incomplete_key_is_refused(self):
        key = dict(KEY10)
        del key[5]
        with self.assertRaises(EvaluationError):
            evaluate(responses("ABCDABCDAB"), key, MarkingRules(num_questions=10))

    def test_only_examined_questions_count(self):
        # Exam uses 5 of the 10 key entries.
        r = evaluate(responses("ABCDA"), KEY10, MarkingRules(num_questions=5))
        self.assertEqual((r.correct, r.max_marks), (5, D("5")))

    def test_invalid_rules(self):
        with self.assertRaises(EvaluationError):
            MarkingRules(num_questions=0)
        with self.assertRaises(EvaluationError):
            MarkingRules(num_questions=5, passing_percentage=D("120"))
        with self.assertRaises(EvaluationError):
            MarkingRules(num_questions=5, multiple_mark_policy="half")


class Ranking(unittest.TestCase):
    def test_competition_ranking_with_ties(self):
        ranks = competition_ranks([("a", D("40")), ("b", D("45")), ("c", D("40")), ("d", D("38")),
                                   ("e", D("45")), ("f", D("12.5"))])
        self.assertEqual(ranks, {"b": 1, "e": 1, "a": 3, "c": 3, "d": 5, "f": 6})

    def test_single_and_empty(self):
        self.assertEqual(competition_ranks([("x", D("0"))]), {"x": 1})
        self.assertEqual(competition_ranks([]), {})

    def test_decimal_ties_are_exact(self):
        ranks = competition_ranks([("a", D("5.25")), ("b", D("5.250")), ("c", D("5.24"))])
        self.assertEqual((ranks["a"], ranks["b"], ranks["c"]), (1, 1, 3))
