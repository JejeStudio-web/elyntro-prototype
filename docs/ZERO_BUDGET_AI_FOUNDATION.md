# Elyntro Zero-Budget Intelligence Foundation

Status: engineering plan based on repository inspection, 2026-10-31.
Scope: isolated branch `work/zero-budget-ai-foundation`. This document does not change production configuration or deploy anything.

## Non-negotiable constraints

1. Provider/API spend must remain exactly €0. Do not add paid API keys, paid credits, paid inference, or a service that silently bills after a trial.
2. "Free" means the currently available free allowance, not unlimited availability. Every provider has quota, availability, acceptable-use and data-handling conditions. If quotas are exhausted, return an honest unavailable/queue state or use an explicitly configured local model; never silently switch to a paid service.
3. Never claim a model is "the best in the world" based on a vendor benchmark. Elyntro must maintain its own task benchmark and report measured results.
4. Do not replace the saved n8n agent, alter the active operational-chat webhook, merge to `main`, or deploy until isolated tests pass and the change is reviewed.
5. Do not expose powerful tools (GitHub write access, arbitrary HTTP, code execution, self-development) to ordinary users. Model quality does not replace authorization.

## Repository findings

- The repo already contains a provider-independent model gateway, registry, router, evaluation store, benchmark runner, comparison runner and adaptive routing code under `elyntro-engine-v6.2.0/elyntro/`.
- `elyntro_api_v1.py` already has a server-side model pool with a primary model and multiple OpenAI-compatible endpoints. Client requests cannot provide provider secrets.
- The Flask app's direct model path uses provider environment configuration; the current codebase documents Groq as the existing provider route.
- The frontend lives under `elyntro-new-ui/`. The active n8n workflow `Elyntro — Operational Chat API v1` (workflow ID `x5QQXNZXF22NuOyv`, active version `6259a482-6f8c-40e8-baba-8d389a16c85b`) currently contains a LangChain Agent node connected to a Groq model (`openai/gpt-oss-120b`). The inspected active graph does **not** invoke the separately saved `Elyntro_agent`. The UI, n8n and Supabase being deployed does not itself prove the full flow works end-to-end. Keep this discrepancy explicit until a real authenticated test proves the route and response.
- Existing benchmark modules are a better foundation than inventing another routing layer. First verify their tests and wire them to a repeatable benchmark set.
- Existing deployment notes identify possible ephemeral SQLite storage and a PostgreSQL expiry date. Persistence risks must be addressed before relying on evaluation or user memory as durable.

## Target architecture

```
UI / authenticated request
        |
        v
Intent and safety gate
        |
        v
Task planner -----> capability/permission policy
        |
        v
Model gateway <---- evaluation-backed registry/router
   |       |
   |       +------ quota/cost guard (hard maximum cost = 0)
   |
   +--> free hosted model A (only if free tier is verified/configured)
   +--> free hosted model B (only if free tier is verified/configured)
   +--> optional local open-weight model (only on real available compute)
        |
        v
Tool execution with allowlists and per-action authorization
        |
        v
Independent result checks + evidence/provenance
        |
        v
Persistent conversation/task state + audit record
```

The router may choose among eligible free endpoints only when there is evidence for the task type. A provider failure can trigger a retry on another eligible free provider only if the request is safe to repeat and the quota/budget gate still passes. Never retry side-effecting tools blindly.

## Model strategy

### Phase 1 — Free hosted inference, no architectural rewrite

Evaluate currently accessible free tiers using the existing model gateway. Candidates are not approved until the exact model identifier, endpoint, quota, terms, privacy treatment and current availability are checked in the provider's official account/docs. Do not trust model names, prices, benchmark claims or release dates copied from third-party articles without verification.

Potential candidates to investigate, not yet endorsements:
- Groq-hosted models already compatible with the current code.
- Google Gemini API models that are explicitly available under its free tier.
- Other providers with a currently documented, non-billing free tier and an API compatible with our adapter.

The model list must be configuration-driven; no model name should be hard-coded as universally available. No key should ever be placed in frontend environment variables, source control, chat messages or logs.

### Phase 2 — Benchmark before routing

Create a fixed, versioned evaluation set covering:
1. Spanish informal intent and ambiguous wording.
2. Multi-step reasoning and detecting flawed assumptions.
3. Coding: implement, explain, test and repair.
4. Research: distinguish sourced facts from inference and cite usable sources.
5. Tool-use planning, authorization and safe handling of failures.
6. Memory: retrieve relevant stored facts without fabricating memories.
7. Calibration: state uncertainty and refuse to invent results.
8. Long-context consistency and following constraints.
9. Structured output and interface/artifact generation.
10. Robustness to prompt injection in retrieved pages and tool outputs.

Use paired prompts, fixed rubrics, blind human review on a sample, and separate development/validation/holdout sets. Report task success, critical-error rate, factual-grounding rate, tool completion rate, latency, token use, provider failures and availability. A single aggregate score must not hide critical failures. Run inexpensive pilot tests first; no paid test calls.

### Phase 3 — Elyntro's own intelligence layer

Build capability through the surrounding system, not by pretending a prompt makes a base model smarter:
- explicit plan -> act -> observe -> verify -> recover loop;
- typed tool contracts, least privilege, approval gates for external side effects;
- evidence tracking with source, timestamp and confidence;
- memory retrieval with provenance, user scope and deletion semantics;
- deterministic checks where possible, plus independent review for important results;
- task state and idempotency so retries do not duplicate actions;
- per-provider quota circuit breakers and honest unavailable responses;
- regression tests for every fixed failure.

### Phase 4 — Open weights and model adaptation

Investigate local inference only after measuring hardware and hosting constraints. Open weights are not equivalent to free inference: RAM/VRAM, compute, storage, energy and uptime still have costs. A model may be fine-tuned only when a specific repeated failure is demonstrated and there is a legally usable, consented, high-quality dataset. Fine-tuning can improve style, formats or domain behavior; it is not a reliable shortcut to frontier general intelligence.

Training a competitive general-purpose foundation model from scratch is not a zero-budget plan. Do not start it unless resources and a narrow, measurable objective materially change.

## Release gates

No model becomes primary unless:
- confirmed to incur no bill under the configured usage;
- current free-tier limits and data terms are recorded;
- required capabilities are tested, including tool-call behavior if used;
- no secret or private tool is exposed to untrusted users;
- it passes the holdout benchmark with no unacceptable critical regressions;
- provider outage and quota exhaustion are handled without paid fallback;
- end-to-end UI -> authenticated backend -> model -> persisted response is verified;
- rollback is documented and the production route remains untouched until approval.

## Immediate execution order

1. Verify which deployed route the UI actually calls and trace one authenticated message end to end; fix any existing `conversation not found` / workflow errors before model selection.
2. Run the existing repository test suite and the existing model-gateway/benchmark tests on this branch; fix regressions before feature work.
3. Add a small, versioned benchmark set and a zero-cost configuration validation that rejects paid/unapproved providers and fails closed when the model pool is empty.
4. Test available free model endpoints in an isolated environment using tiny prompts and strict output limits. Record actual response, errors, latency, quotas and terms; don't infer results from provider marketing.
5. Wire only a verified candidate into an isolated copy of the real chat flow, preserving the current flow as rollback.
6. Review permissions, persistence and production health before asking for approval to switch.

## What success means

Elyntro will not be declared "the world's best AI" by assertion. The engineering target is a system that steadily improves on a public, reproducible task suite, is unusually capable at completing real tasks, verifies outcomes, protects user data and is honest about limitations—while respecting the €0 inference-spend constraint.

## Verified execution update — 2026-11-05

Scope: read-only inspection of connected GitHub, n8n, and Vercel metadata. No model request was issued during this inspection; no provider credits were intentionally consumed. No production workflow, deployment, secret, permission, or user data was changed.

### Confirmed current state

- GitHub branch `work/zero-budget-ai-foundation` contains the foundation plan; this update is isolated on `work/daily-verification-2026-11-05`.
- n8n still exposes 11 workflows. `Elyntro — Operational Chat API v1` (`x5QQXNZXF22NuOyv`) is active and its inspected graph connects `Elyntro AI response` to `Groq model` using `openai/gpt-oss-120b`; it does not invoke the separately saved `Elyntro_agent`.
- The isolated `Elyntro — Saved Agent Connection Test` (`uaBNf9XvrfZnz10V`) remains inactive and references the saved agent by ID. It was not executed.
- Vercel project `elyntro-new-ui` and recent deployments are visible. Recent preview deployment `dpl_BiSw4MAsDJb3PVAUjCsaXiN2VRP5` is READY on `feature/new-elyntro-ui`; this is deployment metadata, not proof that the current frontend successfully reaches n8n.
- The latest retrievable operational workflow execution (ID `37`, 2026-10-31) is listed as `success` at the execution level, but its node data shows the AI node failed with provider rate-limit/too-many-requests errors and flowed to `Return controlled provider error`. The stored response metadata also contains a claimed `sandbox:/Capacidades_Elyntro.pdf` link without a verifiable artifact in this execution. This is evidence of a user-facing failure/false artifact claim, not a successful chat completion.
- The active workflow has retry behavior on the AI node and the inspected execution recorded many model sub-runs before the provider error. Under the €0 constraint, do not re-run live model calls just to reproduce this failure; inspect and test deterministic error-handling logic with simulated/pinned data first.

### Decision and next action

Do not switch the active workflow to `Elyntro_agent` until its saved-agent connection can be tested without a paid or billable model path. Do not use Groq or any other inference route unless its zero-cost eligibility is verified for the account at execution time. The highest-value next isolated engineering task is to add deterministic tests for frontend/backend provider-error handling and artifact-link validation, then inspect the relevant frontend request path and ensure errors are shown as errors rather than rendered as successful assistant output. Production remains unchanged.
