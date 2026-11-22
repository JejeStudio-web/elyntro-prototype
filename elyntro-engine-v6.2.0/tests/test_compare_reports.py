import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "model_lab" / "compare_reports.py"
SPEC = importlib.util.spec_from_file_location("compare_reports", SCRIPT)
compare_reports = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare_reports)


def report(model, checks, latencies=None):
    latencies = latencies or [1.0] * len(checks)
    return {
        "schema_version": 1,
        "model": model,
        "results": [
            {"case_id": "case-" + str(i), "automated_check": check, "latency_seconds": latencies[i]}
            for i, check in enumerate(checks)
        ],
    }


def test_non_regressing_candidate_passes_gate():
    result = compare_reports.compare_reports(report("base", [True, False]), report("candidate", [True, True]))
    assert result["approved_for_further_review"] is True
    assert result["candidate_passes"] == 2


def test_previously_passing_case_cannot_regress():
    result = compare_reports.compare_reports(report("base", [True, True]), report("candidate", [True, False]))
    assert result["approved_for_further_review"] is False
    assert "case-1" in result["reasons"][0]


def test_candidate_errors_are_rejected():
    candidate = report("candidate", [True])
    candidate["results"][0]["error"] = "connection failed"
    result = compare_reports.compare_reports(report("base", [True]), candidate)
    assert result["approved_for_further_review"] is False


def test_case_mismatch_is_rejected():
    result = compare_reports.compare_reports(report("base", [True]), report("candidate", [True, False]))
    assert result["approved_for_further_review"] is False


def test_latency_regression_is_rejected():
    result = compare_reports.compare_reports(report("base", [True], [1.0]), report("candidate", [True], [2.0]))
    assert result["approved_for_further_review"] is False
