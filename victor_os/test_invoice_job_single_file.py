import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import invoice_pipeline


class _FakeQueue:
    def update_progress(self, task_id, progress, message=""):
        return None


class TestInvoiceJobSingleFile(unittest.TestCase):
    def test_single_pdf_job_outputs_zip(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_stub = SimpleNamespace(
                workspace_dir=os.path.join(td, "workspace"),
                base_dir=td,
                invoice_allowed_exts=("pdf",),
                invoice_job_enabled=True,
                invoice_mode="production",
                golden_input_path="",
                golden_images_only=True,
                invoice_schema_path=os.path.join(os.path.dirname(__file__), "schemas", "invoice_43.schema.json"),
                golden_expected_path=os.path.join(td, "golden43_expected.json"),
                model_name="gemini-2.0-flash-001",
            )
            os.makedirs(cfg_stub.workspace_dir, exist_ok=True)
            src = os.path.join(td, "invoice.pdf")
            with open(src, "wb") as f:
                f.write(b"%PDF-1.4\n%single")

            def fake_process(path, ctx):
                out = os.path.join(ctx.ok_dir, "MAIN_Lagos_123456_2026-02-11.pdf")
                with open(out, "wb") as wf:
                    wf.write(b"%PDF-1.4\n%out")
                return invoice_pipeline.DocumentResult(
                    status="ok",
                    reason="validated",
                    output_file=out,
                    evidence={},
                )

            with patch.object(invoice_pipeline, "cfg", cfg_stub):
                with patch.object(invoice_pipeline, "TaskQueue", _FakeQueue):
                    with patch.object(invoice_pipeline, "process_document", side_effect=fake_process):
                        result = invoice_pipeline.run_invoice_job({"input_path": src, "_task_id": "t1"})
            self.assertIn("<<SEND_FILE:", result)
            self.assertIn("OK=1", result)


if __name__ == "__main__":
    unittest.main()
