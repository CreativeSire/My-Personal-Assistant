import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import invoice_pipeline
from task_queue import TaskQueue


class _FakeQueueForProgress:
    def update_progress(self, task_id, progress, message=""):
        return None


class TestTelegramTaskInvoiceFlow(unittest.TestCase):
    def test_queue_completion_contains_send_file_marker(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_stub = SimpleNamespace(
                workspace_dir=os.path.join(td, "workspace"),
                base_dir=td,
                invoice_allowed_exts=("pdf",),
                invoice_job_enabled=True,
                invoice_mode="production",
                golden_input_path="",
                golden_images_only=True,
                invoice_schema_path=os.path.join(os.path.dirname(__file__), "schemas", "invoice_43.schema.json"),
                golden_expected_path=os.path.join(td, "golden43_expected.json"),
                model_name="gemini-2.0-flash-001",
            )
            os.makedirs(cfg_stub.workspace_dir, exist_ok=True)
            src = os.path.join(td, "invoice.pdf")
            with open(src, "wb") as f:
                f.write(b"%PDF-1.4\n%task")

            def fake_process(path, ctx):
                out = os.path.join(ctx.ok_dir, "MAIN_Lagos_123456_2026-02-11.pdf")
                with open(out, "wb") as wf:
                    wf.write(b"%PDF")
                return invoice_pipeline.DocumentResult("ok", "validated", out, {})

            db = os.path.join(td, "tasks.db")
            q = TaskQueue(db_path=db)
            msgs = []
            q.set_notify_callback(lambda user_id, channel, message: msgs.append(message))

            with patch.object(invoice_pipeline, "cfg", cfg_stub):
                with patch.object(invoice_pipeline, "TaskQueue", _FakeQueueForProgress):
                    with patch.object(invoice_pipeline, "process_document", side_effect=fake_process):
                        q.register_handler("invoice_job", invoice_pipeline.run_invoice_job)
                        q.enqueue("invoice_job", {"input_path": src}, channel="telegram", user_id="123")
                        q.start_worker(poll_interval=0.2)
                        time.sleep(1.0)
                        q.stop_worker()

            self.assertTrue(any("<<SEND_FILE:" in m for m in msgs))


if __name__ == "__main__":
    unittest.main()
