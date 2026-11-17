# Automated MCQ Examination Checker

A local web application that reads scanned MCQ answer sheets, matches each
student's responses against a verified answer key, and produces class results
and performance analytics.

All processing runs locally. No paid services or external AI APIs are used,
and examination data never leaves the machine.

> **Status:** Milestones 1–5 are complete: sheet layouts, examinations,
> the Answer Key Library, class lists, paper upload, processing, manual
> verification and marking. The class results dashboard and reports follow — see
> [CHANGELOG.md](CHANGELOG.md) and [context.md](context.md).

## Features available now

- **Sheet layouts** — calibrate an answer-sheet design from one blank sheet.
- **Examinations** — marking rules, question count, pass percentage,
  negative marking and multiple-mark policy.
- **Answer Key Library** — upload PDF, Excel, Word, CSV, JSON or scanned
  keys; verify every answer beside the original; versioned, immutable once
  verified; export to Excel or JSON.
- **Students** — class lists imported from Excel or CSV.
- **Student papers** — upload a whole class's PDF/JPG/PNG answer sheets from
  the dashboard; PDFs are split per student, duplicates are skipped, and
  originals are kept read-only.
- **Processing** — roll numbers and answers are read in the background with
  live progress; interrupted runs resume automatically.
- **Manual verification** — uncertain marks, unreadable or duplicate roll
  numbers are reviewed beside magnified crops of the paper; every correction
  is recorded.
- **Marking** — exact decimal marking against the verified key with negative
  marking, multiple-mark policy, pass/fail and competition ranking; key or
  rule changes require explicit re-evaluation and earlier results are kept.

## How it works

1. **Sheet template.** The layout is learnt once from an image of the blank
   answer sheet: corner markers, answer bubbles and the roll-number grid are
   detected automatically and validated against the expected number of options
   and roll digits.
2. **Alignment.** Each uploaded page is rasterised, lighting is normalised,
   the four corner markers are located (a single cropped marker is
   reconstructed), and the page is warped onto the template. All four
   orientations are tried, so sideways or upside-down scans are handled.
3. **Mark reading.** Every bubble's darkness is compared with the same bubble
   on the blank sheet, which cancels the printed letter and outline. A small
   local search corrects residual misalignment.
4. **Classification.** Each question is *answered*, *blank*, *multiple*, or
   *uncertain*. Uncertain marks (faint marks, partial erasures) are never
   guessed — they go to manual verification.

## Requirements

- Python 3.13 (3.12+ supported)
- Tesseract OCR 5.x (Windows installer: UB Mannheim build). Used for
  answer-key documents and printed-text checks only.

## Setup

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env          # then edit if needed
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py runserver
```

Open <http://127.0.0.1:8000> and sign in.

Papers are processed in the background of the running application. Queued
or interrupted work can also be run from the command line:

```powershell
.venv\Scripts\python manage.py process_papers
```

Reading accuracy on sample papers with manual ground truth
(`samples/ground_truth/<paper>.json`):

```powershell
.venv\Scripts\python manage.py recognition_report --distortions
```

## Data storage

Everything private — the database, uploaded papers, answer keys, results,
logs and backups — is stored under `data/` (or `MCQ_DATA_DIR`), which is
excluded from version control. Sample papers used for validation live in
`samples/`, also excluded.

## Tests

```powershell
.venv\Scripts\python manage.py test
```

- `omr/tests/test_engine.py` — classification rules and calibration on
  generated sheets with different layouts (4/5 options, column/row order).
- `omr/tests/test_samples.py` — runs the engine on the real sample papers in
  `samples/` and compares with manually established ground truth in
  `samples/ground_truth/`, including rotated, cropped, low-resolution, noisy,
  shadowed, perspective-distorted and JPEG-compressed variants. Skipped when
  the private samples are not present.
- `apps/answerkeys/tests/` — key extraction for every format, and the real
  sample key compared with its manually verified answers.
- `apps/exams/tests/` — the full setup workflow through the web interface.
- `apps/core/tests/` — authentication, page rendering and upload safety.
- `evaluation/tests/` — marking rules, decimals, negative marking, ranking.
- `apps/processing/tests/`, `apps/results/tests/` — processing, review,
  resumability and marking end to end, including the real sample paper.

## Project layout

```text
config/          Django settings and URLs
apps/core/       Dashboard, settings, file storage, previews
apps/exams/      Sheet layouts and examinations
apps/answerkeys/ Answer Key Library and key extraction
apps/students/   Class lists
apps/papers/     Student paper upload, storage and viewer
apps/processing/ Background processing, review and corrections
apps/results/    Stored results, re-evaluation and ranking
omr/             Stand-alone OMR engine (no Django dependency)
evaluation/      Stand-alone marking engine (no Django dependency)
templates/       HTML templates
static/          CSS and vendored Bootstrap 5, Bootstrap Icons, Chart.js
data/            Local data (git-ignored)
samples/         Private sample papers and ground truth (git-ignored)
```
