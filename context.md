# Project Context

Read this first at the start of every session, then check `git status` and
`git log`, then the relevant source.

## Objective

A real, local examination-checking application: teachers create an
examination, upload and verify the official answer key, upload a class's
scanned answer sheets, process them with OMR, review uncertain readings,
finalize results and view/export class results. No simulated data — every
result must come from uploaded papers and a verified key.

User direction (2026-11-17): the dashboard is the teacher's home base —
uploading student papers must be available from the dashboard, with
processing progress and results shown there afterwards. Build it like a real
client product (polished SaaS-quality UI).

## Current milestone

**Milestone 4 — complete.** Next: **Milestone 5 — Evaluation engine**
(stand-alone, exact decimal arithmetic; compares final answers with the
verified key; negative marking, multiple-mark policy, pass/fail, competition
ranking; stores results per paper; re-evaluation when the key changes).

Accuracy terminology (agreed with the user, 2026-11-17): **marks** are judged
only against the official answer key; **reading accuracy** is judged against
what is actually on each paper (manual ground truth). The key must never be
used to "correct" what was read.

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Inspect documents, foundation, OMR engine | Done |
| — | UI redesign (design system) | Done |
| 2 | Examinations, sheet layouts, Answer Key Library, students | Done |
| 3 | Student paper upload and storage | Done |
| 4 | OMR processing, manual verification UI, accuracy report | Done |
| 5 | Evaluation engine | Next |
| 6 | Processing center, results dashboard, analytics | Pending |
| 7 | Excel/PDF reports, backups | Pending |
| 8 | Testing, security review, documentation | Pending |

## Architecture

- Django 6.1, SQLite (`data/db.sqlite3`), Bootstrap 5 + Chart.js + Inter font
  (all vendored, offline), WhiteNoise for static files. Python 3.13 in `.venv`.
- All pages require login (`LoginRequiredMiddleware`). Uploaded files are
  served only through authenticated views (no public media URL).
- Apps: `apps/core` (dashboard, settings, file storage, previews),
  `apps/exams` (SheetLayout, Examination), `apps/answerkeys` (AnswerKey,
  AnswerKeyVersion, extraction), `apps/students` (Student roster),
  `apps/papers` (UploadBatch, SourceFile, Paper; upload, splitting,
  duplicate detection, viewer), `apps/processing` (ProcessingJob,
  Correction; engine, worker, review).
- Stand-alone packages (no Django imports): `omr/`, and
  `apps/answerkeys/extraction.py`. The evaluation engine (M5) will also be
  stand-alone.
- Processing: `ProcessingJob` rows list paper ids; a daemon thread in the
  web process (started on demand by `ensure_worker()`) or the
  `process_papers` command runs queued jobs. Each paper is claimed atomically
  (QUEUED → PROCESSING) and committed individually; RUNNING jobs whose
  heartbeat is older than 2 minutes are re-queued on the next start
  (resume). SQLite runs in WAL mode so pages read while the worker writes.
  `MCQ_BACKGROUND_PROCESSING=false` processes synchronously (tests).

### Data model rules

- Paper recognition fields: `roll_number` + `roll_source` (omr|teacher),
  `answers` {q: {value, status (answered|blank|multiple|uncertain), source
  (omr|teacher), confidence, candidates}}, `reading` (raw OMR output),
  `review_flags` [{code, message, blocking}], `processed_image` (aligned PNG
  under `processed_papers/`). Teacher values survive reprocessing. Blocking
  flags: roll_unreadable, uncertain_answers, duplicate_roll, orientation.
  Every manual change is a `Correction` row (field, old, new, user, time).
  A paper is confirmed only with no uncertain answers, a roll number (if the
  sheet has a grid) and no duplicate roll in the examination.

- Papers: each uploaded file is a SourceFile (unique per exam by SHA-256);
  each student's sheet is a Paper = consecutive pages of a source file
  (pages per student configurable for PDFs, default 1; images = 1 page).
  A Paper's `fingerprint` hashes a small render of its first page, so the
  same scan inside a different file is also skipped. Papers are never
  deleted — they can be excluded/restored. Finalized exams reject uploads.
  Originals: `data/examinations/<exam uid>/original_papers/<random>.<ext>`.
- Answer keys are versioned. A version is DRAFT until verified; verified
  versions are immutable (save/delete raise). Editing creates a new draft
  version. Examinations reference a specific verified version
  (`on_delete=PROTECT`), so keys in use cannot be deleted and edits never
  silently change an examination's key.
- An examination's `num_questions` must be ≤ its sheet layout's detected
  capacity; layout and question count lock once a key is attached.
- Key attachment requires: verified, same question count, options within the
  sheet's labels.

## Important files

- `config/settings.py` — env-driven settings, upload limits, logging, Tesseract path.
- `apps/core/files.py` — upload validation (content sniffing, size limit,
  PDF/image/Office/JSON checks), random file names, SHA-256, read-only originals,
  path-traversal-safe `data_path()`.
- `apps/core/previews.py` — authenticated PNG previews of stored PDFs/images.
- `apps/core/navigation.py`, `context_processors.py` — grouped sidebar.
- `apps/answerkeys/extraction.py` — key extraction (PDF text layer, OCR with
  voting, Excel, Word, CSV, JSON).
- `apps/answerkeys/services.py`, `apps/exams/services.py` — business logic.
- `apps/papers/services.py` — `add_file()` (validate, dedupe, store, split),
  `page_fingerprints()`, `paper_stats()`.
- `apps/processing/engine.py` (`process_paper`, `check_roll_conflicts`),
  `worker.py` (enqueue/run/resume/cancel, background thread), `review.py`
  (`save_review`, question/roll crops), management commands
  `process_papers` and `recognition_report`.
- `templates/papers/_uploader.html` + `static/js/uploader.js` — the uploader
  used on the dashboard, the upload page and the examination workspace
  (one request per file with progress; plain form post without JavaScript).
- `static/css/app.css` (design tokens, shell), `static/css/components.css`.
- `omr/` — see Milestone 1 notes below.

## Sample documents (in git-ignored `samples/`)

- `blank_answer_sheet.pdf` — layout `OMR-GKBS001-A4-50Q-V1`: A4, 4 solid
  corner markers, 50 questions × A–D in two blocks of 25 (numbered down each
  column), 5-digit roll number (write-in boxes + 0–9 bubble grid).
- `official_answer_key.pdf` — digital PDF table Q1–Q50; 1 mark each, no
  negative marking, pass mark 40%. Mentions `.json`/`.xlsx` versions (not supplied).
- One completed student sheet (single page, computer-generated vector PDF).
- `ground_truth/` — manual readings: one student sheet, and the answer key
  (read visually from the rendered page, independently of the extractor).

## Document-processing approach

- Student sheets: rasterise at 200 dpi (PDF text layer ignored), normalise
  lighting, find corner markers (filled-square + ink-ratio tests; one missing
  marker reconstructed; marker quad must match sheet proportions), choose
  orientation by correlation with the blank reference, warp to template frame.
- Per-bubble fill = darkness inside 0.75 r minus the blank reference; local
  shift search (±0.45 r). Thresholds (provisional): marked ≥ 0.45, empty < 0.15,
  between → UNCERTAIN. Never guessed.
- Roll number from the bubble grid; names from the class roster (not OCR).
- Answer keys: words with positions → pair each question number with the
  answer letter that follows it on the same row (handles multi-column tables,
  lists, "1-A" forms, spreadsheets). Scanned keys: deskew (Hough estimate +
  projection-profile refinement), table-rule removal, OCR-confusion fixes
  ("Ql"→"Q1", "Cc"→"C"), 3 Tesseract modes × 2 line groupings, voting — an
  answer is suggested only if ≥ 2 readings agree and none disagree;
  disagreements become conflicts. OCR keys require the teacher to confirm they
  compared every answer with the document.

## Tests actually performed

- `manage.py test`: **89 tests, all passing** (2026-11-17).
- Real sample student sheet: OMR output matches manual ground truth exactly
  (roll number; 46 single answers; 1 multiple; 2 blanks; 1 faint mark →
  manual verification), also under 9 artificial distortions.
- Real sample answer key, end-to-end through the web UI (temporary database):
  blank sheet calibrated (50 questions, 5-digit roll grid, ~1 s); key PDF read
  via text layer **50/50, identical to the manual reading**, no warnings;
  verified; examination created; key attached; the real student sheet
  uploaded through the AJAX upload endpoint (1 paper), re-upload under a
  different name detected as a duplicate; workspace, viewer and thumbnails
  rendered and checked visually.
- `manage.py recognition_report --distortions` (2026-11-17), 1 paper with
  manual ground truth: roll number 1/1; answers handled correctly 50/50
  (0 silent errors; the 1 faint mark sent to review, not guessed); identical
  under 9 artificial distortions; 0.2–0.4 s per paper. **One paper is far too
  few for a reliable accuracy figure; the sheet is computer-generated.**
- Live server test with the background thread: upload → start (returned in
  0.13 s) → status polling → job done in 0.9 s, paper in review, no errors.
- Full review flow on the real paper: Q50 (faint B) confirmed as B by the
  teacher, correction logged, paper verified.
- OCR of the same key rendered as an image (benchmark, artificial
  variants): 300 dpi clean 50/50 and 3° tilt 50/50 with 0 wrong; 300 dpi JPEG
  q40 48/50, 0 wrong; heavier noise and ≤ 200 dpi images recover far fewer and
  in 2 of 15 variants produced 1 wrong suggestion (low-resolution digit
  misreads). Hence OCR keys always need full teacher comparison; scan at 300 dpi.

## Known limitations

- Only one completed student sheet, computer-generated (not a scan). Accuracy
  on real scans/photos is not yet measured.
- OMR thresholds are provisional (one sheet); configurable via
  `OMR_MARKED_THRESHOLD` / `OMR_EMPTY_THRESHOLD`.
- Blank sheet must be supplied upright for calibration.
- OCR of low-resolution or noisy answer-key images is unreliable (see above).
- Tesseract has only `eng` and `osd` language data.

## Pending work / next steps

1. Milestone 5: evaluation engine package + stored results; marking runs
   automatically for verified papers; results visible per paper.
2. Not yet done (recorded honestly): OCR cross-check of the handwritten
   roll-number boxes and of the printed exam/template ID (pages of the wrong
   sheet design are already rejected by the template-match score).
2. Ask the user for more completed sheets (ideally real scans) and a class list.

## Git

- Remote: `origin` (GitHub). Branch: `main`. Author identity confirmed by the user.
- Commits carry no tool attribution (user's instruction).
- Push only after the user approves.
