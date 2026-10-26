# API security baseline

This prototype uses capability tokens for persisted task reads, bounded request bodies, and in-process rate limits.

## Task access

- Creating a task returns `task_access_token` alongside the task ID.
- Clients must send that token as `Authorization: Bearer <token>` or `X-Elyntro-Task-Token: <token>` when reading `GET /api/tasks/<task_id>` or downloading an artifact.
- The token is an HMAC-SHA256 capability scoped to the task ID. It is not a user account, login, or identity system.
- Configure `ELYNTRO_TASK_TOKEN_SECRET` with a long random secret for stable tokens across restarts. If unset, Elyntro creates a local `<database path>.task_secret` file with restrictive permissions. On ephemeral hosting, both the database and local key may be lost during replacement/redeployment; do not treat this fallback as durable production storage.

## Request limits

- JSON/API request bodies are limited to 1 MiB.
- `POST /api/chat` defaults to 20 requests per minute per client IP.
- Task-producing POST routes default to 10 requests per minute per client IP.
- Limits can be tuned with `ELYNTRO_RATE_LIMIT_CHAT_PER_MINUTE` and `ELYNTRO_RATE_LIMIT_TASKS_PER_MINUTE`.
- The rate limiter is in-process. It is useful for a single-instance prototype, but is not a distributed quota system and resets on process restart. For multi-instance production, replace it with shared storage such as Redis/Valkey and monitor provider spend.

## Remaining limitations

- Anonymous chat and task creation remain public. Capability tokens protect subsequent reads of a particular persisted task; they do not provide user accounts, user identity, billing, or per-user ownership.
- The unified execute endpoint returns the newly created task and its artifact content to the caller in that same response.
- Generated artifacts are not executed by the server. Static validation is not a guarantee of correctness or security.
- Production deployment should use a persistent database/disk or managed database and a managed secret, plus shared rate limiting and monitoring before accepting sensitive user data or meaningful public traffic.
