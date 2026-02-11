import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from proactive_engine import ProactiveEngine


class TestProactiveEngine(unittest.TestCase):
    def _cfg_stub(self):
        return SimpleNamespace(
            proactive_severity_mode="critical_only",
            proactive_email_enabled=True,
            proactive_telegram_enabled=False,
            whatsapp_enabled=False,
            notify_fanout_mode="channel_aware_fanout",
            critical_consecutive_failures=1,
        )

    def test_dedup_suppression(self):
        engine = ProactiveEngine()
        engine._cfg = self._cfg_stub()
        sent = []

        def notifier(user_id, channel, message):
            sent.append((user_id, channel, message))

        engine.set_notify_callback(notifier)
        engine.register_checks_from_registry(
            [
                {
                    "name": "dup_check",
                    "interval_seconds": 60,
                    "callback": lambda: '{"alert":"same","severity":"critical"}',
                    "channel_hint": "email",
                    "user_id": "ceejay",
                }
            ]
        )

        engine._run_once()
        first_count = len(sent)
        engine._run_once()
        self.assertGreaterEqual(first_count, 1)
        self.assertEqual(len(sent), first_count)

    def test_interval_execution(self):
        counter = {"n": 0}

        def cb():
            counter["n"] += 1
            return None

        engine = ProactiveEngine()
        engine.register_checks_from_registry(
            [{"name": "interval_check", "interval_seconds": 3600, "callback": cb}]
        )
        engine._run_once()
        engine._run_once()
        self.assertEqual(counter["n"], 1)

    def test_rate_limit_behavior(self):
        engine = ProactiveEngine()
        engine._cfg = self._cfg_stub()
        engine._max_notifications_per_hour = 2
        sent = []

        def notifier(user_id, channel, message):
            sent.append(message)

        engine.set_notify_callback(notifier)
        engine.register_checks_from_registry(
            [
                {
                    "name": "a",
                    "interval_seconds": 1,
                    "callback": lambda: '{"alert":"a","severity":"critical"}',
                },
                {
                    "name": "b",
                    "interval_seconds": 1,
                    "callback": lambda: '{"alert":"b","severity":"critical"}',
                },
                {
                    "name": "c",
                    "interval_seconds": 1,
                    "callback": lambda: '{"alert":"c","severity":"critical"}',
                },
            ]
        )
        engine._run_once()
        unique_messages = {m for m in sent}
        self.assertLessEqual(len(unique_messages), 2)


if __name__ == "__main__":
    unittest.main()
