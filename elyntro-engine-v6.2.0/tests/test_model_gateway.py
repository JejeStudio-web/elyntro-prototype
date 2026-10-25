import unittest
import tempfile
import os
from unittest.mock import patch

from elyntro.model_registry import ModelProfile, ModelRegistry
from elyntro.model_evaluation import EvaluationResult, EvaluationStore
from elyntro.model_router import ModelRouter
from elyntro.model_gateway import ModelCallError, ModelCallRequest, ModelGateway, OpenAICompatibleAdapter


class FakeAdapter:
    def __init__(self, answer="hello", input_tokens=10, output_tokens=5):
        self.answer, self.input_tokens, self.output_tokens = answer, input_tokens, output_tokens
        self.calls = 0

    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        self.calls += 1
        return self.answer, self.input_tokens, self.output_tokens


class ModelGatewayTests(unittest.TestCase):
    def setUp(self):
        self.registry = ModelRegistry()
        self.registry.register(ModelProfile("test-model", "fake", frozenset({"text"}), 1.0, 2.0, 10000, 1000))
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = EvaluationStore(os.path.join(self.tempdir.name, "eval.db"))
        self.router = ModelRouter(self.registry, self.store, minimum_samples=2)
        for _ in range(2):
            self.router.record(EvaluationResult("test-model", "chat", .9, 50, 10, 5, True, benchmark_id="test"))
        self.adapter = FakeAdapter()
        self.gateway = ModelGateway(self.router, {"fake": self.adapter})
        self.request = ModelCallRequest("chat", ({"role": "user", "content": "hi"},), frozenset({"text"}), 10, 100, .01, min_quality=.8, max_latency_ms=1000)

    def test_routes_and_calls_registered_adapter(self):
        result = self.gateway.complete(self.request)
        self.assertEqual(result.text, "hello")
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(result.model_id, "test-model")

    def test_missing_adapter_fails_closed(self):
        gateway = ModelGateway(self.router, {})
        with self.assertRaises(ModelCallError):
            gateway.complete(self.request)

    def test_budget_guard_blocks_before_call(self):
        request = ModelCallRequest("chat", ({"role": "user", "content": "hi"},), frozenset({"text"}), 10, 1000, .0001, min_quality=.8, max_latency_ms=1000)
        with self.assertRaises(ModelCallError):
            self.gateway.complete(request)
        self.assertEqual(self.adapter.calls, 0)

    def test_invalid_message_rejected(self):
        with self.assertRaises(ValueError):
            ModelCallRequest("chat", ({"role": "tool", "content": "bad"},), frozenset({"text"}), 10, 100, .01)

    def test_records_only_after_explicit_verification(self):
        response = self.gateway.complete(self.request)
        before = len(self.store.list(model_id="test-model", task_type="chat"))
        self.gateway.record_verified_result(response, task_type="chat", quality=.95, success=True, benchmark_id="human-reviewed-v1")
        after = len(self.store.list(model_id="test-model", task_type="chat"))
        self.assertEqual(after, before + 1)

    def test_openai_adapter_missing_key_does_not_make_request(self):
        adapter = OpenAICompatibleAdapter(api_key_env="ELYNTRO_TEST_MISSING_KEY")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ModelCallError):
                adapter.complete("model", [{"role": "user", "content": "hi"}], 20, 1)


if __name__ == "__main__":
    unittest.main()
