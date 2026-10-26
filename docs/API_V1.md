# Elyntro API v1

The v1 API is an authenticated, OpenAI Chat Completions-compatible HTTP surface. It is intentionally separate from the legacy task-execution endpoints until provider-neutral execution has passed its own integration tests.

## Endpoints

- `GET /api/v1/models`: returns the single model configured for this instance.
- `POST /api/v1/chat/completions`: sends text messages to the configured provider.

Every v1 request requires `Authorization: Bearer <ELYNTRO_API_KEY>`. If `ELYNTRO_API_KEY` is unset, v1 returns HTTP 503 and remains closed. Provider credentials are never returned by the API.

## Configuration

Set these in the deployment environment / secret manager, never in source control:

| Variable | Meaning |
| --- | --- |
| `ELYNTRO_API_KEY` | Client credential required by v1 |
| `ELYNTRO_MODEL_PROVIDER` | `groq`, `openai`, `ollama`, or `openai-compatible` |
| `GROQ_API_KEY` | Provider credential when provider is `groq` |
| `GROQ_MODEL` | Optional Groq model; default `openai/gpt-oss-20b` |
| `OPENAI_API_KEY` | Provider credential when provider is `openai` |
| `OPENAI_MODEL` | Optional OpenAI model; default `gpt-4.1-mini` |
| `ELYNTRO_MODEL_API_KEY` | Credential for generic compatible providers, or optional Ollama credential |
| `ELYNTRO_MODEL_BASE_URL` | Base URL ending in `/v1` for generic compatible providers or custom Ollama URL |
| `ELYNTRO_MODEL` | Model ID for generic compatible providers |
| `OLLAMA_MODEL` | Model ID for Ollama; default `llama3.2` |

For `openai-compatible`, `ELYNTRO_MODEL_BASE_URL`, `ELYNTRO_MODEL_API_KEY`, and `ELYNTRO_MODEL` must all be configured. For `ollama`, a local endpoint can be configured with `ELYNTRO_MODEL_BASE_URL`; a provider API key is optional. The application still requires `ELYNTRO_API_KEY` for client access.

## Example

```bash
curl https://YOUR_HOST/api/v1/models \
  -H "Authorization: Bearer $ELYNTRO_API_KEY"

curl https://YOUR_HOST/api/v1/chat/completions \
  -H "Authorization: Bearer $ELYNTRO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "YOUR_CONFIGURED_MODEL",
    "messages": [{"role": "user", "content": "Responde en una frase."}],
    "max_tokens": 128,
    "temperature": 0.2
  }'
```

## Limits and explicit non-support

- JSON request body limit: 1 MB.
- Up to 20 text messages and 12,000 combined content characters.
- Output token request limit: 2,048.
- Supported roles: `system`, `user`, `assistant`.
- `response_format.type`: `text` or `json_object`.
- Streaming (`stream=true`), tool/function calls, image input, audio input/output, video, embeddings, fine-tuning, and model training are not implemented by this API surface.
- Only the model configured for the instance is exposed. It does not permit arbitrary model selection.
- Provider cost and availability depend on the provider account and its configuration. This code does not provision paid resources.
- API client authentication is a single shared bearer secret, not user accounts, per-user keys, quotas, or billing. Rotate it through the deployment secret manager.
- The v1 chat endpoint is provider-neutral. Legacy task routes still use the Groq-specific helper in parts of the execution pipeline; do not assume that changing `ELYNTRO_MODEL_PROVIDER` switches every legacy agent/task path yet.

## Verification

Run the test suite with `python -m pytest -q`. Unit tests mock the provider HTTP call, so they verify request construction and response handling without consuming provider credits. A successful unit test does not prove production credentials, DNS/network access, provider quota, or Render deployment configuration are valid.
