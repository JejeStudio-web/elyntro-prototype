"""Demonstrate stale-run recovery and audit-only verification records."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3

from elyntro.reliable_runtime import ExecutionLedger

with TemporaryDirectory() as tmp:
    ledger = ExecutionLedger(Path(tmp) / "runtime.db")
    run_id = ledger.begin("demo-interrupted-run", reserve_cost=0.02, daily_budget=1.0)

    # Simulate a process that died and left a run in `running`.
    db = sqlite3.connect(ledger.path)
    try:
        db.execute("UPDATE runtime_runs SET created_at='2000-01-01T00:00:00+00:00' WHERE run_id=?", (run_id,))
        db.commit()
    finally:
        db.close()

    recovered = ledger.recover_stale_runs(older_than_seconds=0)
    print("Recovered:", recovered)
    print("Stored run:", ledger.get_run("demo-interrupted-run")["status"])

    # Verification is only recorded after the application has actually run its
    # own validator against a completed result. An interrupted run cannot be verified.
