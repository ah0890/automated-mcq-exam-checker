# Automated MCQ Examination Checker

A local web application that reads scanned MCQ answer sheets, matches each
student's responses against a verified answer key, and produces class
results, analytics and printable reports.

Everything runs on your own computer. No paid services or external AI APIs are
used, and examination data never leaves the machine.

## What it does

- **Sheet layouts** — learn an answer-sheet design once from a blank sheet:
  corner markers, answer bubbles and the roll-number grid are detected and
  shown on an overlay for confirmation.
- **Answer Key Library** — upload the official key as PDF, Excel, Word, CSV,
  JSON or a scanned image; every extracted answer is checked beside the
  original before it is saved. Verified keys are versioned and never change.
- **Examinations** — marks per question, negative marking, multiple-mark
  policy and pass percentage.
- **Student papers** — upload a whole class's PDF/JPG/PNG sheets from the
  dashboard. Class PDFs are split per student, duplicates are skipped and
  originals are kept read-only.
- **Processing** — roll numbers and answers are read in the background with
  live progress. Interrupted runs resume automatically.
- **Manual verification** — anything uncertain (faint or erased marks,
  unreadable or duplicate roll numbers, unreadable pages) is reviewed beside
  magnified crops of the paper. Nothing is guessed; every correction is logged.
- **Marking** — exact decimal marking against the verified key; negative
  marking, multiple-mark policy, pass/fail and competition ranking. Changing
  the key or rules never silently changes results.
- **Class results & analytics** — rank table with search, sorting and
  filters; distribution, pass/fail, student and question charts; question
  analysis; finalizing locks the examination.
- **Reports** — Excel class result sheet, question analysis and processing
  report; PDF student result cards and examination summary.
- **Backups** — examination backup and restore (verified by SHA-256) and full
  application backups.

## How answer sheets are read

1. Each page is rasterised (the PDF text layer is ignored, so scans and
   digital files are treated the same), lighting is evened out, and the four
   corner markers are located — a single cropped marker is reconstructed.
2. The page is straightened onto the calibrated layout; sideways and
   upside-down pages are detected.
3. Every bubble's darkness is compared with the same bubble on the blank
   sheet, which cancels the printed letter and outline; a small local search
   corrects residual misalignment.
4. Each question is *answered*, *blank*, *multiple* or *uncertain*. Uncertain
   marks always go to the teacher.

Reading accuracy is checked against what is actually on each paper; marks are
then decided only by the verified answer key.

## Requirements

- Windows, macOS or Linux with Python 3.13 (3.12+ supported)
- Tesseract OCR 5.x (Windows: UB Mannheim installer) — used only for scanned
  answer-key documents

## Installation

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env          # adjust TESSERACT_CMD or MCQ_DATA_DIR if needed
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py runserver
```

Open <http://127.0.0.1:8000> and sign in.

## Teacher workflow

1. **Sheet Layouts → Add layout** — upload a blank answer sheet (once per design).
2. **Answer Key Library → Upload** — upload the official key, compare every
   answer with the document, then *Verify & save*.
3. **New examination** — set the rules, then attach the verified key.
4. **Dashboard → Upload student papers** — choose the examination and drop in
   the class's files.
5. **Process papers** on the examination page.
6. **Manual Verification** — resolve anything flagged.
7. **Class Results** — check, then **Finalize results**.
8. **Reports** — download the Excel result sheet and PDF result cards.

## Commands

```powershell
.venv\Scripts\python manage.py process_papers                    # run or resume queued processing
.venv\Scripts\python manage.py recognition_report --distortions  # reading accuracy on samples
.venv\Scripts\python manage.py backup_data --full                # database + all files
.venv\Scripts\python manage.py backup_data --exam <uuid>         # one examination
.venv\Scripts\python manage.py backup_data --restore <file.zip>  # restore an examination
```

## Data and privacy

The database, uploaded papers, answer keys, results, logs and backups are
stored under `data/` (or `MCQ_DATA_DIR`), which is excluded from version
control together with `samples/`. Uploaded files are only served to signed-in
users. Logs record internal identifiers only — never names, roll numbers or
answers.

## Security

- Sign-in required for every page; Django administration for user accounts.
  Finalized results can be reopened, and full backups/restores performed,
  only by administrators.
- CSRF protection; state-changing actions accept POST only.
- Content-Security-Policy (`script-src 'self'`, no inline scripts),
  `X-Frame-Options: DENY`, `nosniff`, same-origin referrer policy.
- Sign-in throttling (5 failures per user and address → 15-minute lockout).
- Uploads are identified by content (not extension), size-limited, validated
  (PDF, image, Office, JSON), stored under random names, fingerprinted and
  read-only; archive restores reject unsafe paths and tampered files.
- The application normally runs on `127.0.0.1` over HTTP. When serving it over
  HTTPS, set `DJANGO_HTTPS=true` (secure cookies, HTTPS redirect, HSTS).

## Tests

```powershell
.venv\Scripts\python manage.py test
```

Unit, integration, security and acceptance tests. Tests that use the private
sample papers in `samples/` (with manual ground truth in
`samples/ground_truth/`) are skipped when those files are absent. See
[docs/final-report.md](docs/final-report.md) for measured results.

## Limitations

- Accuracy has been measured on one completed, computer-generated sample
  sheet (plus artificial distortions of it); real scanned or photographed
  sheets have not yet been measured.
- Mark thresholds are provisional (`OMR_MARKED_THRESHOLD`,
  `OMR_EMPTY_THRESHOLD`).
- Handwritten names and roll-number digits are not read; roll numbers come
  from the bubble grid and names from the class list.
- Scanned answer keys should be 300 dpi; low-resolution key images are read
  unreliably and always require full comparison by the teacher.

## Project layout

```text
config/          Django settings and URLs
apps/core/       Dashboard, settings, sign-in, file storage, previews
apps/exams/      Sheet layouts and examinations
apps/answerkeys/ Answer Key Library and key extraction
apps/students/   Class lists
apps/papers/     Student paper upload, storage and viewer
apps/processing/ Background processing, review and corrections
apps/results/    Stored results, re-evaluation, ranking and analytics
apps/reports/    Excel/PDF reports, backups and restore
omr/             Stand-alone OMR engine (no Django dependency)
evaluation/      Stand-alone marking engine (no Django dependency)
docs/            Final report
templates/       HTML templates
static/          CSS, JavaScript and vendored Bootstrap, Bootstrap Icons, Chart.js, Inter
data/            Local data (git-ignored)
samples/         Private sample papers and ground truth (git-ignored)
```
