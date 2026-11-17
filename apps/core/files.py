"""Safe storage of uploaded files.

Files are identified by content sniffing (never by extension alone), size
limited, stored under a random name inside DATA_DIR, fingerprinted with
SHA-256 and marked read-only so originals cannot be modified accidentally.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

PDF, PNG, JPEG, XLSX, DOCX, JSON, CSV = "pdf", "png", "jpg", "xlsx", "docx", "json", "csv"
IMAGE_KINDS = {PNG, JPEG}
KIND_LABELS = {PDF: "PDF", PNG: "PNG image", JPEG: "JPEG image", XLSX: "Excel workbook",
               DOCX: "Word document", JSON: "JSON file", CSV: "CSV file"}
MAX_ZIP_UNCOMPRESSED = 60 * 1024 * 1024
MAX_PDF_PAGES = 1000


@dataclass(frozen=True)
class StoredFile:
    relative_path: str  # relative to DATA_DIR, forward slashes
    original_name: str
    kind: str
    size: int
    sha256: str

    @property
    def path(self) -> Path:
        return data_path(self.relative_path)


def data_path(relative: str) -> Path:
    """Resolve a DATA_DIR-relative path, refusing anything that escapes it."""
    base = Path(settings.DATA_DIR).resolve()
    full = (base / relative).resolve()
    if base != full and base not in full.parents:
        raise ValidationError("Invalid file location.")
    return full


def clean_filename(name: str) -> str:
    name = Path(name or "file").name
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" .")
    return name[:150] or "file"


def sniff_kind(head: bytes, name: str = "") -> str | None:
    """Identify the file type from its leading bytes."""
    if head.startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    suffix = Path(name).suffix.lower()
    if suffix == ".json":
        return JSON
    if suffix == ".csv":
        return CSV
    return None


def _check_zip(data: bytes, expected: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            if sum(i.file_size for i in zf.infolist()) > MAX_ZIP_UNCOMPRESSED:
                raise ValidationError("The document is too large when uncompressed.")
    except zipfile.BadZipFile as exc:
        raise ValidationError("The document is damaged or not a valid Office file.") from exc
    if "[Content_Types].xml" not in names:
        raise ValidationError("The document is not a valid Office file.")
    if any(n.startswith("xl/") for n in names):
        kind = XLSX
    elif any(n.startswith("word/") for n in names):
        kind = DOCX
    else:
        raise ValidationError("Unsupported Office document.")
    if expected and kind != expected and expected != "office":
        raise ValidationError(f"Expected {KIND_LABELS.get(expected, expected)}, received {KIND_LABELS[kind]}.")
    return kind


def _validate_content(data: bytes, kind: str) -> None:
    if kind == PDF:
        import pymupdf

        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass:
                    raise ValidationError("Password-protected PDFs cannot be processed.")
                if doc.page_count == 0:
                    raise ValidationError("The PDF has no pages.")
                if doc.page_count > MAX_PDF_PAGES:
                    raise ValidationError(f"PDFs are limited to {MAX_PDF_PAGES} pages.")
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("The PDF is damaged or cannot be opened.") from exc
    elif kind in IMAGE_KINDS:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 60_000_000
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
        except Exception as exc:
            raise ValidationError("The image is damaged or too large.") from exc
    elif kind == JSON:
        try:
            json.loads(data.decode("utf-8-sig"))
        except Exception as exc:
            raise ValidationError("The JSON file is not valid.") from exc
    elif kind == CSV:
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("CSV files must be UTF-8 encoded.") from exc


def store_upload(uploaded, folder: str, allowed: set[str]) -> StoredFile:
    """Validate and store an uploaded file under DATA_DIR/<folder>/."""
    limit = settings.MCQ_MAX_UPLOAD_MB * 1024 * 1024
    if uploaded.size > limit:
        raise ValidationError(f"“{clean_filename(uploaded.name)}” exceeds the {settings.MCQ_MAX_UPLOAD_MB} MB limit.")
    data = b"".join(uploaded.chunks())
    if not data:
        raise ValidationError(f"“{clean_filename(uploaded.name)}” is empty.")
    return store_bytes(data, uploaded.name, folder, allowed)


def store_bytes(data: bytes, original_name: str, folder: str, allowed: set[str]) -> StoredFile:
    name = clean_filename(original_name)
    kind = sniff_kind(data[:16], name)
    if kind == "zip":
        office_expected = (allowed & {XLSX, DOCX})
        if not office_expected:
            raise ValidationError(f"“{name}” is not an accepted file type.")
        kind = _check_zip(data, next(iter(office_expected)) if len(office_expected) == 1 else "office")
    if kind not in allowed:
        accepted = ", ".join(sorted(KIND_LABELS[k] for k in allowed))
        raise ValidationError(f"“{name}” is not an accepted file type. Accepted: {accepted}.")
    _validate_content(data, kind)

    digest = hashlib.sha256(data).hexdigest()
    rel = f"{folder.strip('/')}/{uuid.uuid4().hex}.{kind}"
    target = data_path(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    os.chmod(target, stat.S_IREAD)  # originals are read-only
    return StoredFile(rel, name, kind, len(data), digest)


def discard(stored: StoredFile) -> None:
    """Remove a file stored during an operation that was subsequently rolled back."""
    try:
        p = stored.path
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        p.unlink(missing_ok=True)
    except OSError:
        pass
