# Elyntro — repository state and verified runtime

This workspace contains the canonical project extracted from archived deployment bundles. The Flask application is in [app.py](app.py), and the canonical task engine is under [elyntro-engine-v6.2.0/elyntro](elyntro-engine-v6.2.0/elyntro). ZIP archives are historical inputs, not runtime dependencies.

## Repository inclusion status

The verified implementation is present and working, but it is still in the working tree rather than in the Git index. The controlled inclusion plan is documented in [docs/REPO_INCLUSION_MANIFEST.md](docs/REPO_INCLUSION_MANIFEST.md). No commit or push is performed automatically; the canonical source is staged only after explicit approval.

## What is implemented

- Flask web app with health and execution routes.
- `/api/execute` and `/api/agent/execute` send simple chat to the direct provider path and planned work through the canonical `CognitiveLoop` and `TaskExecutor`.
- Task steps invoke application-registered handlers; tasks, events, step results, and artifacts are persisted in SQLite.
- `tool_runtime.py` is a separately tested bounded utility, not the public task route or the canonical task executor.
- Its arithmetic evaluator accepts a restricted expression grammar and does not execute generated Python.
- Optional Groq provider adapter using environment configuration.
- Static validation for generated artifact files.
- SQLite-backed engine/task persistence via the canonical engine package.

## Architecture

- Web layer: Flask app and static page.
- Task execution: `CognitiveLoop` plans registered capabilities; `TaskExecutor` validates steps, bounds retries/timeouts, applies approval gates, and records durable events.
- Cognitive/core engine: `elyntro-engine-v6.2.0/elyntro` package with planner, verifier, memory, task executor, and persistence interfaces.
- Provider adapter: Groq OpenAI-compatible chat endpoint via `GROQ_API_KEY`, `GROQ_MODEL`, and timeout settings used by the app.
- Verification: independent validators are registered for task success; the app distinguishes static validation from factual verification.

## Runtime flow

1. Simple chat uses the direct provider adapter; task-like requests use `CognitiveLoop`.
2. The rule-based planner maps the goal to registered handlers and explicit step dependencies.
3. `TaskExecutor` records attempts, committed results, artifacts, errors, and bounded retries.
4. Task artifacts are stored in SQLite and exposed through the task/artifact endpoints.
5. Registered verifiers check concrete properties; a successful structural check is not factual proof or process isolation.

The built-in research handler uses the configured search provider (Tavily by default; DuckDuckGo is an explicit legacy option), fetches readable public pages, saves retrieved source excerpts, and passes them to Groq for source-grounded synthesis. Search remains best-effort: provider challenges and errors are recorded rather than treated as successful empty searches. If retrieval or Groq synthesis is unavailable, the task keeps its partial events/artifacts and ends failed rather than claiming completion. Source-backed synthesis is not independent fact verification.

## Environment variables

For live Groq synthesis and Tavily search, configure the credentials and provider explicitly:

```bash
GROQ_API_KEY=your_key
GROQ_MODEL=openai/gpt-oss-20b
ELYNTRO_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=your_tavily_key
PORT=5000
ELYNTRO_DB_PATH=/var/data/elyntro.db
```

If `GROQ_API_KEY` or `TAVILY_API_KEY` is missing, the app reports the unavailable capability and does not fabricate a model response or search result. `ELYNTRO_SEARCH_PROVIDER` defaults to `tavily`; `duckduckgo` is an explicit legacy option and can return anti-automation challenges. There is no silent provider fallback.

Tavily search uses the documented [`POST /search` API](https://docs.tavily.com/documentation/api-reference/endpoint/search). A configured search can consume provider credits; review the account's current pricing and limits before enabling it. No live Tavily request was made during local tests.

## SQLite persistence and durable storage

The app resolves the database path from `ELYNTRO_DB_PATH` and keeps a local fallback at `elyntro.db` in the repository root for development. For Render, attach a persistent disk mounted at `/var/data` and set `ELYNTRO_DB_PATH=/var/data/elyntro.db`. Without that disk, SQLite has no durability guarantee: a redeploy or service replacement/restart can discard the database.

The current [render.yaml](render.yaml) does not declare a persistent disk and uses the free plan. Unless the Render service is separately configured with an authorized persistent mount and `ELYNTRO_DB_PATH`, SQLite data is stored on ephemeral service storage and may be lost when the instance restarts, is replaced, or is redeployed. This repository change does not add a disk or alter the plan.

Recommended production configuration:

```bash
export ELYNTRO_DB_PATH=/var/data/elyntro.db
mkdir -p /var/data
```

Do not point SQLite to `/tmp` in a containerized deployment unless the target is backed by a real persistent volume. `/tmp` is suitable only for ephemeral local experiments; it is not durable across restarts.

## Durable background task execution

The first-party web UI opts into asynchronous task execution by sending `async: true` to `/api/agent/execute`. The API returns a task ID and task-access token; the browser polls `GET /api/tasks/<task_id>` for queue state, step events, artifacts and verification details. `POST /api/tasks` also enqueues work. Existing callers of `/api/execute` and `/api/agent/execute` retain the synchronous response contract unless they explicitly request async mode.

Queue records, leases and task step results are stored in SQLite. The worker starts with the Flask app, claims jobs atomically, renews leases, and attempts bounded recovery after an expired lease. A persisted RUNNING or VERIFYING task resumes from committed step results. If interruption happens before a complete plan is saved, Elyntro fails closed rather than guessing how to continue.

Configuration:
- `ELYNTRO_TASK_LEASE_SECONDS`: worker lease duration; default 180 seconds, minimum 30.
- `ELYNTRO_TASK_MAX_RECOVERIES`: maximum claims/recoveries before the queue marks a job failed; default 5.
- `ELYNTRO_DB_PATH`: SQLite database path. The database must live on a persistent filesystem for queue and task state to survive deploys/replacements.

Recovery is **at-least-once**, not exactly-once: a process can stop after an external side effect but before committing its step result. Handlers that mutate external state must be idempotent or use the engine's `OperationJournal`; ambiguous journal outcomes require reconciliation rather than blind replay. The queue does not provide process isolation or forcibly terminate a stuck Python handler. A persistent disk is required on Render; the current blueprint does not provision one automatically.

## Multimodal inputs, tools, and media adapters

The first-party UI accepts up to five text files (8 KB combined) and up to five image/PDF/audio attachments (650 KB combined). `POST /api/multimodal/analyze` accepts PNG/JPEG/WebP data URLs encoded as base64, PDFs (text extraction only, up to 20 pages), and UTF-8 text files. Image understanding depends on whether the configured chat model supports vision. Scanned PDFs without selectable text are rejected; OCR is not implied. `POST /api/multimodal/transcribe` sends bounded audio uploads to the configured OpenAI-compatible `/audio/transcriptions` endpoint; the default model is `whisper-large-v3-turbo` for Groq and `whisper-1` otherwise, overridable with `ELYNTRO_TRANSCRIPTION_MODEL`.

`GET /api/media/capabilities` reports media providers configured on the server. Image generation uses `POST /api/media/generate-image` with an OpenAI-compatible `/images/generations` endpoint; speech generation uses `POST /api/media/generate-speech` with `/audio/speech`; video generation uses a provider adapter exposing `POST /videos` and `GET /videos/{id}`. These adapters are unavailable until configured and are not universal protocols. Environment variables:
- Image: `ELYNTRO_IMAGE_API_BASE_URL`, `ELYNTRO_IMAGE_API_KEY`, `ELYNTRO_IMAGE_MODEL` (falls back to `OPENAI_API_KEY` and OpenAI's base URL).
- Speech: `ELYNTRO_TTS_API_BASE_URL`, `ELYNTRO_TTS_API_KEY`, `ELYNTRO_TTS_MODEL` (same OpenAI fallback).
- Video: `ELYNTRO_VIDEO_API_BASE_URL`, `ELYNTRO_VIDEO_API_KEY`, `ELYNTRO_VIDEO_MODEL`; this adapter assumes the provider's create/status paths follow the stated contract.

The authenticated OpenAI-compatible `POST /api/v1/chat/completions` endpoint supports bounded function calling for the explicit allowlist `calculate`, `web_search`, and `web_open`. It executes at most four model/tool rounds, rejects other tool names, and does not support streaming and tool execution in the same request yet. Web search requires the configured search provider; web open is limited to public HTTP(S) URLs and blocks unsafe destinations. This is not arbitrary shell or unrestricted computer access.

The first-party UI also exposes an optional **quick answer** mode using `POST /api/chat/stream` and Server-Sent Events (SSE). It streams text directly and explicitly does not execute tools or web research; automatic mode remains the default and keeps the planner/tool/task routing.

Long-running work continues to use Elyntro's durable task queue and task status API. The model/tool loop, multimodal analysis, and media generation are distinct execution paths with different capabilities; a successful API response is not by itself independent factual verification. Provider-specific features must be tested against the exact configured model before being advertised as available.

## Run locally

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
gunicorn --bind 0.0.0.0:5000 app:app
```

`requirements.txt` and `requirements-dev.txt` are editable version-range inputs. Use the repository lock files for repeatable production and development installs. The engine is packaged from the local `elyntro-engine-v6.2.0` directory; `PYTHONPATH` is not required.

## Render deployment

Use the existing Flask/gunicorn stack. The project is not a custom rewrite of the engine or runtime; it relies on the extracted canonical engine package and app files.

The current Render blueprint configuration is:

- Build command: `pip install -r requirements.lock`
- Start command: `gunicorn --bind 0.0.0.0:$PORT app:app`
- Python version: `3.14.2` (pinned in `render.yaml`; matches the Python 3.14.2 test runtime and the Python 3.14 lock resolution). Render supports released versions; this pin favors matching the tested patch, not using the newest maintenance patch. Revalidate the locks and suite before upgrading it.
- The blueprint does not declare provider credentials or a persistent disk. Configure credentials as Render secrets, and authorize/configure persistent storage separately if SQLite data must survive service replacement.
- Do not depend on ZIP extraction during build or startup; the project ships with the canonical engine files already unpacked in the repository.
- Keep a health endpoint at `/api/health` for basic service availability only; it is not an intelligence or verification guarantee.

## Tests

Verified in this repository with:

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

Run the commands from the repository root. The test count is intentionally not hard-coded because it changes with the checked-out suite.

## Important limitations

- This app does not claim that model-generated text is factually verified.
- Artifact verification checks registered concrete properties; it does not prove functional correctness or factual truth.
- The public research path can use Tavily when explicitly configured, but live retrieval and Groq synthesis were not verified with authorized credentials in this environment.
- The runtime is not a sandbox: registered Python handlers execute in-process, and a timed-out non-cooperative handler thread cannot be forcibly terminated.
- Public-page retrieval resolves each HTTP(S) target, rejects non-global addresses, and connects directly to one of the validated socket addresses rather than resolving the hostname again for the connection. HTTPS retains the URL hostname for TLS SNI/certificate verification and the HTTP `Host` header. Each redirect is resolved and validated again before connecting; HTTPS-to-HTTP redirects are rejected. This reduces DNS-rebinding/redirect risk but is not a network sandbox or an absolute SSRF guarantee.
- If a provider is not configured, the system reports that limitation explicitly.

## Relevant files

- [app.py](app.py)
- [tool_runtime.py](tool_runtime.py)
- [requirements.txt](requirements.txt)
- [render.yaml](render.yaml)
- [static/index.html](static/index.html)
- [tests/test_engine_integration.py](tests/test_engine_integration.py)
- [elyntro-engine-v6.2.0/elyntro](elyntro-engine-v6.2.0/elyntro)

## Creative Studio, browser guidance, and Founder Mode

The optional visual workspace is available at `/creative-studio`. It uses Three.js/WebGL in the browser and supports primitive editing, camera movement, per-object transforms, local scene persistence and JSON export. The Studio can submit an operator-provided ComfyUI API workflow and display generated outputs, and can request read-only page snapshots plus constrained Playwright guidance actions when explicitly enabled.

See [docs/CREATIVE_STUDIO.md](docs/CREATIVE_STUDIO.md) for configuration, endpoint contracts, threat boundaries, and setup. Playwright dependencies are intentionally separate in [requirements-creative.txt](requirements-creative.txt) so the base Render build does not install a browser automatically.

Founder Mode is a governed self-development workflow. A prompt beginning with `[245jg&]` requires the server-side creator token, asks the configured model to inspect bounded local source context and stores a hash-addressed unified-diff proposal. The proposal must be reviewed before manual implementation. With `ELYNTRO_SELFDEV_ENABLED=true` and a narrowly scoped GitHub token configured, the Studio can also commit supplied allowlisted file contents to a separate branch and open a draft pull request. It never merges, executes proposed code, or deploys. Use durable storage for proposal records, branch protection and human review.
