from task_quality import build_delivery_report, review_execution_plan


def test_plan_review_flags_missing_executor_and_duplicate_ids():
    report = review_execution_plan([
        {"step_id": "1", "name": "Investigar", "handler_key": "research"},
        {"step_id": "1", "name": "Escribir"},
    ])
    assert report["approved"] is False
    assert any("duplicado" in issue for issue in report["issues"])
    assert any("ejecutor" in issue for issue in report["issues"])


def test_delivery_report_does_not_claim_truth_verification():
    report = build_delivery_report(
        task_status="succeeded",
        plan=[{"step_id": "1", "name": "Crear", "handler_key": "create"}],
        events=[{"event_type": "step_succeeded"}],
        artifacts=[{"name": "data.json", "media_type": "application/json", "content": "{\"ok\": true}"}],
        verification={"status": "verified", "reason": "Model review"},
    )
    assert report["status"] == "completed_with_checks"
    assert "no equivale" in report["evidence_review"]["note"]
    assert report["artifacts"][0]["status"] == "checks_passed"


def test_delivery_report_surfaces_bad_artifact_and_failed_step():
    report = build_delivery_report(
        task_status="succeeded",
        plan=[{"step_id": "1", "name": "Create", "handler_key": "create"}],
        events=[{"event_type": "step_failed"}],
        artifacts=[{"name": "data.json", "media_type": "application/json", "content": "not-json"}],
        verification={},
    )
    assert report["status"] == "completed_with_warnings"
    assert len(report["warnings"]) == 2
    assert report["artifacts"][0]["status"] == "checks_failed"


def test_delivery_report_marks_unfinished_task_incomplete():
    report = build_delivery_report(
        task_status="running", plan=[], events=[], artifacts=[], verification={}
    )
    assert report["status"] == "incomplete"
