# Elyntro capability audit and measurable requirements

**Research date:** 2026-10-25
**Scope:** Public product/API documentation and the implementation currently present in this repository. This is a capability map, not a model leaderboard or a claim of parity.

## Source notes

- OpenAI, [Using tools](https://developers.openai.com/api/docs/guides/tools): documents built-in web/file search and connections to remote MCP servers for the API. This describes API capabilities, not every ChatGPT consumer feature. OpenAI's ChatGPT/help pages returned HTTP 403 during this review.
- Anthropic, [Claude product overview](https://claude.com/product/overview): describes task handoff and creation of decks, spreadsheets, and prototypes across apps/files. [Tool use documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) describes connecting Claude to external tools and APIs. Product-level task claims are distinct from the model's tool-use interface.
- Google, [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling) and [video understanding](https://ai.google.dev/gemini-api/docs/video-understanding): document structured tool calls and video input in the Gemini API. These require an application to implement the call loop and handle the media.
- Microsoft, [Copilot for individuals](https://www.microsoft.com/en-us/microsoft-copilot/solutions/individuals): describes Copilot integration with Microsoft 365 apps such as Word, Excel, and PowerPoint. Microsoft product/support pages were intermittently blocked during this review; no inference is made from those blocks.
- Perplexity, [Search API quickstart](https://docs.perplexity.ai/docs/search/quickstart): describes ranked web results, domain filters, multiple queries, and content extraction. This is a developer API, not evidence that its base model alone performs those actions. Perplexity consumer help returned HTTP 403.
- Artificial Analysis, [LLM leaderboard](https://artificialanalysis.ai/leaderboards/models): compares model intelligence, price, throughput, time-to-first-token, and context size. The leaderboard is dynamic; this review did not capture a reproducible dated score export. LMSYS Arena returned HTTP 403 from this environment.

The product pages above describe available product surfaces, not controlled head-to-head results. API documentation describes callable interfaces, not autonomous task success. A competitive claim requires versioned models, prompts, tool access, costs, and repeated evaluation on the same tasks.

## Comparative capability map

| Area | Publicly documented reference capability | What Elyntro currently demonstrates |
| --- | --- | --- |
| Reasoning and problem solving | Vendors expose general-purpose models and task-oriented products; public product pages are not comparable controlled reasoning tests. | A single configurable Groq chat model for direct responses/analysis; no model benchmark or router. The planner is deterministic rules, not model reasoning. |
| Instructions and context | Product experiences provide conversation and work context; API context behavior must be measured per model and configuration. | The UI sends local conversation history, but the direct `/api/execute` chat path calls `_groq_chat` with only the current goal. No durable conversation store is wired. |
| Writing, coding, and files | Claude describes work artifacts; OpenAI documents file search tools; Microsoft integrates Copilot into Office applications. | Deterministic HTML/Markdown templates and Groq-produced analysis. No upload, document parsing, code editing, execution, or debugging workflow is connected to the public task route. |
| Web research and sources | OpenAI API tools document web search; Claude exposes search/tool surfaces; Perplexity Search API provides ranked results and extraction. | Research is now routed through the existing search/fetch helpers and passes excerpts to Groq. The live DuckDuckGo endpoint returned an HTTP 202 human-verification challenge during this audit, so live retrieval was not demonstrated. |
| Tool use and multi-step tasks | OpenAI, Anthropic, and Google document tool/function-calling interfaces; product services may add their own orchestration. | `CognitiveLoop` and `TaskExecutor` execute registered handlers in bounded plans; multi-step success, persistence, and failure are covered by tests. Tool selection remains a rule-based capability mapping. |
| Planning, recovery, verification | Product interfaces may manage tasks, but public marketing does not establish a universal recovery/verification guarantee. | Each task step records events/results, retries are bounded, external effects have journal controls in the engine, and registered validators determine terminal status. No general repair agent or proof of factual truth. |
| Memory and personalization | Project/workspace memory is product-specific and must not be attributed to a model without evidence. | `MemoryStore` exists in the engine but is not passed to the active `CognitiveLoop`; no user identity or durable conversation memory is connected. |
| Image, audio, and video | Gemini documents video and multimodal API input; other vendors offer product/API media features, but exact access depends on model, plan, and endpoint. | No media upload, image/audio/video processing, or generation path is implemented in the app. |
| Artifact creation | Claude documents artifacts; Copilot connects creation to Office apps; other products expose file/code tools through their services/APIs. | A task can create a deterministic HTML/Markdown template and persist/download it. This is not a general-purpose app builder or document-generation suite. |
| Interface and accessibility | Competitor products offer mature conversation/workspace UIs; documentation alone does not quantify their usability. | One text composer with suggestions, task summary, and artifact download. No file attachment, project navigation, persistent history, or multimodal controls. No accessibility audit is recorded. |
| Reliability, privacy, and cost | Artificial Analysis publishes model-level quality, speed, and cost measurements; privacy and retention depend on provider/product terms and configuration. | One optional Groq provider, environment-based secret, SQLite, no measured latency/cost dashboard, no provider failover, and no process sandbox. Render has no persistent disk configured in `render.yaml`. |

## Requirements and acceptance evidence

These are proposed gates, not achieved scores:

1. **Task integrity:** on deterministic success/failure/retry fixtures, every task must reach the correct terminal state; zero failed or incomplete fixtures may be reported as succeeded.
2. **Step traceability:** every planned step must have a started event and either a committed result or a typed failure; verify event/task consistency on restart.
3. **Recovery:** retries must not exceed the step's configured budget. Steps with externally visible side effects must not be blindly replayed after an ambiguous outcome.
4. **Research:** store query, URL, title, retrieved excerpt, retrieval error, and synthesis references. A test oracle must verify that each cited URL came from retrieved evidence. Mark source review separately from independent fact verification.
5. **File/coding work:** use a fixed corpus, isolated execution, expected outputs/tests, resource limits, and explicit security tests. Do not execute generated code in the web process.
6. **Memory:** test relevant recall, isolation by user/project, deletion, expiry, and behavior after restart; measure both useful recall and false retrieval.
7. **Multimodal:** each modality needs its own fixtures and objective metrics (for example transcription error for speech and labeled question-answer accuracy for image/video); do not expose controls before the path works.
8. **Model comparison:** use the same dated task set, system instructions, tools, retry policy, and scoring rubric across systems. Record model/version, provider, exact configuration, latency, token usage/cost, failures, and blinded quality score. Compare against a captured Artificial Analysis snapshot, not a moving page.
9. **Security:** exercise SSRF rejection, prompt injection in retrieved documents, secret redaction, request size/time limits, permissions, and sandbox escape attempts before accepting untrusted tool/code execution. The current public-page fetch resolves and rejects non-global addresses, connects to a validated socket address directly, preserves the URL hostname for HTTPS SNI/certificate verification and the HTTP `Host` header, and revalidates every redirect. This reduces DNS-rebinding and redirect risk but is not a complete SSRF boundary or a network sandbox.

Suggested minimum benchmark slices: 50 fixed cases each for instruction following, reasoning, coding, research, document analysis, planning/tool use, artifact creation, and recovery. Report raw pass rate, criterion pass rate, false-success rate, p50/p95 latency, and actual provider cost. No rival comparison score has been produced in this repository yet.

## Maturity and priority

- **Functional prototype:** current demonstrated baseline for deterministic multi-step task execution and artifact persistence.
- **Reliable system:** not yet reached; live research currently hits an external anti-automation challenge, conversation/memory persistence is absent, and durable Render storage is unconfigured.
- **Competitive product:** not evaluated; missing file workflows, model routing/failover, measurable benchmark results, and mature product UX.
- **Task-specific advantage:** no evidence yet; establish only after paired, reproducible test results.

Highest-priority next technical work: continue testing the configured search provider and retrieval controls, then wire conversation/project identity and memory, and later create an isolated code/artifact worker. Provider selection and paid services require cost/credential decisions; this audit did not create spend or request secrets.

## Reproduction observed

The canonical engine is packaged from `elyntro-engine-v6.2.0` and installed as a local distribution; the import no longer depends on `PYTHONPATH`. `requirements.lock` pins the production dependency graph, and `requirements-dev.lock` additionally pins the test dependencies. Both locks were resolved with pip-tools 7.5.2 under Python 3.14 and use a relative engine path so they work from a clean clone. The suite count is recorded only after a clean locked install is verified.