# Elyntro isolated Playwright worker

## Design
Separate Docker service based on the official Playwright Python image, pinned to Playwright 1.55.0. The image contains Chromium and its OS dependencies; the build also runs `python -m playwright install --with-deps chromium`. The worker runs as a non-root user.

Deploy as a Render **Private Service** using `Dockerfile.browser-worker`, preferably in the same region as Elyntro. Do not expose it publicly and do not add Elyntro API, model-provider, Founder Mode, or GitHub credentials to its environment. Configure only:
- `ELYNTRO_BROWSER_WORKER_TOKEN`: random shared secret (at least 32 random bytes), entered directly in Render
- `ELYNTRO_BROWSER_ALLOWLIST`: comma-separated domains required for browser work

Endpoints: `GET /healthz`, `POST /v1/inspect`, `POST /v1/act`. The latter two require a Bearer token. Browser contexts are ephemeral; downloads and service workers are disabled. Request body, action count, text and screenshot sizes are bounded.

## Elyntro service settings
After the app's routes proxy to the worker, configure the public service with `ELYNTRO_BROWSER_WORKER_URL` (private Render URL, no trailing slash), the same `ELYNTRO_BROWSER_WORKER_TOKEN`, and the same `ELYNTRO_BROWSER_ALLOWLIST`. Only set `ELYNTRO_PLAYWRIGHT_ENABLED=true` after private connectivity and integration tests pass. Never enable Playwright on a revision that launches Chromium inside the public Flask service.

## Security boundary
A separate container/private service reduces blast radius, but application URL checks are not a network firewall. Before untrusted browsing, enforce outbound network policy that denies loopback, RFC1918, link-local, metadata and other non-global destinations at network layer; retain allowlisting and DNS checks as defense in depth. Do not call the worker production-hardened until egress controls and live private-network tests are verified.

## Validation
1. Build succeeds with the pinned image and Chromium.
2. `/healthz` returns 200 and Chromium launches a real headless page.
3. Missing/wrong Bearer token returns 401.
4. Empty allowlist, non-allowlisted hosts, private IPs, disallowed redirects and subresources are rejected.
5. Oversized request/screenshots and destructive/form button actions are rejected.
6. Elyntro reaches worker over Render private networking; public clients cannot reach worker routes.
7. Enable the integration and deploy only after all checks pass.
