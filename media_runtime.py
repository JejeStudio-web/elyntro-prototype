"""Provider-backed media generation endpoints for Elyntro.

Capabilities are advertised only when their server-side provider configuration exists.
Provider credentials are never returned to the browser. Video uses a configured
OpenAI-compatible/REST adapter and may require provider-specific compatibility.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, jsonify, request

media_api = Blueprint("media_api", __name__, url_prefix="/api/media")
MAX_BODY = 64 * 1024
MAX_RESPONSE = 8 * 1024 * 1024


def _provider(kind: str) -> dict:
    prefix = {"image": "ELYNTRO_IMAGE", "audio": "ELYNTRO_TTS", "video": "ELYNTRO_VIDEO"}[kind]
    default_key = os.getenv("OPENAI_API_KEY", "").strip()
    base = os.getenv(prefix + "_API_BASE_URL", "").strip().rstrip("/")
    key = os.getenv(prefix + "_API_KEY", default_key).strip()
    model_defaults = {"image": "gpt-image-1", "audio": "gpt-4o-mini-tts", "video": ""}
    model = os.getenv(prefix + "_MODEL", model_defaults[kind]).strip()
    if not base and default_key and kind in {"image", "audio"}:
        base = "https://api.openai.com/v1"
    parsed = urllib.parse.urlsplit(base) if base else None
    valid_base = bool(parsed and parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
                      and not parsed.query and not parsed.fragment)
    configured = bool(valid_base and key and model)
    if kind == "video":
        configured = bool(valid_base and key and model and os.getenv("ELYNTRO_VIDEO_API_BASE_URL", "").strip())
    return {"kind": kind, "base_url": base, "api_key": key, "model": model,
            "configured": configured, "valid_base": valid_base}


def _same_origin_or_api_key() -> bool:
    expected = os.getenv("ELYNTRO_API_KEY", "").strip()
    auth = request.headers.get("Authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    supplied = bearer or request.headers.get("X-Elyntro-API-Key", "")
    if expected and supplied and secrets.compare_digest(expected, supplied):
        return True
    origin = request.headers.get("Origin", "").rstrip("/")
    host = request.host_url.rstrip("/")
    return bool((origin and origin == host) or request.headers.get("Sec-Fetch-Site", "").casefold() == "same-origin")


def _json_error(message: str, status: int, code: str = "media_error"):
    return jsonify({"ok": False, "error": message, "error_code": code}), status


def _request_json(config: dict, path: str, payload: dict, timeout: int = 90):
    raw = json.dumps(payload).encode("utf-8")
    if len(raw) > MAX_BODY:
        return None, 413, "La solicitud supera el límite de tamaño."
    req = urllib.request.Request(config["base_url"] + path, data=raw,
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": "Bearer " + config["api_key"]}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read(MAX_RESPONSE + 1).decode("utf-8"))
        if not isinstance(data, dict):
            return None, 502, "El proveedor devolvió una estructura inesperada."
        return data, 200, None
    except urllib.error.HTTPError as exc:
        status = 429 if exc.code == 429 else (503 if exc.code >= 500 else 502)
        return None, status, f"El proveedor multimedia respondió HTTP {exc.code}."
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError):
        return None, 502, "No se pudo conectar con el proveedor multimedia configurado."


@media_api.get("/capabilities")
def media_capabilities():
    providers = {kind: _provider(kind) for kind in ("image", "audio", "video")}
    return jsonify({"ok": True, "capabilities": {
        "image_generation": {"available": providers["image"]["configured"], "provider_configured": providers["image"]["configured"], "endpoint": "/api/media/generate-image"},
        "speech_generation": {"available": providers["audio"]["configured"], "provider_configured": providers["audio"]["configured"], "endpoint": "/api/media/generate-speech"},
        "video_generation": {"available": providers["video"]["configured"], "provider_configured": providers["video"]["configured"], "endpoint": "/api/media/generate-video"},
        "video_status": {"available": providers["video"]["configured"], "endpoint": "/api/media/videos/{id}"},
    }})


@media_api.post("/generate-image")
def generate_image():
    if not _same_origin_or_api_key():
        return _json_error("Se requiere una petición del mismo origen o una clave API válida.", 401, "unauthorized")
    config = _provider("image")
    if not config["configured"]:
        return _json_error("La generación de imágenes no está configurada. Configura ELYNTRO_IMAGE_API_BASE_URL, ELYNTRO_IMAGE_API_KEY y ELYNTRO_IMAGE_MODEL (o OPENAI_API_KEY).", 503, "image_provider_not_configured")
    payload = request.get_json(silent=True) or {}
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        return _json_error("prompt debe contener entre 1 y 4000 caracteres.", 400, "invalid_prompt")
    body = {"model": config["model"], "prompt": prompt.strip(), "n": 1, "response_format": "b64_json"}
    size = payload.get("size", "1024x1024")
    if size not in {"1024x1024", "1536x1024", "1024x1536"}:
        return _json_error("size no compatible.", 400, "invalid_size")
    body["size"] = size
    data, status, error = _request_json(config, "/images/generations", body, timeout=120)
    if error:
        return _json_error(error, status, "image_generation_failed")
    items = data.get("data")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return _json_error("El proveedor no devolvió una imagen.", 502, "provider_invalid_response")
    item = items[0]
    encoded = item.get("b64_json")
    if isinstance(encoded, str) and encoded:
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error):
            return _json_error("El proveedor devolvió una imagen codificada no válida.", 502, "provider_invalid_image")
        if len(decoded) > 6 * 1024 * 1024:
            return _json_error("La imagen generada supera el límite de salida.", 502, "image_too_large")
        return jsonify({"ok": True, "kind": "image", "provider": "configured_image_provider",
                        "model": config["model"], "media_type": "image/png",
                        "data_url": "data:image/png;base64," + encoded})
    image_url = item.get("url")
    if isinstance(image_url, str) and image_url.startswith("https://"):
        return jsonify({"ok": True, "kind": "image", "provider": "configured_image_provider",
                        "model": config["model"], "media_type": "image/remote",
                        "url": image_url, "note": "El enlace lo proporciona el proveedor y puede caducar."})
    return _json_error("El proveedor no devolvió una imagen compatible.", 502, "provider_invalid_image")


@media_api.post("/generate-speech")
def generate_speech():
    if not _same_origin_or_api_key():
        return _json_error("Se requiere una petición del mismo origen o una clave API válida.", 401, "unauthorized")
    config = _provider("audio")
    if not config["configured"]:
        return _json_error("La síntesis de voz no está configurada. Configura ELYNTRO_TTS_API_BASE_URL, ELYNTRO_TTS_API_KEY y ELYNTRO_TTS_MODEL (o OPENAI_API_KEY).", 503, "speech_provider_not_configured")
    payload = request.get_json(silent=True) or {}
    text = payload.get("text")
    voice = payload.get("voice", "alloy")
    if not isinstance(text, str) or not text.strip() or len(text) > 5000:
        return _json_error("text debe contener entre 1 y 5000 caracteres.", 400, "invalid_text")
    if not isinstance(voice, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", voice):
        return _json_error("voice no es válida.", 400, "invalid_voice")
    body = {"model": config["model"], "input": text.strip(), "voice": voice, "response_format": "mp3"}
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(config["base_url"] + "/audio/speech", data=raw,
        headers={"Content-Type": "application/json", "Accept": "audio/mpeg",
                 "Authorization": "Bearer " + config["api_key"]}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            audio = response.read(6 * 1024 * 1024 + 1)
            content_type = response.headers.get("Content-Type", "audio/mpeg").split(";", 1)[0]
        if not audio or len(audio) > 6 * 1024 * 1024:
            return _json_error("La respuesta de audio está vacía o supera el límite.", 502, "audio_too_large")
        if content_type not in {"audio/mpeg", "audio/mp3", "audio/wav", "audio/opus", "audio/aac", "audio/flac"}:
            return _json_error("El proveedor devolvió un formato de audio no admitido.", 502, "invalid_audio_type")
        return jsonify({"ok": True, "kind": "audio", "provider": "configured_speech_provider",
                        "model": config["model"], "media_type": content_type,
                        "data_base64": base64.b64encode(audio).decode("ascii")})
    except urllib.error.HTTPError as exc:
        status = 429 if exc.code == 429 else (503 if exc.code >= 500 else 502)
        return _json_error(f"El proveedor de voz respondió HTTP {exc.code}.", status, "speech_generation_failed")
    except (urllib.error.URLError, TimeoutError, OSError):
        return _json_error("No se pudo conectar con el proveedor de voz configurado.", 502, "speech_generation_failed")


@media_api.post("/generate-video")
def generate_video():
    if not _same_origin_or_api_key():
        return _json_error("Se requiere una petición del mismo origen o una clave API válida.", 401, "unauthorized")
    config = _provider("video")
    if not config["configured"]:
        return _json_error("La generación de vídeo no está configurada. Añade un adaptador compatible en ELYNTRO_VIDEO_API_BASE_URL, ELYNTRO_VIDEO_API_KEY y ELYNTRO_VIDEO_MODEL.", 503, "video_provider_not_configured")
    payload = request.get_json(silent=True) or {}
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        return _json_error("prompt debe contener entre 1 y 4000 caracteres.", 400, "invalid_prompt")
    data, status, error = _request_json(config, "/videos", {"model": config["model"], "prompt": prompt.strip()}, timeout=30)
    if error:
        return _json_error(error, status, "video_generation_failed")
    video_id = data.get("id") or data.get("task_id")
    if not isinstance(video_id, str) or not video_id or len(video_id) > 200:
        return _json_error("El proveedor no devolvió un identificador de vídeo consultable.", 502, "provider_invalid_response")
    return jsonify({"ok": True, "kind": "video_task", "provider": "configured_video_provider",
                    "model": config["model"], "video_id": video_id,
                    "status": data.get("status", "submitted"), "provider_response": {
                        key: data[key] for key in ("status", "progress", "created_at") if key in data
                    }}), 202


@media_api.get("/videos/<video_id>")
def get_video_status(video_id: str):
    if not _same_origin_or_api_key():
        return _json_error("Se requiere una petición del mismo origen o una clave API válida.", 401, "unauthorized")
    config = _provider("video")
    if not config["configured"]:
        return _json_error("El proveedor de vídeo no está configurado.", 503, "video_provider_not_configured")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", video_id):
        return _json_error("Identificador de vídeo no válido.", 400, "invalid_video_id")
    req = urllib.request.Request(config["base_url"] + "/videos/" + urllib.parse.quote(video_id, safe=""),
        headers={"Accept": "application/json", "Authorization": "Bearer " + config["api_key"]})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read(1_000_000).decode("utf-8"))
        if not isinstance(data, dict):
            return _json_error("El proveedor devolvió un estado no válido.", 502, "provider_invalid_response")
        safe = {key: data[key] for key in ("id", "status", "progress", "created_at", "completed_at", "error", "output") if key in data}
        return jsonify({"ok": True, "kind": "video_task", "provider": "configured_video_provider", "video": safe})
    except urllib.error.HTTPError as exc:
        status = 404 if exc.code == 404 else (429 if exc.code == 429 else 502)
        return _json_error(f"No se pudo consultar el vídeo (HTTP {exc.code}).", status, "video_status_failed")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError):
        return _json_error("No se pudo consultar el proveedor de vídeo.", 502, "video_status_failed")
