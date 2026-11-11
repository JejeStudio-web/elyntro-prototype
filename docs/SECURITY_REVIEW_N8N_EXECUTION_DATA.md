# Security review: retained n8n execution data

Date: 2026-11-06  
Scope: read-only inspection of the active operational chat workflow and an existing historical execution.  
Branch: `work/security-review-execution-logs-2026-11-06`

## Finding 1 — Sensitive request data is retained in execution records

**Severity: High until retention and redaction settings are verified.**

A historical execution's stored node data included the incoming HTTP request headers, including an Authorization bearer token, and subsequent node data included authenticated identity and conversation-history content. The token and personal content are intentionally not reproduced in this document or in commit messages.

This establishes that execution records can retain authentication material and user conversation data. It does not establish whether the token remains valid now, or who can access the execution records beyond the permissions visible to the connected tooling.

### Required remediation (not applied)

1. Inspect the n8n instance's execution-data saving and retention configuration, including successful and failed runs, before changing any settings.
2. Prefer the least data necessary for diagnosis; prevent raw authorization headers and unnecessary personal conversation content from being retained in execution history where the platform permits.
3. Verify the available execution-data redaction/pruning controls and their effect with a non-sensitive test in an isolated workflow.
4. Review access to historical execution records. Do not export or copy their payloads into GitHub issues, commits, or documentation.
5. If any credential is confirmed to remain valid and exposed to an unauthorized party, follow the provider's rotation/revocation procedure. Do not revoke credentials speculatively in a way that could interrupt service.

No production workflow, instance setting, credential, permission, or historical execution was changed or deleted during this review.

## Finding 2 — Chat response can claim an artifact without an artifact

The inspected historical chat response asserted that a PDF was available through a sandbox-style link. The execution evidence did not show a generated or attached PDF artifact. The model prompt already prohibits unverified file-creation claims, so prompt wording alone has not provided a sufficient guarantee.

### Required remediation (not applied)

1. Treat file creation as a tool-backed operation, not a language-model assertion.
2. Only return a file link after a trusted artifact-generation step produces a real artifact and the application verifies its existence and permitted access.
3. Add a regression test for requests such as “create a PDF” when no artifact tool is connected: the API must return an honest limitation and must not emit a fabricated sandbox link.
4. Verify the complete UI → authenticated workflow → model/tool → persistence → response path in an isolated test before changing production.

## Verification status

- Confirmed: the active operational workflow currently uses its own LangChain Agent connected to the Groq model; the separately saved `Elyntro_agent` is not in that workflow graph.
- Confirmed: a historical execution record contains sensitive request/context data and a response that claims an unverified PDF link.
- Not verified: current instance-wide execution-retention/redaction settings, access scope of historical records, current validity of the historical token, or end-to-end behavior on the current deployment.
- Not performed: credential rotation, log deletion, workflow changes, production changes, or live model calls.

## Next safe action

Inspect n8n execution retention/redaction controls without changing them, then create a non-sensitive isolated test that checks whether authorization headers and conversation content are persisted. Separately add an automated response-contract test for artifact claims. Keep the active production workflow and saved `Elyntro_agent` unchanged until the isolated tests pass and the change is reviewed.

## Follow-up verification — 2026-11-09

Read-only verification was repeated against the connected n8n instance.

- The workflow `Elyntro — Operational Chat API v1` remains active (workflow ID `x5QQXNZXF22NuOyv`). Its current graph still extracts the bearer token into the `token` field in `Normalize request`, then later nodes reference that field for Supabase requests. This keeps the token in upstream execution data unless execution-data redaction/retention prevents it.
- The workflow execution metadata for both the isolated chat-validation suite and orchestration suite reports `redaction.production=false` and `redaction.manual=false`. This is evidence about those recorded executions; it does not by itself reveal every instance-wide retention setting.
- Isolated execution `42` of `Elyntro — Chat Validation Sandbox` completed successfully: 6/6 pure validation cases passed.
- Isolated execution `43` of `Elyntro — Orchestration & Policy Test Suite` completed successfully: 10/10 deterministic simulation cases passed. The suite's own output states that no external calls, credentials, or production data were used.
- The GitHub repository is accessible again through the connected API, and the existing review branch is present. Repository metadata reports default branch `main`; no production code or workflow was changed during this follow-up.

### Updated assessment

The security finding remains open. The tests above validate deterministic simulation logic only; they do not demonstrate JWT verification, Supabase row-level security, provider availability, or real execution-data redaction. Next safe action: test the least-data approach in an inactive isolated workflow using a synthetic bearer token, then inspect saved execution data to confirm the token and message body are absent. Do not alter production settings or rotate credentials until the effect and current credential exposure are established.


## Follow-up verification — 2026-11-10

A new read-only inspection and isolated test run was completed through the connected n8n tools.

- Confirmed again that `Elyntro — Operational Chat API v1` is active and its draft matches the active version. The `Normalize request` node extracts the bearer token into a named `token` field, and downstream HTTP Request nodes reference that value for Supabase authentication.
- Confirmed that the saved `Elyntro_agent` remains published as a separate agent. The operational chat workflow graph inspected in this run still uses its own LangChain Agent node and Groq model; it does not contain a Message an Agent node connected to `Elyntro_agent`.
- Re-ran the deterministic suites in isolation. Execution `44`, `Elyntro — Orchestration & Policy Test Suite`, returned `success` and its aggregate output reported 10/10 cases passed, 0 failed. Its output explicitly records no external calls, no credentials used, and no production data touched.
- Execution `45`, `Elyntro — Chat Validation Sandbox`, returned `success`; its output reported 6/6 validation cases passed and explicitly describes them as pure simulations without token verification, network calls, database writes, or external service calls.
- The runtime metadata for both test executions reports `redaction.production=false` and `redaction.manual=false`. This is a confirmed configuration signal in these execution records, but it does not alone establish the full instance-wide retention policy or who can access past executions.

### Status and next action

No workflow, credential, permission, production setting, or historical execution was modified or deleted. The security finding remains open; the passing suites do not test actual secret redaction or authentication. The next safe step is to inspect the available redaction/retention controls and test their behavior with synthetic data in an inactive isolated workflow before considering any production change. The artifact-claim regression test is also still pending.


## Follow-up verification — 2026-11-11

The repository and existing security-review branch are accessible again through the connected GitHub API. Repository metadata confirms `JejeStudio-web/elyntro-prototype` is not archived and the default branch is `main`.

### Isolated retention change

Updated only the inactive workflow `Elyntro — Chat Validation Sandbox` (ID `UN5Lds4OrheBV8b2`) to minimize stored execution data:

- `saveManualExecutions=false`
- `saveDataSuccessExecution=none`
- `saveDataErrorExecution=none`

n8n returned `appliedOperations=1`, the resulting settings matched these values, and no validation warnings were reported. This is an isolated configuration change; the active operational workflow, credentials, permissions, and production settings were not changed.

### Execution blocker

Attempted a manual run of the inactive sandbox after the change. n8n returned: `Your trial has ended`. No execution ID was created, so the test did not run. This is a concrete platform access/billing blocker; no upgrade or payment was attempted. The changed sandbox settings are saved, but their effect on actual execution persistence could not be runtime-verified.

### Status and next action

- Confirmed: GitHub repository and existing review branch are accessible.
- Confirmed: isolated sandbox execution-retention settings were saved successfully.
- Blocked: n8n cannot execute workflows because the trial has ended.
- Still open: historical operational execution data may contain bearer tokens and conversation content; current validity/access scope is unknown.
- Not done: no production workflow/instance settings changed, no credentials rotated, no historical data deleted, no deployment performed.

Next safe action: do not pay or enable a trial. Preserve the current evidence, check whether the user has an already-available no-cost local n8n/runtime option, and continue code-level regression work in the repository. Production remediation remains blocked until a safe test environment is available.
