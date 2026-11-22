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


    def test_local_llama_cpp_adapter_uses_local_endpoint_without_api_key(self):
        import json
        from elyntro.model_gateway import LocalLlamaCppAdapter
        captured = {}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                return json.dumps({"choices":[{"message":{"content":"local answer"}}],"usage":{"prompt_tokens":4,"completion_tokens":3}}).encode()
        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode())
            captured["timeout"] = timeout
            return Response()
        adapter = LocalLlamaCppAdapter(timeout_seconds=20)
        with patch("elyntro.model_gateway.urlopen", side_effect=fake_urlopen):
            result = adapter.complete("qwen-local", [{"role":"user","content":"hi"}], 32, 7)
        self.assertEqual(result, ("local answer", 4, 3))
        self.assertEqual(captured["url"], "http://127.0.0.1:8080/v1/chat/completions")
        self.assertIsNone(captured["auth"])
        self.assertEqual(captured["body"]["model"], "qwen-local")
        self.assertFalse(captured["body"]["stream"])
        self.assertEqual(captured["timeout"], 7)

    def test_local_llama_cpp_adapter_rejects_bad_endpoint(self):
        from elyntro.model_gateway import LocalLlamaCppAdapter
        for endpoint in ("bad-url", "ftp://127.0.0.1/v1", "http://user:pass@127.0.0.1/v1"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    LocalLlamaCppAdapter(base_url=endpoint)

    def test_local_llama_cpp_adapter_hides_http_error_body(self):
        from io import BytesIO
        from urllib.error import HTTPError
        from elyntro.model_gateway import LocalLlamaCppAdapter
        error = HTTPError("http://127.0.0.1:8080/v1/chat/completions", 500, "bad", {}, BytesIO(b"private response body"))
        with patch("elyntro.model_gateway.urlopen", side_effect=error):
            with self.assertRaises(ModelCallError) as caught:
                LocalLlamaCppAdapter().complete("local", [{"role":"user","content":"hi"}], 20, 1)
        self.assertIn("HTTP 500", str(caught.exception))
        self.assertNotIn("private response body", str(caught.exception))

if __name__ == "__main__":
    unittest.main()
