# Final Report

Automated MCQ Examination Paper Checking & Class Result Management System
Completed 18 November 2026.

## Scope delivered

All eight milestones of the project brief:

| # | Milestone | Result |
|---|-----------|--------|
| 1 | Document inspection, foundation, OMR engine | Done |
| 2 | Examinations, sheet layouts, Answer Key Library, class lists | Done |
| 3 | Student paper upload and storage | Done |
| 4 | Processing and manual verification | Done |
| 5 | Evaluation engine | Done |
| 6 | Class results, analytics, finalization | Done |
| 7 | Reports, exports, backups | Done |
| 8 | Security review, acceptance and performance testing, documentation | Done |

## Test evidence

Automated test suite: **all tests passing** (`manage.py test`), covering the
OMR engine, key extraction for every format, upload safety, processing,
resumability, review, marking, analytics, finalization, reports, backups,
security and the full acceptance workflow.

### Acceptance workflow (real sample files)

The brief's 16-step workflow runs as an automated test through the web
interface with the supplied blank sheet, official answer key and completed
student sheet: sign in → calibrate sheet → create examination → upload key →
verify (extracted key identical to the manually read key, 50/50) → attach →
upload papers → process → review the one uncertain mark → finalize → class
results → Excel export → PDF result card. The marked result matches the
independent hand-check made before the marking engine existed:
42 correct, 5 incorrect, 2 unanswered, 1 multiple-marked → 42/50, 84 %, Pass.

### Recognition (reading accuracy)

Measured against what is actually on the paper (manual ground truth):

| Data | Papers | Roll numbers | Answers handled correctly | Silent errors | Faint marks sent to review |
|------|-------:|-------------:|--------------------------:|--------------:|---------------------------:|
| Supplied sheet | 1 | 1/1 | 50/50 | 0 | 1/1 |
| 9 artificial distortions of it | 9 | 9/9 | 450/450 | 0 | 9/9 |
| 40-page scanner-style batch of random artificial distortions | 40 | 40/40 | 2000/2000 | 0 | — |

**Caveat:** the only completed sheet is computer-generated, and all variants
are artificial. These figures demonstrate robustness, not real-world
accuracy, which still needs real scanned or photographed sheets.

### Answer-key extraction

- Digital PDF key: 50/50, identical to the manually read key.
- OCR of the key rendered as an image: 300 dpi clean and 3°-tilted images
  50/50 with no wrong answers; lower-resolution or noisy images recover fewer
  answers and occasionally mis-pair one, so OCR keys always require explicit
  teacher comparison.

### Performance (development laptop)

- 40-page, 22.7 MB class PDF: upload, split and fingerprint in 0.7 s.
- Processing: 24.4 s for 40 papers (0.61 s per paper), background thread.
- Live server test: processing started in 0.13 s, progress polled until done.

### Security review

- Every one of the application's URLs requires sign-in (tested
  automatically); state-changing endpoints reject GET; CSRF enforced.
- Content-Security-Policy with no inline scripts; frame, MIME-sniffing and
  referrer protections.
- Sign-in throttling with lockout.
- Upload validation by content, size limits, random names, read-only
  originals, path-traversal-safe storage; backup restore rejects unsafe paths
  and tampered files.
- Logs contain no student data (tested).
- `pip-audit`: no known vulnerabilities in the pinned dependencies.
- `check --deploy`: remaining warnings concern HTTPS only and are resolved by
  `DJANGO_HTTPS=true` when served over HTTPS (HSTS preload intentionally off).

## Known limitations

- Real-world accuracy on scanned/photographed sheets is not yet measured.
- OMR thresholds are provisional, based on one sheet.
- Handwritten names and roll-number digits are not read; the printed exam ID
  is not read (pages of a different sheet design are rejected by template
  matching).
- Negative marking follows the brief's formula literally; totals can be below
  zero.
- Designed for a single local machine (SQLite, in-process worker).
