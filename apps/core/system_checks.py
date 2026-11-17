"""Environment checks shown to the teacher, so missing components (e.g.
Tesseract) are visible instead of failing silently during processing."""

import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""


def run_system_checks() -> list[Check]:
    checks = []
    for module, label in [("cv2", "OpenCV"), ("pymupdf", "PyMuPDF"), ("numpy", "NumPy"),
                          ("PIL", "Pillow"), ("openpyxl", "OpenPyXL"), ("reportlab", "ReportLab")]:
        try:
            mod = __import__(module)
            version = getattr(mod, "__version__", None) or getattr(mod, "VersionBind", None) or "installed"
            checks.append(Check(label, True, str(version)))
        except Exception as exc:  # pragma: no cover - depends on environment
            checks.append(Check(label, False, "not installed", f"{exc.__class__.__name__}"))

    cmd = settings.TESSERACT_CMD
    version = _tesseract_version(cmd)
    if version:
        checks.append(Check("Tesseract OCR", True, version, cmd))
    else:
        checks.append(Check("Tesseract OCR", False, "not found", f"Expected at {cmd}. Set TESSERACT_CMD in .env."))

    data_dir = Path(settings.DATA_DIR)
    writable = data_dir.exists() and _writable(data_dir)
    checks.append(Check("Data directory", writable, "writable" if writable else "not writable", str(data_dir)))
    return checks


@lru_cache(maxsize=4)
def _tesseract_version(cmd: str) -> str | None:
    exe = cmd if Path(cmd).exists() else shutil.which(cmd)
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"tesseract\s+v?(\d+\.\d+(?:\.\d+)?)", out.stdout or out.stderr or "")
    return match.group(1) if match else "installed"


def _writable(path: Path) -> bool:
    probe = path / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
