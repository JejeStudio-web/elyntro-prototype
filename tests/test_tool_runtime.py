from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tool_runtime import ToolRuntime, safe_calculate

class FakeModel:
    def __init__(self, outputs): self.outputs=list(outputs)
    def __call__(self, messages, **kwargs):
        return {"text": self.outputs.pop(0), "model":"fake-model", "requested_model":"fake-model", "response_id":"test-id"}

def test_runtime_executes_allowlisted_tool_then_finishes():
    model=FakeModel([
      '{"action":"tool","tool":"calculate","arguments":{"expression":"(12+8)*3"}}',
      '{"action":"final","answer":"El resultado es 60."}'
    ])
    runtime=ToolRuntime(model, {"calculate":lambda args:safe_calculate(args["expression"])}, max_steps=3)
    result=runtime.run("Calcula (12+8)*3")
    assert result["ok"] is True
    assert result["events"][0]["status"] == "succeeded"
    assert result["events"][0]["evidence"]["result"] == 60
    assert result["verification"]["status"] == "tool_evidence_recorded_not_truth_verified"

def test_runtime_rejects_unknown_tool_and_does_not_claim_tool_success():
    model=FakeModel([
      '{"action":"tool","tool":"delete_everything","arguments":{}}',
      '{"action":"final","answer":"No se ejecutó ninguna herramienta no permitida."}'
    ])
    result=ToolRuntime(model, {"calculate":lambda args:safe_calculate("1+1")}).run("haz algo")
    assert result["events"][0]["status"] == "rejected"
    assert result["verification"]["successful_tool_calls"] == 0

def test_runtime_step_limit_is_incomplete_not_success():
    model=FakeModel(['{"action":"tool","tool":"calculate","arguments":{"expression":"1+1"}}']*3)
    result=ToolRuntime(model, {"calculate":lambda args:safe_calculate("1+1")}, max_steps=2).run("calcula")
    assert result["ok"] is False
    assert result["status"] == "step_limit"
    assert "límite" in result["answer"]


def test_runtime_exposes_safe_provider_error_instead_of_generic_decision_failure():
    model = lambda *args, **kwargs: {
        "text": None,
        "error": "secret-bearing provider message must not be echoed",
        "error_code": "provider_not_configured",
    }
    result = ToolRuntime(model, {}).run("Explica algo")
    assert result["status"] == "provider_error"
    assert result["error"]["code"] == "provider_not_configured"
    assert result["error"]["stage"] == "planning"
    assert "no está configurado" in result["answer"]
    assert "secret-bearing" not in result["answer"]


def test_runtime_repairs_invalid_json_once_and_stops_if_repair_is_invalid():
    model = FakeModel(["no-json", "{also-no-json"])
    result = ToolRuntime(model, {}, max_steps=6).run("haz una tarea")
    assert result["status"] == "invalid_decision"
    assert result["error"]["code"] == "invalid_model_decision"


def test_runtime_reports_model_transport_exception_without_exposing_exception_text():
    def unavailable(*args, **kwargs):
        raise RuntimeError("exception may contain sensitive request data")

    result = ToolRuntime(unavailable, {}).run("haz una tarea")
    assert result["status"] == "provider_error"
    assert result["error"]["code"] == "model_request_failed"
    assert "sensitive request data" not in result["answer"]


def test_safe_calculator_never_accepts_code_execution():
    assert safe_calculate("2**5")["result"] == 32
    assert safe_calculate("__import__('os').system('whoami')")["ok"] is False
