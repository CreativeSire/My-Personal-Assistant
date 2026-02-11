import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import invoice_pipeline


class TestInvoiceRouting(unittest.TestCase):
    def _mk_fields(self, **overrides):
        fields = invoice_pipeline._blank_fields()
        fields.update(overrides)
        return invoice_pipeline.ExtractedFields(
            fields=fields,
            confidence_score=float(fields.get("confidence_score", 0.9) or 0.9),
            schema_valid=True,
            violations=[],
            _extractor_model_used="unit_test",
            ocr_text_present=True,
        )

    def test_valid_routes_ok(self):
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
                src = os.path.join(td, "ok.pdf")
                with open(src, "wb") as f:
                    f.write(b"%PDF-1.4\n%ok")
                ctx = invoice_pipeline._build_job_context("job_ok")
                with patch.object(invoice_pipeline, "_extract_text", return_value=("x", {})):
                    with patch.object(
                        invoice_pipeline,
                        "extract_invoice_fields",
                        return_value=self._mk_fields(
                            delivery_date="2026-02-11",
                            receiver_name="MAIN",
                            receiver_location="Lagos",
                            invoice_number="123456",
                            confidence_score=0.9,
                        ),
                    ):
                        out = invoice_pipeline.process_document(src, ctx)
                self.assertEqual(out.status, "ok")
                self.assertIn("artifacts", out.output_file)

    def test_missing_date_routes_review(self):
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
                src = os.path.join(td, "review.pdf")
                with open(src, "wb") as f:
                    f.write(b"%PDF-1.4\n%review")
                ctx = invoice_pipeline._build_job_context("job_review")
                with patch.object(invoice_pipeline, "_extract_text", return_value=("x", {})):
                    with patch.object(
                        invoice_pipeline,
                        "extract_invoice_fields",
                        return_value=invoice_pipeline.ExtractedFields(
                            fields=invoice_pipeline._blank_fields(),
                            confidence_score=0.9,
                            schema_valid=False,
                            violations=["missing_delivery_date"],
                            _extractor_model_used="unit_test",
                            ocr_text_present=True,
                        ),
                    ):
                        out = invoice_pipeline.process_document(src, ctx)
                self.assertEqual(out.status, "review")
                self.assertIn("missing_delivery_date", out.reason)


if __name__ == "__main__":
    unittest.main()
