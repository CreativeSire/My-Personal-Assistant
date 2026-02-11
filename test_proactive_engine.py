import unittest
from unittest.mock import Mock

from proactive_engine import ProactiveEngine


class TestProactiveEngine(unittest.TestCase):
    def test_dedup_suppression(self):
        engine = ProactiveEngine()
        sent = []

        def notifier(user_id, channel, message):
            sent.append((user_id, channel, message))

        engine.set_notify_callback(notifier)
        engine.register_checks_from_registry(
            [
                {
                    "name": "dup_check",
                    "interval_seconds": 60,
                    "callback": lambda: "same alert",
                    "channel_hint": "telegram",
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
                    "callback": lambda: "alert_a",
                },
                {
                    "name": "b",
                    "interval_seconds": 1,
                    "callback": lambda: "alert_b",
                },
                {
                    "name": "c",
                    "interval_seconds": 1,
                    "callback": lambda: "alert_c",
                },
            ]
        )
        engine._run_once()
        unique_messages = {m for m in sent}
        self.assertLessEqual(len(unique_messages), 2)


if __name__ == "__main__":
    unittest.main()
