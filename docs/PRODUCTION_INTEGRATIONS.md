# Production integrations and setup

## Shared accounts and cross-device sync
Configure ELYNTRO_DATABASE_URL in the Render web service with the Internal Database URL from the Render PostgreSQL resource. Never paste it into chat or commit it. Set ELYNTRO_REGISTRATION_ENABLED=true only after that connection is working. On startup, Elyntro creates account, session and sync tables. Passwords use scrypt; sessions are server-side and sent as HttpOnly cookies. The UI syncs conversation records with optimistic versions and keeps guest histories local unless the user explicitly confirms import.

Persistence warning: the currently attached Render PostgreSQL resource is on the Free plan and is scheduled to expire on 2026-11-25. For durable accounts, move to a non-expiring managed database or a provider with an appropriate persistent free tier before that date.

## Founder Mode
Set ELYNTRO_FOUNDER_TOKEN to a long random secret in Render. The sidebar Founder Mode panel validates it and stores it only in the current tab's session storage. Prefix a request with [245jg&]. The app creates a proposal; it does not execute, merge, or deploy proposed code automatically. Optional GitHub self-development requires a least-privilege GitHub token and explicit ELYNTRO_SELFDEV_ENABLED=true.

## Private browser worker
The worker is built by CI from Dockerfile.browser-worker, but it must run as a private Render service. Do not deploy it as an internet-facing public web service. Create a private service using the Dockerfile, port 10000, with ELYNTRO_BROWSER_WORKER_TOKEN (same long random secret on worker and Elyntro) and ELYNTRO_BROWSER_ALLOWLIST (comma-separated permitted public domains, e.g. docs.python.org,github.com). Set ELYNTRO_BROWSER_WORKER_URL to the private internal hostname at port 10000 and ELYNTRO_PLAYWRIGHT_ENABLED=true only after testing /healthz from inside the private network. A private service may incur a charge; obtain approval before provisioning a paid plan. Browser actions are intentionally limited and destructive/transactional buttons are blocked.

## ComfyUI
Set COMFYUI_BASE_URL to the root URL of a running ComfyUI instance reachable from Elyntro. It must expose /prompt, /history, /view and /object_info/CheckpointLoaderSimple. Elyntro discovers installed checkpoints through /api/creative/comfyui/models; a GPU-backed ComfyUI host is not provisioned by this repository. Keep provider API keys in the provider's secret settings, not in the browser. A hosted GPU endpoint may incur usage charges.

## Smoke tests after configuration
1. GET /api/identity/status reports database_configured: true.
2. Create a test account, sign out, sign in, and confirm the CSRF cookie is set.
3. Save a conversation, open Elyntro on a second device, sign in, and verify the same conversation appears.
4. Validate Founder Mode and submit a harmless prefixed request; confirm it creates a proposal without applying it.
5. Verify browser worker and ComfyUI status, then test a real browser inspection and image generation.
