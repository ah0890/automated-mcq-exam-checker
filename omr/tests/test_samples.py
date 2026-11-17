"""Validation against the user's real sample papers.

Sample papers and their manually established ground truth live in the
git-ignored ``samples/`` directory, so these tests are skipped on machines
that do not have them.

Ground-truth format (samples/ground_truth/<paper stem>.json):
    {"roll_number": "12345",
     "answers": {"1": "C", "21": "MULTIPLE:AB", "22": "BLANK", "50": "UNCERTAIN:B", ...}}
"UNCERTAIN:X" means a mark too faint to accept automatically (X = intended option).
"""

import json
import unittest
from pathlib import Path

import cv2
import numpy as np

from omr import LayoutSpec, build_template, load_pages, read_sheet
from omr.reader import ANSWERED, BLANK, MULTIPLE, UNCERTAIN

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
BLANK_SHEET = SAMPLES / "blank_answer_sheet.pdf"
TRUTH_DIR = SAMPLES / "ground_truth"


def expected_status(truth: str):
    if truth == "BLANK":
        return BLANK, None
    if truth.startswith("MULTIPLE:"):
        return MULTIPLE, truth.split(":", 1)[1]
    if truth.startswith("UNCERTAIN:"):
        return UNCERTAIN, None
    return ANSWERED, truth


def distort(img, kind, rng):
    h, w = img.shape
    if kind == "rotate_+3":
        M = cv2.getRotationMatrix2D((w / 2, h / 2), 3, 1)
        return cv2.warpAffine(img, M, (w, h), borderValue=255)
    if kind == "rotate_-7_shift_crop":
        M = cv2.getRotationMatrix2D((w / 2, h / 2), -7, 0.93)
        M[:, 2] += (20, -15)
        return cv2.warpAffine(img, M, (w, h), borderValue=255)
    if kind == "upside_down":
        return cv2.rotate(img, cv2.ROTATE_180)
    if kind == "sideways":
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if kind == "low_res_100dpi":
        return cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    if kind == "noise_blur":
        n = cv2.GaussianBlur(img, (5, 5), 1.5).astype(np.float32) + rng.normal(0, 18, img.shape)
        return np.clip(n, 0, 255).astype(np.uint8)
    if kind == "shadow":
        return (img * np.linspace(1.0, 0.55, w)[None, :]).astype(np.uint8)
    if kind == "phone_perspective":
        s = np.float32([[0, 0], [w, 0], [0, h], [w, h]])
        d = np.float32([[60, 40], [w - 20, 0], [0, h - 30], [w - 70, h - 60]])
        return cv2.warpPerspective(img, cv2.getPerspectiveTransform(s, d), (w, h), borderValue=255)
    if kind == "jpeg_q30":
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 30])
        return cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    raise ValueError(kind)


DISTORTIONS = ["rotate_+3", "rotate_-7_shift_crop", "upside_down", "sideways", "low_res_100dpi",
               "noise_blur", "shadow", "phone_perspective", "jpeg_q30"]


@unittest.skipUnless(BLANK_SHEET.exists() and TRUTH_DIR.exists(), "private sample papers not available")
class SamplePapers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = build_template(load_pages(BLANK_SHEET)[0], LayoutSpec(roll_digits=5))
        cls.papers = []
        for truth_file in sorted(TRUTH_DIR.glob("*.json")):
            pdf = SAMPLES / f"{truth_file.stem}.pdf"
            if pdf.exists():
                cls.papers.append((pdf, json.loads(truth_file.read_text(encoding="utf-8"))))

    def test_template_matches_sheet_design(self):
        self.assertEqual(self.template.capacity, 50)
        self.assertEqual(len(self.template.roll_columns), 5)

    def assert_matches_truth(self, reading, truth, label):
        self.assertEqual(reading.roll_number, truth["roll_number"], f"{label}: roll number")
        for a in reading.answers:
            status, value = expected_status(truth["answers"][str(a.number)])
            self.assertEqual((a.status, a.value), (status, value), f"{label}: Q{a.number} fills={a.fills}")

    def test_papers_match_ground_truth(self):
        self.assertTrue(self.papers, "no ground-truth files found")
        for pdf, truth in self.papers:
            reading, _ = read_sheet(load_pages(pdf)[0], self.template, num_questions=len(truth["answers"]))
            self.assert_matches_truth(reading, truth, pdf.name)

    def test_papers_survive_scan_distortions(self):
        rng = np.random.default_rng(0)
        for pdf, truth in self.papers:
            page = load_pages(pdf)[0]
            for kind in DISTORTIONS:
                with self.subTest(paper=pdf.name, distortion=kind):
                    reading, _ = read_sheet(distort(page, kind, rng), self.template,
                                            num_questions=len(truth["answers"]))
                    self.assert_matches_truth(reading, truth, f"{pdf.name}/{kind}")
