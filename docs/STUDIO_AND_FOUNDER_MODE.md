# Elyntro Studio

Open `/static/studio.html`. Three.js/OrbitControls provide real-time WebGL rendering, orbit/zoom camera control, object focus, primitive materials and JSON export. Prompt-to-scene conversion currently uses deterministic templates, not an LLM.

## ComfyUI
Set server variables `ELYNTRO_COMFYUI_URL`, `ELYNTRO_COMFYUI_WORKFLOW` (JSON file within this repository), `ELYNTRO_COMFYUI_PROMPT_NODE`, optionally `ELYNTRO_COMFYUI_PROMPT_INPUT` (default `text`), and a long random `ELYNTRO_FOUNDER_TOKEN`. Only the configured string input is changed and sent to `/prompt`; a queue ID does not prove completion. No models, GPU or ComfyUI server are bundled.

## Playwright
Optional: `pip install -r requirements-browser.txt && python -m playwright install chromium`. Founder token required. Sessions are headless, isolated, max three, 30-minute TTL. Downloads disabled, non-public requests blocked, only click/fill/scroll/back/forward actions exposed. This is not a full network sandbox.

## Founder Mode / self-development
Configure `ELYNTRO_FOUNDER_TOKEN` in the secret manager; never commit/share it. Shared capability token, not a full user/role system. Proposals support approve/reject, but are held in memory; approval means manual review only and never writes files, executes code, commits, or deploys. True self-modification requires an isolated worker, constrained workspace, resource/network limits, audit logs, required checks, human review and rollback.

Render Free does not include Chromium, ComfyUI, GPU or persistent session storage. Three.js loads from a pinned CDN. This change provisions no paid resources.