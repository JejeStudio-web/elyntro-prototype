"""SQLite helpers with explicit transaction and connection lifetime semantics."""
from __future__ import annotations

import sqlite3


class ClosingConnection(sqlite3.Connection):
    """Connection context manager that commits/rolls back and always closes.

    sqlite3.Connection.__exit__ only handles the transaction; it does not close
    the connection. Elyntro uses short-lived connections, so close on context exit
    to avoid descriptor leaks and ResourceWarning on supported Python versions.
    Explicit close() remains safe and idempotent.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()
