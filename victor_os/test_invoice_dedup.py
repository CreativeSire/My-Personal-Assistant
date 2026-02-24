import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import invoice_pipeline


class TestInvoiceDedup(unittest.TestCase):
    def test_hash_identical_skip(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(
                invoice_pipeline,
                "cfg",
                SimpleNamespace(
                    workspace_dir=td,
                    base_dir=td,
                    invoice_allowed_exts=("pdf",),
                    invoice_job_enabled=True,
                    model_name="gemini-2.0-flash-001",
                ),
            ):
                src = os.path.join(td, "x.pdf")
                with open(src, "wb") as f:
                    f.write(b"%PDF-1.4\n%fake")
                ctx = invoice_pipeline._build_job_context("job1")
                with patch.object(invoice_pipeline, "_extract_text", return_value=("Invoice 123456\nDate 2026-02-11\nOutlet: Main, Lagos", {})):
                    first = invoice_pipeline.process_document(src, ctx)
                    second = invoice_pipeline.process_document(src, ctx)
                self.assertEqual(first.status, "ok")
                self.assertEqual(second.status, "skipped_duplicate")

    def test_name_collision_gets_dup_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(
                invoice_pipeline,
                "cfg",
                SimpleNamespace(
                    workspace_dir=td,
                    base_dir=td,
                    invoice_allowed_exts=("pdf",),
                    invoice_job_enabled=True,
                    model_name="gemini-2.0-flash-001",
                ),
            ):
                ctx = invoice_pipeline._build_job_context("job2")
                a = invoice_pipeline._resolve_collision("MAIN_Lagos_123456_2026-02-11.pdf", ctx)
                b = invoice_pipeline._resolve_collision("MAIN_Lagos_123456_2026-02-11.pdf", ctx)
                self.assertTrue(a.endswith(".pdf"))
                self.assertIn("_DUP2.pdf", b)


if __name__ == "__main__":
    unittest.main()
