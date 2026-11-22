#!/usr/bin/env python3
"""Conservative non-regression gate for two local-model benchmark JSON reports."""
import argparse
import json
import sys
from pathlib import Path


def load_report(path):
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Could not read a valid report: " + str(path)) from exc
    if report.get("schema_version") != 1 or not isinstance(report.get("results"), list):
        raise ValueError("Unsupported report schema: " + str(path))
    return report


def compare_reports(baseline, candidate, max_latency_ratio=1.25):
    base = {item.get("case_id"): item for item in baseline["results"] if item.get("case_id")}
    cand = {item.get("case_id"): item for item in candidate["results"] if item.get("case_id")}
    reasons = []
    if not base or set(base) != set(cand):
        reasons.append("Baseline and candidate must contain the same non-empty set of case IDs.")
    regressions = []
    candidate_errors = []
    for case_id in sorted(set(base) & set(cand)):
        if "error" in cand[case_id]:
            candidate_errors.append(case_id)
        if base[case_id].get("automated_check") is True and cand[case_id].get("automated_check") is not True:
            regressions.append(case_id)
    if regressions:
        reasons.append("Candidate regresses on previously passing cases: " + ", ".join(regressions))
    if candidate_errors:
        reasons.append("Candidate has request errors on cases: " + ", ".join(candidate_errors))

    def average_latency(items):
        values = [item.get("latency_seconds") for item in items.values()
                  if isinstance(item.get("latency_seconds"), (int, float)) and item.get("latency_seconds") >= 0]
        return sum(values) / len(values) if values else None

    base_latency = average_latency(base)
    candidate_latency = average_latency(cand)
    if base_latency and candidate_latency is not None and candidate_latency > base_latency * max_latency_ratio:
        reasons.append("Candidate average latency exceeds the configured regression threshold.")
    return {
        "schema_version": 1,
        "approved_for_further_review": not reasons,
        "baseline_model": baseline.get("model"),
        "candidate_model": candidate.get("model"),
        "baseline_passes": sum(item.get("automated_check") is True for item in base.values()),
        "candidate_passes": sum(item.get("automated_check") is True for item in cand.values()),
        "baseline_average_latency_seconds": round(base_latency, 3) if base_latency is not None else None,
        "candidate_average_latency_seconds": round(candidate_latency, 3) if candidate_latency is not None else None,
        "max_latency_ratio": max_latency_ratio,
        "reasons": reasons,
        "warning": "Non-regression gate only. It does not certify factuality, safety, or intelligence and never promotes model weights automatically."
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--max-latency-ratio", type=float, default=1.25)
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.max_latency_ratio < 1:
        parser.error("--max-latency-ratio must be at least 1.0")
    try:
        result = compare_reports(load_report(args.baseline), load_report(args.candidate), args.max_latency_ratio)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["approved_for_further_review"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
