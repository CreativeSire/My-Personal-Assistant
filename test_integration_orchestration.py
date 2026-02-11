import os
import tempfile
import time
import unittest

from proactive_engine import ProactiveEngine
from task_queue import TaskQueue
from workflow_engine import WorkflowEngine


class TestIntegrationOrchestration(unittest.TestCase):
    def test_proactive_dedup_and_notify(self):
        engine = ProactiveEngine()
        sent = []
        engine.set_notify_callback(lambda user_id, channel, msg: sent.append((user_id, channel, msg)))
        engine.register_checks_from_registry(
            [{"name": "ping", "interval_seconds": 60, "callback": lambda: "hello", "user_id": "u1", "channel_hint": "telegram"}]
        )
        engine._run_once()
        first_count = len(sent)
        engine._run_once()
        self.assertGreaterEqual(first_count, 1)
        self.assertEqual(len(sent), first_count)

    def test_queue_workflow_notification_path(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "q.db")
            q = TaskQueue(db_path=db)
            msgs = []
            q.set_notify_callback(lambda user_id, channel, m: msgs.append(m))
            q.register_handler("workflow_run", lambda payload: f"workflow={payload.get('workflow_name')}")
            task_id = q.enqueue("workflow_run", {"workflow_name": "ops_daily_brief"}, channel="telegram", user_id="u1")
            q.start_worker(poll_interval=0.2)
            time.sleep(0.8)
            q.stop_worker()
            task = q.get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status.value, "completed")
            self.assertTrue(any("Task completed:" in m for m in msgs))


if __name__ == "__main__":
    unittest.main()
