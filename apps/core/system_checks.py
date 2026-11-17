"""Environment checks shown to the teacher, so missing components (e.g.
Tesseract) are visible instead of failing silently during processing."""

import shutil
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def run_system_checks() -> list[Check]:
    checks = []
    for module, label in [("cv2", "OpenCV"), ("pymupdf", "PyMuPDF"), ("numpy", "NumPy"),
                          ("PIL", "Pillow"), ("openpyxl", "OpenPyXL"), ("reportlab", "ReportLab")]:
        try:
            mod = __import__(module)
            checks.append(Check(label, True, getattr(mod, "__version__", getattr(mod, "VersionBind", "installed"))))
        except Exception as exc:  # pragma: no cover - depends on environment
            checks.append(Check(label, False, f"not importable: {exc.__class__.__name__}"))

    cmd = settings.TESSERACT_CMD
    found = Path(cmd).exists() or shutil.which(cmd) is not None
    checks.append(Check("Tesseract OCR", found, cmd if found else f"not found at {cmd} (set TESSERACT_CMD)"))

    data_dir = Path(settings.DATA_DIR)
    writable = data_dir.exists() and _writable(data_dir)
    checks.append(Check("Data directory", writable, "writable" if writable else "not writable"))
    return checks


def _writable(path: Path) -> bool:
    probe = path / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
