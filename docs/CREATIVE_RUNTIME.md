# Elyntro Creative Runtime — staged implementation

## Three.js scene

Open `/creative-studio` (served by Flask as a static asset). The scene uses Three.js WebGL and OrbitControls from a pinned CDN version. It supports live geometry creation, object selection, removal/reset, camera presets, automatic orbit, simple natural-language camera guidance, and live renderer frame-rate estimates. This is browser-side rendering; it does not provide a server-side render farm or model-generated meshes.

## ComfyUI

Configure `COMFYUI_BASE_URL` on the machine that can reach the ComfyUI API, for example `http://127.0.0.1:8188` for a same-host development setup. Protect Elyntro's endpoints with `ELYNTRO_API_KEY`. The client accepts a caller-supplied ComfyUI API workflow JSON object and replaces positive prompt text in `CLIPTextEncode` nodes. It submits to `/prompt` and can read bounded history metadata. It does not download models, install custom nodes, expose output files, or start ComfyUI. A Render deployment cannot reach a user's local computer through loopback; run Elyntro and ComfyUI in the same trusted environment or use an explicitly secured reachable host.

## Browser control with Playwright

The optional read-only inspection endpoint is disabled by default. To enable in a controlled local environment, install Playwright and its Chromium runtime, set `ELYNTRO_PLAYWRIGHT_ENABLED=true`, `ELYNTRO_BROWSER_ALLOWLIST=example.com,docs.example.org`, and configure `ELYNTRO_API_KEY`. Requests must target a public HTTP(S) URL whose hostname matches the allowlist. It opens an ephemeral, headless browser, extracts a bounded title/text/link snapshot and closes the browser. It does not click buttons, submit forms, use persistent cookies, download files or execute arbitrary action plans. This deliberately provides a safe foundation rather than pretending full computer-use is complete.

## Founder Mode and self-modification

Set `ELYNTRO_FOUNDER_TOKEN` to expose proposal creation. `POST /api/creative/founder/proposals` saves a bounded patch proposal with SHA-256, timestamp, and review policy to `ELYNTRO_PROPOSAL_DIR` (defaults to `/tmp/elyntro-proposals`). Proposal creation is not code execution and does not alter the working tree. Automatic patch application is intentionally absent: future implementation should create a branch, run tests/security checks in an isolated worker, show a diff and require explicit human approval before merge/deploy.

## Endpoints

- `GET /api/creative/status`: capability status.
- `POST /api/creative/comfyui/generate`: authenticated workflow submission.
- `GET /api/creative/comfyui/history/<prompt_id>`: authenticated bounded history lookup.
- `POST /api/creative/browser/inspect`: authenticated read-only page inspection; disabled by default.
- `POST /api/creative/founder/proposals`: founder-token-gated proposal storage, never automatic apply.

## Deployment notes

The frontend is static and adds no Python dependencies. The server-side integrations are opt-in and rely on existing Python standard library plus optional Playwright. No API keys, accounts, paid services or cloud resources are created by this change. Run `python -m pytest -q` before merging and manually test each integration in the environment where its provider is configured.
