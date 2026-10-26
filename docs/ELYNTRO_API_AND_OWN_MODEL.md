# Elyntro API and Own-Model Strategy

Status: architecture proposal; not yet implemented in production.

## Objective

Build both:
1. **Elyntro API** — an API owned and controlled by the Elyntro project, with stable endpoints, authentication, policy enforcement, memory, tool orchestration, observability, and provider-independent model adapters.
2. **Elyntro models** — a staged path from self-hosted open-weight models to models fine-tuned for Elyntro tasks, and only later to training a foundation model if data, evaluation quality, compute, and budget justify it.

Owning the API does not itself mean owning the underlying model weights. These are separate layers with separate deployment and cost requirements.

## Reference principles from leading AI systems

Use public documentation and observable capabilities as references, not proprietary implementation guesses:
- OpenAI: tool calling, structured outputs, multimodal APIs, agent workflows.
- Google Gemini: multimodal inputs and long-context workflows.
- Anthropic Claude: tool-use contracts, constrained/structured outputs, safety boundaries.
- Open-weight ecosystem: self-hosting, reproducible evaluation, fine-tuning and portability.

Elyntro must implement its own interfaces and control plane rather than copying provider-specific request/response schemas.

## Target architecture

```text
Clients (web app / SDK / integrations)
        |
        v
Elyntro API (versioned HTTP endpoints + streaming)
        |
        v
Identity, authorization, quotas, request validation
        |
        v
Agent Orchestrator
  |          |             |
  v          v             v
Model      Tool Runtime   Memory / Files
Router     (isolated)     (scoped, durable)
  |
  +--> Provider adapters (optional external models)
  |
  +--> Self-hosted inference endpoint (open-weight models)
        |
        v
Independent evaluation and verification
        |
        v
Auditable task result + artifacts + evidence
```

## API design

Start with a versioned namespace, `/api/v1`, and a documented OpenAPI contract.

Proposed endpoints:
- `GET /api/v1/health` — liveness/readiness details without secrets.
- `GET /api/v1/models` — models available to the current deployment and their capabilities.
- `POST /api/v1/chat/completions` — synchronous chat; streaming can use server-sent events.
- `POST /api/v1/responses` — unified response/agent request with optional tools and output constraints.
- `POST /api/v1/tasks` — create a long-running task.
- `GET /api/v1/tasks/{task_id}` — inspect task state, requiring ownership authorization.
- `GET /api/v1/tasks/{task_id}/events` — task progress/events.
- `GET /api/v1/tasks/{task_id}/artifacts/{artifact_id}` — authorized artifact access.
- `POST /api/v1/files` and `GET /api/v1/files/{file_id}` — scoped file lifecycle.
- `GET /api/v1/usage` — usage and quota information for the authenticated account.

Do not expose provider credentials, internal stack traces, or unrestricted tool execution. Keep compatibility aliases only while clients migrate.

## Model adapter contract

Every backend adapter should expose a provider-neutral interface for:
- capability metadata (text, image, audio, video, tool calls, context limits);
- generation and optional streaming;
- structured output / tool-call parsing;
- timeout, cancellation and retry semantics;
- token/compute usage where available;
- normalized errors and provenance metadata.

The router chooses a backend by capability, configured policy, latency, availability, privacy constraints, and cost limits. It must not silently switch to a less capable model when a task requires a missing capability. Fallback behavior must be explicit and logged.

## Own-model roadmap

### Stage 1 — Establish a baseline
- Create a representative evaluation set for Spanish and English, coding, tool selection, factuality, multimodal understanding and instruction following.
- Record quality, latency, memory use and estimated cost for each candidate model.
- Keep a provider adapter as a temporary option, not as a dependency in the core architecture.

### Stage 2 — Self-host an open-weight model
- Select a license-compatible model after benchmarking against the evaluation set.
- Serve inference behind a private endpoint; keep it inaccessible directly from the public internet.
- Apply request limits, model concurrency limits, timeouts, health checks and resource monitoring.
- Validate hardware availability and cost before selecting model size. A free web host is not assumed to support GPU inference.

### Stage 3 — Adapt the model to Elyntro
- Collect only permitted, consented, privacy-reviewed examples.
- Build a versioned training dataset with held-out evaluation data and leakage checks.
- Test prompt/format tuning first; evaluate parameter-efficient fine-tuning (for example LoRA) only if measured gaps justify it.
- Compare each candidate against the unchanged baseline and reject regressions in safety, factuality, tool-use reliability or general capability.

### Stage 4 — Consider training from scratch only with evidence
Pretraining a foundation model is a separate, expensive research and infrastructure project. Do not commit to it until there is a data rights plan, compute budget, evaluation pipeline, and evidence that self-hosting and fine-tuning cannot meet requirements.

## Independent verification

The evaluator must be separate from the model being evaluated. It should:
- verify claims against retrieved evidence when a task requires research;
- run deterministic tests for code and structured artifacts where possible;
- distinguish verified, partially verified, unverified and failed outcomes;
- retain provenance (tool, URL/file, timestamp, relevant excerpt or digest);
- avoid claiming success solely because the model says it succeeded.

A second model can be one signal, but it is not sufficient as the sole factual verifier.

## Security and operational requirements

- Authenticate accounts and enforce per-user/task ownership; task tokens alone are not user identity.
- Apply quotas at the shared server/store layer, not only in process memory.
- Keep secrets in deployment secret storage and never commit them.
- Isolate code execution from the API process and restrict network/filesystem access.
- Store conversation memory and uploaded files with explicit scope, retention and deletion semantics.
- Log metadata and decisions without unnecessarily logging sensitive prompt/file contents.
- Require tests, review and explicit approval before production deployment; provide rollback.
- Make provider changes and model versions observable and reversible.

## Implementation sequence

1. Inspect the existing Flask API, tool runtime, task engine, persistence and deployment constraints.
2. Write the OpenAPI contract and adapter interface without breaking current clients.
3. Implement a minimal model router and deterministic fake adapter for tests.
4. Migrate current model calls behind the adapter; preserve current behavior while adding tests.
5. Add authenticated account ownership, durable quotas and safe task/file authorization.
6. Add evaluation datasets and a reproducible model benchmark.
7. Deploy a self-hosted inference service separately from the web API, if compatible compute is available.
8. Integrate one open-weight model behind the same adapter and compare it on the benchmark.
9. Fine-tune only after the benchmark identifies stable, measurable gaps.
10. Consider foundation-model pretraining only as a separately approved research project.

## Initial acceptance criteria

- Core API and agent runtime can run tests without any external model API key.
- Model calls go through one documented adapter interface.
- API responses identify the selected model/backend and verification status where relevant.
- Task status and artifacts are authorized against a real account identity.
- Provider failures, timeouts and missing capabilities return explicit errors, not fabricated success.
- The self-hosted model can be disabled or replaced without rewriting orchestration.
- CI tests the API contract, adapter behavior, authorization, failure paths and evaluation reporting.

## Important current-state caveat

This document defines the target and implementation order. It does not claim that account authentication, isolated GPU inference, fine-tuning, or a proprietary foundation model already exists.
