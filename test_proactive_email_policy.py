import unittest
from types import SimpleNamespace

from proactive_engine import ProactiveEngine


class TestProactiveEmailPolicy(unittest.TestCase):
    def _engine(self) -> ProactiveEngine:
        engine = ProactiveEngine()
        engine._cfg = SimpleNamespace(
            proactive_severity_mode="critical_only",
            proactive_email_enabled=True,
            proactive_telegram_enabled=False,
            whatsapp_enabled=False,
            notify_fanout_mode="channel_aware_fanout",
            critical_consecutive_failures=2,
        )
        return engine

    def test_non_critical_suppressed(self):
        engine = self._engine()
        sent = []
        engine.add_channel_notifier("email", lambda user_id, message: sent.append(message))
        engine.register_checks_from_registry(
            [
                {
                    "name": "warn_check",
                    "interval_seconds": 1,
                    "callback": lambda: '{"alert":"x","severity":"warning"}',
                    "channel_hint": "email",
                    "user_id": "u1",
                }
            ]
        )
        engine._run_once()
        self.assertEqual(sent, [])

    def test_critical_requires_streak_then_sends_email_only(self):
        engine = self._engine()
        sent = []
        telegram_sent = []
        engine.add_channel_notifier("email", lambda user_id, message: sent.append(message))
        engine.add_channel_notifier("telegram", lambda user_id, message: telegram_sent.append(message))
        engine.register_checks_from_registry(
            [
                {
                    "name": "critical_check",
                    "interval_seconds": 1,
                    "callback": lambda: '{"alert":"x","severity":"critical"}',
                    "channel_hint": "telegram",
                    "user_id": "u1",
                }
            ]
        )
        engine._run_once()
        self.assertEqual(len(sent), 0)
        engine._last_run["critical_check"] = 0
        engine._run_once()
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(telegram_sent), 0)


if __name__ == "__main__":
    unittest.main()
