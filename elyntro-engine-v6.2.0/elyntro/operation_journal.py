"""Durable intent journal for external side effects.

The journal deliberately does not execute tools. Callers record intent before an
external side effect, mark it started, then record the outcome. A started operation
without a terminal result is treated as uncertain after a crash and is never replayed
automatically.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .sqlite_utils import ClosingConnection


class OperationConflictError(ValueError):
    """An idempotency key was reused for a different logical operation."""


class InvalidOperationTransition(ValueError):
    """The requested journal state transition is not permitted."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationJournal:
    """SQLite-backed write-ahead intent log for externally visible operations.

    States: prepared -> started -> succeeded/failed/uncertain. An uncertain operation
    requires explicit reconciliation. A succeeded key can be safely looked up again;
    it is never executed by this journal.
    """

    TERMINAL = frozenset({"succeeded", "failed", "uncertain", "resolved_not_applied", "resolved_applied"})

    def __init__(self, path: str) -> None:
        self.path = path
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS operation_journal (
                operation_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                operation_name TEXT NOT NULL,
                request_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('prepared','started','succeeded','failed','uncertain','resolved_not_applied','resolved_applied')),
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_operation_journal_state ON operation_journal(state, updated_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS operation_journal_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL REFERENCES operation_journal(operation_id),
                event_type TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def prepare(self, idempotency_key: str, operation_name: str, request: Any) -> dict[str, Any]:
        """Persist intent before side effects; repeat calls return the same record."""
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 250:
            raise ValueError("idempotency_key must contain 1–250 characters")
        if not isinstance(operation_name, str) or not operation_name.strip() or len(operation_name) > 250:
            raise ValueError("operation_name must contain 1–250 characters")
        encoded = self._json(request)
        now = _now()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM operation_journal WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if row:
                if row["operation_name"] != operation_name or row["request_json"] != encoded:
                    raise OperationConflictError("idempotency key already belongs to a different operation or request")
                db.commit()
                return self._record(row, db)
            operation_id = str(uuid.uuid4())
            db.execute("INSERT INTO operation_journal(operation_id,idempotency_key,operation_name,request_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (operation_id, idempotency_key, operation_name, encoded, "prepared", now, now))
            db.execute("INSERT INTO operation_journal_events(operation_id,event_type,detail_json,created_at) VALUES(?,?,?,?)",
                       (operation_id, "prepared", self._json({"operation_name": operation_name}), now))
            db.commit()
            return self.get(operation_id)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def transition(self, operation_id: str, new_state: str, *, result: Any = None, error: str | None = None,
                   actor: str = "system") -> dict[str, Any]:
        allowed = {"prepared": {"started"}, "started": {"succeeded", "failed", "uncertain"},
                   "uncertain": {"resolved_not_applied", "resolved_applied"}}
        if new_state not in self.TERMINAL | {"started"}:
            raise ValueError(f"unsupported state: {new_state}")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 200:
            raise ValueError("actor must contain 1–200 characters")
        if error is not None and (not isinstance(error, str) or len(error) > 4000):
            raise ValueError("error must be a string of at most 4000 characters")
        result_json = None if result is None else self._json(result)
        now = _now()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM operation_journal WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None:
                raise KeyError(operation_id)
            old = row["state"]
            if new_state not in allowed.get(old, set()):
                raise InvalidOperationTransition(f"transition {old!r} -> {new_state!r} is not allowed")
            db.execute("UPDATE operation_journal SET state=?,result_json=?,error=?,updated_at=?,version=version+1 WHERE operation_id=?",
                       (new_state, result_json, error, now, operation_id))
            db.execute("INSERT INTO operation_journal_events(operation_id,event_type,detail_json,created_at) VALUES(?,?,?,?)",
                       (operation_id, new_state, self._json({"from": old, "to": new_state, "actor": actor.strip(), "error": error}), now))
            db.commit()
            return self.get(operation_id)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, operation_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM operation_journal WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None:
                raise KeyError(operation_id)
            return self._record(row, db)

    def get_by_key(self, idempotency_key: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM operation_journal WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if row is None:
                raise KeyError(idempotency_key)
            return self._record(row, db)

    def list_uncertain(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        with self._connect() as db:
            rows = db.execute("SELECT operation_id FROM operation_journal WHERE state='uncertain' ORDER BY updated_at LIMIT ?", (limit,)).fetchall()
            return [self._record(db.execute("SELECT * FROM operation_journal WHERE operation_id=?", (r["operation_id"],)).fetchone(), db) for r in rows]

    @staticmethod
    def _record(row: sqlite3.Row, db: sqlite3.Connection) -> dict[str, Any]:
        item = dict(row)
        item["request"] = json.loads(item.pop("request_json"))
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") is not None else None
        events = db.execute("SELECT event_id,event_type,detail_json,created_at FROM operation_journal_events WHERE operation_id=? ORDER BY event_id", (item["operation_id"],)).fetchall()
        item["events"] = [{"event_id": e["event_id"], "event_type": e["event_type"], "detail": json.loads(e["detail_json"]), "created_at": e["created_at"]} for e in events]
        return item
