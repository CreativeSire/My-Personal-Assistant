import os
import tempfile
import time
import unittest

from task_queue import TaskQueue


class TestDeliveryDedupeReplay(unittest.TestCase):
    def test_replayed_completion_is_suppressed(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "q.db")
            q = TaskQueue(db_path=db)
            sent: list[str] = []
            q.set_notify_callback(lambda user_id, channel, message: sent.append(message))

            def _handler(_payload):
                return "RESULT_FIXED_PAYLOAD"

            q.register_handler("workflow_run", _handler)
            task_id = q.enqueue(
                "workflow_run",
                {"workflow_name": "ops_daily_brief"},
                channel="telegram",
                user_id="u1",
            )

            q.start_worker(poll_interval=0.1)
            time.sleep(0.7)
            q.stop_worker()

            # Queue completion path should have sent exactly one completion notification.
            self.assertTrue(any("Task completed:" in m for m in sent))
            baseline = len(sent)

            # Replay same completion event; dedupe ledger should suppress duplicate send.
            q._notify_deduped(  # noqa: SLF001 - intentional for replay simulation
                user_id="u1",
                channel="telegram",
                dedupe_key=f"{task_id}:completed",
                message="Task completed: RESULT_FIXED_PAYLOAD",
            )
            self.assertEqual(len(sent), baseline)


if __name__ == "__main__":
    unittest.main()

