"""Guarded integrations for 3D, ComfyUI, browser inspection and founder proposals.

External model/browser integrations are opt-in and fail closed when unconfigured.
Self-modification creates reviewable proposals only; it never executes or applies code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

creative_api = Blueprint("creative_api", __name__, url_prefix="/api/creative")
MAX_PROMPT = 4000
MAX_BODY = 64 * 1024


def _authorized(founder: bool = False) -> bool:
    expected = os.getenv("ELYNTRO_FOUNDER_TOKEN" if founder else "ELYNTRO_API_KEY", "").strip()
    if not expected:
        return False
    header = request.headers.get("Authorization", "")
    bearer = header[7:].strip() if header.lower().startswith("bearer ") else ""
    supplied = bearer or request.headers.get("X-Elyntro-Founder-Token" if founder else "X-Elyntro-API-Key", "")
    return bool(supplied) and secrets.compare_digest(supplied, expected)


def _json_error(message: str, status: int):
    return jsonify({"ok": False, "error": message}), status


def _comfy_base() -> str:
    base = os.getenv("COMFYUI_BASE_URL", "").strip().rstrip("/")
    if not base:
        return ""
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return ""
    # A fixed operator-configured origin only; clients cannot select a host or URL.
    return base


@creative_api.get("/status")
def creative_status():
    return jsonify({
        "ok": True,
        "features": {
            "threejs_scene": {"available": True, "mode": "browser-local", "route": "/creative-studio"},
            "comfyui": {"available": bool(_comfy_base()), "configured": bool(_comfy_base()),
                        "reason": None if _comfy_base() else "Configura COMFYUI_BASE_URL en el entorno donde se ejecuta ComfyUI o Elyntro."},
            "playwright": {"available": _playwright_available(), "enabled": os.getenv("ELYNTRO_PLAYWRIGHT_ENABLED", "").lower() == "true"},
            "founder_mode": {"available": bool(os.getenv("ELYNTRO_FOUNDER_TOKEN", "").strip()), "proposal_only": True},
            "self_modification": {"mode": "reviewable_proposals_only", "automatic_apply": False},
        },
        "security": {"browser_navigation": "public-http(s)-only; allowlist required", "self_modification": "never auto-applies code"}
    })


def _playwright_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
        return True
    except Exception:
        return False


@creative_api.post("/comfyui/generate")
def comfyui_generate():
    if not _authorized():
        return _json_error("Se requiere una clave API de Elyntro configurada.", 401)
    base = _comfy_base()
    if not base:
        return _json_error("ComfyUI no está configurado. Define COMFYUI_BASE_URL en el servidor que tenga acceso a ComfyUI.", 503)
    payload = request.get_json(silent=True) or {}
    prompt = payload.get("prompt")
    workflow = payload.get("workflow")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT:
        return _json_error("prompt debe ser texto de 1 a 4000 caracteres.", 400)
    if not isinstance(workflow, dict) or len(json.dumps(workflow)) > MAX_BODY:
        return _json_error("workflow debe ser un objeto JSON de hasta 64 KiB.", 400)
    # Only substitute a positive prompt in a caller-provided ComfyUI API workflow.
    # No arbitrary remote URL or model download is accepted.
    prompt_nodes = 0
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and node.get("class_type") == "CLIPTextEncode" and "text" in inputs:
            inputs["text"] = prompt.strip()
            prompt_nodes += 1
    if prompt_nodes == 0:
        return _json_error("El workflow no contiene ningún nodo CLIPTextEncode con entrada text.", 400)
    body = json.dumps({"prompt": workflow}).encode()
    req = urllib.request.Request(base + "/prompt", data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read(128_000).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return jsonify({"ok": False, "error": "No se pudo enviar el workflow a ComfyUI.", "detail": type(exc).__name__}), 502
    return jsonify({"ok": True, "provider": "ComfyUI", "prompt_id": data.get("prompt_id"),
                    "number": data.get("number"), "node_errors": data.get("node_errors", {})}), 202


@creative_api.get("/comfyui/history/<prompt_id>")
def comfyui_history(prompt_id: str):
    if not _authorized():
        return _json_error("Se requiere una clave API de Elyntro configurada.", 401)
    base = _comfy_base()
    if not base:
        return _json_error("ComfyUI no está configurado.", 503)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id):
        return _json_error("prompt_id no válido.", 400)
    req = urllib.request.Request(base + "/history/" + urllib.parse.quote(prompt_id, safe=""))
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read(128_000).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return jsonify({"ok": False, "error": "No se pudo consultar el historial de ComfyUI.", "detail": type(exc).__name__}), 502
    return jsonify({"ok": True, "history": data.get(prompt_id, {})})


@creative_api.post("/browser/inspect")
def browser_inspect():
    if not _authorized():
        return _json_error("Se requiere una clave API de Elyntro configurada.", 401)
    if os.getenv("ELYNTRO_PLAYWRIGHT_ENABLED", "").lower() != "true":
        return _json_error("El control Playwright está desactivado por defecto. Actívalo solo en un entorno aislado.", 503)
    try:
        from search_providers import is_public_http_url
        from playwright.sync_api import sync_playwright
    except Exception:
        return _json_error("Playwright no está instalado en este entorno.", 503)
    payload = request.get_json(silent=True) or {}
    url = payload.get("url")
    allowed = [x.strip().lower() for x in os.getenv("ELYNTRO_BROWSER_ALLOWLIST", "").split(",") if x.strip()]
    if not isinstance(url, str) or not is_public_http_url(url):
        return _json_error("Solo se admiten URL HTTP(S) públicas validadas.", 400)
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not allowed or not any(host == d or host.endswith("." + d) for d in allowed):
        return _json_error("El dominio no está en ELYNTRO_BROWSER_ALLOWLIST.", 403)
    # Read-only snapshot: no form submission, clicks, downloads, file access, or persistent profile.
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800}, accept_downloads=False)
            page.goto(url, wait_until="domcontentloaded", timeout=12000)
            snapshot = page.evaluate("""() => ({
              title: document.title.slice(0,300),
              url: location.href,
              text: (document.body?.innerText || '').slice(0,12000),
              links: Array.from(document.querySelectorAll('a[href]')).slice(0,80).map(a => ({
                text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0,160),
                href: a.href
              })).filter(a => /^https?:/.test(a.href))
            })""")
            browser.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": "No se pudo inspeccionar la página.", "detail": type(exc).__name__}), 502
    return jsonify({"ok": True, "snapshot": snapshot, "mode": "read_only", "actions_taken": []})


@creative_api.post("/founder/proposals")
def founder_proposal():
    if not _authorized(founder=True):
        return _json_error("Founder Mode requiere ELYNTRO_FOUNDER_TOKEN.", 401)
    payload = request.get_json(silent=True) or {}
    title, rationale, patch = payload.get("title"), payload.get("rationale"), payload.get("patch")
    if not all(isinstance(x, str) and x.strip() for x in (title, rationale, patch)):
        return _json_error("title, rationale y patch son obligatorios.", 400)
    if len(title) > 160 or len(rationale) > 3000 or len(patch) > 30000:
        return _json_error("La propuesta supera los límites de tamaño.", 400)
    # This is deliberately a proposal, not an execution endpoint.
    proposal_id = "prop_" + uuid.uuid4().hex[:16]
    record = {
        "id": proposal_id, "title": title.strip(), "rationale": rationale.strip(),
        "patch": patch, "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "created_at": int(time.time()), "status": "pending_human_review",
        "policy": {"auto_apply": False, "execute_patch": False, "requires_tests": True, "requires_human_approval": True}
    }
    root = Path(os.getenv("ELYNTRO_PROPOSAL_DIR", "/tmp/elyntro-proposals"))
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / (proposal_id + ".json")).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return _json_error("No se pudo guardar la propuesta en este entorno.", 500)
    return jsonify({"ok": True, "proposal": record, "note": "No se ha aplicado ningún cambio al código."}), 201
