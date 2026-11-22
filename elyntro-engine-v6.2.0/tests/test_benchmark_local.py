"""Unit tests for the local-model baseline benchmark; no model server required."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import io
import json

SCRIPT = Path(__file__).parents[1] / "model_lab" / "benchmark_local.py"
SPEC = importlib.util.spec_from_file_location("benchmark_local", SCRIPT)
benchmark_local = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark_local)


def test_nonempty_check():
    assert benchmark_local.evaluate("nonempty", "  respuesta  ")[0] is True
    assert benchmark_local.evaluate("nonempty", "  ")[0] is False


def test_json_check_requires_expected_schema():
    assert benchmark_local.evaluate("json_object", '{"task":"Mochila","steps":["A","B"]}')[0] is True
    assert benchmark_local.evaluate("json_object", '{"task":"Mochila","steps":["A"]}')[0] is False
    assert benchmark_local.evaluate("json_object", "not-json")[0] is False


def test_bullet_check_rejects_extra_text():
    assert benchmark_local.evaluate("three_bullets", "- A\n- B\n- C")[0] is True
    assert benchmark_local.evaluate("three_bullets", "Introducción\n- A\n- B\n- C")[0] is False


def test_uncertainty_check_is_heuristic():
    assert benchmark_local.evaluate("mentions_unknown", "No tengo esa información.")[0] is True
    assert benchmark_local.evaluate("mentions_unknown", "Sacaste un 10.")[0] is False


def test_request_completion_parses_openai_compatible_response():
    response = io.BytesIO(json.dumps({"choices":[{"message":{"content":"hola"}}],"usage":{"total_tokens":3}}).encode())
    with patch.object(benchmark_local.urllib.request, "urlopen", return_value=response):
        text, seconds, usage = benchmark_local.request_completion("http://localhost:8080/v1", "local", [], 1)
    assert text == "hola"
    assert seconds >= 0
    assert usage["total_tokens"] == 3
