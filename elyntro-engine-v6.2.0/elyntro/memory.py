"""Scoped, auditable contextual memory for Elyntro.

This module stores only information explicitly submitted by the application.
It does not infer facts, scrape conversations, or decide consent by itself.
"""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import json
import re
import sqlite3
from dataclasses import dataclass
from contextlib import contextmanager
from typing import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


MEMORY_TYPES = {"explicit", "episodic", "inferred"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[\w'-]+", text.casefold()) if len(t) > 1}


@dataclass(frozen=True)
class Memory:
    memory_id: str
    namespace: str
    memory_type: str
    content: str
    tags: tuple[str, ...]
    source: str
    confidence: float
    created_at: str
    updated_at: str
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "namespace": self.namespace,
            "memory_type": self.memory_type,
            "content": self.content,
            "tags": list(self.tags),
            "source": self.source,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }


class MemoryStore:
    """SQLite-backed memory with namespace isolation and user-facing controls."""

    def __init__(self, path: str | Path = "elyntro.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memories_namespace
                    ON memories(namespace, updated_at);
                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

    @staticmethod
    def _row(row: sqlite3.Row) -> Memory:
        return Memory(
            memory_id=row["memory_id"], namespace=row["namespace"],
            memory_type=row["memory_type"], content=row["content"],
            tags=tuple(json.loads(row["tags_json"])), source=row["source"],
            confidence=row["confidence"], created_at=row["created_at"],
            updated_at=row["updated_at"], expires_at=row["expires_at"],
        )

    def add(
        self, *, namespace: str, content: str, memory_type: str = "explicit",
        tags: list[str] | None = None, source: str = "user",
        confidence: float = 1.0, expires_at: str | None = None,
        sensitive: bool = False, inferred_consent: bool = False,
    ) -> Memory:
        """Add a memory. Sensitive content and unconsented inferences are rejected."""
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace must be non-empty")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be non-empty")
        if len(content) > 20_000:
            raise ValueError("content exceeds 20,000 characters")
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"memory_type must be one of {sorted(MEMORY_TYPES)}")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if sensitive:
            raise ValueError("Sensitive information must not be stored in contextual memory")
        if memory_type == "inferred" and not inferred_consent:
            raise PermissionError("Explicit consent is required to store inferred memories")
        clean_tags = sorted({t.strip() for t in (tags or []) if isinstance(t, str) and t.strip()})
        if any(len(t) > 100 for t in clean_tags):
            raise ValueError("tags must be 100 characters or fewer")
        now, memory_id = _now(), str(uuid4())
        with self._connect() as db:
            db.execute("""INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (memory_id, namespace.strip(), memory_type, content.strip(),
                 json.dumps(clean_tags), source[:200], confidence, now, now, expires_at))
            db.execute("INSERT INTO memory_events(namespace,memory_id,action,created_at) VALUES(?,?,?,?)",
                (namespace.strip(), memory_id, "created", now))
        return self.get(namespace.strip(), memory_id)

    def get(self, namespace: str, memory_id: str) -> Memory:
        with self._connect() as db:
            row = db.execute("SELECT * FROM memories WHERE namespace=? AND memory_id=?",
                             (namespace, memory_id)).fetchone()
        if row is None:
            raise KeyError("Memory not found in this namespace")
        return self._row(row)

    def list(self, namespace: str, *, limit: int = 200) -> list[Memory]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        now = _now()
        with self._connect() as db:
            rows = db.execute("""SELECT * FROM memories WHERE namespace=?
                AND (expires_at IS NULL OR expires_at > ?) ORDER BY updated_at DESC LIMIT ?""",
                (namespace, now, limit)).fetchall()
        return [self._row(r) for r in rows]

    def retrieve(self, namespace: str, query: str, *, limit: int = 8,
                 min_score: float = 0.12) -> list[dict[str, Any]]:
        """Retrieve relevant memories using transparent lexical overlap scoring.

        This intentionally simple baseline is replaceable by embeddings later.
        It is not semantic search and must not be treated as proof of truth.
        """
        if not query.strip():
            return []
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        q = _tokens(query)
        if not q:
            return []
        ranked = []
        for memory in self.list(namespace, limit=1000):
            content_tokens = _tokens(memory.content)
            tag_tokens = _tokens(" ".join(memory.tags))
            if not content_tokens and not tag_tokens:
                continue
            overlap = len(q & content_tokens) / len(q)
            tag_overlap = len(q & tag_tokens) / len(q)
            score = min(1.0, overlap * 0.8 + tag_overlap * 0.2) * memory.confidence
            if score >= min_score:
                item = memory.to_dict()
                item["relevance_score"] = round(score, 4)
                ranked.append(item)
        ranked.sort(key=lambda x: (x["relevance_score"], x["updated_at"]), reverse=True)
        return ranked[:limit]

    def update(self, namespace: str, memory_id: str, *, content: str | None = None,
               tags: list[str] | None = None, confidence: float | None = None) -> Memory:
        current = self.get(namespace, memory_id)
        new_content = current.content if content is None else content.strip()
        new_tags = list(current.tags) if tags is None else tags
        new_confidence = current.confidence if confidence is None else confidence
        if not new_content or len(new_content) > 20_000:
            raise ValueError("content must be non-empty and at most 20,000 characters")
        if not 0 <= new_confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        clean_tags = sorted({t.strip() for t in new_tags if isinstance(t, str) and t.strip()})
        now = _now()
        with self._connect() as db:
            db.execute("UPDATE memories SET content=?, tags_json=?, confidence=?, updated_at=? WHERE namespace=? AND memory_id=?",
                       (new_content, json.dumps(clean_tags), new_confidence, now, namespace, memory_id))
            db.execute("INSERT INTO memory_events(namespace,memory_id,action,created_at) VALUES(?,?,?,?)",
                       (namespace, memory_id, "updated", now))
        return self.get(namespace, memory_id)

    def delete(self, namespace: str, memory_id: str) -> None:
        with self._connect() as db:
            cur = db.execute("DELETE FROM memories WHERE namespace=? AND memory_id=?", (namespace, memory_id))
            if cur.rowcount == 0:
                raise KeyError("Memory not found in this namespace")
            db.execute("INSERT INTO memory_events(namespace,memory_id,action,created_at) VALUES(?,?,?,?)",
                       (namespace, memory_id, "deleted", _now()))

    def clear(self, namespace: str) -> int:
        with self._connect() as db:
            rows = db.execute("SELECT memory_id FROM memories WHERE namespace=?", (namespace,)).fetchall()
            now = _now()
            db.executemany("INSERT INTO memory_events(namespace,memory_id,action,created_at) VALUES(?,?,?,?)",
                           [(namespace, r["memory_id"], "deleted", now) for r in rows])
            db.execute("DELETE FROM memories WHERE namespace=?", (namespace,))
        return len(rows)

    def export(self, namespace: str) -> dict[str, Any]:
        memories = [m.to_dict() for m in self.list(namespace, limit=1000)]
        return {"schema_version": 1, "namespace": namespace, "exported_at": _now(), "memories": memories}

    def events(self, namespace: str, *, limit: int = 200) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as db:
            rows = db.execute("SELECT memory_id,action,created_at FROM memory_events WHERE namespace=? ORDER BY event_id DESC LIMIT ?",
                              (namespace, limit)).fetchall()
        return [dict(r) for r in rows]
