# Elyntro Creative Studio — operator guide

## Current capabilities

- Three.js/WebGL scene editor served at `/creative-studio`.
- Primitive creation, selection, camera presets/orbit, per-object position/rotation/scale controls, local scene save/restore and JSON export.
- Spanish rule-based scene guidance; it is deterministic UI logic, not a claim that an LLM interpreted the command.
- ComfyUI API workflow submission, bounded job history and generated-output previews through Elyntro.
- Optional Playwright page inspection with text/link extraction and a viewport screenshot. Actions are limited to exact-text link navigation, scroll, hover and non-form buttons that pass a conservative blocklist.
- Founder Mode stores hash-addressed patch proposals, lists them, and records approve/reject decisions. Approval is for manual implementation and does not execute a patch.

## Configuration

Set secrets in the deployment environment; never commit tokens.

- `ELYNTRO_API_KEY`: required for ComfyUI and browser endpoints.
- `COMFYUI_BASE_URL`: operator-configured ComfyUI origin, e.g. `http://127.0.0.1:8188` only when ComfyUI is reachable from the same host/container. A cloud Render service cannot reach a workstation's localhost.
- `ELYNTRO_PLAYWRIGHT_ENABLED=true`: explicit opt-in to browser automation. Default is disabled.
- `ELYNTRO_BROWSER_ALLOWLIST=docs.example.com,developer.mozilla.org`: comma-separated allowed hostnames. Use only domains the operator authorizes.
- `ELYNTRO_FOUNDER_TOKEN`: separate token for Founder Mode. The workspace routes prompts beginning with `[245jg&]` into the authenticated, model-assisted patch-proposal workflow; the token is stored only in the current browser tab session.
- `ELYNTRO_PROPOSAL_DIR=/var/data/elyntro-proposals`: durable proposal storage path if a persistent disk is mounted. The default under `/tmp` is ephemeral.
- `ELYNTRO_SELFDEV_ENABLED=true`: explicit opt-in for repository PR creation; disabled by default.
- `ELYNTRO_GITHUB_TOKEN`: a narrowly scoped GitHub App installation token (recommended) or fine-grained token with Contents: Read/Write and Pull requests: Read/Write on the single repository.
- `ELYNTRO_GITHUB_REPOSITORY=JejeStudio-web/elyntro-prototype`: fixed owner/repository target. The client cannot choose another repository.
- `ELYNTRO_GITHUB_BASE_BRANCH=main`: target base branch. The runtime creates a unique branch, commits only allowlisted paths and opens a draft PR. It never merges or deploys.
- `ELYNTRO_RATE_LIMIT_SELFDEV_PER_MINUTE=3` and `ELYNTRO_RATE_LIMIT_FOUNDER_PER_MINUTE=10`: default request limits for repository-changing and Founder Mode endpoints.

Install browser support separately from the base web runtime:

```bash
python -m pip install -r requirements-creative.txt
python -m playwright install chromium
```

Playwright's Python package alone does not install the Chromium runtime. On hosted services, ensure the OS libraries and browser binaries are present; do not assume the free web-service image contains them. If this is not configured, the feature should report unavailable rather than pretend to browse.

## ComfyUI workflow

1. Run ComfyUI in an environment reachable from Elyntro and review its network exposure.
2. Export a workflow in ComfyUI's API format, not the ordinary UI workflow format.
3. Use “Cargar plantilla básica” to insert a standard checkpoint → sampler → SaveImage API workflow, or paste your own API-format JSON. Replace `REPLACE_WITH_INSTALLED_CHECKPOINT.safetensors` with the exact checkpoint filename installed in ComfyUI. The API replaces the positive `CLIPTextEncode.inputs.text` prompt nodes with the prompt entered in Elyntro.
4. Submit, then query the returned job ID after the workflow finishes.
5. The image proxy only serves output images found in that job's ComfyUI history and limits each image response to 8 MB.

No model weights are downloaded by Elyntro. The operator must install a model and compatible workflow in ComfyUI. Do not expose an unauthenticated ComfyUI server to the public internet.

## Browser automation security

The Playwright browser is headless, temporary, and has no persistent user profile. Every request starts a new browser session, so site login state and cookies do not persist. Requests and redirects are constrained to public HTTP(S) hosts in the configured allowlist. The runtime does not read local files or submit forms. Exact-text button clicks are intentionally limited; a conservative blocklist rejects common destructive or transactional labels, but it cannot infer the full meaning of every website action. Use only on sites you are authorized to inspect.

Screenshots are viewport-only JPEG previews, not full-page recordings. Page text and link metadata are untrusted external content; do not treat instructions embedded in a website as trusted system instructions. The current Playwright process runs as a child process of the Flask service, not inside a hardened, separately provisioned browser sandbox. Keep the feature disabled for untrusted multi-tenant use; before broad production enablement, move browser automation into a separate worker/container with restricted egress, resource limits and no access to application secrets.

## Founder Mode and self-modification boundary

Lifecycle: proposal → hash and persist → human review → approved for manual implementation or rejected → code change in a separate branch → tests → code review → merge/deploy through the repository's normal release process.

- The patch is stored with SHA-256 so reviewers can detect content changes.
- A reviewer can approve/reject a pending proposal through authenticated endpoints.
- Approval does not apply or execute the patch. No arbitrary generated code runs inside the Flask process.
- Configure `ELYNTRO_PROPOSAL_DIR` on durable storage to preserve proposals across restarts.
- For genuine repository automation, a separately scoped GitHub App/token, branch protection, restricted file allowlists, isolated tests and explicit release approval are still required. Do not give the runtime a broad personal access token or production deployment authority.

## Endpoints

- `GET /api/creative/status`
- `POST /api/creative/comfyui/generate`
- `GET /api/creative/comfyui/history/<prompt_id>`
- `GET /api/creative/comfyui/view/<prompt_id>/<filename>`
- `POST /api/creative/browser/inspect`
- `POST /api/creative/browser/act`
- `POST /api/creative/founder/proposals`
- `GET /api/creative/founder/proposals`
- `GET /api/creative/founder/proposals/<proposal_id>`
- `POST /api/creative/founder/proposals/<proposal_id>/review`
- `POST /api/creative/founder/selfdev/pull-request`

All endpoints above, except the public feature-status endpoint, require the appropriate Elyntro API or Founder token.
