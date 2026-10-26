# Elyntro Studio: visual runtime, ComfyUI, browser bridge and Founder Mode

## Current implementation in this branch

- `/studio` serves `static/studio.html`: an interactive Three.js WebGL scene with orbit controls, resize handling, geometry switching, wireframe, animation, scale and camera reset.
- The Studio includes a ComfyUI connection panel. Its direct browser connection is diagnostic only: CORS and reachability depend on the user's environment. Sending prompts to `/prompt` requires a valid ComfyUI API workflow graph; a natural-language prompt is not itself a valid graph.
- A web-guide panel opens a URL in a separate tab and records user-authored steps. It does not inspect or control third-party pages.
- Founder Mode produces reviewable proposals. The server endpoint `POST /api/studio/founder/proposals` returns a deterministic governance scaffold, not AI-generated code and not an applied change.
- `GET /api/studio/status` reports whether integration settings exist without returning secrets.

## What is not yet implemented

### ComfyUI / open models
No model weights or ComfyUI server are hosted by this app. For real generation, run ComfyUI on a machine with compatible hardware, install a model and workflow, and connect through a private backend or a deliberately configured browser CORS policy. Do not expose ComfyUI's unauthenticated API to the public internet. A production adapter should validate workflow nodes, cap queue size and output size, authenticate requests, and poll job status without proxying arbitrary URLs.

### Playwright and visual guidance
No Playwright dependency or browser worker is bundled into the web service. The public app must not accept arbitrary URLs and execute actions against them: that would create SSRF, credential theft, cross-site action and abuse risks. The intended architecture is a separate isolated worker with:
- explicit per-session user consent and visible action preview;
- strict domain allowlists and navigation/redirect validation;
- no access to local files, private networks, cloud metadata or saved browser credentials by default;
- read-only observation by default, with confirmation for submissions, purchases, deletion or account changes;
- screenshots/DOM snapshots and a step-by-step audit trail;
- resource/time limits and disposable browser contexts.

### Self-modification / Founder Mode
The current implementation only creates proposals. A future change executor should operate on a dedicated Git feature branch, use a sandbox with no production secrets, generate a patch, run tests and static/security checks, show a diff, and require human approval before merge/deploy. Never let a public chat request directly rewrite or deploy the running application.

## Deployment
The app's `app.py` registers the Studio blueprint and serves `/studio`. The page imports Three.js from jsDelivr; if the CDN is unavailable or blocked, the 3D viewport reports that failure. No paid resources are created and no external credentials are added.
