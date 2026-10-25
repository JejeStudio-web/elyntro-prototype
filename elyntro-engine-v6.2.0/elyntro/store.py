"""SQLite persistence for tasks, events, and idempotency keys."""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .contracts import Task, utc_now


class SQLiteStore:
    def __init__(self, path: str | Path = "elyntro.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a transaction-scoped connection and always close it afterwards."""
        connection = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id, event_id);
                CREATE TABLE IF NOT EXISTS idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE
                );
            """)

    def create_task(self, task: Task, idempotency_key: str | None = None) -> Task:
        with self._connect() as db:
            if idempotency_key:
                existing = db.execute("SELECT task_id FROM idempotency WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
                if existing:
                    return self.get_task(existing["task_id"])
            now = utc_now()
            task.created_at = now
            task.updated_at = now
            db.execute("INSERT INTO tasks(task_id, data_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                       (task.task_id, json.dumps(task.to_dict()), now, now))
            db.execute("INSERT INTO events(task_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                       (task.task_id, "task_created", json.dumps({"status": task.status.value}), now))
            if idempotency_key:
                db.execute("INSERT INTO idempotency(idempotency_key, task_id) VALUES (?, ?)", (idempotency_key, task.task_id))
        return task

    def get_task(self, task_id: str) -> Task:
        with self._connect() as db:
            row = db.execute("SELECT data_json FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"Task not found: {task_id}")
        return Task.from_dict(json.loads(row["data_json"]))

    def save_task(self, task: Task) -> None:
        task.updated_at = utc_now()
        with self._connect() as db:
            result = db.execute("UPDATE tasks SET data_json = ?, updated_at = ? WHERE task_id = ?",
                                (json.dumps(task.to_dict()), task.updated_at, task.task_id))
            if result.rowcount != 1:
                raise KeyError(f"Task not found: {task.task_id}")

    def add_event(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO events(task_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                       (task_id, event_type, json.dumps(payload or {}), utc_now()))

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT event_id, event_type, payload_json, created_at FROM events WHERE task_id = ? ORDER BY event_id", (task_id,)).fetchall()
        return [{"event_id": r["event_id"], "event_type": r["event_type"], "payload": json.loads(r["payload_json"]), "created_at": r["created_at"]} for r in rows]

    def list_tasks(self, limit: int = 50) -> list[Task]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as db:
            rows = db.execute("SELECT data_json FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [Task.from_dict(json.loads(r["data_json"])) for r in rows]
