import os
import tempfile
import time

from task_queue import TaskQueue, TaskStatus


def test_enqueue_idempotency_reuses_existing_task():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "q.db")
        queue = TaskQueue(db_path=db_path)
        t1 = queue.enqueue(
            task_type="invoice_job",
            payload={"x": 1},
            idempotency_key="same-key",
        )
        t2 = queue.enqueue(
            task_type="invoice_job",
            payload={"x": 1},
            idempotency_key="same-key",
        )
        assert t1 == t2


def test_recover_stale_running_tasks():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "q.db")
        queue = TaskQueue(db_path=db_path)
        task_id = queue.enqueue(task_type="invoice_job", payload={"x": 1})
        conn = queue._connect()
        try:
            conn.execute(
                "UPDATE tasks SET status=?, started_at=?, lease_expires_at=? WHERE id=?",
                (TaskStatus.RUNNING, time.time() - 9999, time.time() - 10, task_id),
            )
            conn.commit()
        finally:
            conn.close()
        recovered = queue.recover_stale_tasks(stale_seconds=1)
        assert recovered >= 1
        task = queue.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.PENDING

