"""Guarded integrations for 3D, ComfyUI, browser inspection and founder proposals.

External model/browser integrations are opt-in and fail closed when unconfigured.
Self-modification creates reviewable proposals only; it never executes or applies code.
"""
from __future__ import annotations

import base64
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
            "playwright": {"available": bool(os.getenv("ELYNTRO_BROWSER_WORKER_URL", "").strip() and os.getenv("ELYNTRO_BROWSER_WORKER_TOKEN", "").strip()), "enabled": os.getenv("ELYNTRO_PLAYWRIGHT_ENABLED", "").lower() == "true", "mode": "private_worker"},
            "founder_mode": {"available": bool(os.getenv("ELYNTRO_FOUNDER_TOKEN", "").strip()), "proposal_only": True},
            "self_modification": {
                "mode": "reviewable_proposals_and_optional_draft_pr",
                "automatic_apply": False,
                "github_pr_creation_enabled": os.getenv("ELYNTRO_SELFDEV_ENABLED", "").lower() == "true"
                    and bool(os.getenv("ELYNTRO_GITHUB_TOKEN", "").strip())
                    and bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", os.getenv("ELYNTRO_GITHUB_REPOSITORY", "").strip())),
            },
        },
        "security": {"browser_navigation": "public-http(s)-only; allowlist required", "self_modification": "branch and draft PR only; never auto-merges or deploys"}
    })


def _playwright_available() -> bool:
    try:
        import playwright.async_api  # noqa: F401
        return True
    except Exception:
        return False


def _proxy_browser_worker(endpoint: str):
    """Fail closed unless browser actions are sent to the separately deployed private worker."""
    base = os.getenv("ELYNTRO_BROWSER_WORKER_URL", "").strip().rstrip("/")
    token = os.getenv("ELYNTRO_BROWSER_WORKER_TOKEN", "").strip()
    if not base or not token:
        return _json_error("El worker privado de Playwright no está configurado; el navegador permanece desactivado.", 503)
    if "://" not in base:
        base = "http://" + base
    parsed = urllib.parse.urlsplit(base)
    # The token is sent over plain HTTP only to Render's internal service-name:10000
    # address. Reject dotted/public hostnames, IP literals, and arbitrary ports to
    # prevent accidentally leaking the shared secret to a public endpoint.
    if (parsed.scheme != "http" or not parsed.hostname or "." in parsed.hostname
            or parsed.hostname.lower() in {"localhost", "127.0.0.1"}
            or parsed.port != 10000 or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        return _json_error("ELYNTRO_BROWSER_WORKER_URL debe ser el host:10000 interno del servicio privado de Render.", 503)
    payload = request.get_json(silent=True) or {}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_BODY:
        return _json_error("La petición al navegador supera el límite de tamaño.", 413)
    req = urllib.request.Request(
        base + endpoint, data=data,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            raw = response.read(2_500_001)
            status = response.status
        if len(raw) > 2_500_000:
            return _json_error("La respuesta del worker supera el límite de tamaño.", 502)
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            return _json_error("El worker devolvió una respuesta no válida.", 502)
        return jsonify(result), status
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(256_001)
            result = json.loads(raw.decode("utf-8"))
            if isinstance(result, dict):
                return jsonify(result), exc.code
        except (OSError, ValueError, UnicodeDecodeError):
            pass
        return _json_error("El worker privado rechazó la petición.", exc.code)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError) as exc:
        return jsonify({"ok": False, "error": "No se pudo contactar con el worker privado.", "detail": type(exc).__name__}), 502


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
    # Update only CLIP conditioning connected to a sampler's positive input.
    # Never overwrite negative conditioning with the positive prompt.
    positive_ids = set()
    for node in workflow.values():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        if node.get("class_type") in {"KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced"}:
            positive = node["inputs"].get("positive")
            if isinstance(positive, list) and positive and isinstance(positive[0], (str, int)):
                positive_ids.add(str(positive[0]))
    prompt_nodes = 0
    if positive_ids:
        for node_id in positive_ids:
            node = workflow.get(node_id)
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if isinstance(inputs, dict) and node.get("class_type") == "CLIPTextEncode" and isinstance(inputs.get("text"), str):
                inputs["text"] = prompt.strip()
                prompt_nodes += 1
    else:
        # A single encoder is unambiguous; multi-encoder workflows must expose sampler links.
        encoders = [(node_id, node) for node_id, node in workflow.items()
                    if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode"
                    and isinstance(node.get("inputs"), dict) and isinstance(node["inputs"].get("text"), str)]
        if len(encoders) == 1:
            encoders[0][1]["inputs"]["text"] = prompt.strip()
            prompt_nodes = 1
    if prompt_nodes == 0:
        return _json_error("No se pudo identificar el nodo CLIPTextEncode positivo. Conecta un sampler al nodo positive; los workflows ambiguos se rechazan.", 400)
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
    record = data.get(prompt_id, {})
    images = []
    outputs = record.get("outputs", {}) if isinstance(record, dict) else {}
    if isinstance(outputs, dict):
        for node_id, node in outputs.items():
            for image in (node.get("images", []) if isinstance(node, dict) else []):
                if not isinstance(image, dict):
                    continue
                filename, subfolder, kind = image.get("filename"), image.get("subfolder", ""), image.get("type", "output")
                if isinstance(filename, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", filename) and isinstance(subfolder, str) and len(subfolder) <= 240 and kind == "output":
                    images.append({"node_id": str(node_id)[:40], "filename": filename, "subfolder": subfolder,
                                   "type": "output", "url": "/api/creative/comfyui/view/" + prompt_id + "/" + urllib.parse.quote(filename, safe="")})
    return jsonify({"ok": True, "history": record, "images": images[:30]})


@creative_api.get("/comfyui/view/<prompt_id>/<filename>")
def comfyui_view(prompt_id: str, filename: str):
    if not _authorized():
        return _json_error("Se requiere una clave API de Elyntro configurada.", 401)
    base = _comfy_base()
    if not base:
        return _json_error("ComfyUI no está configurado.", 503)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", filename):
        return _json_error("Identificador o nombre de imagen no válido.", 400)
    try:
        history_req = urllib.request.Request(base + "/history/" + urllib.parse.quote(prompt_id, safe=""))
        with urllib.request.urlopen(history_req, timeout=8) as response:
            record = json.loads(response.read(128_000).decode("utf-8")).get(prompt_id, {})
        match = None
        for node in (record.get("outputs", {}) if isinstance(record, dict) else {}).values():
            for item in (node.get("images", []) if isinstance(node, dict) else []):
                if isinstance(item, dict) and item.get("filename") == filename and item.get("type", "output") == "output":
                    match = item
                    break
            if match:
                break
        if not match:
            return _json_error("La imagen no figura en el historial de este trabajo.", 404)
        subfolder = match.get("subfolder", "")
        if not isinstance(subfolder, str) or len(subfolder) > 240 or ".." in subfolder.split("/") or subfolder.startswith("/"):
            return _json_error("Ruta de salida no válida.", 400)
        query = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": "output"})
        image_req = urllib.request.Request(base + "/view?" + query, headers={"Accept": "image/png,image/jpeg,image/webp"})
        with urllib.request.urlopen(image_req, timeout=12) as response:
            payload = response.read(8_000_001)
            media_type = response.headers.get_content_type()
        if len(payload) > 8_000_000 or media_type not in {"image/png", "image/jpeg", "image/webp"}:
            return _json_error("La imagen supera el límite de 8 MB o tiene un formato no admitido.", 413)
        from flask import Response
        return Response(payload, mimetype=media_type, headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"})
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return jsonify({"ok": False, "error": "No se pudo recuperar la imagen de ComfyUI.", "detail": type(exc).__name__}), 502


@creative_api.post("/browser/inspect")
def browser_inspect():
    if not _authorized():
        return _json_error("Se requiere una clave API de Elyntro configurada.", 401)
    if os.getenv("ELYNTRO_PLAYWRIGHT_ENABLED", "").lower() != "true":
        return _json_error("El control Playwright está desactivado por defecto.", 503)
    return _proxy_browser_worker("/v1/inspect")


@creative_api.post("/browser/act")
def browser_act():
    if not _authorized():
        return _json_error("Se requiere una clave API de Elyntro configurada.", 401)
    if os.getenv("ELYNTRO_PLAYWRIGHT_ENABLED", "").lower() != "true":
        return _json_error("El control Playwright está desactivado por defecto.", 503)
    return _proxy_browser_worker("/v1/act")


def _github_api(method: str, path: str, payload: dict | None = None, *, allow_missing: bool = False):
    token = os.getenv("ELYNTRO_GITHUB_TOKEN", "").strip()
    repo = os.getenv("ELYNTRO_GITHUB_REPOSITORY", "").strip()
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("GitHub App/token or repository is not configured")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + path,
        data=data,
        headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Elyntro-FounderMode",
                 "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise RuntimeError("GitHub API response exceeded the size limit")
        return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        if allow_missing and exc.code == 404:
            return None
        raise RuntimeError("GitHub API returned HTTP " + str(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("GitHub API request failed: " + type(exc).__name__) from exc


def _selfdev_path_allowed(path: str) -> bool:
    if not isinstance(path, str) or not path or len(path) > 220 or path.startswith("/") or "\\" in path:
        return False
    parts = path.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        return False
    if path in {"app.py", "creative_runtime.py", "elyntro_api_v1.py", "tool_runtime.py",
                "external_resources.py", "README.md", "requirements-creative.txt"}:
        return True
    return path.startswith(("static/", "tests/", "docs/"))


@creative_api.post("/founder/selfdev/pull-request")
def founder_selfdev_pull_request():
    """Commit proposed file contents to an isolated branch and open a PR; never merge/deploy."""
    if not _authorized(founder=True):
        return _json_error("Founder Mode requiere ELYNTRO_FOUNDER_TOKEN.", 401)
    if os.getenv("ELYNTRO_SELFDEV_ENABLED", "").lower() != "true":
        return _json_error("La auto-modificación de repositorio está desactivada por defecto.", 503)
    if not os.getenv("ELYNTRO_GITHUB_TOKEN", "").strip():
        return _json_error("Configura un token GitHub de alcance mínimo en el servidor.", 503)
    payload = request.get_json(silent=True) or {}
    title, rationale, goal, files = payload.get("title"), payload.get("rationale"), payload.get("goal"), payload.get("files")
    if not all(isinstance(value, str) and value.strip() for value in (title, rationale, goal)):
        return _json_error("title, rationale y goal son obligatorios.", 400)
    if len(title) > 160 or len(rationale) > 3000 or len(goal) > 5000:
        return _json_error("El título, motivo u objetivo supera el límite de tamaño.", 400)
    if not isinstance(files, list) or not files or len(files) > 12:
        return _json_error("files debe contener entre 1 y 12 archivos.", 400)
    clean_files, seen, total_bytes = [], set(), 0
    for item in files:
        if not isinstance(item, dict) or not _selfdev_path_allowed(item.get("path")):
            return _json_error("Ruta no permitida. Solo se aceptan app/runtime, static, tests, docs y README.", 400)
        path, content = item["path"], item.get("content")
        if path in seen:
            return _json_error("No se admiten rutas duplicadas.", 400)
        if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > 200_000:
            return _json_error("Cada archivo debe contener texto de hasta 200 KB.", 400)
        seen.add(path)
        total_bytes += len(content.encode("utf-8"))
        clean_files.append({"path": path, "content": content})
    if total_bytes > 500_000:
        return _json_error("El conjunto de archivos supera 500 KB.", 413)
    base_branch = os.getenv("ELYNTRO_GITHUB_BASE_BRANCH", "main").strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", base_branch) or ".." in base_branch.split("/"):
        return _json_error("ELYNTRO_GITHUB_BASE_BRANCH no es válido.", 503)
    branch_name = "elyntro-selfdev-" + uuid.uuid4().hex[:12]
    branch_created = False
    try:
        ref_data = _github_api("GET", "/git/ref/heads/" + urllib.parse.quote(base_branch, safe="/"))
        base_sha = ref_data.get("object", {}).get("sha") if isinstance(ref_data, dict) else None
        if not isinstance(base_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", base_sha):
            raise RuntimeError("Could not resolve base branch commit")
        _github_api("POST", "/git/refs", {"ref": "refs/heads/" + branch_name, "sha": base_sha})
        branch_created = True
        committed = []
        for item in clean_files:
            path = item["path"]
            encoded_path = urllib.parse.quote(path, safe="/")
            existing = _github_api("GET", "/contents/" + encoded_path + "?ref=" + urllib.parse.quote(branch_name, safe=""), allow_missing=True)
            if existing is not None and (not isinstance(existing, dict) or existing.get("type") != "file" or not isinstance(existing.get("sha"), str)):
                raise RuntimeError("Existing path is not a regular file: " + path)
            body = {"message": "Founder Mode: " + title.strip()[:100], "content": base64.b64encode(item["content"].encode("utf-8")).decode("ascii"), "branch": branch_name}
            if existing is not None:
                body["sha"] = existing["sha"]
            _github_api("PUT", "/contents/" + encoded_path, body)
            committed.append(path)
        pr_body = (
            "## Founder Mode self-development proposal\n\n"
            + "**Goal:** " + goal.strip() + "\n\n"
            + "**Rationale:** " + rationale.strip() + "\n\n"
            + "**Files changed:**\n" + "\n".join("- " + path for path in committed)
            + "\n\n**Governance:** generated changes are isolated on a branch. CI and human code review are required. "
              "This workflow never merges the PR or deploys production."
        )
        pr = _github_api("POST", "/pulls", {"title": title.strip(), "head": branch_name, "base": base_branch, "body": pr_body, "draft": True})
        return jsonify({"ok": True, "branch": branch_name, "files": committed, "pull_request": {
            "number": pr.get("number"), "url": pr.get("html_url"), "state": pr.get("state"), "draft": pr.get("draft", True)
        }, "note": "Cambios escritos en una rama aislada. No se han fusionado ni desplegado."}), 201
    except RuntimeError as exc:
        if branch_created:
            try:
                _github_api("DELETE", "/git/refs/heads/" + branch_name)
            except RuntimeError:
                pass
        return jsonify({"ok": False, "error": "No se pudo crear el PR de auto-desarrollo.", "detail": str(exc)}), 502


def _proposal_root() -> Path:
    return Path(os.getenv("ELYNTRO_PROPOSAL_DIR", "/tmp/elyntro-proposals"))


def save_founder_proposal(title: str, rationale: str, patch: str) -> dict:
    if not all(isinstance(value, str) and value.strip() for value in (title, rationale, patch)):
        raise ValueError("title, rationale y patch son obligatorios.")
    if len(title) > 160 or len(rationale) > 3000 or len(patch) > 30000:
        raise ValueError("La propuesta supera los límites de tamaño.")
    proposal_id = "prop_" + uuid.uuid4().hex[:16]
    record = {
        "id": proposal_id, "title": title.strip(), "rationale": rationale.strip(),
        "patch": patch, "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "created_at": int(time.time()), "status": "pending_human_review",
        "policy": {"auto_apply": False, "execute_patch": False, "requires_tests": True, "requires_human_approval": True}
    }
    root = _proposal_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / (proposal_id + ".json")).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


@creative_api.get("/founder/proposals")
def founder_list_proposals():
    if not _authorized(founder=True):
        return _json_error("Founder Mode requiere ELYNTRO_FOUNDER_TOKEN.", 401)
    root = _proposal_root()
    proposals = []
    try:
        root.mkdir(parents=True, exist_ok=True)
        for path in sorted(root.glob("prop_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:100]:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(item, dict) and re.fullmatch(r"prop_[a-f0-9]{16}", str(item.get("id", ""))):
                    proposals.append({key: item.get(key) for key in ("id", "title", "rationale", "patch_sha256", "created_at", "reviewed_at", "status", "policy")})
            except (OSError, ValueError):
                continue
    except OSError:
        return _json_error("No se pudieron leer las propuestas.", 500)
    return jsonify({"ok": True, "proposals": proposals})


@creative_api.get("/founder/proposals/<proposal_id>")
def founder_get_proposal(proposal_id: str):
    if not _authorized(founder=True):
        return _json_error("Founder Mode requiere ELYNTRO_FOUNDER_TOKEN.", 401)
    if not re.fullmatch(r"prop_[a-f0-9]{16}", proposal_id):
        return _json_error("Identificador de propuesta no válido.", 400)
    path = _proposal_root() / (proposal_id + ".json")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _json_error("Propuesta no encontrada.", 404)
    except (OSError, ValueError):
        return _json_error("No se pudo leer la propuesta.", 500)
    patch = record.get("patch")
    if not isinstance(patch, str) or hashlib.sha256(patch.encode()).hexdigest() != record.get("patch_sha256"):
        return _json_error("La integridad del parche no coincide con su SHA-256 registrado.", 409)
    return jsonify({"ok": True, "proposal": record})


@creative_api.post("/founder/proposals/<proposal_id>/review")
def founder_review_proposal(proposal_id: str):
    if not _authorized(founder=True):
        return _json_error("Founder Mode requiere ELYNTRO_FOUNDER_TOKEN.", 401)
    if not re.fullmatch(r"prop_[a-f0-9]{16}", proposal_id):
        return _json_error("Identificador de propuesta no válido.", 400)
    decision = (request.get_json(silent=True) or {}).get("decision")
    if decision not in {"approve", "reject"}:
        return _json_error("decision debe ser approve o reject.", 400)
    path = _proposal_root() / (proposal_id + ".json")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _json_error("Propuesta no encontrada.", 404)
    except (OSError, ValueError):
        return _json_error("No se pudo leer la propuesta.", 500)
    if record.get("status") != "pending_human_review":
        return _json_error("Solo se pueden revisar propuestas pendientes.", 409)
    patch = record.get("patch")
    actual_hash = hashlib.sha256(patch.encode()).hexdigest() if isinstance(patch, str) else ""
    if not actual_hash or actual_hash != record.get("patch_sha256"):
        return _json_error("La integridad del parche no coincide con su SHA-256 registrado.", 409)
    reviewed_hash = (request.get_json(silent=True) or {}).get("reviewed_patch_sha256")
    if decision == "approve" and reviewed_hash != actual_hash:
        return _json_error("Para aprobar, debes enviar el SHA-256 del parche que has revisado.", 409)
    record["status"] = "approved_for_manual_implementation" if decision == "approve" else "rejected"
    record["reviewed_at"] = int(time.time())
    record["review_decision"] = decision
    record["policy"] = {"auto_apply": False, "execute_patch": False, "requires_tests": True, "requires_human_approval": True}
    try:
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return _json_error("No se pudo guardar la revisión.", 500)
    return jsonify({"ok": True, "proposal": {key: record.get(key) for key in ("id", "title", "status", "reviewed_at", "patch_sha256", "policy")},
                    "note": "La aprobación autoriza revisión/implementación manual; no aplica ni ejecuta el parche."})


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
    root = _proposal_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / (proposal_id + ".json")).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return _json_error("No se pudo guardar la propuesta en este entorno.", 500)
    return jsonify({"ok": True, "proposal": record, "note": "No se ha aplicado ningún cambio al código."}), 201
