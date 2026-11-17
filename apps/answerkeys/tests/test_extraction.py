"""Answer-key extraction tests.

Generated documents exercise each format and layout; the private sample key
(samples/, git-ignored) is checked against its manually verified answers.
"""

import io
import json
import unittest
from pathlib import Path

import pymupdf
from django.conf import settings
from django.test import SimpleTestCase

from apps.answerkeys.extraction import extract, normalise_ocr_token

KEY = "CADBBACDAB" * 3  # 30 questions used by generated fixtures
SAMPLES = Path(settings.BASE_DIR) / "samples"


def make_pdf_table(answers: str, columns: int = 3) -> bytes:
    """A multi-column 'Question | Answer' table like a printed answer key."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "OFFICIAL ANSWER KEY   Total Questions 30", fontsize=12)
    rows = -(-len(answers) // columns)
    for i, a in enumerate(answers):
        col, row = divmod(i, rows)
        x, y = 72 + col * 160, 110 + row * 20
        page.insert_text((x, y), f"Q{i + 1}", fontsize=11)
        page.insert_text((x + 60, y), a, fontsize=11)
    return doc.tobytes()


class Formats(SimpleTestCase):
    def check(self, result, expected=KEY):
        self.assertEqual("".join(result.answers.get(q, "?") for q in range(1, len(expected) + 1)), expected)
        self.assertFalse(result.conflicts)

    def test_pdf_multi_column_table(self):
        r = extract(make_pdf_table(KEY), "pdf", "ABCD")
        self.check(r)
        self.assertEqual(r.suggested_total, 30)

    def test_excel_two_column_pairs(self):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Question", "Answer", "", "Question", "Answer"])
        for i in range(15):
            ws.append([f"Q{i + 1}", KEY[i], None, f"Q{i + 16}", KEY[i + 15]])
        buf = io.BytesIO()
        wb.save(buf)
        self.check(extract(buf.getvalue(), "xlsx", "ABCD"))

    def test_excel_numeric_question_column(self):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["No", "Key"])
        for i, a in enumerate(KEY, start=1):
            ws.append([i, a])
        buf = io.BytesIO()
        wb.save(buf)
        self.check(extract(buf.getvalue(), "xlsx", "ABCD"))

    def test_word_table_and_inline_list(self):
        import docx

        d = docx.Document()
        d.add_paragraph("Answer key")
        d.add_paragraph(" ".join(f"{i}. {a}" for i, a in enumerate(KEY[:10], start=1)))
        t = d.add_table(rows=20, cols=2)
        for i in range(20):
            t.cell(i, 0).text = f"Q{i + 11}"
            t.cell(i, 1).text = KEY[i + 10]
        buf = io.BytesIO()
        d.save(buf)
        self.check(extract(buf.getvalue(), "docx", "ABCD"))

    def test_csv_compact_form(self):
        data = "\n".join(f"{i}-{a}" for i, a in enumerate(KEY, start=1)).encode()
        self.check(extract(data, "csv", "ABCD"))

    def test_json_variants(self):
        for payload in ({str(i): a for i, a in enumerate(KEY, start=1)},
                        {"answers": [{"question": i, "answer": a} for i, a in enumerate(KEY, start=1)]},
                        list(KEY)):
            self.check(extract(json.dumps(payload).encode(), "json", "ABCD"))

    def test_conflicting_answers_are_reported_not_guessed(self):
        data = b"Q1 A\nQ2 B\nQ3 C\nQ1 D\n"
        r = extract(data, "csv", "ABCD")
        self.assertNotIn(1, r.answers)
        self.assertEqual(r.conflicts[1], ["A", "D"])

    def test_missing_and_extra_questions_are_reported(self):
        data = "\n".join(f"Q{i} A" for i in (1, 2, 3, 5, 31)).encode()
        r = extract(data, "csv", "ABCD", expected=5)
        self.assertNotIn(31, r.answers)
        self.assertTrue(any("4" in w for w in r.warnings))
        self.assertTrue(any("beyond question 5" in w for w in r.warnings))

    def test_letters_outside_options_are_ignored(self):
        r = extract(b"Q1 A\nQ2 E\nQ3 B\n", "csv", "ABCD")
        self.assertEqual(r.answers, {1: "A", 3: "B"})

    def test_ocr_token_normalisation(self):
        self.assertEqual(normalise_ocr_token("Ql"), "Q1")
        self.assertEqual(normalise_ocr_token("Q9g"), "Q9")
        self.assertEqual(normalise_ocr_token("Cc"), "C")
        self.assertEqual(normalise_ocr_token("Q"), "Q")
        self.assertEqual(normalise_ocr_token("Question"), "Question")


@unittest.skipUnless((SAMPLES / "official_answer_key.pdf").exists()
                     and (SAMPLES / "ground_truth" / "official_answer_key.json").exists(),
                     "private sample answer key not available")
class SampleAnswerKey(SimpleTestCase):
    def setUp(self):
        self.data = (SAMPLES / "official_answer_key.pdf").read_bytes()
        truth = json.loads((SAMPLES / "ground_truth" / "official_answer_key.json").read_text(encoding="utf-8"))
        self.truth = {int(q): a for q, a in truth["answers"].items()}

    def test_pdf_text_layer_matches_verified_key(self):
        r = extract(self.data, "pdf", "ABCD")
        self.assertEqual(r.answers, self.truth)
        self.assertEqual(r.suggested_total, len(self.truth))
        self.assertFalse(r.conflicts)

    def test_ocr_of_300dpi_scan_never_contradicts_key(self):
        png = pymupdf.open(stream=self.data, filetype="pdf")[0].get_pixmap(dpi=300).tobytes("png")
        r = extract(png, "png", "ABCD", expected=len(self.truth), tesseract_cmd=settings.TESSERACT_CMD)
        wrong = {q: a for q, a in r.answers.items() if self.truth.get(q) != a}
        self.assertEqual(wrong, {})
        self.assertGreaterEqual(len(r.answers), 45)
