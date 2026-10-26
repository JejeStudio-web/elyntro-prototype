# Elyntro: GPU, RAM, storage, and cloud-budget plan

Status: planning/runbook only. This document does **not** provision infrastructure, create a paid account, launch a GPU, or authorize spending.

## Executive decision

Use a short-lived, single-GPU machine for a controlled experiment; keep the existing Render web app separate. Do not put PyTorch and a continuously running GPU model into the current lightweight Render web service. The current Render service is on the Free plan in Oregon; its current deployment does not constitute GPU capacity.

Recommended first experiment:
- One NVIDIA GPU with 24 GB VRAM (RTX 4090-class or comparable), CUDA-compatible PyTorch, 8+ vCPU, 24–32 GB system RAM, and 50–100 GB temporary disk.
- 2-hour initial benchmark, then stop the instance and download the checkpoint, evaluation report, logs, and data manifests.
- Hard spending target: **US$5 maximum for the first test**, including a margin for storage, startup time, taxes, and pricing variance. If the provider's current estimate cannot fit this cap, do not launch; reduce the duration or ask for a new budget approval.
- No unattended jobs, automatic scale-up, reserved capacity, or 24/7 GPU serving.
- Do not upload personal data. Use a dedicated secret and do not place API keys in Git, notebooks, logs, or dataset artifacts.

The current model is a very small experimental Transformer with byte tokenization. A GPU will make its training run faster; it will not make the architecture, dataset, or resulting model equivalent to a modern general-purpose assistant. Establish a measured baseline before increasing model size or data volume.

## What must exist before GPU work starts

1. **Authorized cloud account and explicit budget**: an account owner who is permitted to accept the provider's terms, a payment method/credits if needed, billing alerts where available, and an agreed spending ceiling. Do not launch a paid resource until the owner has approved the exact provider, machine, maximum duration, and maximum cost.
2. **GPU access**: NVIDIA CUDA GPU with at least 16 GB VRAM for this prototype; 24 GB is the preferred starting point. Verify that the actual instance has the advertised GPU and that torch.cuda.is_available() returns true.
3. **Host resources**: ideally 8+ vCPU, 24–32 GB RAM, and enough disk for dependencies, streamed data, checkpoints, and logs. Check free disk before downloading datasets.
4. **Reproducible code and data**: merge/rebase the model PRs in order, pin dependencies where practical, preserve source/license manifests, keep held-out evaluation data separate, and record the commit SHA and all training parameters.
5. **Secure connectivity**: training does not need a public inbound port. For later inference, use private networking or a secured HTTPS endpoint, rotateable secrets, authentication, request limits, monitoring, and a shutdown/rollback procedure.
6. **Artifact persistence**: export checkpoint, metrics, logs, and manifests before stopping/deleting the machine. Temporary container disks can be lost when an instance is stopped or deleted; verify provider-specific storage behavior.

## Cost model (indicative, not a quote)

RunPod's public GPU pricing page, checked 26 October 2026, lists an RTX 4090 at US$0.89/hour and storage separately. Prices, availability, region, cloud tier, and taxes may change. Verify the final checkout estimate before starting: https://www.runpod.io/pricing

| GPU runtime | Compute estimate at $0.89/hour | Decision |
|---:|---:|---|
| 2 hours | $1.78 | Initial smoke test; target |
| 4 hours | $3.56 | Only after reviewing the first result |
| 10 hours | $8.90 | Requires a separate budget decision |
| 24 hours | $21.36 | Not approved by this plan |
| 30 days continuously (720 h) | $640.80 | Do not use for always-on serving |

These are compute-only calculations. Storage, tax/VAT, networking, startup/idle time, and any additional services can raise the total. A machine that remains running while idle can continue to incur charges. Set a timer/stop procedure and confirm the instance is actually stopped.

For continuous inference, do not leave this training GPU running by default. First benchmark request volume and latency. Compare a hosted inference API, CPU inference for the tiny prototype, or an on-demand/serverless GPU endpoint before approving an always-on GPU budget.

## Execution stages and acceptance gates

### Stage 0 — No-cost readiness
- Confirm the model PRs are merged or rebase the experiment branch onto the intended base.
- Run tests and review dataset manifests/licenses.
- Record baseline training time, peak system RAM, peak VRAM, loss, tokens/second, inference latency, and the evaluation outputs.
- Add an evaluation set covering Spanish instruction following, factual calibration, simple arithmetic, code, prompt injection, and safety. Keep it held out from training.

**Gate:** tests pass; data schema is valid; no secret or dataset corpus is committed; budget owner approves the exact GPU quote.

### Stage 1 — Capped GPU smoke test
- Launch one GPU only, with a short explicit time limit and no public ports.
- Verify GPU model, VRAM, driver, CUDA, PyTorch, available disk, and memory before training.
- Run a small subset first. Save logs and checkpoint periodically.
- Measure actual peak VRAM and runtime; stop immediately on unexpected cost, OOM loops, or unstable results.
- Export the artifacts, verify that the checkpoint reloads on a clean process, then stop/delete the instance.

**Gate:** successful reload, reproducible configuration, recorded costs/resources, and a reviewed evaluation report. A falling training loss alone is not a quality pass.

### Stage 2 — Controlled scale-up
Only if Stage 1 passes, choose a new data/step/model-size target based on measured throughput and memory. Calculate the projected cost as:

estimated GPU cost = measured hours × quoted hourly rate + storage + taxes/other charges

Use a new explicit budget ceiling for this stage. Do not extrapolate model quality from the tiny smoke test.

### Stage 3 — Inference integration
- Keep the public Flask app and the GPU model server separate.
- Do not point Render at a private host until the host has stable private/TLS connectivity and an authenticated endpoint.
- Test /health, authenticated /v1/models, chat completion, bad-key rejection, request limits, latency, restart recovery, and failure handling.
- Store secrets in the hosting provider's secret/environment-variable manager, never in source control.
- Add persistent/shared rate limiting and observability before any public multi-user release.

**Gate:** only integrate after a real checkpoint passes evaluation and the server's security and reliability tests.

## Current blockers and inputs still needed

- An explicitly approved maximum spend for the first GPU test (suggested cap: US$5 total).
- Confirmation that an authorized account owner can use the selected provider and has billing/credits enabled.
- A decision whether the immediate objective is **training only** (short-lived GPU) or **always-on inference** (different, substantially more expensive budget).
- After the smoke run: real measured throughput, VRAM/RAM peaks, artifact size, and evaluation results.

No GPU has been allocated, no payment has been made, and no trained checkpoint is claimed by this plan.
