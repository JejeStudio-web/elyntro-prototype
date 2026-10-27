# Elyntro API v1 — first implementation

This document describes the first implemented provider-neutral chat API. It is a first integration step, not a claim that Elyntro's model is already trained or that every planned API feature exists.

## Endpoints

### `GET /api/v1/models`

Lists every configured model alias, its provider and supported capabilities. It never returns API keys or credentials.

### `POST /api/v1/chat/completions`

Accepts a bounded subset of the OpenAI Chat Completions request shape:
- `messages`: hasta 20 mensajes con roles `system`, `user` o `assistant`; el contenido puede ser texto o partes de texto e imágenes base64 PNG/JPEG/WebP en mensajes de usuario.
- `model`: optional `elyntro-auto`, `elyntro-default`, a public alias from `/models`, or its configured upstream model ID. Other IDs are rejected.
- `max_tokens`: optional integer from 1 to 4096.
- `temperature`: optional number from 0 to 2.
- `response_format`: optional `{"type":"json_object"}`.
- `stream`: `true` devuelve eventos Server-Sent Events (`text/event-stream`) del proveedor configurado; `false` devuelve JSON normal.

The response uses a Chat Completions-compatible envelope and adds an `elyntro` metadata object. Provider token usage is passed through when supplied; otherwise usage is reported as zero rather than guessed.

## Multi-model routing (recommended)

Set `ELYNTRO_MODEL_POOL` as a JSON array in the hosting provider's server-side environment. Each entry has a public alias (`id`), provider label, upstream `model`, HTTPS `base_url`, and optional `api_key_env` naming the server environment variable containing that provider's key. Set `primary: true` for the default model. At most eight entries are accepted; aliases must be unique. Only HTTPS upstreams are allowed, except localhost/127.0.0.1 for local development. A configured key variable must exist for that entry to be listed.

Example structure (replace values with models and secrets available to your account):

```json
[
  {"id":"fast","provider":"groq","model":"llama-3.3-70b-versatile","base_url":"https://api.groq.com/openai/v1","api_key_env":"GROQ_API_KEY","primary":true},
  {"id":"reasoner","provider":"openai","model":"gpt-4.1-mini","base_url":"https://api.openai.com/v1","api_key_env":"OPENAI_API_KEY"}
]
```

Use `GET /api/v1/models` to discover aliases. Send `"model":"fast"` or `"model":"reasoner"` to select one; `elyntro-auto` and `elyntro-default` choose the primary entry. The pool is configuration-driven and does not automatically grant provider access, free quota, or cross-provider fallback. Configure provider credentials directly in Render's environment settings; never put them in the repository or frontend.

## Configure the model backend

Choose one backend with `ELYNTRO_MODEL_PROVIDER`. Do not commit keys to source control.

### Groq (default, backward compatible)

- `ELYNTRO_MODEL_PROVIDER=groq` (optional; this is the default)
- `GROQ_API_KEY` required
- `GROQ_MODEL` optional; defaults to `openai/gpt-oss-20b`

### OpenAI

- `ELYNTRO_MODEL_PROVIDER=openai`
- `OPENAI_API_KEY` required
- `OPENAI_MODEL` optional; defaults to `gpt-4.1-mini`

### Local Ollama or another self-hosted OpenAI-compatible endpoint

For Ollama's OpenAI-compatible endpoint:
- `ELYNTRO_MODEL_PROVIDER=ollama`
- `ELYNTRO_MODEL_BASE_URL=http://127.0.0.1:11434/v1` (change to the reachable private endpoint in deployment)
- `ELYNTRO_MODEL=your-installed-model-name`
- `ELYNTRO_MODEL_API_KEY` is optional for a local endpoint that does not require authentication.

For another OpenAI-compatible service:
- `ELYNTRO_MODEL_PROVIDER=openai-compatible`
- `ELYNTRO_MODEL_BASE_URL` required
- `ELYNTRO_MODEL` required
- `ELYNTRO_MODEL_API_KEY` optional, depending on the service.

Do not expose an inference server to the public internet without authentication, network controls, and request limits. Render's ordinary free web service should not be assumed to provide GPU inference; a self-hosted model may require a separate machine or model-serving service.

## Example request

```bash
curl -X POST https://YOUR-ELYNTRO-HOST/api/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "elyntro-default",
    "messages": [{"role": "user", "content": "Explica qué es la fotosíntesis"}],
    "max_tokens": 500,
    "temperature": 0.2
  }'
```

## API key authentication

The versioned endpoints support a deployment-wide API key. Configure the secret
as `ELYNTRO_API_KEY` in the hosting provider's environment settings; never commit
it to the repository or send it to a browser application.

When configured, the following endpoints require either
`Authorization: Bearer YOUR_KEY` or `X-Elyntro-API-Key: YOUR_KEY`:
- `GET /api/v1/models`
- `POST /api/v1/chat/completions`
- `POST /api/v1/agent/execute`

Missing or invalid keys receive HTTP 401 with error code `invalid_api_key`.
Comparison uses a constant-time HMAC comparison. If `ELYNTRO_API_KEY` is unset,
the API remains in backward-compatible public mode; this is intended for local
development only. Set the variable in production before exposing these endpoints.
This is a shared deployment key, not per-user identity, key issuance, revocation,
or per-account quota management. Do not embed it in frontend JavaScript.

## Security and current limitations

- Existing global request-size limits apply, and this endpoint has a per-IP request limit.
- El endpoint acepta texto o imágenes base64 acotadas; audio y vídeo todavía no están soportados.
- The endpoint uses the backend configured by the server; clients cannot select arbitrary upstream models or pass upstream URLs.
- Provider credentials are read from environment variables and are not returned by the API.
- Audio/vídeo, cuentas por usuario, cuotas por cuenta, facturación persistente y emisión/revocación de claves personales siguen pendientes.
- The API is an Elyntro-controlled gateway; it does not make external model weights proprietary to Elyntro.
- Existing application calls now use the same configured provider adapter for ordinary chat, model-assisted artifact creation, and model-assisted routing.


## Streaming and image inputs

`POST /api/v1/chat/completions` supports `stream: true` via upstream
Server-Sent Events when the selected provider supports streaming. Clients must
read `data:` events until `[DONE]`; an error occurring after the stream starts
is delivered as an SSE error event.

User messages may contain text parts and image parts in OpenAI-compatible
`image_url` format, but image URLs must be bounded base64 data URLs for PNG,
JPEG, or WebP. Remote URLs are rejected. Image support depends on the configured
model/provider; this API cannot add vision to a text-only model. Audio and video
input are not supported by this version.

## Recursos externos conectados al runtime del agente

El runtime de tareas mantiene una lista de herramientas permitidas y un máximo de cinco pasos por petición. Además de cálculo seguro, búsqueda web configurada y lectura acotada de páginas públicas, ahora puede consultar:
- `wikipedia_search`: API de Wikipedia en español, sin clave.
- `crossref_search`: metadatos bibliográficos y DOI mediante Crossref, sin clave.
- `arxiv_search`: artículos científicos de arXiv, sin clave.

Estas herramientas consultan hosts fijos, limitan consulta, tiempo y tamaño de respuesta y devuelven título, URL y extracto para que Elyntro pueda mostrar procedencia. El contenido externo es evidencia no confiable, nunca instrucciones. La búsqueda web general sigue dependiendo de `ELYNTRO_SEARCH_PROVIDER` y de la configuración del proveedor elegido; Tavily requiere `TAVILY_API_KEY`, mientras que el adaptador DuckDuckGo no requiere esa clave. Los conectores públicos no necesitan crear cuentas ni activar servicios de pago.

## Límites de esta integración

- La conexión a una API pública no garantiza disponibilidad permanente ni exactitud del resultado.
- Los conectores devuelven fuentes; no prueban por sí solos que una afirmación sea verdadera.
- Las credenciales de modelos y de búsqueda, si se usan, se configuran solo en el servidor.
- No se han añadido claves, servicios de pago ni nuevas cuentas externas.
