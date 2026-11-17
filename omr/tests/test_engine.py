import unittest

import cv2
import numpy as np

from omr import LayoutSpec, OMRError, ReadingThresholds, build_template, read_sheet
from omr.reader import ANSWERED, BLANK, MULTIPLE, UNCERTAIN, _classify

from .sheetgen import draw_sheet

T = ReadingThresholds()


class ClassificationRules(unittest.TestCase):
    def test_single_clear_mark(self):
        r = _classify(1, [0.0, 0.66, 0.0, 0.0], "ABCD", T)
        self.assertEqual((r.status, r.value), (ANSWERED, "B"))
        self.assertEqual(r.confidence, 1.0)

    def test_blank(self):
        r = _classify(1, [0.0, 0.02, 0.0, 0.01], "ABCD", T)
        self.assertEqual((r.status, r.value), (BLANK, None))

    def test_multiple(self):
        r = _classify(1, [0.66, 0.65, 0.0, 0.0], "ABCD", T)
        self.assertEqual((r.status, r.value), (MULTIPLE, "AB"))

    def test_faint_mark_is_never_guessed(self):
        r = _classify(1, [0.0, 0.28, 0.0, 0.0], "ABCD", T)
        self.assertEqual((r.status, r.value), (UNCERTAIN, None))
        self.assertEqual(r.candidates, "B")

    def test_clear_mark_plus_partial_erasure_is_uncertain(self):
        r = _classify(1, [0.66, 0.0, 0.25, 0.0], "ABCD", T)
        self.assertEqual(r.status, UNCERTAIN)
        self.assertEqual(r.candidates, "AC")


class GeneratedSheetLayouts(unittest.TestCase):
    """Calibration must adapt to the layout, not assume one sheet design."""

    def check_layout(self, *, questions, options, blocks, roll_digits, order):
        labels = "ABCDEFGH"[:options]
        blank = draw_sheet(questions=questions, options=options, blocks=blocks, roll_digits=roll_digits, order=order)
        spec = LayoutSpec(options_per_question=options, option_labels=labels, roll_digits=roll_digits, question_order=order)
        tpl = build_template(blank, spec)
        self.assertEqual(tpl.capacity, questions)
        self.assertEqual(len(tpl.roll_columns), roll_digits)

        answers = {q: [(q * 7) % options] for q in range(1, questions + 1)}
        answers[2] = []                # blank
        answers[3] = [0, options - 1]  # multiple
        roll = "".join(str((i * 3 + 1) % 10) for i in range(roll_digits))
        sheet = draw_sheet(questions=questions, options=options, blocks=blocks, roll_digits=roll_digits,
                           answers=answers, roll=roll, fill_level={4: 185}, order=order)
        reading, _ = read_sheet(sheet, tpl)
        self.assertEqual(reading.roll_number, roll)
        for a in reading.answers:
            if a.number == 2:
                self.assertEqual(a.status, BLANK)
            elif a.number == 3:
                self.assertEqual(a.status, MULTIPLE)
            elif a.number == 4:
                self.assertEqual(a.status, UNCERTAIN, a)
            else:
                self.assertEqual((a.status, a.value), (ANSWERED, labels[answers[a.number][0]]), a)
        return reading

    def test_four_options_two_blocks_column_order(self):
        self.check_layout(questions=40, options=4, blocks=2, roll_digits=5, order="column")

    def test_five_options_three_blocks_row_order(self):
        self.check_layout(questions=30, options=5, blocks=3, roll_digits=3, order="row")

    def test_wrong_option_count_is_rejected(self):
        blank = draw_sheet(questions=20, options=4, blocks=2, roll_digits=4)
        with self.assertRaises(OMRError) as ctx:
            build_template(blank, LayoutSpec(options_per_question=5, option_labels="ABCDE", roll_digits=4))
        self.assertEqual(ctx.exception.code, "calibration_answer_grid")

    def test_rotated_upside_down_page(self):
        blank = draw_sheet(questions=20, options=4, blocks=2, roll_digits=4)
        tpl = build_template(blank, LayoutSpec(roll_digits=4))
        sheet = draw_sheet(questions=20, options=4, blocks=2, roll_digits=4,
                           answers={q: [q % 4] for q in range(1, 21)}, roll="2741")
        reading, _ = read_sheet(cv2.rotate(sheet, cv2.ROTATE_180), tpl)
        self.assertEqual(reading.alignment["rotation"], 180)
        self.assertEqual(reading.roll_number, "2741")
        self.assertEqual([a.value for a in reading.answers], ["ABCD"[q % 4] for q in range(1, 21)])

    def test_page_without_markers_is_an_error(self):
        blank = draw_sheet(questions=20, options=4, blocks=2, roll_digits=4)
        tpl = build_template(blank, LayoutSpec(roll_digits=4))
        with self.assertRaises(OMRError) as ctx:
            read_sheet(np.full((1754, 1240), 255, np.uint8), tpl)
        self.assertEqual(ctx.exception.code, "markers_not_found")

    def test_question_count_beyond_capacity_is_rejected(self):
        blank = draw_sheet(questions=20, options=4, blocks=2, roll_digits=4)
        tpl = build_template(blank, LayoutSpec(roll_digits=4))
        with self.assertRaises(OMRError):
            read_sheet(blank, tpl, num_questions=21)
