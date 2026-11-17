"""Optical Mark Recognition engine.

Independent of Django: it takes page images and a calibrated sheet template
and returns structured, confidence-scored readings. It never decides marks;
the evaluation engine does that from verified readings.
"""

from .errors import OMRError
from .imaging import load_pages
from .reader import ReadingThresholds, SheetReading, read_sheet
from .template import LayoutSpec, SheetTemplate, build_template

__all__ = [
    "OMRError",
    "load_pages",
    "LayoutSpec",
    "SheetTemplate",
    "build_template",
    "ReadingThresholds",
    "SheetReading",
    "read_sheet",
]
