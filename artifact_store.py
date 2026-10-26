"""Versioned, namespace-scoped artifact storage for Workspace and Time Machine.

Artifacts are immutable snapshots: updates append a version, never overwrite bytes.
SQLite is suitable for the current single-instance prototype; production multi-instance
deployments should move this interface to shared object storage + transactional metadata.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ArtifactVersion:
    artifact_id: str
    version: int
    namespace: str
    project_id: str | None
    task_id: str | None
    name: str
    media_type: str
    content: bytes
    sha256: str
    created_at: str
    metadata: dict

    def summary(self) -> dict:
        return {
            "artifact_id": self.artifact_id, "version": self.version,
            "namespace": self.namespace, "project_id": self.project_id,
            "task_id": self.task_id, "name": self.name, "media_type": self.media_type,
            "size_bytes": len(self.content), "sha256": self.sha256,
            "created_at": self.created_at, "metadata": self.metadata,
        }


class ArtifactStore:
    """Append-only artifact versions with namespace checks on every read/write."""

    def __init__(self, path: str | Path = "elyntro.db", *, max_bytes: int = MAX_ARTIFACT_BYTES):
        if max_bytes < 1 or max_bytes > 25 * 1024 * 1024:
            raise ValueError("max_bytes must be between 1 byte and 25 MiB")
        self.path, self.max_bytes = str(path), max_bytes
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
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
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_namespace ON artifacts(namespace, created_at);
                CREATE TABLE IF NOT EXISTS artifact_versions (
                    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                    version INTEGER NOT NULL,
                    namespace TEXT NOT NULL,
                    project_id TEXT,
                    task_id TEXT,
                    name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    content BLOB NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (artifact_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_artifact_versions_project
                    ON artifact_versions(namespace, project_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_artifact_versions_task
                    ON artifact_versions(namespace, task_id, created_at);
            """)

    @staticmethod
    def _validate_text(value: str, field: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"Invalid {field}")
        if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
            raise ValueError(f"Invalid control character in {field}")
        return value.strip()

    def save(self, *, namespace: str, name: str, content: bytes | str,
             media_type: str = "text/plain", artifact_id: str | None = None,
             project_id: str | None = None, task_id: str | None = None,
             metadata: dict | None = None) -> ArtifactVersion:
        namespace = self._validate_text(namespace, "namespace", 200)
        name = self._validate_text(name, "name", 240)
        media_type = self._validate_text(media_type, "media_type", 120)
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, bytes) or len(content) > self.max_bytes:
            raise ValueError("Artifact content must be bytes/text within the configured size limit")
        for value, field in ((project_id, "project_id"), (task_id, "task_id")):
            if value is not None:
                self._validate_text(value, field, 200)
        metadata = {} if metadata is None else metadata
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        metadata_json = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(metadata_json.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError("metadata exceeds size limit")
        aid = artifact_id or str(uuid4())
        if not isinstance(aid, str) or not aid.strip() or len(aid) > 100:
            raise ValueError("Invalid artifact_id")
        now = _now()
        digest = hashlib.sha256(content).hexdigest()
        with self._connect() as db:
            if artifact_id:
                owner = db.execute("SELECT namespace FROM artifacts WHERE artifact_id=?", (aid,)).fetchone()
                if owner is None or owner["namespace"] != namespace:
                    raise KeyError("Artifact not found")
            else:
                db.execute("INSERT INTO artifacts VALUES (?, ?, ?)", (aid, namespace, now))
            row = db.execute("SELECT COALESCE(MAX(version), 0) AS v FROM artifact_versions WHERE artifact_id=? AND namespace=?",
                             (aid, namespace)).fetchone()
            version = int(row["v"]) + 1
            db.execute("""INSERT INTO artifact_versions
                (artifact_id, version, namespace, project_id, task_id, name, media_type,
                 content, sha256, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (aid, version, namespace, project_id, task_id, name, media_type,
                 sqlite3.Binary(content), digest, now, metadata_json))
        return ArtifactVersion(aid, version, namespace, project_id, task_id, name,
                               media_type, content, digest, now, metadata)

    def get(self, *, namespace: str, artifact_id: str, version: int | None = None) -> ArtifactVersion:
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace is required")
        with self._connect() as db:
            if version is None:
                row = db.execute("""SELECT * FROM artifact_versions WHERE namespace=? AND artifact_id=?
                    ORDER BY version DESC LIMIT 1""", (namespace, artifact_id)).fetchone()
            else:
                row = db.execute("""SELECT * FROM artifact_versions WHERE namespace=? AND artifact_id=? AND version=?""",
                                 (namespace, artifact_id, version)).fetchone()
        if row is None:
            raise KeyError("Artifact version not found")
        content = bytes(row["content"])
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise RuntimeError("Artifact integrity check failed")
        return ArtifactVersion(row["artifact_id"], row["version"], row["namespace"],
            row["project_id"], row["task_id"], row["name"], row["media_type"], content,
            row["sha256"], row["created_at"], json.loads(row["metadata_json"]))

    def list(self, *, namespace: str, project_id: str | None = None,
             task_id: str | None = None, limit: int = 50) -> list[dict]:
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace is required")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        clauses, params = ["v.namespace=?"], [namespace]
        if project_id is not None:
            clauses.append("v.project_id=?"); params.append(project_id)
        if task_id is not None:
            clauses.append("v.task_id=?"); params.append(task_id)
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(f"""SELECT v.* FROM artifact_versions v JOIN (
                SELECT artifact_id, MAX(version) AS latest FROM artifact_versions
                WHERE namespace=? GROUP BY artifact_id
            ) latest ON latest.artifact_id=v.artifact_id AND latest.latest=v.version
            WHERE {' AND '.join(clauses)} ORDER BY v.created_at DESC LIMIT ?""",
                [namespace, *params[:-1], limit]).fetchall()
        return [ArtifactVersion(row["artifact_id"], row["version"], row["namespace"],
            row["project_id"], row["task_id"], row["name"], row["media_type"],
            bytes(row["content"]), row["sha256"], row["created_at"],
            json.loads(row["metadata_json"])).summary() for row in rows]

    def restore(self, *, namespace: str, artifact_id: str, version: int) -> ArtifactVersion:
        old = self.get(namespace=namespace, artifact_id=artifact_id, version=version)
        return self.save(namespace=namespace, name=old.name, content=old.content,
            media_type=old.media_type, artifact_id=artifact_id, project_id=old.project_id,
            task_id=old.task_id, metadata={**old.metadata, "restored_from_version": version})
