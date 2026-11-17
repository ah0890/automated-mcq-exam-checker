"""Measure recognition accuracy against manually established ground truth.

Reading accuracy is judged against what is actually on each paper (the
ground truth), not against the answer key — the key decides marks, not what
the student filled in.

Ground truth: samples/ground_truth/<paper stem>.json with "roll_number" and
"answers" ({"1": "C", "21": "MULTIPLE:AB", "22": "BLANK", "50": "UNCERTAIN:B"}).
"UNCERTAIN:X" marks a deliberately faint mark: the correct behaviour is to
send it to manual review, never to guess.
"""

import json
import time
from pathlib import Path

import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.processing.engine import thresholds
from omr import LayoutSpec, build_template, load_pages, read_sheet
from omr.reader import ANSWERED, BLANK, MULTIPLE, UNCERTAIN


def expected(truth: str):
    if truth == "BLANK":
        return BLANK, None
    if truth.startswith("MULTIPLE:"):
        return MULTIPLE, truth.split(":", 1)[1]
    if truth.startswith("UNCERTAIN:"):
        return UNCERTAIN, None
    return ANSWERED, truth


class Command(BaseCommand):
    help = "Report OMR recognition accuracy on sample papers with manual ground truth."

    def add_arguments(self, parser):
        parser.add_argument("--samples", default=str(Path(settings.BASE_DIR) / "samples"))
        parser.add_argument("--blank", default="blank_answer_sheet.pdf")
        parser.add_argument("--roll-digits", type=int, default=5)
        parser.add_argument("--options", default="ABCD")
        parser.add_argument("--distortions", action="store_true",
                            help="Also run artificial scan distortions (reported separately).")

    def handle(self, *args, **opts):
        root = Path(opts["samples"])
        truth_dir = root / "ground_truth"
        blank = root / opts["blank"]
        if not blank.exists() or not truth_dir.exists():
            raise CommandError("Sample blank sheet or ground_truth folder not found.")
        template = build_template(load_pages(blank)[0], LayoutSpec(options_per_question=len(opts["options"]),
                                                                   option_labels=opts["options"],
                                                                   roll_digits=opts["roll_digits"]))
        papers = []
        for tf in sorted(truth_dir.glob("*.json")):
            truth = json.loads(tf.read_text(encoding="utf-8"))
            src = next((p for p in root.glob(f"{tf.stem}.*") if p.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg")),
                       None)
            if "roll_number" in truth and src:
                papers.append((src, truth))
        if not papers:
            raise CommandError("No papers with ground truth found.")

        variants = [("as supplied", None)]
        if opts["distortions"]:
            from omr.tests.test_samples import DISTORTIONS, distort

            variants += [(d, d) for d in DISTORTIONS]
        rng = np.random.default_rng(0)

        self.stdout.write(f"Template: {template.capacity} questions, {len(template.roll_columns)} roll digits")
        self.stdout.write(f"Thresholds: marked >= {thresholds().marked}, empty < {thresholds().empty}\n")
        for label, kind in variants:
            stats = dict(papers=0, roll_ok=0, roll_unread=0, roll_wrong=0, answers=0, correct=0, wrong=0,
                         uncertain_flagged=0, uncertain_expected=0, uncertain_extra=0, seconds=0.0)
            wrong_items = []
            for src, truth in papers:
                page = load_pages(src)[0]
                if kind:
                    page = distort(page, kind, rng)
                t = time.perf_counter()
                reading, _ = read_sheet(page, template, thresholds(), num_questions=len(truth["answers"]))
                stats["seconds"] += time.perf_counter() - t
                stats["papers"] += 1
                if reading.roll_number == truth["roll_number"]:
                    stats["roll_ok"] += 1
                elif reading.roll_number is None:
                    stats["roll_unread"] += 1
                else:
                    stats["roll_wrong"] += 1
                    wrong_items.append(f"{src.name}: roll number read incorrectly")
                for a in reading.answers:
                    exp_status, exp_value = expected(truth["answers"][str(a.number)])
                    stats["answers"] += 1
                    if exp_status == UNCERTAIN:
                        stats["uncertain_expected"] += 1
                    if (a.status, a.value) == (exp_status, exp_value):
                        stats["correct"] += 1
                        if a.status == UNCERTAIN:
                            stats["uncertain_flagged"] += 1
                    elif a.status == UNCERTAIN:
                        stats["uncertain_extra"] += 1  # safe: sent to review, not guessed
                    else:
                        stats["wrong"] += 1
                        wrong_items.append(f"{src.name}: Q{a.number} read {a.status}/{a.value}, "
                                           f"expected {exp_status}/{exp_value}")
            n = stats["answers"]
            self.stdout.write(self.style.MIGRATE_HEADING(f"[{label}]"))
            self.stdout.write(
                f"  papers {stats['papers']} | roll numbers correct {stats['roll_ok']}/{stats['papers']}"
                f" (unread {stats['roll_unread']}, wrong {stats['roll_wrong']})\n"
                f"  answers handled correctly {stats['correct']}/{n} ({100 * stats['correct'] / n:.1f}%)"
                f" | wrong (silent errors) {stats['wrong']}"
                f" | extra sent to review {stats['uncertain_extra']}"
                f" | faint marks correctly sent to review {stats['uncertain_flagged']}/{stats['uncertain_expected']}\n"
                f"  read time {stats['seconds']:.2f} s ({stats['seconds'] / stats['papers']:.2f} s per paper)")
            for w in wrong_items:
                self.stdout.write(self.style.ERROR(f"  ! {w}"))
        if len(papers) < 10:
            self.stdout.write(self.style.WARNING(
                f"\nOnly {len(papers)} paper(s) have ground truth — too few for a reliable accuracy figure."))
