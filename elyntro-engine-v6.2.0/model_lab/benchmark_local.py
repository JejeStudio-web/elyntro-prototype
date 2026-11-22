#!/usr/bin/env python3
"""Small, dependency-free smoke benchmark for an OpenAI-compatible local model.

This is a diagnostic baseline, not a validated intelligence or safety evaluation.
It writes the model outputs to a local JSON file; do not include private data in prompts.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


CASES = [
    {
        "id": "spanish_instruction",
        "messages": [
            {"role": "system", "content": "Responde en español. Sé breve y preciso."},
            {"role": "user", "content": "Explica en dos frases qué diferencia hay entre masa y volumen."},
        ],
        "check": "nonempty",
    },
    {
        "id": "structured_json",
        "messages": [
            {"role": "system", "content": "Devuelve únicamente un objeto JSON válido, sin Markdown."},
            {"role": "user", "content": 'Representa esta tarea como JSON con las claves "task" (string) y "steps" (array de 2 strings): preparar una mochila escolar.'},
        ],
        "check": "json_object",
    },
    {
        "id": "follow_constraints",
        "messages": [
            {"role": "system", "content": "Sigue exactamente las restricciones del usuario."},
            {"role": "user", "content": "Dame exactamente tres viñetas, cada una con una técnica para estudiar mejor. No añadas introducción ni conclusión."},
        ],
        "check": "three_bullets",
    },
    {
        "id": "uncertainty",
        "messages": [
            {"role": "system", "content": "No inventes datos. Si no puedes saber algo a partir de la información disponible, dilo claramente."},
            {"role": "user", "content": "¿Qué nota saqué ayer en el examen de matemáticas? No te he dado esa información."},
        ],
        "check": "mentions_unknown",
    },
]


def request_completion(base_url: str, model: str, messages: list, timeout: float) -> tuple[str, float, dict]:
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Local model request failed ({type(exc).__name__}); check the server URL, model alias, and timeout.") from None
    elapsed = round(time.perf_counter() - started, 3)
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Server response did not contain choices[0].message.content.") from None
    return str(text), elapsed, payload.get("usage") or {}


def evaluate(check: str, output: str) -> tuple[bool | None, str]:
    value = output.strip()
    if check == "nonempty":
        return bool(value), "non-empty response"
    if check == "json_object":
        try:
            parsed = json.loads(value)
            ok = isinstance(parsed, dict) and isinstance(parsed.get("task"), str) and isinstance(parsed.get("steps"), list) and len(parsed["steps"]) == 2 and all(isinstance(x, str) for x in parsed["steps"])
            return ok, "valid JSON object with task and exactly two string steps"
        except json.JSONDecodeError:
            return False, "output is not valid bare JSON"
    if check == "three_bullets":
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        bullets = [line for line in lines if line.startswith(("-", "*", "•"))]
        return len(lines) == 3 and len(bullets) == 3, "exactly three non-empty bullet lines and no extra text"
    if check == "mentions_unknown":
        lowered = value.lower()
        markers = ("no sé", "no tengo", "no puedo saber", "no me has", "no dispongo", "información suficiente", "no hay información")
        return any(marker in lowered for marker in markers), "explicitly acknowledges missing information (heuristic)"
    return None, "manual review required"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", required=True, help="Model alias returned by GET /v1/models")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", default="model_lab/results/local-baseline.json")
    args = parser.parse_args()

    results = []
    for case in CASES:
        try:
            output, seconds, usage = request_completion(args.base_url, args.model, case["messages"], args.timeout)
            passed, criteria = evaluate(case["check"], output)
            results.append({
                "case_id": case["id"], "latency_seconds": seconds,
                "automated_check": passed, "check_description": criteria,
                "usage": usage, "output": output,
            })
            status = "PASS" if passed is True else "FAIL" if passed is False else "REVIEW"
            print(f"{status} {case['id']} — {seconds:.3f}s")
        except RuntimeError as exc:
            results.append({"case_id": case["id"], "error": str(exc)})
            print(f"ERROR {case['id']} — {exc}")
            break

    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "model": args.model,
        "note": "Diagnostic baseline only. Heuristic checks do not establish general intelligence, factuality, or safety.",
        "results": results,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report written to {target}")
    return 1 if any(item.get("automated_check") is False or "error" in item for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
