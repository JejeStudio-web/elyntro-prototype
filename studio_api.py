"""Elyntro Studio status and governed Founder Mode proposal API.

This module deliberately does not execute browser actions or mutate source code.
Those capabilities require a separately authenticated worker and explicit approval.
"""
from __future__ import annotations

import os
import re
from flask import Blueprint, jsonify, request

studio_api = Blueprint("studio_api", __name__, url_prefix="/api/studio")
MAX_GOAL_CHARS = 4000


def _configured(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


@studio_api.get("/status")
def status():
    return jsonify({
        "ok": True,
        "features": {
            "threejs_frontend": True,
            "comfyui": _configured("COMFYUI_BASE_URL"),
            "playwright_bridge": _configured("ELYNTRO_PLAYWRIGHT_BRIDGE_URL")
            and _configured("ELYNTRO_PLAYWRIGHT_BRIDGE_TOKEN"),
            "founder_mode_proposals": True,
            "self_modification_execution": False,
        },
        "notes": {
            "comfyui": "Requires an explicitly configured reachable ComfyUI instance and an API workflow.",
            "playwright": "Requires a private authenticated bridge; no browser actions are exposed by this API.",
            "self_modification": "Proposal-only. Applying changes requires reviewed diffs, tests and explicit approval.",
        },
    })


@studio_api.post("/founder/proposals")
def create_founder_proposal():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Se esperaba un objeto JSON."}), 400
    goal = payload.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        return jsonify({"error": "Escribe un objetivo de mejora."}), 400
    goal = goal.strip()
    if len(goal) > MAX_GOAL_CHARS:
        return jsonify({"error": f"El objetivo supera {MAX_GOAL_CHARS} caracteres."}), 400
    # This is a deterministic proposal scaffold, not a claim that an LLM analyzed the code.
    slug = re.sub(r"[^a-z0-9]+", "-", goal.casefold()).strip("-")[:60] or "improvement"
    proposal = {
        "id": slug,
        "goal": goal,
        "status": "draft",
        "mode": "proposal-only",
        "requires_human_approval": True,
        "execution_performed": False,
        "acceptance_gates": [
            "Inspect affected files and define a minimal diff",
            "Add unit and integration tests",
            "Run the full test suite and static checks",
            "Review security, privacy, dependencies and cost impact",
            "Obtain explicit approval before merge or deployment",
        ],
        "rollback": "Keep changes on a feature branch and revert the reviewed commit if verification fails.",
    }
    return jsonify({"ok": True, "proposal": proposal}), 201
