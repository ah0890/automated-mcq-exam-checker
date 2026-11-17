"""Answer-key extraction from PDF, Excel, Word, CSV, JSON and images.

No Django dependency. Every format is reduced to positioned text tokens and
answers are found by pairing each question number with the answer letter
that follows it on the same row. This handles multi-column tables
("Q1 C | Q18 A | Q35 A"), lists ("1. A"), compact forms ("1-A") and
spreadsheets alike.

Extraction is only a suggestion: the teacher always verifies the result.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from statistics import median

Q_PREFIXED = re.compile(r"^(?:Q(?:uestion|ues|n)?|No)\.?\s*[#:\-]?\s*(\d{1,3})[.):\-]?$", re.I)
BARE_NUMBER = re.compile(r"^(\d{1,3})[.):\-]?$")
ANSWER = re.compile(r"^[\(\[]?([A-Za-z])[\)\]]?[.,;:]?$")
INLINE = re.compile(r"^(?:Q(?:uestion)?\.?\s*)?(\d{1,3})\s*[.):\-=]+\s*[\(\[]?([A-Za-z])[\)\]]?$", re.I)


@dataclass
class Token:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class ExtractionResult:
    answers: dict[int, str] = field(default_factory=dict)
    method: str = ""
    warnings: list[str] = field(default_factory=list)
    conflicts: dict[int, list[str]] = field(default_factory=dict)
    suggested_total: int | None = None
    details: dict = field(default_factory=dict)

    def answers_for(self, total: int) -> dict[int, str]:
        return {q: a for q, a in self.answers.items() if 1 <= q <= total}


# --- token pairing -----------------------------------------------------------------

def group_lines(tokens: list[Token]) -> list[list[Token]]:
    """Group tokens into visual rows using vertical-centre proximity."""
    if not tokens:
        return []
    heights = [max(t.y1 - t.y0, 1e-6) for t in tokens]
    tol = median(heights) * 0.6
    rows: list[list[Token]] = []
    row_centres: list[float] = []
    for t in sorted(tokens, key=lambda t: (t.y0 + t.y1) / 2):
        cy = (t.y0 + t.y1) / 2
        if rows and abs(cy - row_centres[-1]) <= tol:
            rows[-1].append(t)
            n = len(rows[-1])
            row_centres[-1] += (cy - row_centres[-1]) / n  # running mean of the row
        else:
            rows.append([t])
            row_centres.append(cy)
    return [sorted(r, key=lambda t: t.x0) for r in rows]


_OCR_DIGIT = str.maketrans({"l": "1", "I": "1", "|": "1", "!": "1", "O": "0", "o": "0"})


def normalise_ocr_token(text: str) -> str:
    """Undo common OCR confusions in table cells:
    'Ql' → 'Q1', 'Q9g' → 'Q9', 'Cc' → 'C' (bold capital read twice)."""
    t = text.strip()
    m = re.match(r"^(Q\.?)([0-9lI|!Oo]{1,3})([a-z]?)$", t)
    if m and (any(ch.isdigit() for ch in m.group(2)) or not m.group(3)):
        return m.group(1) + m.group(2).translate(_OCR_DIGIT)
    m = re.match(r"^([A-Za-z])\1?$", t, re.I)
    if m and len(t) == 2 and t[0].upper() == t[1].upper():
        return t[0].upper()
    return t


LIST_NUMBER = re.compile(r"^(\d{1,3})[.):]$")


def split_cells(line: list[Token]) -> list[Token]:
    """Split multi-word cells (e.g. a spreadsheet cell 'Q1 A') into word tokens."""
    out = []
    for t in line:
        words = t.text.split()
        if len(words) <= 1:
            out.append(t)
            continue
        step = (t.x1 - t.x0) / len(words)
        out.extend(Token(w, t.x0 + i * step, t.y0, t.x0 + (i + 1) * step, t.y1) for i, w in enumerate(words))
    return out


def pair_tokens(lines: list[list[Token]], labels: str, allow_bare: bool,
                list_style_only: bool = False) -> list[tuple[int, str]]:
    """Pair question numbers with the answer letter that follows them.

    allow_bare: accept numbers without a 'Q' prefix.
    list_style_only: with allow_bare, only accept list-style numbers ('1.', '2)').
    """
    labels = labels.upper()
    bare_re = LIST_NUMBER if list_style_only else BARE_NUMBER
    pairs = []
    for line in lines:
        texts = [t.text.strip() for t in split_cells(line)]
        i = 0
        while i < len(texts):
            text = texts[i]
            m = INLINE.match(text)
            if m and m.group(2).upper() in labels:
                pairs.append((int(m.group(1)), m.group(2).upper()))
                i += 1
                continue
            qm = Q_PREFIXED.match(text) or (bare_re.match(text) if allow_bare else None)
            if qm and i + 1 < len(texts):
                am = ANSWER.match(texts[i + 1])
                if am and am.group(1).upper() in labels:
                    pairs.append((int(qm.group(1)), am.group(1).upper()))
                    i += 2
                    continue
            i += 1
    return pairs


def resolve(pairs: list[tuple[int, str]], result: ExtractionResult) -> None:
    seen: dict[int, set[str]] = {}
    for q, a in pairs:
        if q < 1:
            continue
        seen.setdefault(q, set()).add(a)
    for q, letters in sorted(seen.items()):
        if len(letters) == 1:
            result.answers[q] = next(iter(letters))
        else:
            result.conflicts[q] = sorted(letters)


def extract_from_lines(lines: list[list[Token]], labels: str, method: str) -> ExtractionResult:
    result = ExtractionResult(method=method)
    prefixed = pair_tokens(lines, labels, allow_bare=False)
    if len(prefixed) >= 3:
        # Also accept list-style items ("1. A") for questions the table did not cover.
        covered = {q for q, _ in prefixed}
        listed = [(q, a) for q, a in pair_tokens(lines, labels, allow_bare=True, list_style_only=True)
                  if q not in covered]
        resolve(prefixed + listed, result)
    else:
        bare = pair_tokens(lines, labels, allow_bare=True)
        resolve(bare, result)
        if bare:
            result.warnings.append("Question numbers had no “Q” prefix; please check the pairing carefully.")
    result.suggested_total = _suggest_total(" ".join(t.text for line in lines for t in line))
    return result


def _suggest_total(text: str) -> int | None:
    m = re.search(r"Total\s+(?:No\.?\s+of\s+)?Questions?\s*[:\-]?\s*(\d{1,3})", text, re.I)
    return int(m.group(1)) if m else None


def finalize(result: ExtractionResult, expected: int | None) -> ExtractionResult:
    total = expected or result.suggested_total or (max(result.answers) if result.answers else 0)
    result.details["expected_total"] = total
    if total:
        extra = sorted(q for q in result.answers if q > total)
        if extra:
            result.warnings.append(f"Ignored answers beyond question {total}: {_ranges(extra)}.")
            for q in extra:
                result.answers.pop(q)
        missing = [q for q in range(1, total + 1) if q not in result.answers]
        if missing:
            result.warnings.append(f"No answer found for question(s) {_ranges(missing)}.")
    if result.conflicts:
        result.warnings.append(
            "Conflicting answers found for question(s) " + _ranges(sorted(result.conflicts)) + "."
        )
    if not result.answers:
        result.warnings.append("No question–answer pairs could be recognised in this document.")
    return result


def _ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}" if start == prev else f"{start}–{prev}")
            start = prev = n
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
    return ", ".join(out)


# --- format readers ------------------------------------------------------------------

def extract(data: bytes, kind: str, labels: str = "ABCD", expected: int | None = None,
            tesseract_cmd: str | None = None) -> ExtractionResult:
    readers = {
        "pdf": lambda: _from_pdf(data, labels, tesseract_cmd),
        "png": lambda: _from_image_bytes(data, labels, tesseract_cmd),
        "jpg": lambda: _from_image_bytes(data, labels, tesseract_cmd),
        "xlsx": lambda: _from_xlsx(data, labels),
        "docx": lambda: _from_docx(data, labels),
        "csv": lambda: _from_csv(data, labels),
        "json": lambda: _from_json(data, labels),
    }
    if kind not in readers:
        raise ValueError(f"Unsupported answer-key format: {kind}")
    return finalize(readers[kind](), expected)


def _from_pdf(data: bytes, labels: str, tesseract_cmd: str | None) -> ExtractionResult:
    import pymupdf

    lines: list[list[Token]] = []
    offset = 0.0
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            words = page.get_text("words")
            tokens = [Token(w[4], w[0], w[1] + offset, w[2], w[3] + offset) for w in words]
            lines.extend(group_lines(tokens))
            offset += page.rect.height + 50
        result = extract_from_lines(lines, labels, "PDF text layer")
        if len(result.answers) >= 3:
            return result
        # Scanned PDF: no usable text layer → OCR each page.
        images = []
        for page in doc:
            pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
            images.append(pix.tobytes("png"))
    runs_per_page = [_ocr_lines(png, tesseract_cmd, y_offset=i * 100000) for i, png in enumerate(images)]
    merged_runs = [[line for page in runs_per_page for line in page[r]] for r in range(len(runs_per_page[0]))]
    result = _vote(merged_runs, labels, "OCR (scanned PDF)")
    result.warnings.append("This PDF has no text layer; answers were read with OCR — check each one.")
    return result


def _from_image_bytes(data: bytes, labels: str, tesseract_cmd: str | None) -> ExtractionResult:
    result = _vote(_ocr_lines(data, tesseract_cmd), labels, "OCR (image)")
    result.warnings.append("Answers were read from an image with OCR — check each one.")
    return result


MIN_OCR_VOTES = 2


def _vote(runs: list[list[list[Token]]], labels: str, method: str) -> ExtractionResult:
    """Combine several OCR readings. A question is accepted only when every run
    that saw it agrees; any disagreement becomes a conflict for the teacher."""
    result = ExtractionResult(method=method)
    votes: dict[int, dict[str, int]] = {}
    for lines in runs:
        pairs = pair_tokens(lines, labels, allow_bare=False)
        if len(pairs) < 3:
            pairs = pair_tokens(lines, labels, allow_bare=True)
        per_run: dict[int, set[str]] = {}
        for q, a in pairs:
            per_run.setdefault(q, set()).add(a)
        for q, letters in per_run.items():
            for a in letters:
                votes.setdefault(q, {}).setdefault(a, 0)
                votes[q][a] += 1
        if result.suggested_total is None:
            result.suggested_total = _suggest_total(" ".join(t.text for line in lines for t in line))
    unconfirmed = []
    for q, tally in sorted(votes.items()):
        if q < 1:
            continue
        if len(tally) > 1:
            result.conflicts[q] = sorted(tally)
        elif next(iter(tally.values())) >= MIN_OCR_VOTES:
            result.answers[q] = next(iter(tally))
        else:
            unconfirmed.append(q)  # seen by only one reading: too weak to suggest
    if unconfirmed:
        result.details["unconfirmed"] = unconfirmed
    result.details["ocr_runs"] = len(runs)
    return result


def _ocr_lines(image_bytes: bytes, tesseract_cmd: str | None, y_offset: float = 0) -> list[list[list[Token]]]:
    """OCR with several page-segmentation modes and two line groupings; returns one
    line list per reading, for voting."""
    import cv2
    import numpy as np
    import pytesseract
    from PIL import Image, ImageOps

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    with Image.open(io.BytesIO(image_bytes)) as im:
        gray = np.array(ImageOps.exif_transpose(im).convert("L"))
    if gray.shape[1] < 2000:  # upscale very small images for OCR
        f = 2000 / gray.shape[1]
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    gray = _deskew(gray)
    # Remove table rules, which confuse the OCR line finder.
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    for size in [(max(40, gray.shape[1] // 30), 1), (1, max(40, gray.shape[0] // 30))]:
        lines_mask = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size))
        gray = np.where(lines_mask > 0, 255, gray).astype(np.uint8)

    runs: list[list[list[Token]]] = []
    for psm in (6, 4, 11):
        d = pytesseract.image_to_data(gray, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
        keep = [i for i in range(len(d["text"])) if d["text"][i].strip() and float(d["conf"][i]) > 20]
        tokens = {i: Token(normalise_ocr_token(d["text"][i]), d["left"][i], d["top"][i] + y_offset,
                           d["left"][i] + d["width"][i], d["top"][i] + d["height"][i] + y_offset) for i in keep}
        # Grouping 1: Tesseract's own line structure (robust to slight skew).
        by_line: dict[tuple, list[Token]] = {}
        for i in keep:
            by_line.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(tokens[i])
        runs.append([sorted(v, key=lambda t: t.x0) for v in by_line.values()])
        # Grouping 2: geometric rows (joins table cells Tesseract split into blocks).
        runs.append(group_lines(list(tokens.values())))
    return runs


def _deskew(gray):
    """Rotate so text rows are horizontal. The angle is the median orientation
    of long near-horizontal strokes (table rules and text baselines)."""
    import cv2
    import numpy as np

    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    min_len = gray.shape[1] // 8
    lines = cv2.HoughLinesP(bw, 1, np.pi / 1800, threshold=200, minLineLength=min_len, maxLineGap=8)
    if lines is None:
        return gray
    angles = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):  # shape differs across OpenCV versions
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) < 15:
            angles.append(a)
    if len(angles) < 3:
        return gray
    estimate = float(np.median(angles))

    # Refine with a projection-profile search: rows are sharpest when level.
    small = cv2.resize(bw, (bw.shape[1] // 3, bw.shape[0] // 3), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape

    def sharpness(a):
        M = cv2.getRotationMatrix2D((sw / 2, sh / 2), a, 1.0)
        prof = cv2.warpAffine(small, M, (sw, sh)).sum(axis=1, dtype=np.float64)
        return float(np.var(np.diff(prof)))

    candidates = np.arange(estimate - 1.0, estimate + 1.0001, 0.1)
    angle = float(max(candidates, key=sharpness))
    if abs(angle) < 0.1:
        return gray
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)


def _from_xlsx(data: bytes, labels: str) -> ExtractionResult:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines: list[list[Token]] = []
    offset = 0
    for ws in wb.worksheets:
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            line = [Token(_cell(v), c, offset + r, c + 0.9, offset + r + 0.9)
                    for c, v in enumerate(row) if v is not None and _cell(v)]
            if line:
                lines.append(line)
        offset += 100000
    wb.close()
    return extract_from_lines(lines, labels, "Excel workbook")


def _cell(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _from_docx(data: bytes, labels: str) -> ExtractionResult:
    import docx

    document = docx.Document(io.BytesIO(data))
    lines: list[list[Token]] = []
    y = 0
    for para in document.paragraphs:
        words = para.text.split()
        if words:
            lines.append([Token(w, i, y, i + 0.9, y + 0.9) for i, w in enumerate(words)])
        y += 1
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            # Merged cells repeat their text; drop consecutive duplicates.
            dedup = [c for i, c in enumerate(cells) if c and (i == 0 or c != cells[i - 1])]
            if dedup:
                lines.append([Token(c, i, y, i + 0.9, y + 0.9) for i, c in enumerate(dedup)])
            y += 1
    return extract_from_lines(lines, labels, "Word document")


def _from_csv(data: bytes, labels: str) -> ExtractionResult:
    text = data.decode("utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    lines = []
    for r, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        line = [Token(v.strip(), c, r, c + 0.9, r + 0.9) for c, v in enumerate(row) if v.strip()]
        if line:
            lines.append(line)
    return extract_from_lines(lines, labels, "CSV file")


def _from_json(data: bytes, labels: str) -> ExtractionResult:
    """Accepts {"1": "A", ...}, {"answers": {...}}, [{"question": 1, "answer": "A"}, ...] or ["A", "C", ...]."""
    obj = json.loads(data.decode("utf-8-sig"))
    result = ExtractionResult(method="JSON file")
    if isinstance(obj, dict):
        for key in ("answers", "answer_key", "key", "correct_answers"):
            if key in obj:
                if isinstance(obj.get("total_questions"), int):
                    result.suggested_total = obj["total_questions"]
                obj = obj[key]
                break
    pairs: list[tuple[int, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            m = re.search(r"(\d{1,3})", str(k))
            if m and isinstance(v, str):
                pairs.append((int(m.group(1)), v.strip().upper()))
    elif isinstance(obj, list):
        for i, item in enumerate(obj, start=1):
            if isinstance(item, str):
                pairs.append((i, item.strip().upper()))
            elif isinstance(item, dict):
                q = item.get("question") or item.get("q") or item.get("number") or i
                a = item.get("answer") or item.get("correct") or item.get("key")
                if a is not None:
                    pairs.append((int(re.sub(r"\D", "", str(q)) or i), str(a).strip().upper()))
    invalid = sorted({q for q, a in pairs if len(a) != 1 or a not in labels.upper()})
    if invalid:
        result.warnings.append(f"Answers outside {labels} were ignored for question(s) {_ranges(invalid)}.")
    resolve([(q, a) for q, a in pairs if len(a) == 1 and a in labels.upper()], result)
    return result
