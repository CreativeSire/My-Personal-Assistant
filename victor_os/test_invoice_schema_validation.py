import unittest

import invoice_pipeline


class TestInvoiceSchemaValidation(unittest.TestCase):
    def _mk(self, **overrides):
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

    def test_six_digit_invoice_enforced(self):
        fields = self._mk(
            delivery_date="2026-02-11",
            receiver_name="Lagos Outlet",
            receiver_location="Ikeja",
            invoice_number="12345",
            confidence_score=0.9,
        )
        out = invoice_pipeline._validate_fields(fields)
        self.assertFalse(out.schema_valid)
        self.assertIn("invoice_number_not_6_digits", out.violations)

    def test_missing_date_routes_invalid(self):
        fields = self._mk(
            receiver_name="Lagos Outlet",
            receiver_location="Ikeja",
            invoice_number="123456",
            confidence_score=0.9,
        )
        out = invoice_pipeline._validate_fields(fields)
        self.assertFalse(out.schema_valid)
        self.assertIn("missing_delivery_date", out.violations)

    def test_location_normalization(self):
        norm = invoice_pipeline._normalize_receiver_location("No 22 Allen Avenue, Ikeja")
        self.assertEqual(norm.lower(), "ikeja")


if __name__ == "__main__":
    unittest.main()
