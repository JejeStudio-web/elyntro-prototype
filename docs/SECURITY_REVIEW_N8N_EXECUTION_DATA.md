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
