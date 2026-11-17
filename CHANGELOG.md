# Changelog

## Milestone 1 — Document inspection and foundation (2026-11-17)

### Document analysis
- Inspected the supplied blank answer sheet, official answer key and one
  completed student sheet (layout `OMR-GKBS001-A4-50Q-V1`): A4, one page per
  student, four solid corner markers, 50 questions × options A–D in two
  columns of 25, a 5-digit roll-number bubble grid with write-in boxes, and
  fill-the-bubble marking.
- The answer key is a digital PDF; its text layer yields all 50 answers.

### Added
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
