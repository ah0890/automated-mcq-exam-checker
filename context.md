# Project Context

Read this first at the start of every session, then check `git status` and
`git log`, then the relevant source.

## Objective

A real, local examination-checking application: teachers create an
examination, upload and verify the official answer key, upload a class's
scanned answer sheets, process them with OMR, review uncertain readings,
finalize results and view/export class results. No simulated data — every
result must come from uploaded papers and a verified key.

## Current milestone

**Milestone 1 — complete** (foundation + OMR engine validated on samples).
Next: **Milestone 2 — Examinations and Answer Key Library.**

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Inspect documents, foundation | Done |
| 2 | Examinations, answer key upload/extraction/verification | Next |
| 3 | Student paper upload and storage | Pending |
| 4 | OMR/OCR integration, manual verification UI, accuracy report | Pending (engine core done in M1) |
| 5 | Evaluation engine | Pending |
| 6 | Processing center, results dashboard, analytics | Pending |
| 7 | Excel/PDF reports, backups | Pending |
| 8 | Testing, security review, documentation | Pending |

## Architecture

- Django 6.1, SQLite (`data/db.sqlite3`), Bootstrap 5 + Chart.js (vendored,
  offline), WhiteNoise for static files. Python 3.13 in `.venv`.
- All pages require login (`LoginRequiredMiddleware`).
- `omr/` is a stand-alone package (no Django imports). The evaluation engine
  will be a separate stand-alone package taking structured answers + a
  verified key.
- Background processing plan: database-backed job/page state + a worker
  management command; resumable and idempotent (SHA-256 file fingerprints).
- Private data lives in `data/` (git-ignored). Uploaded files will be served
  only through authenticated views.

## Important files

- `config/settings.py` — env-driven settings, upload limits, logging, Tesseract path.
- `apps/core/` — dashboard, navigation (`navigation.py`), system checks.
- `omr/imaging.py` — PDF/image loading (raster only), illumination normalisation.
- `omr/alignment.py` — corner markers, missing-marker reconstruction, shape check, orientation.
- `omr/template.py` — `LayoutSpec`, `SheetTemplate`, calibration from the blank sheet.
- `omr/reader.py` — fill measurement and classification, `ReadingThresholds`.
- `omr/tests/` — engine tests; `test_samples.py` uses `samples/` (private).

## Sample documents (in git-ignored `samples/`)

- `blank_answer_sheet.pdf` — layout `OMR-GKBS001-A4-50Q-V1`: A4, 4 solid
  corner markers, 50 questions × A–D in two blocks of 25 (numbered down each
  column), 5-digit roll number (write-in boxes + 0–9 bubble grid), fill-the-
  bubble instructions.
- `official_answer_key.pdf` — digital PDF table Q1–Q50; 1 mark each, no
  negative marking, pass mark 40%. Mentions `.json`/`.xlsx` versions (not supplied).
- One completed student sheet (single page, vector PDF — not a scanned image).
- `ground_truth/` — manually established readings for supplied sheets.

## Document-processing approach

- Student sheets: rasterise at 200 dpi (PDF text layer deliberately ignored),
  normalise lighting, find corner markers (filled-square test + ink-ratio test
  to reject hollow boxes; one missing marker reconstructed by parallelogram
  completion; marker quad must match sheet proportions), try 0°/180° (or
  90°/270° for landscape input) and keep the best correlation with the blank
  reference, warp to template frame.
- Per-bubble fill = darkness inside 0.75 r of the bubble minus the same on the
  blank reference; per-question local shift search (±0.45 r) via template matching.
- Classification thresholds (provisional, from one sample sheet): marked ≥ 0.45,
  empty < 0.15; anything in between → UNCERTAIN (manual verification).
  Measured: filled ≈ 0.65–0.69, untouched 0.00, one faint mark 0.28.
- Roll number: read from the bubble grid. Planned (M4): OCR of write-in digit
  boxes as a cross-check; mismatch → review.
- Student names: planned from a class roster keyed by roll number, not
  handwriting OCR.
- Answer keys: PDF text layer first (50/50 on the sample); OCR for scanned
  keys needs table-aware parsing (plain Tesseract got 28/50 on a rendered key
  image); Excel/JSON import planned. Teacher verification is always required.

## Tests actually performed

- `manage.py test`: 20 tests, all passing (2026-11-17).
- Real sample sheet (1 sheet, 50 questions, 1 roll number): engine output
  matches manual ground truth exactly — roll number correct; 46 single answers
  correct; 1 multiple mark and 2 blanks identified; 1 faint mark routed to
  manual verification (not guessed). Same result under 9 artificial
  distortions (±3°/−7° rotation with a cropped corner marker, upside down,
  sideways, 100 dpi, noise+blur, shadow gradient, phone perspective, JPEG q30).
- Read time ≈ 0.4–0.7 s per page on the development machine.

## Known limitations

- Only one completed sheet available, and it is a computer-generated vector
  PDF, not a scan. Accuracy on real scanned/photographed sheets is not yet
  measured. The distortion tests are artificial.
- Thresholds are provisional and based on one sheet.
- Blank sheet must be supplied upright for calibration.
- Tesseract has only `eng` and `osd` language data installed.

## Pending work / next steps

1. Milestone 2: Examination model and CRUD; Answer Key Library (upload PDF /
   Excel / JSON / Word / image, extraction, verification UI, versions,
   export, association with examinations); sheet-template calibration UI.
2. Ask the user for: more completed sheets (ideally real scans), the key's
   `.xlsx`/`.json` versions, and a class roster.

## Git

- Remote: `origin` (GitHub). Branch: `main`. Author identity confirmed by the user.
- Push only after the user approves.
