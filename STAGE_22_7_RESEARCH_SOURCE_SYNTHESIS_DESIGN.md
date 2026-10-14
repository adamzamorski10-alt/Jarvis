# Stage 22.7 — Research / Source Synthesis Design

**Status:** Design proposed; implementation not started  
**Branch:** `stage-22.7/research-source-synthesis-discovery`  
**Design baseline:** commit `8d941d07fec40acf8d7279ddd799c62ec1037378`

## 1. Scope

Implement one authenticated, read-only research vertical slice:

`POST /api/web/research` → validated search → bounded public HTML fetch → deterministic text extraction → no-tools AI synthesis → strict citation validation → structured response.

No browser automation, JavaScript execution, login/session support, file downloads, persistence of full page bodies, embeddings, autonomous research loops, or changes to existing `GET /api/web/search` behavior.

## 2. Proposed modules

Add a sibling package `app/research/`:

- `models.py`: strict frozen request/source/claim/response contracts.
- `fetcher.py`: controlled HTTP fetcher; validates each URL and redirect, enforces transport and byte/time budgets.
- `extractor.py`: standard-library HTMLParser extraction to bounded plain text; no HTML execution or rendering.
- `synthesis.py`: orchestration of search → fetch → source records → synthesis → citation validation.
- `citation_validator.py`: deterministic validation that all cited source IDs exist in the supplied evidence and are unique/valid.
- `__init__.py`: narrow public exports.

Keep search provider implementations and `WebSearchService` unchanged except for small shared URL-validation helpers only if tests prove that reuse is safe and useful.

## 3. Contracts

### Request

Use a strict Pydantic model (`extra="forbid"`, frozen after validation):

- `query`: trimmed nonblank string, max 600 characters.
- `max_sources`: default 5, range 1–5.
- `country`: default PL, exactly two ASCII letters, normalized uppercase.
- `search_lang`: default pl, bounded safe language token.
- `freshness`: optional bounded string, same accepted values as Web Search 1.0.

The endpoint must not accept arbitrary source URLs from the caller in this first version; sources must originate from the validated search provider. This keeps the feature aligned with explicit search intent and reduces abuse surface.

### Source

Application-generated immutable ID such as `src_1`; bounded title, canonical final URL, normalized host, retrieval timestamp, retrieval status, bounded plain-text excerpt, and a safe error code when unavailable. Do not include response headers, cookies, internal IP addresses, or raw HTML.

### Claim and response

- `ResearchClaim`: bounded text, one or more `source_ids`, and evidence status (`supported`, `conflicting`, or `insufficient`).
- `ResearchResponse`: query, answer, provider/model identifiers when available, source records, claims, warnings, and a `synthesis_status` such as `complete` or `evidence_only`.
- Unknown citation IDs are invalid. The application, not the model, assigns source IDs and constructs canonical source metadata.
- Keep source excerpts and total answer size bounded. If synthesis fails, return a clear evidence-only result with available sources and a warning rather than fabricate an answer.

## 4. Provider integration

The existing provider-neutral interface is `AIProvider.chat(model, messages, tools=None)`; `ProviderManager.get_provider()` supplies the configured provider and `get_config()` supplies the selected model. The first implementation should use this interface directly through a narrow injected synthesis adapter, passing `tools=None` explicitly.

Do not call `AIService.chat()` to synthesize research: that method owns conversation persistence, classification, capability execution, clarification, and agent-loop behavior, which are inappropriate for a bounded source summarizer. Do not use AI Manager or its planning/action contracts for synthesis.

The synthesis prompt must have a fixed trusted system message and a separate user message containing only bounded evidence records. State clearly that source content is untrusted data. XML/JSON delimiters may aid readability but are not a security boundary. The model must return structured JSON matching a strict Pydantic schema; parse and validate it, never trust it directly.

## 5. Fetching and SSRF boundary

The fetcher is the highest-risk new component. It must not ship with only a pre-request DNS lookup followed by a normal HTTP client request, because DNS could change between validation and connection.

Required behavior:

- Only `http` and `https`; reject userinfo, fragments if not needed, malformed ports, whitespace/control characters, unsupported schemes, excessive URL length, and IP-literal targets unless explicitly supported safely.
- Resolve hostnames through a controlled resolver and validate all returned addresses. Block loopback, private, link-local, multicast, reserved, unspecified, and otherwise non-public IPv4/IPv6 addresses.
- Pin the validated DNS result to the actual outbound connection for each request/hop; preserve the original hostname for TLS SNI and certificate validation. If a safe pinned transport cannot be implemented and tested using the current stack, defer source fetching rather than ship a TOCTOU-prone approximation.
- Disable automatic redirects. Follow at most two redirects manually; fully revalidate and re-resolve/re-pin each target.
- No ambient cookies, authorization headers, proxy credentials, or search-provider credentials may be forwarded to a source.
- Use explicit connect/read/overall timeouts, stream the body, enforce a 1 MiB per-response limit and 4 MiB total budget, and abort immediately when a cap is crossed. Ignore untrusted Content-Length as the sole limit.
- Accept only HTML/XHTML content types in the first slice. Reject compressed-body expansion beyond the byte cap; measure the decoded bytes actually retained.
- Apply an allowlist for supported charsets or decode defensively with replacement. Never run scripts, load subresources, or follow meta-refresh.
- Avoid logging query text or page bodies. Audit safe metadata only.

Default proposed budget: at most 5 candidate sources, 1 MiB per fetched response, 4 MiB total response bytes, 12-second per-source timeout, at most 2 redirects, and a bounded total research deadline. These values may be tightened after tests; do not loosen them merely to make a provider pass.

## 6. Extraction and evidence handling

Use Python's standard-library `html.parser.HTMLParser` initially; `services/core/pyproject.toml` has no dedicated HTML extraction dependency, so avoid adding one unless extraction acceptance tests show it is necessary.

Extractor requirements:
- collect title and readable body text;
- ignore script/style/template/noscript and non-content metadata;
- decode entities and normalize whitespace;
- preserve a bounded excerpt and cap total extracted characters per source;
- never execute, render, or trust markup/metadata;
- report empty/low-quality extraction explicitly rather than treating it as evidence.

The synthesized response must not claim independent corroboration merely because multiple search results repeat the same snippet. Claims should be traceable to fetched text. When sources disagree or the evidence is weak, mark the claim and surface a warning.

## 7. Route and dependency injection

Add `app.api.schemas.research.py` for request/response API contracts and `app.api.routes.research.py` with `APIRouter(prefix="/web")`, exposing `POST /research`.

The route must:
1. set `Cache-Control: no-store` and `Pragma: no-cache`;
2. call `require_authenticated(request)`;
3. obtain the existing `PermissionEngine` and evaluate a dedicated `web.research` capability with bounded metadata only;
4. deny before search/fetch/synthesis if permission is not ALLOW;
5. invoke the injected research service;
6. map known provider/fetch/synthesis errors to safe structured HTTP errors, never leaking secrets, internal addresses, raw HTML, or stack traces.

Register the router in `app/main.py`. Add a dependency factory in `app/dependencies.py` that wires the existing `ProviderManager`, existing Web Search service/provider, audit service, and synthesis adapter. Avoid creating an independent credentials store or second provider configuration.

The new capability definition must be registered consistently with existing `web.search`; research permission must not be inferred from model output. Permission denial tests must prove no outbound provider or source-fetch call occurred.

## 8. Audit events

Use application audit events for requested/succeeded/failed research. Record request ID, query hash and length, selected source count, fetched count, extraction status counts, and safe error codes. Do not record raw query, page content, full snippets, API keys, cookies, resolved internal addresses, or synthesis prompt contents.

If existing `AuditEventType` does not support research-specific event types cleanly, add the minimal enum values and tests. Do not use a best-effort audit write as authorization; permission evaluation remains mandatory.

## 9. Implementation sequence (separate small commits)

1. **Contracts + citation validator** — models and deterministic unit tests only.
2. **HTML extraction** — bounded parser and hostile HTML tests.
3. **Pinned public fetcher** — SSRF-safe transport and hostile URL/redirect/body tests. Stop here if pinned transport cannot be verified.
4. **Synthesis adapter + orchestrator** — no-tools provider call, strict output parser, evidence-only fallback.
5. **API and DI integration** — authenticated POST route, permission, audit, safe errors, API tests.
6. **Targeted integration tests** — mocked network/provider, source-to-claim traceability, no-side-effect permission denials.
7. **Final hostile acceptance gate** — run the full suite only after the feature is integrated; inspect all failures/warnings without weakening gates.

Each step must pass targeted tests before the next. Do not run full regression after every tiny change.

## 10. Acceptance criteria

- Every returned claim is linked only to application-issued source IDs that were supplied to synthesis.
- No tool calls are exposed to the synthesis model.
- Prompt-injection content from pages cannot trigger a capability, permission change, extra fetch, or side effect.
- Private/local/reserved destinations are blocked even through redirects and DNS changes.
- Every byte/time/source/redirect/context/output limit is enforced during processing, not only after buffering.
- Permission denial happens before all outbound work.
- Synthesis failures and invalid model output return safe, honest evidence-only results or a clear failure; no fabricated success.
- Existing Web Search 1.0 behavior and all previous security tests remain intact.
- Final CI passes on the implementation HEAD before Stage 22.7 is closed.

## 11. Explicit non-goals

Browser automation, arbitrary user-supplied URL fetching, PDF/Office parsing, authentication to external sites, JavaScript, crawling links beyond search candidates, durable full-page cache, embeddings/vector database, and autonomous iterative research are deferred.
