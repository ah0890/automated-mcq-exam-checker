import os
import shutil
import stat
import tempfile

import pymupdf
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.core.files import JPEG, PDF, PNG, XLSX, data_path, store_bytes, store_upload


class FileStorage(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(DATA_DIR=self.tmp, MCQ_MAX_UPLOAD_MB=1)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        for root, dirs, files in os.walk(self.tmp):
            for f in files:
                os.chmod(os.path.join(root, f), stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pdf(self):
        doc = pymupdf.open()
        doc.new_page()
        return doc.tobytes()

    def test_valid_pdf_is_stored_read_only_with_fingerprint(self):
        stored = store_bytes(self.pdf(), "../../evil name.pdf", "x", {PDF})
        self.assertEqual(stored.kind, PDF)
        self.assertEqual(stored.original_name, "evil name.pdf")
        self.assertTrue(stored.relative_path.startswith("x/"))
        self.assertEqual(len(stored.sha256), 64)
        self.assertFalse(os.access(stored.path, os.W_OK))

    def test_extension_is_not_trusted(self):
        with self.assertRaises(ValidationError):
            store_bytes(b"<script>alert(1)</script>", "paper.pdf", "x", {PDF})

    def test_disallowed_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            store_bytes(self.pdf(), "a.pdf", "x", {PNG, JPEG})

    def test_damaged_pdf_is_rejected(self):
        with self.assertRaises(ValidationError):
            store_bytes(b"%PDF-1.4 garbage", "a.pdf", "x", {PDF})

    def test_size_limit(self):
        big = SimpleUploadedFile("big.pdf", b"%PDF-" + b"0" * (1024 * 1024 + 10))
        with self.assertRaises(ValidationError):
            store_upload(big, "x", {PDF})

    def test_zip_that_is_not_office_is_rejected(self):
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("hello.txt", "hi")
        with self.assertRaises(ValidationError):
            store_bytes(buf.getvalue(), "a.xlsx", "x", {XLSX})

    def test_paths_cannot_escape_data_dir(self):
        with self.assertRaises(ValidationError):
            data_path("../outside.txt")
