
# Patched by The Forge
from typing import Any
from proactive_engine import ProactiveEngine, ProactiveCheck
import pytest
import asyncio
import time
import types


class TestProactiveEngine:
    def _cfg_stub(self):
        cfg = types.SimpleNamespace()
        cfg.default_owner_user_id = "test_user"
        cfg.proactive_severity_mode = "warning_and_above"
        cfg.notify_fanout_mode = "email_only"
        cfg.proactive_email_enabled = True
        cfg.proactive_telegram_enabled = True
        cfg.whatsapp_enabled = True
        cfg.critical_consecutive_failures = 1
        return cfg

    def test_register_checks_from_registry(self):
        engine = ProactiveEngine()
        engine._cfg = self._cfg_stub()  # Correct instantiation
        engine.register_checks_from_registry(
            [
                {"name": "test", "interval_seconds": 60, "callback": lambda: None},
                {"interval_seconds": 60, "callback": lambda: None},
                {"name": "test", "interval_seconds": 60},
            ]
        )
        assert len(engine._checks) == 1

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
        engine._run_once()
        engine._run_once()
        assert len(sent) == 2

    def test_alert_throttling(self):
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
                    "callback": lambda: '{"alert":"same","severity":"critical"}',
                },
                {
                    "name": "b",
                    "interval_seconds": 1,
                    "callback": lambda: '{"alert":"same","severity":"critical"}',
                },
                {
                    "name": "c",
                    "interval_seconds": 1,
                    "callback": lambda: '{"alert":"same","severity":"critical"}',
                },
            ]
        )
        engine._run_once()
        engine._run_once()
        engine._run_once()
        assert len(sent) == 1

        def test_callback_failure(self):
            engine = ProactiveEngine()
            engine._cfg = self._cfg_stub()
            engine._max_notifications_per_hour = 2
            sent = []
    
            def notifier(user_id, channel, message):
                sent.append(message)
    
            def failing_callback():
                raise Exception("test")
        
            engine.set_notify_callback(notifier)
            engine.register_checks_from_registry(
                [
                    {
                        "name": "a",
                        "interval_seconds": 1,
                        "callback": failing_callback,
                    }
                ]
            )
            engine._run_once()
            assert len(sent) == 0
