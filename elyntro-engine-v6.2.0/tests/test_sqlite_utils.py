import sqlite3
import tempfile
import unittest
from pathlib import Path

from elyntro.sqlite_utils import ClosingConnection


class ClosingConnectionTests(unittest.TestCase):
    def test_context_exit_closes_connection_after_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'connection.db')
            with sqlite3.connect(path, factory=ClosingConnection) as db:
                db.execute('CREATE TABLE sample (value INTEGER)')
                db.execute('INSERT INTO sample VALUES (7)')
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute('SELECT 1')
            with sqlite3.connect(path, factory=ClosingConnection) as check:
                self.assertEqual(check.execute('SELECT value FROM sample').fetchone()[0], 7)

    def test_context_exit_rolls_back_and_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'rollback.db')
            db = sqlite3.connect(path, factory=ClosingConnection)
            db.execute('CREATE TABLE sample (value INTEGER)')
            db.commit()
            with self.assertRaisesRegex(RuntimeError, 'expected'):
                with db:
                    db.execute('INSERT INTO sample VALUES (9)')
                    raise RuntimeError('expected')
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute('SELECT 1')
            with sqlite3.connect(path, factory=ClosingConnection) as check:
                self.assertEqual(check.execute('SELECT COUNT(*) FROM sample').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
