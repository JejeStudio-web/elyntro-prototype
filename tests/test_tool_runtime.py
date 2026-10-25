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

def test_safe_calculator_never_accepts_code_execution():
    assert safe_calculate("2**5")["result"] == 32
    assert safe_calculate("__import__('os').system('whoami')")["ok"] is False
