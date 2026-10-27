"""Small, authenticated OpenAI-compatible API surface for Elyntro.

This module deliberately does not expose a public, unauthenticated proxy to a
provider credential. Configure ELYNTRO_API_KEY and a model provider before use.
Streaming and bounded image inputs are supported where the configured provider/model allows them; audio/video are not yet supported.
"""
from __future__ import annotations

import json
import html
import re
import urllib.parse
from html.parser import HTMLParser
import os
import re
import secrets
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Blueprint, Response, jsonify, request, stream_with_context
from tool_runtime import safe_calculate
from search_providers import TavilySearchProvider, fetch_public_http

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
    if provider == "ollama":
        model = os.getenv("ELYNTRO_MODEL", os.getenv("OLLAMA_MODEL", default_model)).strip()
    else:
        model = os.getenv(model_env, default_model).strip()
    base_url = default_url.rstrip("/")
    if provider == "openai-compatible" and not base_url:
        base_url = ""
    return {
        "provider": provider, "supported": True, "configured": bool((key or provider == "ollama") and model and base_url),
        "model": model or None, "base_url": base_url, "key_env": key_env, "api_key": key,
    }


def _model_pool():
    """Return configured model aliases from server-side environment only.

    ELYNTRO_MODEL_POOL is a JSON array of entries with id, provider, model,
    base_url, and api_key_env. Secrets are referenced by environment-variable
    name and are never accepted from client requests.
    """
    raw = os.getenv("ELYNTRO_MODEL_POOL", "").strip()
    if not raw:
        config = _config()
        return [{**config, "id": config.get("model"), "primary": True}] if config.get("supported") and config.get("configured") else []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(entries, list):
        return []
    pool, seen = [], set()
    for item in entries[:8]:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        provider = item.get("provider", "openai-compatible")
        model = item.get("model")
        base_url = item.get("base_url")
        key_env = item.get("api_key_env", "")
        if not all(isinstance(v, str) and v.strip() for v in (model_id, provider, model, base_url)):
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", model_id) or model_id in seen:
            continue
        if not re.fullmatch(r"https://[^\s]+|http://(?:127\.0\.0\.1|localhost)(?::[0-9]+)?(?:/[^\s]*)?", base_url):
            continue
        if key_env and not re.fullmatch(r"[A-Z][A-Z0-9_]{1,99}", key_env):
            continue
        api_key = os.getenv(key_env, "").strip() if key_env else ""
        if key_env and not api_key:
            continue
        seen.add(model_id)
        pool.append({"id": model_id, "provider": provider, "model": model,
                     "base_url": base_url.rstrip("/"), "api_key": api_key,
                     "key_env": key_env, "supported": True, "configured": True,
                     "primary": bool(item.get("primary", False)),
                     "capabilities": item.get("capabilities", {}) if isinstance(item.get("capabilities", {}), dict) else {}})
    if pool and not any(item["primary"] for item in pool):
        pool[0]["primary"] = True
    return pool


def _select_model(requested_model):
    pool = _model_pool()
    if not pool:
        return None
    if requested_model in (None, "elyntro-auto", "elyntro-default"):
        return next((item for item in pool if item.get("primary")), pool[0])
    return next((item for item in pool if requested_model in {item["id"], item["model"]}), None)


def _authorized():
    expected = os.getenv("ELYNTRO_API_KEY", "").strip()
    if not expected:
        return False, "ELYNTRO_API_KEY no está configurada; la API v1 permanece cerrada."
    auth = request.headers.get("Authorization", "")
    supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return bool(supplied and secrets.compare_digest(supplied, expected)), "Credencial de API no válida."


def _stream_completion(config, body):
    """Yield provider SSE events without buffering the complete answer."""
    body = dict(body)
    body["stream"] = True
    http_request = Request(
        config["base_url"] + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                 "User-Agent": "Elyntro/1.1"},
        method="POST",
    )
    if config["api_key"]:
        http_request.add_unredirected_header("Authorization", "Bearer " + config["api_key"])

    @stream_with_context
    def generate():
        try:
            with urlopen(http_request, timeout=45) as upstream:
                for raw_line in upstream:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        yield "data: [DONE]\n\n"
                        return
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        yield "data: " + json.dumps(parsed, ensure_ascii=False, separators=(",", ":")) + "\n\n"
            yield "data: [DONE]\n\n"
        except HTTPError as exc:
            error = {"error": {"message": "El proveedor rechazó la solicitud durante el streaming",
                               "type": "provider_error", "status": exc.code}}
            yield "data: " + json.dumps(error, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
        except (URLError, TimeoutError, OSError):
            error = {"error": {"message": "Se perdió la conexión con el proveedor durante el streaming",
                               "type": "provider_connection_error"}}
            yield "data: " + json.dumps(error, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


class _PlainTextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


_PUBLIC_TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "calculate", "description": "Evalúa aritmética segura sin ejecutar código.", "parameters": {"type": "object", "properties": {"expression": {"type": "string", "maxLength": 180}}, "required": ["expression"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "web_search", "description": "Busca páginas web reales mediante el proveedor de búsqueda configurado.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "maxLength": 400}}, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "web_open", "description": "Lee el texto de una URL pública HTTP(S) con protección SSRF.", "parameters": {"type": "object", "properties": {"url": {"type": "string", "maxLength": 1000}}, "required": ["url"], "additionalProperties": False}}},
]


def _execute_public_tool(name, arguments):
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "Los argumentos deben ser un objeto JSON."}
    if name == "calculate":
        result = safe_calculate(arguments.get("expression"))
        return result if isinstance(result, dict) else {"ok": True, "result": result}
    if name == "web_search":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 400:
            return {"ok": False, "error": "query debe contener entre 1 y 400 caracteres."}
        results, error = TavilySearchProvider().search(query.strip(), limit=5)
        if error and not results:
            return {"ok": False, "error": "La búsqueda web no está configurada o no pudo completarse.", "code": error}
        return {"ok": True, "results": results[:5], "warning": "Los resultados de búsqueda son pistas, no verificación independiente."}
    if name == "web_open":
        url = arguments.get("url")
        if not isinstance(url, str) or len(url) > 1000:
            return {"ok": False, "error": "url no válida."}
        response, error = fetch_public_http(url, timeout=8, max_bytes=350_000, max_redirects=3)
        if error or not response or response.get("status", 500) >= 400:
            return {"ok": False, "error": "No se pudo recuperar esa página pública.", "code": error or "http_error"}
        content_type = str(response.get("headers", {}).get("Content-Type", "")).lower()
        if "text/html" not in content_type and "text/plain" not in content_type:
            return {"ok": False, "error": "La URL no devuelve HTML o texto plano."}
        body = response["body"].decode("utf-8", errors="replace")
        parser = _PlainTextHTML()
        if "text/html" in content_type:
            parser.feed(body)
            text_content = re.sub(r"\\s+", " ", " ".join(parser.parts)).strip()
        else:
            text_content = body
        return {"ok": True, "url": response.get("url"), "content": text_content[:12000]}
    return {"ok": False, "error": "Herramienta no permitida."}


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
    pool = _model_pool()
    if not pool:
        return _error("No hay modelos configurados. Configura ELYNTRO_MODEL_POOL o el proveedor heredado.", 503, "provider_not_configured")
    return jsonify({"object": "list", "data": [{
        "id": item["id"], "object": "model", "owned_by": item["provider"],
        "capabilities": {"chat_completions": True, "streaming": True,
                         "vision": item.get("capabilities", {}).get("vision", "provider-dependent"),
                         "audio": False, "video": False},
        "elyntro": {"provider": item["provider"], "upstream_model": item["model"], "primary": item["primary"]}
    } for item in pool]})


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
    stream_requested = payload.get("stream", False)
    if not isinstance(stream_requested, bool):
        return _error("stream debe ser booleano.", 400, "invalid_stream")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        return _error(f"messages debe contener entre 1 y {MAX_MESSAGES} mensajes.", 400, "invalid_messages")
    clean_messages = []
    total_chars = 0
    has_images = False
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"system", "user", "assistant"}:
            return _error("Cada mensaje debe tener role system, user o assistant.", 400, "invalid_message")
        role, value = item["role"], item.get("content")
        if isinstance(value, str):
            total_chars += len(value)
        elif isinstance(value, list) and role == "user":
            if not value or len(value) > 12:
                return _error("El contenido multimodal debe tener entre 1 y 12 partes.", 400, "invalid_message")
            for part in value:
                if not isinstance(part, dict) or part.get("type") not in {"text", "image_url"}:
                    return _error("Solo se admiten partes de texto e imagen.", 400, "multimodal_not_supported")
                if part["type"] == "text":
                    if set(part) != {"type", "text"} or not isinstance(part.get("text"), str):
                        return _error("Parte de texto no válida.", 400, "invalid_message")
                    total_chars += len(part["text"])
                else:
                    image = part.get("image_url")
                    if set(part) != {"type", "image_url"} or not isinstance(image, dict) or set(image) - {"url", "detail"}:
                        return _error("Parte de imagen no válida.", 400, "invalid_message")
                    image_url = image.get("url")
                    if not isinstance(image_url, str) or not re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}", image_url):
                        return _error("Las imágenes deben ser data URL base64 PNG, JPEG o WebP; no se admiten URLs remotas.", 400, "invalid_image")
                    try:
                        if len(__import__("base64").b64decode(image_url.split(",", 1)[1], validate=True)) > 650_000:
                            return _error("Cada imagen debe ocupar como máximo 650 KB.", 413, "image_too_large")
                    except (ValueError, __import__("binascii").Error):
                        return _error("La imagen no contiene base64 válido.", 400, "invalid_image")
                    if image.get("detail", "auto") not in {"auto", "low", "high"}:
                        return _error("Nivel de detalle de imagen no válido.", 400, "invalid_image")
                    has_images = True
        else:
            return _error("content debe ser texto o una lista de partes de texto/imagen en un mensaje user.", 400, "invalid_message")
        if total_chars > MAX_CONTENT_CHARS:
            return _error("El texto total supera el límite de 12.000 caracteres.", 413, "content_too_large")
        clean_messages.append({"role": role, "content": value})
    requested_model = payload.get("model")
    config = _select_model(requested_model)
    if config is None:
        return _error("No hay un modelo configurado disponible para esa selección. Consulta GET /api/v1/models.", 400 if requested_model else 503,
                      "model_not_available" if requested_model else "provider_not_configured")
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
        return _error("temperature debe ser un número entre 0 y 2.", 400, "invalid_temperature")
    response_format = payload.get("response_format")
    if response_format is not None:
        if not isinstance(response_format, dict) or response_format.get("type") not in {"text", "json_object"}:
            return _error("response_format admite text o json_object.", 400, "invalid_response_format")
    tool_schemas = []
    tool_names = set()
    supplied_tools = payload.get("tools", [])
    if supplied_tools:
        if stream_requested:
            return _error("El streaming con ejecución de herramientas aún no está habilitado; usa stream=false.", 400, "stream_tools_not_supported")
        if not isinstance(supplied_tools, list) or len(supplied_tools) > len(_PUBLIC_TOOL_SCHEMAS):
            return _error("tools debe ser una lista de hasta tres herramientas permitidas.", 400, "invalid_tools")
        schema_by_name = {item["function"]["name"]: item for item in _PUBLIC_TOOL_SCHEMAS}
        for tool in supplied_tools:
            if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
                return _error("Cada herramienta debe usar type=function.", 400, "invalid_tool")
            name = tool["function"].get("name")
            if name not in schema_by_name or name in tool_names:
                return _error("Herramienta no permitida o duplicada. Permitidas: calculate, web_search, web_open.", 400, "tool_not_allowed")
            tool_names.add(name)
            tool_schemas.append(schema_by_name[name])
    tool_choice = payload.get("tool_choice", "auto")
    if not (isinstance(tool_choice, str) and tool_choice in {"auto", "none", "required"}) and not (
        isinstance(tool_choice, dict) and tool_choice.get("type") == "function"
        and isinstance(tool_choice.get("function"), dict)
        and tool_choice["function"].get("name") in tool_names
    ):
        return _error("tool_choice debe ser auto, none, required o una función permitida.", 400, "invalid_tool_choice")
    if tool_choice == "required" and not tool_schemas:
        return _error("tool_choice=required exige que se envíe al menos una herramienta.", 400, "invalid_tool_choice")
    if isinstance(tool_choice, dict) and tool_choice.get("function", {}).get("name") not in tool_names:
        return _error("tool_choice hace referencia a una herramienta no habilitada.", 400, "invalid_tool_choice")
    body = {"model": config["model"], "messages": clean_messages,
            "max_tokens": max_tokens, "temperature": temperature}
    if response_format and response_format.get("type") == "json_object":
        body["response_format"] = {"type": "json_object"}
    if stream_requested:
        return _stream_completion(config, body)
    if tool_schemas:
        body["tools"] = tool_schemas
        body["tool_choice"] = tool_choice
    started = time.monotonic()
    tool_events = []
    max_rounds = 4
    for round_index in range(max_rounds + 1):
        http_request = Request(config["base_url"] + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Elyntro/1.2"}, method="POST")
        if config["api_key"]:
            http_request.add_unredirected_header("Authorization", "Bearer " + config["api_key"])
        try:
            with urlopen(http_request, timeout=60) as upstream:
                data = json.loads(upstream.read(2_000_000).decode("utf-8"))
        except HTTPError as exc:
            status = 429 if exc.code == 429 else (503 if exc.code >= 500 else 502)
            return _error(f"El proveedor respondió HTTP {exc.code}.", status,
                           "provider_rate_limited" if exc.code == 429 else "provider_error",
                           retryable=(exc.code == 429 or exc.code >= 500))
        except (URLError, TimeoutError, OSError):
            return _error("No se pudo conectar con el proveedor o se agotó el tiempo de espera.", 502,
                          "provider_connection_failed", retryable=True)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error("El proveedor devolvió una respuesta que no es JSON válido.", 502, "provider_invalid_response")
        try:
            choice = data["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason") or "stop"
        except (KeyError, IndexError, TypeError):
            return _error("La respuesta del proveedor tiene una estructura inesperada.", 502, "provider_invalid_response")
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        if not calls:
            answer = message.get("content") if isinstance(message, dict) else None
            if not isinstance(answer, str) or not answer.strip():
                return _error("El proveedor devolvió una respuesta vacía o no textual.", 502, "provider_empty_response", retryable=True)
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            def _safe_usage_int(value):
                if isinstance(value, bool): return 0
                try: parsed = int(value)
                except (TypeError, ValueError, OverflowError): return 0
                return max(0, min(parsed, 2**31 - 1))
            returned_model = data.get("model") if isinstance(data.get("model"), str) and data.get("model").strip() else config["model"]
            return jsonify({
                "id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion", "created": int(time.time()),
                "model": config["id"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": _safe_usage_int(usage.get("prompt_tokens", 0)),
                          "completion_tokens": _safe_usage_int(usage.get("completion_tokens", 0)),
                          "total_tokens": _safe_usage_int(usage.get("total_tokens", 0))},
                "elyntro": {"provider": config["provider"], "upstream_model": returned_model,
                            "latency_ms": int((time.monotonic() - started) * 1000),
                            "streaming": False, "multimodal": has_images,
                            "tool_events": tool_events, "tool_rounds": round_index}
            })
        if not tool_schemas:
            return _error("El modelo intentó llamar herramientas, pero no se habilitó ninguna.", 502, "unexpected_tool_call")
        if round_index >= max_rounds:
            return _error("Se alcanzó el límite de rondas de herramientas sin respuesta final.", 502, "tool_round_limit")
        if len(calls) > 3:
            return _error("El modelo solicitó demasiadas herramientas en una sola ronda.", 502, "too_many_tool_calls")
        body["messages"].append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
        for call in calls:
            try:
                call_id = str(call["id"])
                function = call["function"]
                name = function["name"]
                if name not in tool_names:
                    raise ValueError("tool_not_allowed")
                args = json.loads(function.get("arguments", "{}"))
                result = _execute_public_tool(name, args)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                call_id = str(call.get("id", "invalid-call"))[:100] if isinstance(call, dict) else "invalid-call"
                name = "invalid"
                result = {"ok": False, "error": "La llamada de herramienta del modelo no cumplía el contrato permitido."}
            safe_result = json.dumps(result, ensure_ascii=False)[:12000]
            tool_events.append({"tool": name, "status": "completed" if result.get("ok") else "failed",
                                "summary": str(result.get("error") or "Herramienta ejecutada.")[:240]})
            body["messages"].append({"role": "tool", "tool_call_id": call_id, "content": safe_result})
    return _error("No se obtuvo una respuesta final del proveedor.", 502, "tool_execution_failed")
