"""Authenticated OpenAI-compatible HTTP server for a locally trained Elyntro checkpoint.
Run with ELYNTRO_MODEL_CHECKPOINT=/path/to/checkpoint.pt and ELYNTRO_INFERENCE_API_KEY=<secret> python -m elyntro_model.server.
Do not expose directly to the internet without TLS and network controls."""
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from flask import Flask, jsonify, request
import torch
from .generate import load_checkpoint
from .tokenizer import ByteTokenizer

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
MODEL_NAME = os.environ.get("ELYNTRO_NATIVE_MODEL_NAME", "elyntro-native").strip() or "elyntro-native"
CHECKPOINT = os.environ.get("ELYNTRO_MODEL_CHECKPOINT", "").strip()
API_KEY = os.environ.get("ELYNTRO_INFERENCE_API_KEY", "").strip()
MAX_OUTPUT_TOKENS = max(1, min(1024, int(os.environ.get("ELYNTRO_NATIVE_MAX_TOKENS", "256"))))
MAX_MESSAGES = max(1, min(32, int(os.environ.get("ELYNTRO_NATIVE_MAX_MESSAGES", "16"))))
RATE_PER_MINUTE = max(1, min(120, int(os.environ.get("ELYNTRO_NATIVE_RATE_PER_MINUTE", "12"))))
DEVICE_SETTING = os.environ.get("ELYNTRO_NATIVE_DEVICE", "auto")
_lock = threading.Lock()
_buckets = defaultdict(deque)
_model = None
_tokenizer = ByteTokenizer()
_device = None


def get_model():
    global _model, _device
    if _model is None:
        if not CHECKPOINT:
            raise RuntimeError("ELYNTRO_MODEL_CHECKPOINT is not configured")
        if DEVICE_SETTING == "auto":
            _device = "cuda" if torch.cuda.is_available() else ("mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu")
        else:
            _device = DEVICE_SETTING
        _model = load_checkpoint(CHECKPOINT, _device)
    return _model


@app.before_request
def protect_and_limit():
    if request.path == "/health":
        return None
    if not API_KEY:
        return jsonify({"error": {"message": "Inference server is not configured with an API key", "type": "server_configuration_error"}}), 503
    authorization = request.headers.get("Authorization", "")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not secrets.compare_digest(supplied, API_KEY):
        return jsonify({"error": {"message": "Invalid API key", "type": "authentication_error"}}), 401
    now, ip = time.monotonic(), request.remote_addr or "unknown"
    with _lock:
        bucket = _buckets[ip]
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= RATE_PER_MINUTE:
            return jsonify({"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}}), 429
        bucket.append(now)
        if len(_buckets) > 5000:
            for old in list(_buckets)[:500]:
                if not _buckets[old]:
                    _buckets.pop(old, None)


@app.get("/health")
def health():
    return jsonify({"ok": True, "model_configured": bool(CHECKPOINT), "model_loaded": _model is not None,
                    "device": _device, "model": MODEL_NAME, "max_output_tokens": MAX_OUTPUT_TOKENS})


@app.get("/v1/models")
def models():
    return jsonify({"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "elyntro"}]})


@app.post("/v1/chat/completions")
def chat_completions():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": {"message": "Expected a JSON object", "type": "invalid_request_error"}}), 400
    if body.get("model", MODEL_NAME) not in {MODEL_NAME, "elyntro-default"}:
        return jsonify({"error": {"message": "Unknown model", "type": "invalid_request_error"}}), 400
    if body.get("stream"):
        return jsonify({"error": {"message": "Streaming is not supported yet", "type": "unsupported_feature"}}), 400
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        return jsonify({"error": {"message": f"messages must contain 1..{MAX_MESSAGES} items", "type": "invalid_request_error"}}), 400
    prompt_parts = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"} or not isinstance(message.get("content"), str):
            return jsonify({"error": {"message": "Each message needs a supported role and string content", "type": "invalid_request_error"}}), 400
        content = message["content"].strip()
        if not content or len(content) > 12000:
            return jsonify({"error": {"message": "Message content must be 1..12000 characters", "type": "invalid_request_error"}}), 400
        prompt_parts.append(f"<|{message['role']}|>\n{content}\n")
    prompt = "".join(prompt_parts) + "<|assistant|>\n"
    requested = body.get("max_tokens", 128)
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        return jsonify({"error": {"message": "max_tokens must be a positive integer", "type": "invalid_request_error"}}), 400
    max_tokens = min(requested, MAX_OUTPUT_TOKENS)
    temperature = body.get("temperature", 0.7)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
        return jsonify({"error": {"message": "temperature must be between 0 and 2", "type": "invalid_request_error"}}), 400
    try:
        model = get_model()
        ids = torch.tensor([_tokenizer.encode(prompt, add_bos=True)], dtype=torch.long, device=_device)
        start = time.perf_counter()
        with torch.no_grad():
            output = model.generate(ids, max_new_tokens=max_tokens, temperature=max(0.05, float(temperature)), top_k=40, eos_token_id=_tokenizer.EOS)
        generated = _tokenizer.decode(output[0, ids.shape[1]:].tolist()).strip()
        elapsed = time.perf_counter() - start
    except Exception:
        app.logger.exception("Native model inference failed")
        return jsonify({"error": {"message": "Model inference failed; check server logs", "type": "server_error"}}), 503
    return jsonify({"id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion", "created": int(time.time()),
                    "model": MODEL_NAME, "choices": [{"index": 0, "message": {"role": "assistant", "content": generated}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": ids.shape[1], "completion_tokens": int(output.shape[1] - ids.shape[1]), "total_tokens": int(output.shape[1])},
                    "elyntro_metrics": {"latency_seconds": round(elapsed, 4), "device": _device}})


if __name__ == "__main__":
    app.run(host=os.environ.get("ELYNTRO_NATIVE_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8090")), debug=False)
