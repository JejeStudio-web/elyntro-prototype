import tempfile
import unittest
from pathlib import Path

from elyntro.persistent_jobs import PersistentJobStore, JobStep
from elyntro.reliable_runtime import ExecutionLedger
from elyntro.runtime_health import RuntimeHealthAuditor


from elyntro.sqlite_utils import ClosingConnection
class RuntimeHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ledger = ExecutionLedger(root / "runtime.sqlite")
        self.jobs = PersistentJobStore(root / "jobs.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_stale_run_is_reported_without_mutating_ledger(self):
        run_id = self.ledger.begin("health-stale", 0.01, 1.0)
        import sqlite3
        with sqlite3.connect(self.ledger.path, factory=ClosingConnection) as db:
            db.execute("UPDATE runtime_runs SET created_at='2000-01-01T00:00:00+00:00' WHERE run_id=?", (run_id,))
        report = RuntimeHealthAuditor(self.ledger).audit(stale_after_seconds=1)
        self.assertFalse(report.healthy)
        self.assertIn("RUN_STALE", {f.code for f in report.findings})
        self.assertEqual(self.ledger.get_run("health-stale")["status"], "running")

    def test_empty_ledgers_are_healthy(self):
        report = RuntimeHealthAuditor(self.ledger, self.jobs).audit()
        self.assertTrue(report.healthy)
        self.assertEqual(report.runtime_runs_checked, 0)
        self.assertEqual(report.jobs_checked, 0)

    def test_invalid_limits_rejected(self):
        auditor = RuntimeHealthAuditor(self.ledger)
        with self.assertRaises(ValueError):
            auditor.audit(limit=0)
        with self.assertRaises(ValueError):
            auditor.audit(stale_after_seconds=-1)

    def test_interrupted_runtime_run_requires_review(self):
        run_id = self.ledger.begin("health-interrupted", 0.01, 1.0)
        self.ledger.finish(run_id, "interrupted", 0.01, {"reason": "test interruption"})
        report = RuntimeHealthAuditor(self.ledger).audit()
        self.assertIn("RUN_INTERRUPTED", {f.code for f in report.findings})


if __name__ == "__main__":
    unittest.main()
