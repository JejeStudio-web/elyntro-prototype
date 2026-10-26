"""Small, authenticated OpenAI-compatible API surface for Elyntro.

This module deliberately does not expose a public, unauthenticated proxy to a
provider credential. Configure ELYNTRO_API_KEY and a model provider before use.
Streaming and multimodal payloads are rejected explicitly until implemented.
"""
from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Blueprint, jsonify, request

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")
MAX_MESSAGES = 20
MAX_CONTENT_CHARS = 12_000
MAX_OUTPUT_TOKENS = 2_048
MAX_REQUEST_BYTES = 1_000_000


def _config():
    provider = os.getenv("ELYNTRO_MODEL_PROVIDER", "groq").strip().casefold()
    defaults = {
        "groq": ("GROQ_API_KEY", "GROQ_MODEL", "openai/gpt-oss-20b", "https://api.groq.com/openai/v1"),
        "openai": ("OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4.1-mini", "https://api.openai.com/v1"),
        "ollama": ("ELYNTRO_MODEL_API_KEY", "OLLAMA_MODEL", "llama3.2", os.getenv("ELYNTRO_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")),
        "openai-compatible": ("ELYNTRO_MODEL_API_KEY", "ELYNTRO_MODEL", "model", os.getenv("ELYNTRO_MODEL_BASE_URL", "")),
    }
    if provider not in defaults:
        return {"provider": provider, "supported": False, "configured": False, "model": None, "base_url": ""}
    key_env, model_env, default_model, default_url = defaults[provider]
    key = os.getenv(key_env, "").strip()
    model = os.getenv(model_env, default_model).strip()
    base_url = default_url.rstrip("/")
    if provider == "openai-compatible" and not base_url:
        base_url = ""
    return {
        "provider": provider, "supported": True, "configured": bool((key or provider == "ollama") and model and base_url),
        "model": model or None, "base_url": base_url, "key_env": key_env, "api_key": key,
    }


def _authorized():
    expected = os.getenv("ELYNTRO_API_KEY", "").strip()
    if not expected:
        return False, "ELYNTRO_API_KEY no está configurada; la API v1 permanece cerrada."
    auth = request.headers.get("Authorization", "")
    supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return bool(supplied and secrets.compare_digest(supplied, expected)), "Credencial de API no válida."


def _error(message, status, code, retryable=False):
    error_id = uuid.uuid4().hex
    return jsonify({
        "error": {"message": message, "type": "elyntro_api_error", "code": code,
                  "retryable": bool(retryable), "error_id": error_id}
    }), status


def _client_error():
    authorized, message = _authorized()
    if authorized:
        return None
    return _error(message, 401 if os.getenv("ELYNTRO_API_KEY", "").strip() else 503,
                  "invalid_api_key" if os.getenv("ELYNTRO_API_KEY", "").strip() else "api_not_configured")


@api_v1.get("/models")
def models():
    denied = _client_error()
    if denied:
        return denied
    config = _config()
    if not config["supported"]:
        return _error("Proveedor no compatible. Usa groq, openai, ollama u openai-compatible.", 503, "provider_unsupported")
    if not config["configured"]:
        return _error("El proveedor no está configurado. Revisa la clave, el modelo y la URL base.", 503, "provider_not_configured")
    return jsonify({"object": "list", "data": [{
        "id": config["model"], "object": "model", "owned_by": config["provider"],
        "capabilities": {"chat_completions": True, "streaming": False, "vision": False, "audio": False}
    }]})


@api_v1.post("/chat/completions")
def chat_completions():
    denied = _client_error()
    if denied:
        return denied
    if request.content_length and request.content_length > MAX_REQUEST_BYTES:
        return _error("La solicitud supera el límite de 1 MB.", 413, "request_too_large")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("El cuerpo debe ser un objeto JSON.", 400, "invalid_json")
    if payload.get("stream") is True:
        return _error("stream=true no está implementado; usa stream=false u omite el campo.", 400, "streaming_not_supported")
    if payload.get("tools") or payload.get("functions"):
        return _error("Las llamadas a herramientas no forman parte de este endpoint todavía.", 400, "tools_not_supported")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        return _error(f"messages debe contener entre 1 y {MAX_MESSAGES} mensajes.", 400, "invalid_messages")
    clean_messages = []
    total_chars = 0
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"system", "user", "assistant"}:
            return _error("Cada mensaje debe tener role system, user o assistant y contenido de texto.", 400, "invalid_message")
        value = item.get("content")
        if not isinstance(value, str):
            return _error("Solo se admite contenido de texto; imágenes y audio aún no están soportados.", 400, "multimodal_not_supported")
        total_chars += len(value)
        if total_chars > MAX_CONTENT_CHARS:
            return _error("El contenido total supera el límite de 12.000 caracteres.", 413, "content_too_large")
        clean_messages.append({"role": item["role"], "content": value})
    config = _config()
    if not config["supported"]:
        return _error("Proveedor no compatible. Usa groq, openai, ollama u openai-compatible.", 503, "provider_unsupported")
    if not config["configured"]:
        return _error("El proveedor no está configurado. Revisa la clave, el modelo y la URL base.", 503, "provider_not_configured")
    requested_model = payload.get("model")
    if requested_model is not None and requested_model != config["model"]:
        return _error("El modelo solicitado no coincide con el modelo configurado para esta instancia.", 400, "model_not_available")
    try:
        max_tokens = int(payload.get("max_tokens", 800))
    except (TypeError, ValueError):
        return _error("max_tokens debe ser un entero.", 400, "invalid_max_tokens")
    if max_tokens < 1 or max_tokens > MAX_OUTPUT_TOKENS:
        return _error(f"max_tokens debe estar entre 1 y {MAX_OUTPUT_TOKENS}.", 400, "invalid_max_tokens")
    try:
        temperature = float(payload.get("temperature", 0.2))
    except (TypeError, ValueError):
        return _error("temperature debe ser un número entre 0 y 2.", 400, "invalid_temperature")
    if not 0 <= temperature <= 2:
        return _error("temperature debe estar entre 0 y 2.", 400, "invalid_temperature")
    body = {"model": config["model"], "messages": clean_messages,
            "max_tokens": max_tokens, "temperature": temperature}
    response_format = payload.get("response_format")
    if response_format is not None:
        if not isinstance(response_format, dict) or response_format.get("type") not in {"text", "json_object"}:
            return _error("response_format admite text o json_object.", 400, "invalid_response_format")
        if response_format.get("type") == "json_object":
            body["response_format"] = {"type": "json_object"}
    request_id = "chatcmpl-" + uuid.uuid4().hex
    http_request = Request(
        config["base_url"] + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Elyntro/1.0"},
        method="POST",
    )
    if config["api_key"]:
        http_request.add_unredirected_header("Authorization", "Bearer " + config["api_key"])
    started = time.monotonic()
    try:
        with urlopen(http_request, timeout=45) as response:
            raw = response.read(2_000_000)
            data = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        status = 429 if exc.code == 429 else (503 if exc.code >= 500 else 502)
        return _error(f"El proveedor respondió HTTP {exc.code}.", status,
                      "provider_rate_limited" if exc.code == 429 else "provider_error",
                      retryable=(exc.code == 429 or exc.code >= 500))
    except (URLError, TimeoutError):
        return _error("No se pudo conectar con el proveedor o se agotó el tiempo de espera.", 502,
                      "provider_connection_failed", retryable=True)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error("El proveedor devolvió una respuesta que no es JSON válido.", 502, "provider_invalid_response")
    try:
        choice = data["choices"][0]
        answer = choice["message"]["content"]
        finish_reason = choice.get("finish_reason") or "stop"
    except (KeyError, IndexError, TypeError):
        return _error("La respuesta del proveedor tiene una estructura inesperada.", 502, "provider_invalid_response")
    if not isinstance(answer, str):
        return _error("El proveedor devolvió contenido no textual, no soportado por esta API.", 502, "provider_invalid_response")
    if not answer.strip():
        return _error("El proveedor devolvió una respuesta vacía.", 502, "provider_empty_response", retryable=True)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}

    def _safe_usage_int(value):
        # Provider usage metadata is untrusted input; keep the public schema numeric.
        if isinstance(value, bool):
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, min(parsed, 2**31 - 1))

    returned_model = data.get("model") if isinstance(data.get("model"), str) and data.get("model").strip() else config["model"]
    return jsonify({
        "id": request_id, "object": "chat.completion", "created": int(time.time()),
        "model": returned_model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                     "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": _safe_usage_int(usage.get("prompt_tokens", 0)),
                  "completion_tokens": _safe_usage_int(usage.get("completion_tokens", 0)),
                  "total_tokens": _safe_usage_int(usage.get("total_tokens", 0))},
        "elyntro": {"provider": config["provider"], "latency_ms": int((time.monotonic() - started) * 1000),
                    "streaming": False, "multimodal": False}
    })
