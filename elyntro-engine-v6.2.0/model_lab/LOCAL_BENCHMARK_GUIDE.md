# Local model benchmark and candidate gate

These scripts do not train or promote weights. They create a repeatable diagnostic report and reject obvious regressions before human review.

## 1. Run the baseline against a live local llama.cpp server

From the engine directory, make sure the server is running at http://127.0.0.1:8080/v1. Obtain its exact model alias:

    curl -s http://127.0.0.1:8080/v1/models

Run the smoke benchmark, replacing MODEL_ALIAS with the returned model ID:

    python3 model_lab/benchmark_local.py --model "MODEL_ALIAS" --output model_lab/results/baseline.json

The four prompts check Spanish instruction following, constrained JSON, exact bullet count, and acknowledgement of missing information. The checks are small heuristics, not proof of quality or safety.

## 2. Compare a candidate with the baseline

Run the same benchmark for the candidate and save it separately, then compare:

    python3 model_lab/benchmark_local.py --model "CANDIDATE_ALIAS" --output model_lab/results/candidate.json
    python3 model_lab/compare_reports.py --baseline model_lab/results/baseline.json --candidate model_lab/results/candidate.json --output model_lab/results/comparison.json

The gate rejects mismatched test cases, request errors, regressions on previously passing cases, or average latency over 1.25x baseline. Exit code 0 means only "eligible for further review"; 1 means rejected; 2 means the report could not be read. It does not modify weights or production configuration.

## Constraints

- This runs locally and requires the model server to be running. GitHub-hosted CI cannot reach 127.0.0.1 on the user's iMac.
- Keep reports free of private prompts and personal data.
- Review license, output quality, factuality, safety, and held-out tests before any adapter training or model change.
- Do not automatically train on raw user conversations or promote weights without explicit permission, validation, and rollback.
