"""Examination backup / restore and full application backup.

Examination backup: a ZIP with `manifest.json` (every record of the
examination, linked by permanent UUIDs — never database ids) and `files/…`
(every stored original, template and processed image, each with its
SHA-256). Restoring recreates the examination in any installation and
refuses to overwrite an examination that already exists.

Full backup: a consistent copy of the SQLite database (online backup API)
plus all data files, for disaster recovery.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import stat
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify

from apps.answerkeys.models import AnswerKey, AnswerKeyVersion
from apps.core.files import data_path
from apps.exams.models import Examination, SheetLayout
from apps.papers.models import Paper, SourceFile, UploadBatch
from apps.processing.models import Correction, ProcessingJob
from apps.results.models import EvaluationRun, FinalizationEvent, Result

FORMAT = "mcq-exam-backup"
VERSION = 1
MAX_ARCHIVE_BYTES = 4 * 1024 ** 3
BACKUP_DIR = "backups"

# Restore order (parents before children).
MODELS = [SheetLayout, AnswerKey, AnswerKeyVersion, Examination, UploadBatch, SourceFile, Paper, ProcessingJob,
          Correction, Result, EvaluationRun, FinalizationEvent]


def backups_dir() -> Path:
    d = data_path(BACKUP_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- serialisation --------------------------------------------------------------------------

def _value(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (Decimal, UUID)):
        return str(v)
    return v


def _dump(obj: models.Model) -> dict:
    out = {}
    for f in obj._meta.concrete_fields:
        if f.primary_key:
            continue
        if isinstance(f, models.ForeignKey):
            rel = getattr(obj, f.name)
            if rel is None:
                out[f.name] = None
            elif rel._meta.model is get_user_model():
                out[f.name] = {"user": rel.get_username()}
            elif hasattr(rel, "uid"):
                out[f.name] = {"uid": str(rel.uid)}
            else:  # pragma: no cover - all exported relations have a uid or are users
                out[f.name] = None
        else:
            out[f.name] = _value(getattr(obj, f.attname))
    if isinstance(obj, ProcessingJob):  # database ids are not portable; store paper uids
        uids = dict(Paper.objects.filter(pk__in=obj.paper_ids).values_list("pk", "uid"))
        out["paper_ids"] = [str(uids[i]) for i in obj.paper_ids if i in uids]
    return out


def _records(exam: Examination) -> dict[str, list]:
    keys = AnswerKey.objects.filter(versions__examinations=exam).distinct() | \
        AnswerKey.objects.filter(versions__results__exam=exam).distinct()
    keys = AnswerKey.objects.filter(pk__in=keys.values("pk"))
    rec = {
        "SheetLayout": [exam.sheet_layout],
        "AnswerKey": list(keys),
        "AnswerKeyVersion": list(AnswerKeyVersion.objects.filter(key__in=keys)),
        "Examination": [exam],
        "UploadBatch": list(UploadBatch.objects.filter(exam=exam)),
        "SourceFile": list(SourceFile.objects.filter(exam=exam)),
        "Paper": list(Paper.objects.filter(exam=exam)),
        "ProcessingJob": list(ProcessingJob.objects.filter(exam=exam)),
        "Correction": list(Correction.objects.filter(paper__exam=exam)),
        "Result": list(Result.objects.filter(exam=exam)),
        "EvaluationRun": list(EvaluationRun.objects.filter(exam=exam)),
        "FinalizationEvent": list(FinalizationEvent.objects.filter(exam=exam)),
    }
    return rec


def _file_paths(rec) -> set[str]:
    paths = set()
    layout = rec["SheetLayout"][0]
    paths.add(layout.blank_path)
    tdir = data_path(layout.template_dir)
    for f in tdir.glob("*"):
        paths.add(f"{layout.template_dir}/{f.name}")
    preview = layout.blank_path.rsplit("/", 1)[0] + "/preview.png"
    if data_path(preview).exists():
        paths.add(preview)
    for v in rec["AnswerKeyVersion"]:
        if v.source_path:
            paths.add(v.source_path)
    for s in rec["SourceFile"]:
        paths.add(s.path)
    for p in rec["Paper"]:
        if p.processed_image:
            paths.add(p.processed_image)
    return {p for p in paths if p and data_path(p).exists()}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_exam(exam: Examination, user=None) -> Path:
    exam = Examination.objects.select_related("sheet_layout").get(pk=exam.pk)  # always the stored state
    rec = _records(exam)
    files = sorted(_file_paths(rec))
    manifest = {
        "format": FORMAT, "version": VERSION, "created_at": timezone.now().isoformat(),
        "created_by": user.get_username() if user else None,
        "exam": {"uid": str(exam.uid), "name": exam.name, "class_section": exam.class_section,
                 "exam_date": exam.exam_date.isoformat(), "status": exam.status},
        "records": {name: [_dump(o) for o in objs] for name, objs in rec.items()},
        "files": {p: _sha(data_path(p)) for p in files},
    }
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    name = f"exam-{slugify(exam.name)[:40] or 'examination'}-{stamp}.zip"
    target = backups_dir() / name
    tmp = target.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=1))
        for p in files:
            zf.write(data_path(p), f"files/{p}")
    os.replace(tmp, target)
    return target


# --- restore ---------------------------------------------------------------------------------

def _safe_member(name: str) -> bool:
    return not (name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts or ":" in name)


def _resolve(field: models.Field, value):
    if value is None:
        return None
    rel_model = field.related_model
    if "user" in value:
        return get_user_model().objects.filter(username=value["user"]).first()
    return rel_model.objects.get(uid=value["uid"])


def _build(model, data: dict):
    kwargs = {}
    for f in model._meta.concrete_fields:
        if f.primary_key or f.name not in data:
            continue
        v = data[f.name]
        if isinstance(f, models.ForeignKey):
            kwargs[f.name] = _resolve(f, v)
        elif isinstance(f, models.DateTimeField) and v:
            kwargs[f.name] = parse_datetime(v)
        elif isinstance(f, models.DateField) and v:
            kwargs[f.name] = parse_date(v)
        elif isinstance(f, models.DecimalField) and v is not None:
            kwargs[f.name] = Decimal(v)
        else:
            kwargs[f.name] = v
    if model is ProcessingJob:
        pks = dict(Paper.objects.filter(uid__in=kwargs.get("paper_ids") or []).values_list("uid", "pk"))
        kwargs["paper_ids"] = [pks[UUID(u)] for u in kwargs.get("paper_ids") or [] if UUID(u) in pks]
    obj = model(**kwargs)
    # Preserve original timestamps (auto_now/auto_now_add would overwrite them).
    obj.save_base(raw=True)
    return obj


def read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(zf.read("manifest.json"))
    except Exception as exc:
        raise ValidationError("This is not an examination backup (manifest missing or unreadable).") from exc
    if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        raise ValidationError("Unsupported backup format or version.")
    return manifest


@transaction.atomic
def restore_exam(archive) -> Examination:
    """Restore an examination backup (file path or file-like)."""
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise ValidationError("The file is not a valid ZIP archive.") from exc
    with zf:
        if any(not _safe_member(n) for n in zf.namelist()):
            raise ValidationError("The archive contains unsafe file paths.")
        if sum(i.file_size for i in zf.infolist()) > MAX_ARCHIVE_BYTES:
            raise ValidationError("The archive is too large.")
        manifest = read_manifest(zf)
        exam_uid = manifest["exam"]["uid"]
        if Examination.objects.filter(uid=exam_uid).exists():
            raise ValidationError(f"The examination “{manifest['exam']['name']}” already exists in this "
                                  "installation; it was not overwritten.")
        # Verify every file before touching the database or disk.
        for rel, sha in manifest["files"].items():
            data = zf.read(f"files/{rel}")
            if hashlib.sha256(data).hexdigest() != sha:
                raise ValidationError(f"A file in the backup is damaged ({Path(rel).name}).")
            target = data_path(rel)
            if target.exists() and _sha(target) != sha:
                raise ValidationError(f"A different file already exists at {rel}; restore stopped.")

        records = manifest["records"]
        for model in MODELS:
            for data in records.get(model.__name__, []):
                if "uid" in data and model.objects.filter(uid=data["uid"]).exists():
                    continue  # shared records (sheet layout, answer keys) already present
                if model is Result and Result.objects.filter(paper__uid=data["paper"]["uid"]).exists():
                    continue
                _build(model, data)

        for rel in manifest["files"]:
            target = data_path(rel)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(f"files/{rel}"))
            if "/processed_papers/" not in rel:
                os.chmod(target, stat.S_IREAD)  # originals stay read-only
    return Examination.objects.get(uid=exam_uid)


# --- full backup --------------------------------------------------------------------------------

def full_backup() -> Path:
    """Consistent database copy + all data files (excluding backups and logs)."""
    base = Path(settings.DATA_DIR).resolve()
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    target = backups_dir() / f"full-backup-{stamp}.zip"
    tmp = target.with_suffix(".part")
    db_path = Path(settings.DATABASES["default"]["NAME"])
    snapshot = io.BytesIO()
    with sqlite3.connect(str(db_path)) as src, sqlite3.connect(":memory:") as mem:
        src.backup(mem)
        for line in mem.iterdump():
            snapshot.write((line + "\n").encode("utf-8"))
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", "Full backup of the MCQ Examination Checker.\n"
                                  "database.sql: SQL dump of the database (restore with: sqlite3 db.sqlite3 < database.sql)\n"
                                  "data/: examination files, answer keys and sheet layouts.\n")
        zf.writestr("database.sql", snapshot.getvalue())
        for path in base.rglob("*"):
            rel = path.relative_to(base).as_posix()
            if path.is_dir() or rel.startswith((BACKUP_DIR + "/", "logs/")) or rel.startswith("db.sqlite3") \
                    or rel == ".secret_key" or rel.endswith(".part"):
                continue
            zf.write(path, f"data/{rel}")
    os.replace(tmp, target)
    return target


def list_backups() -> list[dict]:
    items = []
    for p in sorted(backups_dir().glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        items.append({"name": p.name, "size": st.st_size, "kind": "Full" if p.name.startswith("full-") else "Examination",
                      "created": datetime.fromtimestamp(st.st_mtime, tz=timezone.get_current_timezone())})
    return items
