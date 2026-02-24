import os
import unittest
from unittest.mock import patch

import config


class TestDailyBriefingConfig(unittest.TestCase):
    def test_boot_toggle_from_env(self):
        with patch.dict(os.environ, {"DAILY_BRIEFING_BOOT_RUN": "true"}, clear=False):
            cfg = config.load_config()
            self.assertTrue(cfg.daily_briefing_boot_run)

    def test_required_env_validation(self):
        cfg = config.VictorConfig(
            google_api_key="",
            gemini_api_key="",
            telegram_bot_token="",
            memory_v3_enabled=False,
        )
        with self.assertRaises(Exception):
            config.validate_or_raise(cfg)


if __name__ == "__main__":
    unittest.main()
