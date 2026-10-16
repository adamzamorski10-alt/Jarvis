# Stage 22.7 — Implementation Status

**Current branch:** `stage-22/research-ci-fixes` (PR #6, draft)  
**Status:** Implementation-complete checkpoint reached. Final hostile review found no additional blocking defect in the reviewed Stage 22.7 paths. CI verification is required for the latest documentation-only reconciliation before the stage can be formally closed. PR #6 remains Draft and is not approved for merge or production use.

## Completed in the current implementation slice

- Added immutable Pydantic request, source, citation, and response contracts.
- Added source-ID and citation-reference validation.
- Added URL-shape and DNS validation that rejects IP literals, non-default ports, URL userinfo, malformed hostnames, and any non-public DNS answer, including mixed public/private answers.
- Added a custom HTTPCore network backend that resolves on each new connection and connects to a validated IP while retaining the original request URL hostname in the HTTPX/HTTPCore request.
- Added a bounded HTML extractor using the standard library.
- Added a bounded public-page fetcher with redirects disabled, fixed request headers, finite timeouts, HTML/XHTML content-type restrictions, identity-only encoding, streamed body-size enforcement, and concurrency limits.
- Added deterministic tests for contracts, URL/DNS policy, HTML extraction, fetch limits, redirects, rejected destinations, connection retries, host-header preservation at the adapter boundary, and DNS rebinding before a new connection.
- Fixed the adapter test fixture to return a stream-compatible fake response instead of constructing an incompatible httpcore.Response.

## Verification evidence

- Latest branch HEAD at this checkpoint: `8d7b62bd4d15af4515c796c03027a14e303a5586`.
- GitHub Actions run [#38889607053](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38889607053) passed on that exact HEAD: **2983 passed, 18 skipped, 96 warnings** in 224.75 seconds.
- The paired documentation-triggered run [#38889602974](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38889602974) also completed successfully on the same SHA.
- The earlier duplicate run [#38885011813](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38885011813) passed on attempt 2 after attempt 1 hit `test_recovery_vs_cancellation_race`.
- The latest full run still reports existing warnings, including SQLAlchemy connection lifecycle warnings and a coroutine-not-awaited warning in an unrelated background-manager test. These warnings did not fail CI and should not be mistaken for Stage 22.7-specific regressions.

## Security review and remaining closure gates

Confirmed in the current code and focused tests:

- The production transport re-resolves and validates the destination when opening a new TCP connection, then connects to a validated IP while preserving the original hostname for HTTP Host and TLS verification.
- A real local TLS handshake test checks origin SNI, rejection of an untrusted certificate, successful verification for a trusted matching certificate, and rejection of a hostname mismatch.
- URL policy rejects IP literals, non-default ports, URL userinfo, malformed hostnames, and any non-public DNS answer, including mixed public/private answers.
- Fetching disables redirects and compressed content, checks HTML/XHTML content types, enforces streamed body-size limits, and applies a total request budget that includes semaphore waiting, DNS preflight, and HTTP retrieval.
- Synthesis receives no tools and validates source IDs and exact excerpts against the retrieved evidence bundle.

Still required before accepting the stage:

1. Complete a hostile end-to-end review of transport behavior, redirect/error paths, timeout/cancellation budgets, prompt injection, citation integrity, and permission/readiness integration.
2. Review error-detail exposure and lifecycle behavior in the full Operator execution context; make changes only with targeted tests.
3. Run focused research tests and retain the green full-core regression evidence for the final accepted code SHA.
4. Keep PR #6 draft and do not merge until the stage is explicitly accepted.

The DNS preflight helper alone is not an SSRF boundary. Production fetching must use the connection-pinning transport and fail closed if validation or connection setup fails. Green CI is necessary, but it is not by itself a security approval.


## Stage 22.7C — Synthesis adapter

- Added a provider-neutral synthesis adapter using the configured AI provider's non-tool chat call.
- Retrieved page content and metadata are explicitly treated as untrusted input; the synthesis call receives no tools.
- Output is schema-validated. Every citation source ID must belong to the current retrieval bundle, and every cited excerpt must exactly occur in retrieved source text; offsets are computed by the application.
- Empty evidence skips the provider call and returns an unsynthesized response with an explicit limitation.
- Added focused tests for valid citations, fabricated excerpts, unknown source IDs, no-evidence behavior, unexpected tool calls, and provider timeouts.
- Core CI passed on commit `724c3adeafb6acd053d75da3b8bf2e771d421d6e`: 2983 tests passed and 18 were skipped (run #436). This evidence predates the Operator integration.

## Stage 22.7D — Operator integration (CI verified; hostile gate open)

- Explicit research phrasing is routed through the existing `web.search` service capability; ordinary web-search requests keep the existing search path.
- The integration calls the research runner only from the existing service execution path, after the application's readiness and permission checks. It does not create a new permission bypass or expose synthesis tools.
- Retrieval resources are created on demand and closed after each research run. Research-provider setup failures are isolated from ordinary chat/search.
- Added tests for English/Polish explicit research phrasing, ordinary-search non-matching, and invocation through the existing permission-checked execution path.
- Commit `8d7b62bd4d15af4515c796c03027a14e303a5586` has a successful full core test run (#38889607053): 2983 passed, 18 skipped, 96 warnings. The earlier duplicate run also succeeded on attempt 2 (#38885011813), after its first attempt failed on the recovery/cancellation race test.

## Remaining mandatory gates

1. Complete the hostile security review of transport/TLS/SNI, DNS rebinding, redirects, byte/time budgets, prompt injection, and citation integrity.
2. Review permission, readiness, error handling, and resource lifecycle behavior in the full integration context.
3. Run focused research tests and a reliable final regression gate; investigate recurring flakes rather than dismissing them.
4. Keep PR #6 draft until the stage is accepted; Stage 22.7 remains open.


## Verification checkpoint — 2026-10-16 (latest retrieval fix)

- Current implementation commit: `1f3e3ab147819a8d38eb547f1d24e905f91725eb` (`fix: retry surplus research sources after retrieval failures`).
- Both core CI runs passed on this exact SHA: [#38938654202](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38938654202) and [#38938734342](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38938734342). Each reports the `core-tests` job completed successfully.
- Retrieval now searches a bounded surplus (up to three candidate URLs per requested source, capped at 20), fetches in bounded batches, continues to surplus candidates after failures, prefers successfully retrieved evidence, and never returns more source records than requested. Failure statuses are retained when there are too few successful replacements.
- Added focused tests for successful replacement after a failed fetch and retaining failure status when no replacement is available.
- Additional targeted security fixes already on this branch sanitize research exception details in both the research runner and outer service handler; a synthesis test checks that source prompt-injection content is framed as untrusted evidence and that tools are not passed to the provider.
- Static hostile review confirms the explicit research path still evaluates the existing `web.search` permission before invoking the research runner; the runner creates the fetcher on demand and closes it in `finally`. The pinned transport validates destinations at connection time and uses the original URL hostname for HTTP/TLS identity.
- **Not yet accepted:** the hostile end-to-end review and final documentation/CI gate must be completed on the final documentation-bearing HEAD. PR #6 remains Draft; do not merge or start Stage 23.


## Evidence-budget citation integrity fix — 2026-10-16

- During hostile review, found that citation validation checked excerpts against the full retrieved page text even when the synthesis prompt received only a prefix because of the aggregate evidence-character budget.
- Fixed the adapter to validate source IDs and excerpts against exactly the source text supplied to the model. Citations to sources omitted by the aggregate budget or excerpts outside a supplied prefix are rejected.
- Added regression test `test_synthesis_rejects_quote_from_source_text_omitted_by_evidence_budget`.
- Code commit: `c1a12fd423bf7beabbbbcd4873e4d72eabbdc77f`; test commit: `4e607f7ad5789812da814ee77fda5bef450ec9ee`.
- CI for these commits is running; do not treat the fix as verified until the latest code-bearing HEAD completes both required core CI runs successfully.


## Verification checkpoint — synthesis evidence-budget fix and CI recovery (2026-10-16)

- Current code HEAD: `73bbdd853951317f5685b8a324a341a10604dcc5` (`fix: preserve unknown-source synthesis error contract`).
- The evidence-budget citation check remains in place: citations are validated only against evidence actually supplied to the model. The follow-up compatibility fix restored the established “unknown or unretrieved source” error wording so the existing contract test remains stable; it does not weaken the evidence-budget guard.
- Full core CI passed on this exact SHA: [push run #38944568104](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38944568104) and [PR run #38944705439](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38944705439).
- Earlier runs on intermediate evidence-budget commits failed because the error wording differed from the established test contract. The wording was corrected; those intermediate failures are not treated as passing verification.
- **Stage 22.7 remains open.** Documentation-bearing HEAD still needs its own CI run, and the final hostile review must explicitly cover source-budget citation validation, bounded surplus retrieval, transport/DNS/TLS, permission/readiness checks, error sanitization, and resource cleanup. Keep PR #6 Draft and do not merge or begin Stage 23.


## Final hostile review checkpoint — 2026-10-16

Review covered the source-candidate retry loop, evidence-budget citation validation, explicit research routing, permission/readiness checks, public-page transport, and error/resource handling.

- Retrieval candidates are deduplicated and capped at `min(20, max_sources * 3)`; batches are limited by remaining required successful sources and configured concurrency. Failed candidates are retained as failure status only if successful evidence is insufficient; output never exceeds `max_sources`.
- Synthesis uses no tools. Citation source IDs and exact excerpts are checked against the evidence actually supplied to the model. The citation offsets refer back to the retrieved source text.
- Research routing uses the existing `web.search` permission evaluation; readiness is rechecked before capability execution. Research exceptions are returned with generic details in the research path.
- Fetching rejects redirects, non-HTML/compressed content, over-limit bodies, non-public/mixed DNS answers, userinfo, IP literals and non-default ports. The pinned transport re-resolves on new connections, connects to validated IPs, and retains the original hostname for Host/SNI/certificate validation. DNS work is bounded and worker slots remain occupied until uncancellable resolver threads exit.
- Tests on the code commit and the current documentation-bearing HEAD passed: **2,987 passed, 18 skipped** on each of the three successful runs for HEAD `1f53d9f909b4c4f0ebd89a3f4206c08deab8044e`: [#38946004772](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38946004772), [#38946003514](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38946003514), [#38945987779](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38945987779).
- Known warnings remain in the broader suite (including SQLAlchemy connection cleanup and a background-manager unawaited coroutine warning); they were not introduced by this research work and are not hidden by this report.
- This is a scoped code-and-test hostile review, not an external penetration test or formal security certification. No additional blocking defect was identified in the reviewed Stage 22.7 paths.
- Stage 22.7 can be marked implementation-complete only after this final documentation-bearing commit's CI gate is green. Keep PR #6 Draft and do not merge or start Stage 23 before the requested comprehensive AI Operator handoff summary.
