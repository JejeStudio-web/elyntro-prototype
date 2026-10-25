import tempfile
import unittest
from pathlib import Path
from elyntro.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.tmp.name) / "memory.db")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, **kwargs):
        base = {"namespace": "user-1", "content": "Prefers concise technical explanations", "tags": ["style", "technical"]}
        base.update(kwargs)
        return self.store.add(**base)

    def test_add_and_read(self):
        memory = self.add()
        self.assertEqual(self.store.get("user-1", memory.memory_id).content, "Prefers concise technical explanations")

    def test_namespace_isolation(self):
        memory = self.add()
        with self.assertRaises(KeyError):
            self.store.get("user-2", memory.memory_id)

    def test_retrieve_relevant(self):
        self.add()
        self.add(content="Enjoys playing tennis on weekends", tags=["sport"])
        results = self.store.retrieve("user-1", "technical explanations", min_score=0.1)
        self.assertEqual(len(results), 1)
        self.assertGreater(results[0]["relevance_score"], 0)

    def test_empty_query(self):
        self.add()
        self.assertEqual(self.store.retrieve("user-1", "  "), [])

    def test_update(self):
        memory = self.add()
        updated = self.store.update("user-1", memory.memory_id, content="Prefers detailed technical explanations")
        self.assertIn("detailed", updated.content)

    def test_delete(self):
        memory = self.add()
        self.store.delete("user-1", memory.memory_id)
        self.assertEqual(self.store.list("user-1"), [])

    def test_clear_namespace_only(self):
        self.add()
        self.store.add(namespace="user-2", content="Separate memory")
        self.assertEqual(self.store.clear("user-1"), 1)
        self.assertEqual(len(self.store.list("user-2")), 1)

    def test_export(self):
        self.add()
        exported = self.store.export("user-1")
        self.assertEqual(exported["schema_version"], 1)
        self.assertEqual(len(exported["memories"]), 1)

    def test_sensitive_memory_rejected(self):
        with self.assertRaises(ValueError):
            self.add(sensitive=True)

    def test_inferred_memory_requires_consent(self):
        with self.assertRaises(PermissionError):
            self.add(memory_type="inferred")
        memory = self.add(memory_type="inferred", inferred_consent=True)
        self.assertEqual(memory.memory_type, "inferred")

    def test_bad_confidence_rejected(self):
        with self.assertRaises(ValueError):
            self.add(confidence=2.0)

    def test_events_audited(self):
        memory = self.add()
        self.store.update("user-1", memory.memory_id, confidence=0.8)
        actions = [e["action"] for e in self.store.events("user-1")]
        self.assertIn("created", actions)
        self.assertIn("updated", actions)


if __name__ == "__main__":
    unittest.main()
