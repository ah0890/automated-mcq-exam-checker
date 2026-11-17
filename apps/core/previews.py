"""Render stored documents as PNG previews for authenticated views."""

import cv2
import numpy as np
import pymupdf
from django.http import FileResponse, Http404, HttpResponse

from .files import IMAGE_KINDS, PDF, data_path


def page_count(relative_path: str, kind: str) -> int:
    if kind == PDF:
        with pymupdf.open(data_path(relative_path)) as doc:
            return doc.page_count
    return 1 if kind in IMAGE_KINDS else 0


def png_response(relative_path: str, kind: str, page: int = 1, width: int = 1400) -> HttpResponse:
    path = data_path(relative_path)
    if not path.exists():
        raise Http404("File not found.")
    if kind == PDF:
        with pymupdf.open(path) as doc:
            if not 1 <= page <= doc.page_count:
                raise Http404("No such page.")
            p = doc[page - 1]
            zoom = width / p.rect.width
            png = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    elif kind in IMAGE_KINDS:
        img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise Http404("Image cannot be read.")
        if img.shape[1] > width:
            f = width / img.shape[1]
            img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        png = cv2.imencode(".png", img)[1].tobytes()
    elif kind == "png-file":
        return image_file_response(relative_path)
    else:
        raise Http404("No preview for this file type.")
    resp = HttpResponse(png, content_type="image/png")
    resp["Cache-Control"] = "private, max-age=600"
    return resp


def image_file_response(relative_path: str) -> FileResponse:
    path = data_path(relative_path)
    if not path.exists():
        raise Http404("File not found.")
    resp = FileResponse(open(path, "rb"), content_type="image/png")
    resp["Cache-Control"] = "private, max-age=600"
    return resp


def download_response(relative_path: str, filename: str) -> FileResponse:
    path = data_path(relative_path)
    if not path.exists():
        raise Http404("File not found.")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=filename)
