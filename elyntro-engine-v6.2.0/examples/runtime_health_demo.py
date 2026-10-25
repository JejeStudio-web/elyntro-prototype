"""Read-only operational audit example; it never retries or mutates jobs."""
import json
import tempfile
from pathlib import Path

from elyntro import ExecutionLedger, PersistentJobStore, RuntimeHealthAuditor

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    ledger = ExecutionLedger(root / "runtime.sqlite")
    jobs = PersistentJobStore(root / "jobs.sqlite")
    ledger.begin("demo-in-flight-run", reserve_cost=0.01, daily_budget=1.0)
    report = RuntimeHealthAuditor(ledger, jobs).audit(stale_after_seconds=300)
    print(json.dumps(report.to_dict(), indent=2))
