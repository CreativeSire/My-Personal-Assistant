import unittest
from types import SimpleNamespace

from proactive_engine import ProactiveEngine


class TestAlertEmailOnlyFlow(unittest.TestCase):
    def test_email_only_channels(self):
        engine = ProactiveEngine()
        engine._cfg = SimpleNamespace(
            proactive_email_enabled=True,
            proactive_telegram_enabled=False,
            whatsapp_enabled=False,
            notify_fanout_mode="channel_aware_fanout",
            proactive_severity_mode="critical_only",
            critical_consecutive_failures=1,
        )
        channels = engine._build_channels("telegram")
        self.assertEqual(channels, ["email"])


if __name__ == "__main__":
    unittest.main()
