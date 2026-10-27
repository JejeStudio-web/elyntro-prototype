"""Deterministic task-plan review and delivery checks for Elyntro.

These checks validate execution metadata and artifact structure. They do not claim
that a factual answer is true merely because a model or a source says so.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any


def review_execution_plan(plan: Any, goal: str = "") -> dict:
    """Validate the shape and safety-relevant basics of a proposed execution plan."""
    if not isinstance(plan, list) or not plan:
        return {
            "status": "needs_attention",
            "approved": False,
            "step_count": 0,
            "issues": ["No se recibió un plan de ejecución estructurado."],
            "checks": [{"name": "non_empty_plan", "passed": False}],
        }
    issues: list[str] = []
    checks: list[dict] = []
    ids: list[str] = []
    for index, step in enumerate(plan):
        if not isinstance(step, dict):
            issues.append(f"El paso {index + 1} no es un objeto.")
            continue
        step_id = step.get("step_id") or step.get("id")
        name = step.get("name")
        handler = step.get("handler_key") or step.get("handler")
        if not isinstance(name, str) or not name.strip():
            issues.append(f"El paso {index + 1} no tiene nombre.")
        if not isinstance(handler, str) or not handler.strip():
            issues.append(f"El paso {index + 1} no identifica un ejecutor.")
        if step_id is not None:
            if str(step_id) in ids:
                issues.append(f"Identificador de paso duplicado: {step_id}.")
            ids.append(str(step_id))
    checks.append({"name": "non_empty_plan", "passed": True})
    checks.append({"name": "step_structure", "passed": not issues})
    checks.append({"name": "bounded_plan", "passed": len(plan) <= 20})
    if len(plan) > 20:
        issues.append("El plan supera el límite recomendado de 20 pasos; divídelo en fases.")
    return {
        "status": "approved" if not issues else "needs_attention",
        "approved": not issues,
        "step_count": len(plan),
        "issues": issues,
        "checks": checks,
    }


def _artifact_checks(artifact: Any) -> dict:
    if not isinstance(artifact, dict):
        return {"name": "unknown artifact", "status": "failed", "checks": [
            {"name": "artifact_object", "passed": False}
        ]}
    name = str(artifact.get("name") or "artefacto")
    content = artifact.get("content")
    media = str(artifact.get("media_type") or "").lower()
    checks: list[dict] = []
    text_content = content if isinstance(content, str) else (
        json.dumps(content, ensure_ascii=False) if content is not None else ""
    )
    checks.append({"name": "non_empty", "passed": bool(text_content.strip())})
    lower_name = name.lower()
    if lower_name.endswith(".json") or "json" in media:
        try:
            json.loads(text_content)
            checks.append({"name": "json_syntax", "passed": True})
        except (ValueError, TypeError):
            checks.append({"name": "json_syntax", "passed": False})
    elif lower_name.endswith(".py") or "python" in media:
        try:
            ast.parse(text_content)
            checks.append({"name": "python_syntax", "passed": True})
        except (SyntaxError, ValueError, TypeError):
            checks.append({"name": "python_syntax", "passed": False})
    elif lower_name.endswith((".html", ".htm")) or "html" in media:
        looks_like_html = bool(re.search(r"<html\b", text_content, re.I) and
                               re.search(r"</html\s*>", text_content, re.I))
        checks.append({"name": "html_document_shape", "passed": looks_like_html})
    passed = all(item["passed"] for item in checks)
    return {
        "name": name,
        "media_type": str(artifact.get("media_type") or "unknown"),
        "status": "checks_passed" if passed else "checks_failed",
        "checks": checks,
        "scope": "Comprobaciones estructurales; no demuestran corrección funcional ni verdad factual.",
    }


def build_delivery_report(*, task_status: str, plan: Any, events: Any,
                          artifacts: Any, verification: Any) -> dict:
    """Return an honest, machine-readable summary of execution and validation."""
    plan_report = review_execution_plan(plan)
    safe_events = [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []
    failed_events = [
        event for event in safe_events
        if str(event.get("event_type") or event.get("status") or "").casefold()
        in {"step_failed", "failed", "rejected", "error"}
    ]
    artifact_reports = [_artifact_checks(item) for item in artifacts] if isinstance(artifacts, list) else []
    failed_artifacts = [item for item in artifact_reports if item["status"] == "checks_failed"]
    execution_complete = str(task_status).casefold() in {"succeeded", "completed"}
    all_artifact_checks_pass = not failed_artifacts
    verification_data = verification if isinstance(verification, dict) else {}
    evidence_status = verification_data.get("status", "not_provided")
    if execution_complete and all_artifact_checks_pass and not failed_events:
        status = "completed_with_checks"
    elif execution_complete:
        status = "completed_with_warnings"
    else:
        status = "incomplete"
    return {
        "status": status,
        "execution": {
            "status": str(task_status or "unknown"),
            "complete": execution_complete,
            "event_count": len(safe_events),
            "failed_event_count": len(failed_events),
        },
        "plan_review": plan_report,
        "artifacts": artifact_reports,
        "evidence_review": {
            "status": str(evidence_status),
            "reason": str(verification_data.get("reason") or
                          "No se proporcionó una explicación de verificación.")[:500],
            "note": "El estado de evidencia no equivale por sí solo a demostrar la verdad factual.",
        },
        "warnings": (
            (["Hay pasos con errores o rechazados."] if failed_events else []) +
            (["Uno o más artefactos no superaron las comprobaciones estructurales."] if failed_artifacts else []) +
            (["La ejecución no está marcada como completada."] if not execution_complete else [])
        ),
    }
