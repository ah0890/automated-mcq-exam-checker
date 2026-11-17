"""Page loading and image normalisation."""

from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

from .errors import OMRError

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
DEFAULT_DPI = 200
MAX_PIXELS = 60_000_000  # guards against decompression bombs


def load_pages(source: str | Path | bytes, *, suffix: str | None = None, dpi: int = DEFAULT_DPI) -> list[np.ndarray]:
    """Return every page of a PDF or image as an 8-bit grayscale array.

    PDFs are rasterised (the text/vector layer is deliberately ignored so real
    scans and digital files follow the same path). Image EXIF orientation is
    honoured.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        suffix = (suffix or path.suffix).lower()
        data = path.read_bytes()
    else:
        data = source
        suffix = (suffix or "").lower()

    if suffix in PDF_SUFFIXES:
        return _load_pdf(data, dpi)
    if suffix in IMAGE_SUFFIXES:
        return [_load_image(data)]
    raise OMRError("unsupported_format", f"Unsupported file type '{suffix}'.")


def _load_pdf(data: bytes, dpi: int) -> list[np.ndarray]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise OMRError("unreadable_pdf", "The PDF could not be opened.") from exc
    if doc.needs_pass:
        raise OMRError("encrypted_pdf", "The PDF is password protected.")
    pages = []
    with doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
            if pix.width * pix.height > MAX_PIXELS:
                raise OMRError("page_too_large", "A PDF page is too large to process.")
            arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
            pages.append(arr.copy())
    if not pages:
        raise OMRError("empty_pdf", "The PDF has no pages.")
    return pages


def _load_image(data: bytes) -> np.ndarray:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            return np.array(im.convert("L"))
    except Image.DecompressionBombError as exc:
        raise OMRError("page_too_large", "The image is too large to process.") from exc
    except Exception as exc:
        raise OMRError("unreadable_image", "The image could not be decoded.") from exc


def normalise_illumination(gray: np.ndarray) -> np.ndarray:
    """Flatten uneven lighting (shadows, scanner gradients).

    The background is estimated with a large morphological closing (which
    removes dark print and marks) and divided out.
    """
    # Lighting varies slowly, so estimate it at quarter resolution for speed.
    h, w = gray.shape
    small = cv2.resize(gray, (max(1, w // 4), max(1, h // 4)), interpolation=cv2.INTER_AREA)
    k = max(5, (min(small.shape) // 40) | 1)
    background = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    background = cv2.GaussianBlur(background, (0, 0), k / 2)
    background = cv2.resize(background, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.divide(gray, np.maximum(background, 1), scale=255)
