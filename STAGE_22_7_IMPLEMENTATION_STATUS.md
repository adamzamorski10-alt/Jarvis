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
