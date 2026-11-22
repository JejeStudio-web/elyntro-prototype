# Elyntro — Open-Model Adaptation Plan

Status: implementation foundation in progress. This document does not claim that a fine-tuned model exists.

## Decision

Elyntro will start from a compatible open-weight pretrained model and adapt it only when repeatable evaluations show that prompt, tool, or orchestration changes are insufficient. Training a foundation model from scratch is out of scope for the current hardware and zero-budget constraint.

## Current hardware and constraints

The currently recorded development machine is an Intel iMac (2012-era), 32 GB RAM, macOS Catalina. A Qwen2.5-3B Q4_K_M GGUF model was previously served by llama.cpp, but that runtime must be revalidated before relying on it. The machine is a reasonable target for experimenting with quantized inference, not a dependable LoRA/QLoRA training workstation.

The existing engine package requires Python >=3.10; the recorded Mac system Python is 3.8.2. Do not run the engine by assuming the system Python is compatible. A separate supported Python environment is required, and Catalina compatibility must be tested before installing dependencies.

## Architecture boundary

- The existing Elyntro engine remains responsible for planning, permissions, execution, independent verification, persistence, and audit events.
- Model calls go through a provider adapter. The new `LocalLlamaCppAdapter` speaks the local llama.cpp OpenAI-compatible chat-completions endpoint and defaults to `http://127.0.0.1:8080/v1`.
- Local inference sends no authorization header by default. If the server is configured with authentication, an explicit environment-variable name can be provided to the adapter. Never put secrets in source, prompts, logs, or benchmark fixtures.
- The adapter is a building block, not yet proof that the public Flask routes use the local model. Application routing integration and real-server smoke tests remain separate tasks.
- Keep external model providers optional and explicit. The zero-cost default must not silently call a paid API.

## Required stages

1. **Baseline:** reproduce the current branch's tests and inspect actual model-routing call sites. Separate deterministic mocks from live-model tests.
2. **Local smoke test:** start llama.cpp with the already-downloaded model, verify `/v1/models` and one chat completion, and record model hash, runtime flags, latency, and output.
3. **License gate:** verify the exact model's license and permitted use before redistribution or commercial deployment. Do not assume “open weights” means unrestricted licensing.
4. **Benchmark:** create a versioned suite for Spanish instruction following, structured JSON, tool-plan validity, refusal to invent evidence, context retention, and bounded task execution. Keep development, validation, and holdout cases separate. Score with deterministic checks where possible and human review for qualitative claims.
5. **Model comparison:** evaluate the baseline model and at least one license-compatible alternative using the same prompt, sampling parameters, and cases. Record quality, failures, latency, memory, and cost.
6. **Data curation:** only after identifying repeated model-specific failures, create a small, licensed, reviewed training set. Exclude credentials, personal information, and unreviewed private conversations.
7. **Adapter training:** attempt LoRA/QLoRA only when a genuinely free, compatible compute option is available, or when a local experiment is shown to fit practical limits. Record the exact base model revision, tokenizer, dataset version, hyperparameters, and output adapter hash.
8. **Acceptance:** compare the adapter against the base model on the held-out suite. Reject it if it regresses tool correctness, factual uncertainty, safety, latency, or any critical task. Keep the prior model as rollback.
9. **Integration:** enable the adapted model only through an explicit server-side configuration flag. Never let a model update production weights, code, permissions, or deployment configuration autonomously.

## Acceptance criteria

- Existing tests continue to pass.
- Unit tests prove the local adapter does not require a key by default, does not leak provider response bodies in errors, validates endpoint configuration, and respects timeouts.
- A real llama.cpp smoke test succeeds from the target environment.
- Model results are recorded against a named benchmark version; passing deterministic engine tests is not represented as proof of model intelligence.
- No paid provider is called by the local path.
- Rollback to the previous model is documented and tested.

## Current limitations

- The local adapter is now implemented and unit tests have been added, but CI has not yet been run against this change at the time of writing.
- The adapter is not yet wired into the main Flask chat/task routes.
- No LoRA/QLoRA adapter has been trained.
- The Qwen2.5-3B license and distribution terms remain a gate.
- The Mac runtime, latency, and actual RAM use must be measured again.
