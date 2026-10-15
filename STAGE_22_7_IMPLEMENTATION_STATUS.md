# Stage 22.7 — Implementation Status

**Current branch:** `stage-22/research-source-synthesis`  
**Status:** In progress — not approved for merge or production use.

## Completed in the current implementation slice

- Added immutable Pydantic request, source, citation, and response contracts.
- Added source-ID and citation-reference validation.
- Added URL-shape and DNS preflight validation that rejects IP literals, non-default ports, userinfo, malformed hostnames, and any non-public DNS answer.
- Added a custom HTTPCore network backend that resolves on each new connection and connects to the validated IP while keeping the original request hostname for HTTP Host and TLS verification.
- Added a bounded HTML extractor using the standard library.
- Added a bounded public-page fetcher with redirects disabled, fixed request headers, finite timeouts, content-type restrictions, identity-only encoding, streamed body-size enforcement, and concurrency limits.
- Added deterministic tests for contracts, URL/DNS policy, HTML extraction, fetch limits, redirects, and the connection-pinning decision.

## Mandatory unresolved gates

1. Run focused tests and the full core regression suite in CI; no successful test run is recorded for this implementation yet.
2. Review the custom HTTPX/HTTPCore adapter against the exact installed dependency versions and verify TLS hostname validation, request host preservation, and no second DNS lookup before connection.
3. Add adversarial transport tests for DNS rebinding, IPv4/IPv6 mixed answers, connection failures, malformed/oversized headers, timeout behavior, and redirects.
4. Implement and test the research orchestration layer, bounded source selection, deduplication, safe partial-result statuses, and audit integration.
5. Implement synthesis only after retrieval gates pass; keep model tools disabled and validate every citation against retrieved source evidence.
6. Integrate explicit research intent through existing permission/readiness controls without changing ordinary question routing.
7. Run the full hostile review and final regression gate before closing Stage 22.7.

The DNS preflight helper alone is not an SSRF boundary. Production fetching must use the connection-pinning transport, and any failure of that transport must fail closed.
