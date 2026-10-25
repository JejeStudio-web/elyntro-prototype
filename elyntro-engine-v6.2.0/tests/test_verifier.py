import unittest

from elyntro.verifier import EvidenceVerifier, VerificationStatus


class EvidenceVerifierTests(unittest.TestCase):
    def test_verifies_with_registered_independent_validator(self):
        verifier = EvidenceVerifier()
        verifier.register("report exists", lambda criterion, evidence, ctx: "report.md" in ctx["artifact_names"])
        report = verifier.verify(
            ["report exists"],
            [{"criterion": "report exists", "detail": "Created report", "artifact": "report.md"}],
            artifacts=[{"name": "report.md", "content": "Report body"}],
            task_id="task-1",
        )
        self.assertEqual(report.status, VerificationStatus.VERIFIED)
        self.assertTrue(report.passed)
        self.assertEqual(report.task_id, "task-1")

    def test_evidence_without_validator_is_inconclusive(self):
        report = EvidenceVerifier().verify(
            ["correct answer"], [{"criterion": "correct answer", "detail": "It is correct"}]
        )
        self.assertEqual(report.status, VerificationStatus.INCONCLUSIVE)
        self.assertEqual(report.unresolved_criteria, ("correct answer",))
        self.assertFalse(report.passed)

    def test_missing_evidence_fails(self):
        report = EvidenceVerifier().verify(["tests pass"], [])
        self.assertEqual(report.status, VerificationStatus.FAILED)
        self.assertEqual(report.failed_criteria, ("tests pass",))

    def test_validator_rejection_fails(self):
        verifier = EvidenceVerifier()
        verifier.register("tests pass", lambda *_: False)
        report = verifier.verify(["tests pass"], [{"criterion": "tests pass", "detail": "Claimed passing"}])
        self.assertEqual(report.status, VerificationStatus.FAILED)

    def test_missing_artifact_reference_fails(self):
        verifier = EvidenceVerifier()
        verifier.register("file exists", lambda *_: True)
        report = verifier.verify(
            ["file exists"], [{"criterion": "file exists", "detail": "created", "artifact": "missing.txt"}]
        )
        self.assertEqual(report.status, VerificationStatus.FAILED)

    def test_validator_exception_fails_closed(self):
        def broken(*_):
            raise RuntimeError("checker crashed")
        verifier = EvidenceVerifier()
        verifier.register("check", broken)
        report = verifier.verify(["check"], [{"criterion": "check", "detail": "evidence"}])
        self.assertEqual(report.status, VerificationStatus.FAILED)
        self.assertIn("RuntimeError", report.checks[0].detail)

    def test_rejects_duplicate_criteria(self):
        with self.assertRaises(ValueError):
            EvidenceVerifier().verify(["same", "same"], [])

    def test_rejects_invalid_evidence_shape(self):
        with self.assertRaises(ValueError):
            EvidenceVerifier().verify(["criterion"], [{"criterion": "criterion", "detail": " "}])

    def test_rejects_non_boolean_validator_result(self):
        verifier = EvidenceVerifier()
        verifier.register("criterion", lambda *_: "yes")
        report = verifier.verify(["criterion"], [{"criterion": "criterion", "detail": "evidence"}])
        self.assertEqual(report.status, VerificationStatus.FAILED)


    def test_executor_uses_verifier_and_routes_inconclusive_to_approval(self):
        import tempfile
        from pathlib import Path
        from elyntro.contracts import TaskRequest, TaskStatus
        from elyntro.engine import Engine
        from elyntro.store import SQLiteStore
        from elyntro.task_executor import StepResult, TaskExecutor, TaskStep

        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(SQLiteStore(Path(directory) / "tasks.db"))
            task = engine.submit(TaskRequest(goal="Create report", success_criteria=["report is valid"]))
            executor = TaskExecutor(engine, {
                "make": lambda inputs, context: StepResult(output={"ok": True}, evidence=[
                    {"criterion": "report is valid", "detail": "handler says valid"}
                ])
            }, verifier=EvidenceVerifier())
            result = executor.execute(task.task_id, [TaskStep("s1", "Make report", "make", {})])
            self.assertEqual(result.status, TaskStatus.NEEDS_APPROVAL)

    def test_executor_succeeds_only_after_validator_passes(self):
        import tempfile
        from pathlib import Path
        from elyntro.contracts import TaskRequest, TaskStatus
        from elyntro.engine import Engine
        from elyntro.store import SQLiteStore
        from elyntro.task_executor import StepResult, TaskExecutor, TaskStep

        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(SQLiteStore(Path(directory) / "tasks.db"))
            task = engine.submit(TaskRequest(goal="Create report", success_criteria=["report is valid"]))
            verifier = EvidenceVerifier()
            verifier.register("report is valid", lambda criterion, evidence, context: context["goal"] == "Create report")
            executor = TaskExecutor(engine, {
                "make": lambda inputs, context: StepResult(output={"ok": True}, evidence=[
                    {"criterion": "report is valid", "detail": "report created"}
                ])
            }, verifier=verifier)
            result = executor.execute(task.task_id, [TaskStep("s1", "Make report", "make", {})])
            self.assertEqual(result.status, TaskStatus.SUCCEEDED)

    def test_report_serialization(self):
        report = EvidenceVerifier().verify(["criterion"], [])
        data = report.to_dict()
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["failed_criteria"], ["criterion"])


if __name__ == "__main__":
    unittest.main()
