"""Demonstrate durable intent tracking for an external side effect."""
from pathlib import Path
from tempfile import TemporaryDirectory

from elyntro.operation_journal import OperationJournal

with TemporaryDirectory() as tmp:
    journal = OperationJournal(str(Path(tmp) / "operations.sqlite"))
    operation = journal.prepare("ticket:create:42", "create_ticket", {"title": "Investigate outage"})
    print("Prepared:", operation["operation_id"], operation["state"])
    journal.transition(operation["operation_id"], "started")
    # In a real integration, call the external service here and persist its result.
    result = journal.transition(operation["operation_id"], "succeeded", result={"ticket_id": "OPS-42"})
    print("Final state:", result["state"])
    print("Result:", result["result"])
