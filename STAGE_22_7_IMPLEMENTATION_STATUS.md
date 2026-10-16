# Stage 22.7 — Implementation Status

**Current branch:** `stage-22/research-ci-fixes` (PR #6, draft)  
**Status:** In progress — not approved for merge or production use.

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
