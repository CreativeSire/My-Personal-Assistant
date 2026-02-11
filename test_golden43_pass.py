import json
import os
import re
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import invoice_pipeline


class _FakeQueue:
    def update_progress(self, task_id, progress, message=""):
        return None


class TestGolden43Pass(unittest.TestCase):
    def test_golden_43_mode_uses_test_zip_and_verifies(self):
        zip_path = r"c:\Users\HomePC\Desktop\Test.zip"
        if not os.path.exists(zip_path):
            self.skipTest("Golden fixture not present: c:\\Users\\HomePC\\Desktop\\Test.zip")

        with tempfile.TemporaryDirectory() as td:
            workspace = os.path.join(td, "workspace")
            os.makedirs(workspace, exist_ok=True)

            # Build expected labels from actual zip basenames so comparison is deterministic.
            expected = {}
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = [n for n in zf.namelist() if not n.endswith("/")]
                image_names = [n for n in names if os.path.splitext(n)[1].lower() in {".jpg", ".jpeg", ".png"}]
                self.assertEqual(len(image_names), 43)
                for full in image_names:
                    hasher = __import__("hashlib").sha256()
                    with zf.open(full) as src:
                        hasher.update(src.read())
                    source_id = hasher.hexdigest()
                    fields = invoice_pipeline._blank_fields()
                    fields.update(
                        {
                            "delivery_date": "2026-01-01",
                            "receiver_name": "Golden Outlet",
                            "receiver_location": "Lagos",
                            "receiver_outlet": "Golden Outlet (Lagos)",
                            "invoice_number": "123456",
                            "confidence_score": 0.95,
                            "ocr_text_present": True,
                            "_extractor_model_used": "golden_test_model",
                        }
                    )
                    expected[source_id] = fields

            expected_path = os.path.join(td, "golden43_expected.json")
            with open(expected_path, "w", encoding="utf-8") as f:
                json.dump(expected, f, indent=2)

            cfg_stub = SimpleNamespace(
                invoice_job_enabled=True,
                invoice_mode="golden_verify",
                golden_input_path=zip_path,
                golden_images_only=True,
                invoice_schema_path=os.path.join(os.path.dirname(__file__), "schemas", "invoice_43.schema.json"),
                golden_expected_path=expected_path,
                workspace_dir=workspace,
                base_dir=td,
                invoice_allowed_exts=("png", "jpg", "jpeg", "pdf"),
                model_name="gemini-2.0-flash-001",
            )

            def fake_process(path, ctx):
                source_id = invoice_pipeline._sha256_file(path)
                fields = expected[source_id]
                out_name = f"{fields['receiver_name']}_{fields['receiver_location']}_{fields['invoice_number']}_{fields['delivery_date']}.pdf"
                out_pdf = os.path.join(ctx.ok_dir, out_name)
                with open(out_pdf, "wb") as wf:
                    wf.write(b"%PDF-1.4\n%golden")
                return invoice_pipeline.DocumentResult(
                    status="ok",
                    reason="validated",
                    output_file=out_pdf,
                    evidence={
                        "input_file": os.path.basename(path),
                        "source_id": source_id,
                        "fields": fields,
                    },
                )

            with patch.object(invoice_pipeline, "cfg", cfg_stub):
                with patch.object(invoice_pipeline, "TaskQueue", _FakeQueue):
                    with patch.object(invoice_pipeline, "process_document", side_effect=fake_process):
                        result = invoice_pipeline.run_invoice_job({"mode": "golden_verify", "_task_id": "golden43"})

            self.assertIn("<<SEND_FILE:", result)
            self.assertIn("Total=43", result)
            self.assertIn("OK=43", result)
            self.assertIn("Review=0", result)
            self.assertIn("Failed=0", result)

            m = re.search(r"<<SEND_FILE:\s*(.*?)>>", result)
            self.assertIsNotNone(m)
            rel_zip = m.group(1)
            abs_zip = os.path.join(td, rel_zip.replace("/", os.sep))
            self.assertTrue(os.path.exists(abs_zip))

            job_dir = os.path.dirname(abs_zip)
            report_path = os.path.join(job_dir, "golden_verification_report.json")
            self.assertTrue(os.path.exists(report_path))
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
            self.assertEqual(report.get("processed_count"), 43)
            self.assertTrue(report.get("pass_counts"))
            self.assertTrue(report.get("pass_labels"))
            self.assertTrue(report.get("pass_rename"))
            self.assertTrue(report.get("pass_all"))


if __name__ == "__main__":
    unittest.main()
