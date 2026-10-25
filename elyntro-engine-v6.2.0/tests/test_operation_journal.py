import os
import tempfile
import unittest

from elyntro.operation_journal import InvalidOperationTransition, OperationConflictError, OperationJournal


class OperationJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = OperationJournal(os.path.join(self.tmp.name, "operations.sqlite"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_prepare_is_idempotent_for_same_request(self):
        first = self.journal.prepare("invoice:123", "send_invoice", {"invoice_id": 123})
        second = self.journal.prepare("invoice:123", "send_invoice", {"invoice_id": 123})
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertEqual(second["state"], "prepared")

    def test_key_cannot_be_reused_for_different_request(self):
        self.journal.prepare("payment:123", "charge", {"cents": 500})
        with self.assertRaises(OperationConflictError):
            self.journal.prepare("payment:123", "charge", {"cents": 700})

    def test_started_operation_can_be_marked_uncertain_after_crash(self):
        item = self.journal.prepare("mail:1", "send_mail", {"to": "example@example.com"})
        self.journal.transition(item["operation_id"], "started")
        uncertain = self.journal.transition(item["operation_id"], "uncertain", error="process stopped before result")
        self.assertEqual(uncertain["state"], "uncertain")
        self.assertEqual(len(self.journal.list_uncertain()), 1)
        with self.assertRaises(InvalidOperationTransition):
            self.journal.transition(item["operation_id"], "started")

    def test_success_result_is_durable_and_terminal(self):
        item = self.journal.prepare("ticket:7", "create_ticket", {"title": "Bug"})
        self.journal.transition(item["operation_id"], "started")
        done = self.journal.transition(item["operation_id"], "succeeded", result={"ticket_id": "T-7"})
        self.assertEqual(self.journal.get_by_key("ticket:7")["result"], {"ticket_id": "T-7"})
        self.assertEqual(done["state"], "succeeded")
        with self.assertRaises(InvalidOperationTransition):
            self.journal.transition(item["operation_id"], "started")

    def test_uncertain_resolution_requires_explicit_terminal_decision(self):
        item = self.journal.prepare("shipment:2", "book_shipment", {"order": 2})
        self.journal.transition(item["operation_id"], "started")
        self.journal.transition(item["operation_id"], "uncertain", error="timeout")
        resolved = self.journal.transition(item["operation_id"], "resolved_applied", actor="operator-1", result={"shipment_id": "S2"})
        self.assertEqual(resolved["state"], "resolved_applied")
        self.assertEqual(self.journal.list_uncertain(), [])


if __name__ == "__main__":
    unittest.main()
