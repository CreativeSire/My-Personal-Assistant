import os
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import invoice_pipeline


class _FakeQueue:
    def update_progress(self, task_id, progress, message=""):
        return None


class TestInvoiceJobZipBatch(unittest.TestCase):
    def test_zip_batch_generates_counts_and_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_stub = SimpleNamespace(
                workspace_dir=os.path.join(td, "workspace"),
                base_dir=td,
                invoice_allowed_exts=("pdf", "png"),
                invoice_job_enabled=True,
                invoice_mode="production",
                golden_input_path="",
                golden_images_only=True,
                invoice_schema_path=os.path.join(os.path.dirname(__file__), "schemas", "invoice_43.schema.json"),
                golden_expected_path=os.path.join(td, "golden43_expected.json"),
                model_name="gemini-2.0-flash-001",
            )
            os.makedirs(cfg_stub.workspace_dir, exist_ok=True)
            pdf_path = os.path.join(td, "a.pdf")
            png_path = os.path.join(td, "b.png")
            txt_path = os.path.join(td, "c.txt")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n%a")
            with open(png_path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("ignored")
            zpath = os.path.join(td, "batch.zip")
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.write(pdf_path, "a.pdf")
                zf.write(png_path, "b.png")
                zf.write(txt_path, "c.txt")

            def fake_process(path, ctx):
                if path.endswith(".pdf"):
                    out = os.path.join(ctx.ok_dir, "MAIN_Lagos_123456_2026-02-11.pdf")
                    with open(out, "wb") as wf:
                        wf.write(b"%PDF")
                    return invoice_pipeline.DocumentResult("ok", "validated", out, {})
                out = os.path.join(ctx.review_dir, "b.pdf")
                with open(out, "wb") as wf:
                    wf.write(b"%PDF")
                return invoice_pipeline.DocumentResult("review", "missing_delivery_date", out, {})

            with patch.object(invoice_pipeline, "cfg", cfg_stub):
                with patch.object(invoice_pipeline, "TaskQueue", _FakeQueue):
                    with patch.object(invoice_pipeline, "process_document", side_effect=fake_process):
                        result = invoice_pipeline.run_invoice_job({"input_path": zpath, "_task_id": "tzip"})
            self.assertIn("<<SEND_FILE:", result)
            self.assertIn("Total=2", result)
            self.assertIn("OK=1", result)
            self.assertIn("Review=1", result)


if __name__ == "__main__":
    unittest.main()
