# Changelog

## Milestone 4 — Processing and manual verification (2026-11-17)

### Added

- **Processing:** start from the examination workspace; papers are read in
  the background with a live progress bar; each paper is saved as soon as it
  is read; interrupted runs resume automatically; papers are never read twice
  unless reprocessing is requested; runs can be stopped.
- Roll number and every answer stored with status (answered, blank, multiple,
  uncertain) and confidence; aligned page image kept for review.
- Review flags: unreadable roll number, uncertain marks, duplicate roll
  numbers within the examination, doubtful orientation; pages that are not
  the answer sheet fail cleanly without stopping the batch.
- **Manual Verification:** review queue across examinations; review screen
  with roll-number crop beside the written digits, magnified crops of
  uncertain/multiple questions, the full aligned page, editable answers and a
  correction history. Papers are confirmed only when nothing is unresolved.
- **Processing Center:** per-examination status and recent runs.
- Reprocess selected papers; teacher corrections are kept.
- `process_papers` command (run/resume queued work) and
  `recognition_report` command (reading accuracy against manual ground truth).
- 17 new tests (89 total), including the real sample paper end to end.

## Milestone 3 — Student paper upload and storage (2026-11-17)

### Added

- **Upload from the dashboard**, from a dedicated Upload Student Papers page
  and from each examination's workspace: choose the examination, drop in the
  whole class's PDF/JPG/PNG files, and watch per-file progress and results.
  One failed file never stops the batch; works without JavaScript too.
- PDFs containing a whole class are split into individual papers, with a
  configurable number of pages per student; short last papers are flagged.
- Duplicate protection: identical files (SHA-256) and identical pages inside
  different files (page fingerprint) are skipped and reported.
- Per-examination storage of read-only originals; papers are numbered in
  upload order and can be excluded/restored but never deleted.
- Examination workspace: paper counts, paper table with thumbnails, paper
  viewer with previous/next navigation and original-file download.
- 15 new tests (72 total), including the real sample paper.

## Milestone 2 — Examinations and Answer Key Library (2026-11-17)

### Added

- **Sheet layouts:** upload a blank answer sheet; bubbles, roll-number grid
  and corner markers are detected and validated against the described layout;
  an overlay preview shows every detected bubble and the reading order.
- **Examinations:** create/edit with subject, class, date, exam ID, question
  count (validated against the sheet), marks per question, negative marking,
  multiple-mark policy and passing percentage. Examination workspace page
  with next-action guidance, marking summary and answer-key panel.
- **Answer Key Library:** upload PDF, Excel, Word, CSV, JSON or scanned
  images; position-aware extraction; verification screen with the original
  document side by side, highlighted missing/conflicting/changed answers;
  immutable verified versions with full history; edit-as-new-version;
  Excel/JSON export; original download. Examinations keep their key version
  until explicitly switched.
- **Students:** class roster with search/filter, add/edit, and Excel/CSV
  import with validation report.
- **Dashboard:** live counts, "Upload student papers" panel listing
  examinations ready for papers, setup checklist from real state, recent
  examinations.
- Secure file storage: content sniffing, size limits, random names, SHA-256
  fingerprints, read-only originals, authenticated previews and downloads.
- 37 new tests (57 total): extraction per format, file safety, key
  versioning, examination rules, roster import, page rendering, and real
  sample-key validation.

## Interface redesign (2026-11-17)

- Design system (Inter, navy/blue tokens), grouped sidebar with mobile
  drawer, sticky top bar, split-screen sign-in, redesigned dashboard and
  settings.

## Milestone 1 — Document inspection and foundation (2026-11-17)

### Document analysis

- Inspected the supplied blank answer sheet, official answer key and one
  completed student sheet (layout `OMR-GKBS001-A4-50Q-V1`): A4, one page per
  student, four solid corner markers, 50 questions × options A–D in two
  columns of 25, a 5-digit roll-number bubble grid with write-in boxes, and
  fill-the-bubble marking.
- The answer key is a digital PDF; its text layer yields all 50 answers.

### Added in Milestone 1

- Django 6.1 project with login-protected pages (`LoginRequiredMiddleware`),
  secure session/cookie settings, environment-based configuration and an
  auto-generated local secret key.
- Base interface: sidebar navigation for all eleven modules (unbuilt modules
  are labelled with their milestone), dashboard, settings page and a system
  status panel (OpenCV, PyMuPDF, Tesseract, data directory).
- Offline static assets (Bootstrap 5.3.3, Bootstrap Icons 1.11.3,
  Chart.js 4.4.1) served by WhiteNoise.
- `omr` package: PDF/image loading, illumination normalisation, corner-marker
  detection with single-marker reconstruction, orientation detection,
  raster-based template calibration, per-bubble fill measurement against the
  blank reference, local misalignment correction and confidence-scored
  classification (answered / blank / multiple / uncertain).
- Tests: 14 OMR tests and 6 web tests.
- `.gitignore` excluding all examination data, samples, databases and secrets.
