import sqlite3
import threading
import time

from durable_task_queue import DurableTaskQueue


def test_queue_persists_job_and_worker_finishes(tmp_path):
    path = tmp_path / "queue.db"
    received = []
    queue = DurableTaskQueue(path, lambda job: (received.append(job["task_id"]) or {"queue_state": "succeeded"}), poll_seconds=0.05)
    queue.enqueue(task_id="t1", goal="do", constraints=[], success_criteria=["done"],
                  namespace="test", idempotency_key="job:t1")
    queue.start()
    deadline = time.time() + 3
    while time.time() < deadline and queue.get("t1")["state"] != "succeeded":
        time.sleep(0.03)
    queue.stop()
    assert queue.get("t1")["state"] == "succeeded"
    assert received == ["t1"]
    reopened = DurableTaskQueue(path, lambda job: {"queue_state": "succeeded"})
    assert reopened.get("t1")["state"] == "succeeded"


def test_queue_recovers_expired_lease(tmp_path):
    path = tmp_path / "queue.db"
    queue = DurableTaskQueue(path, lambda job: {"queue_state": "succeeded"}, lease_seconds=30)
    queue.enqueue(task_id="t2", goal="do", constraints=[], success_criteria=["done"],
                  namespace="test", idempotency_key="job:t2")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE durable_task_jobs SET state='running', lease_until=?, attempt_count=1 WHERE task_id='t2'", (time.time() - 1,))
    job = queue._claim()
    assert job is not None
    assert job["task_id"] == "t2"
    assert queue.get("t2")["state"] == "running"


def test_queue_rejects_conflicting_idempotency_key(tmp_path):
    queue = DurableTaskQueue(tmp_path / "queue.db", lambda job: {"queue_state": "succeeded"})
    queue.enqueue(task_id="t3", goal="do", constraints=[], success_criteria=["done"],
                  namespace="test", idempotency_key="job:t3")
    try:
        queue.enqueue(task_id="t3", goal="other", constraints=[], success_criteria=["done"],
                      namespace="test", idempotency_key="different")
    except ValueError:
        pass
    else:
        raise AssertionError("conflicting idempotency key should fail")
