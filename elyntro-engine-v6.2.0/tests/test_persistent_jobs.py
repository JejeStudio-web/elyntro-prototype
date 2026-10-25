import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from elyntro.persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry


class PersistentJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PersistentJobStore(Path(self.tmp.name) / "jobs.db")

    def tearDown(self):
        self.tmp.cleanup()

    def make_job(self, *, idempotent=True, approval=False, key="job-1"):
        return self.store.create_job(
            idempotency_key=key, task_type="text_transform", payload={"text": " hello "},
            steps=[JobStep("normalize", "normalize_text", {"text": " hello "}, idempotent=idempotent)],
            require_approval=approval,
        )

    def test_create_and_claim_persisted_job(self):
        job_id = self.make_job()
        claimed = self.store.claim_next(worker_id="worker-a")
        self.assertEqual(claimed["job_id"], job_id)
        self.assertEqual(claimed["state"], "running")
        self.assertEqual(self.store.get_job(job_id)["steps"][0]["state"], "pending")

    def test_approval_gates_job(self):
        job_id = self.make_job(approval=True)
        self.assertIsNone(self.store.claim_next(worker_id="worker-a"))
        self.store.approve(job_id, approved_by="user")
        self.assertEqual(self.store.claim_next(worker_id="worker-a")["job_id"], job_id)

    def test_safe_step_requeued_after_expired_lease(self):
        job_id = self.make_job(idempotent=True)
        self.store.claim_next(worker_id="worker-a", lease_seconds=1)
        self.store.start_step(job_id, "normalize", worker_id="worker-a")
        db = self.store._connect()
        try:
            old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
            db.execute("UPDATE jobs SET lease_until=? WHERE job_id=?", (old, job_id))
        finally:
            db.close()
        recovered = self.store.recover_expired_leases()
        self.assertEqual(recovered[0]["state"], "queued")
        self.assertEqual(recovered[0]["safe_steps_requeued"], ["normalize"])
        self.assertEqual(self.store.get_job(job_id)["steps"][0]["state"], "pending")

    def test_non_idempotent_step_requires_reconciliation(self):
        job_id = self.make_job(idempotent=False)
        self.store.claim_next(worker_id="worker-a", lease_seconds=1)
        self.store.start_step(job_id, "normalize", worker_id="worker-a")
        db = self.store._connect()
        try:
            old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
            db.execute("UPDATE jobs SET lease_until=? WHERE job_id=?", (old, job_id))
        finally:
            db.close()
        recovered = self.store.recover_expired_leases()
        self.assertEqual(recovered[0]["state"], "interrupted")
        self.assertEqual(self.store.get_job(job_id)["steps"][0]["state"], "uncertain")
        with self.assertRaises(ValueError):
            self.store.resume_interrupted(job_id)
        self.store.resume_interrupted(job_id, reconciled_steps={"normalize": {"succeeded": False, "retry_safe": True}})
        self.assertEqual(self.store.get_job(job_id)["state"], "queued")

    def test_completed_steps_preserved_on_recovery(self):
        job_id = self.store.create_job(idempotency_key="two", task_type="demo", payload={}, steps=[
            JobStep("one", "a", {}, idempotent=True), JobStep("two", "b", {}, idempotent=True)])
        self.store.claim_next(worker_id="w", lease_seconds=1)
        self.store.start_step(job_id, "one", worker_id="w")
        self.store.finish_step(job_id, "one", output={"value": 1})
        self.store.start_step(job_id, "two", worker_id="w")
        db = self.store._connect()
        try:
            old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
            db.execute("UPDATE jobs SET lease_until=? WHERE job_id=?", (old, job_id))
        finally:
            db.close()
        self.store.recover_expired_leases()
        steps = self.store.get_job(job_id)["steps"]
        self.assertEqual([s["state"] for s in steps], ["succeeded", "pending"])
        self.assertEqual(steps[0]["output"], {"value": 1})

    def test_completion_requires_all_steps_and_passed_verification(self):
        job_id = self.make_job()
        self.store.claim_next(worker_id="w")
        with self.assertRaises(ValueError):
            self.store.complete_job(job_id, result={"ok": True}, verification={"passed": True, "verifier_id": "v"})
        self.store.start_step(job_id, "normalize", worker_id="w")
        self.store.finish_step(job_id, "normalize", output={"text": "HELLO"})
        with self.assertRaises(ValueError):
            self.store.complete_job(job_id, result={"ok": True}, verification={"passed": False, "verifier_id": "v"})
        self.store.complete_job(job_id, result={"text": "HELLO"}, verification={"passed": True, "verifier_id": "text-check", "evidence": ["normalized"]})
        self.assertEqual(self.store.get_job(job_id)["state"], "succeeded")

    def test_duplicate_idempotency_key_rejected(self):
        self.make_job(key="same")
        with self.assertRaises(Exception):
            self.make_job(key="same")

    def test_task_verifier_registry_fails_closed(self):
        registry = TaskVerifierRegistry()
        self.assertFalse(registry.verify("unknown", {}, {})["passed"])
        registry.register("sum", "sum-check-v1", lambda result, context: {"passed": result.get("total") == context.get("expected"), "evidence": ["compared total"]})
        verdict = registry.verify("sum", {"total": 4}, {"expected": 4})
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["verifier_id"], "sum-check-v1")

    def test_missing_job_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.store.get_job("no-such-job")

    def test_step_ids_must_be_unique(self):
        with self.assertRaises(ValueError):
            self.store.create_job(idempotency_key="dup", task_type="demo", payload={}, steps=[
                JobStep("x", "a", {}), JobStep("x", "b", {})])

    def test_list_jobs_state_filter(self):
        self.make_job()
        self.make_job(approval=True, key="job-2")
        self.assertEqual(len(self.store.list_jobs(state="queued")), 1)
        self.assertEqual(len(self.store.list_jobs(state="waiting_approval")), 1)


if __name__ == "__main__":
    unittest.main()
