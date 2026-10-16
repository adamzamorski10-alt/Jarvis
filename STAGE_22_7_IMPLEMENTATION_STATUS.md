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

- GitHub Actions **Core Tests** run #383 completed successfully for commit `cf554c082acf98ed6a3f0de787b48cae1d1733cf`.
- Result: **2964 passed, 18 skipped, 95 warnings** in 311.43 seconds.
- Run: https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38704716174
- The preceding run #379 failed on the test fixture's unsupported `httpcore.Response(stream=...)` constructor argument. That fixture was corrected in commit `cf554c082acf98ed6a3f0de787b48cae1d1733cf`; the subsequent full core run is green.

## Mandatory unresolved gates

1. Verify the actual transport/TLS path against the installed HTTPX/HTTPCore versions, including TLS SNI, certificate hostname validation, and the original Host header on a real request. The current adapter-level unit test does **not** prove a real TLS handshake or certificate validation.
2. Add or confirm adversarial transport tests for IPv4/IPv6 mixed answers, special-use/reserved IP ranges, timeout behavior, malformed/oversized headers, and new-connection behavior. The URL-policy tests cover representative private, loopback, link-local, multicast, unspecified, and mixed public/private answers, but the full transport-level matrix remains a gate.
3. Finish review of orchestration, bounded source selection, deduplication, safe partial-result statuses, and audit integration.
4. Implement synthesis only after retrieval security gates pass; keep model tools disabled and validate every citation against retrieved source evidence.
5. Integrate explicit research intent through existing permission/readiness controls without changing ordinary question routing.
6. Run the full hostile review and final regression gate before closing Stage 22.7.

The DNS preflight helper alone is not an SSRF boundary. Production fetching must use the connection-pinning transport, and any failure of that transport must fail closed. Green unit/core CI is necessary but does not by itself close the TLS/SNI or final security gates.


## Stage 22.7C — Synthesis adapter

- Added a provider-neutral synthesis adapter using the configured AI provider's non-tool chat call.
- Retrieved page content and metadata are explicitly treated as untrusted input; the synthesis call receives no tools.
- Output is schema-validated. Every citation source ID must belong to the current retrieval bundle, and every cited excerpt must exactly occur in retrieved source text; offsets are computed by the application.
- Empty evidence skips the provider call and returns an unsynthesized response with an explicit limitation.
- Added focused tests for valid citations, fabricated excerpts, unknown source IDs, no-evidence behavior, unexpected tool calls, and provider timeouts.
- Core CI passed on commit `724c3adeafb6acd053d75da3b8bf2e771d421d6e`: 2983 tests passed and 18 were skipped (run #436). This evidence predates the Operator integration.

## Stage 22.7D — Operator integration (verification in progress)

- Explicit research phrasing is routed through the existing `web.search` service capability; ordinary web-search requests keep the existing search path.
- The integration calls the research runner only from the existing service execution path, after the application's readiness and permission checks. It does not create a new permission bypass or expose synthesis tools.
- Retrieval resources are created on demand and closed after each research run. Research-provider setup failures are isolated from ordinary chat/search.
- Added tests for English/Polish explicit research phrasing, ordinary-search non-matching, and invocation through the existing permission-checked execution path.
- Commit `fa5d26419e061b7c2f433ee1c0ede051df17c694` has one successful full core run (#38885006470: 2983 passed, 18 skipped). A concurrent duplicate full run failed on the pre-existing recovery/cancellation race test; a rerun of the failed job is in progress. Do not treat the final integration gate as closed until the rerun and subsequent latest-SHA CI are verified.

## Remaining mandatory gates

1. Confirm the rerun and run CI for the final documentation/code SHA.
2. Review permission, readiness, error handling, and lifecycle behavior in the full integration context.
3. Complete the hostile security review of transport/TLS/SNI, DNS rebinding, redirects, byte/time budgets, prompt injection, and citation integrity.
4. Run the focused research tests and a reliable full regression gate; investigate recurring flakes rather than dismissing them.
5. Update this status only from verified final evidence and keep PR #6 draft until the stage is accepted.
