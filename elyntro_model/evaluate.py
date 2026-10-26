"""Evaluate checkpoint behavior on a local JSONL prompt/reference set."""
import argparse
import json
import math
import time
from pathlib import Path
import torch
from .generate import load_checkpoint
from .tokenizer import ByteTokenizer


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--data", type=Path, required=True, help="JSONL rows with prompt and optional reference/category")
    p.add_argument("--out", type=Path, default=Path("reports/model-evaluation.json"))
    p.add_argument("--max-cases", type=int, default=100)
    p.add_argument("--tokens", type=int, default=96)
    p.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    a = p.parse_args()
    if min(a.max_cases, a.tokens) < 1:
        p.error("max-cases and tokens must be positive")
    if a.device == "auto":
        device = "cuda" if torch.cuda.is_available() else ("mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu")
    else:
        device = a.device
    model = load_checkpoint(a.checkpoint, device)
    tokenizer = ByteTokenizer()
    cases = []
    for line in a.data.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
        if len(cases) >= a.max_cases:
            break
    results, latencies = [], []
    for index, case in enumerate(cases):
        prompt = case.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            results.append({"index": index, "error": "missing_prompt"})
            continue
        ids = torch.tensor([tokenizer.encode(prompt, add_bos=True)], dtype=torch.long, device=device)
        start = time.perf_counter()
        with torch.no_grad():
            output = model.generate(ids, max_new_tokens=a.tokens, temperature=0.7, top_k=40, eos_token_id=tokenizer.EOS)
        latency = time.perf_counter() - start
        latencies.append(latency)
        generated = tokenizer.decode(output[0, ids.shape[1]:].tolist())
        reference = case.get("reference")
        # Exact match is intentionally only a coarse diagnostic; it is not a semantic score.
        exact_match = (generated.strip().casefold() == reference.strip().casefold()) if isinstance(reference, str) else None
        results.append({"index": index, "category": str(case.get("category", "unspecified")),
                        "prompt": prompt, "generated": generated, "exact_match": exact_match,
                        "latency_seconds": round(latency, 4)})
    report = {
        "checkpoint": str(a.checkpoint), "device": device, "cases_requested": len(cases),
        "cases_evaluated": sum("generated" in row for row in results),
        "mean_latency_seconds": round(sum(latencies) / len(latencies), 4) if latencies else None,
        "exact_match_rate": (sum(row["exact_match"] is True for row in results) / sum(row["exact_match"] is not None for row in results)) if any(row["exact_match"] is not None for row in results) else None,
        "limitations": ["Exact match is not a semantic quality metric", "Human review and task-specific metrics are required", "This harness does not establish factuality or safety"],
        "results": results,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
