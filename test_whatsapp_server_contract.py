import os
import unittest

from config import VictorConfig, validate_or_raise


class TestWhatsAppServerContract(unittest.TestCase):
    def test_fail_fast_when_whatsapp_enabled_missing_twilio(self):
        cfg = VictorConfig(
            google_api_key="k",
            gemini_api_key="k",
            telegram_bot_token="t",
            memory_v3_enabled=False,
            whatsapp_enabled=True,
            twilio_sid="",
            twilio_auth_token="",
            twilio_whatsapp_number="",
        )
        with self.assertRaises(Exception):
            validate_or_raise(cfg)

    def test_dual_mode_flags_are_valid(self):
        cfg = VictorConfig(
            google_api_key="k",
            gemini_api_key="k",
            telegram_bot_token="t",
            memory_v3_enabled=False,
            whatsapp_enabled=False,
            app_env="dev",
            whatsapp_fallback_inmemory=True,
        )
        out = validate_or_raise(cfg)
        self.assertEqual(out.app_env, "dev")
        self.assertTrue(out.whatsapp_fallback_inmemory)


if __name__ == "__main__":
    unittest.main()
