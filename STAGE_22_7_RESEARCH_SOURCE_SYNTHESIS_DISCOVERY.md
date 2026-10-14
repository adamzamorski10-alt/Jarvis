# Stage 22.7 — Research / Source Synthesis Discovery

**Status:** Discovery complete; design and implementation not yet started  
**Base:** `stage-22/capability-contract-closure` at `9924ae18042d27b9859d59f4ed0e62b79d873e26`  
**Discovery branch:** `stage-22.7/research-source-synthesis-discovery`

## 1. Goal

Upgrade the current Web Search 1.0 capability from returning ranked links/snippets into a controlled research workflow that can gather evidence from sources, compare claims, and produce a traceable answer. This is not browser automation and must not grant the AI authority to execute tools or treat external text as instructions.

## 2. Current architecture verified

- `app.web_search.models` defines bounded, frozen `WebSearchRequest`, `WebSearchResult`, and `WebSearchResponse` contracts. Results currently contain title, URL, snippet, source, and rank; no fetched page body, citation identifier, extraction metadata, or synthesis model exists in this package.
- `app.web_search.providers.base.WebSearchProvider` is a provider-neutral search contract. Brave and DuckDuckGo implement it and stream responses under explicit size caps.
- `WebSearchService.search()` validates provider identity, query identity, result count, result URL shape, rank bounds, and duplicates. Audit events record a query hash/length rather than raw query text.
- `app.api.routes.web_search` exposes authenticated search/config routes and applies the existing permission engine. Search currently uses GET, so query text can appear in URL/access logs; this remains a separate hardening concern.
- `OperationClassifier` recognizes explicit WEB_SEARCH intent, and intentionally does not let an AI-only guess trigger network access.
- `OperatorCapabilityResolver/Executor` owns the `web.search` capability. The application remains the execution authority.
- `AIService` and `ProviderManager` are the existing user-facing chat/provider path. `AIManager` is a separate structured decision/planning path and is not a generic trusted summarizer.
- `ManagerPromptBuilder` and `ContextAssemblyService` already state that user text, memory, task context, tool results, and external content are untrusted data. They explicitly do not rely on delimiters as a security boundary.

## 3. Main gap

The current feature can discover sources but cannot safely establish what those sources say, attribute synthesized claims to exact evidence, distinguish corroborated from conflicting claims, or communicate uncertainty. Reusing snippets alone would produce shallow and potentially misleading answers. Adding a browser or arbitrary URL fetcher at this stage would expand the attack surface before a bounded research contract exists.

## 4. Recommended minimal architecture

Keep the existing search provider contract stable. Add a separate application-owned research layer with explicit, bounded stages:

1. **Discover** — call the existing Web Search service with an explicit user research intent.
2. **Select** — deduplicate and cap candidate URLs; accept only validated public HTTP(S) URLs, with credentials/userinfo forbidden.
3. **Fetch** — use a dedicated server-side source fetcher with strict timeout, redirect-hop limit, response-byte cap, content-type allowlist, and extraction-size cap. Do not forward provider credentials or user cookies. Reject local/private/link-local/reserved destinations and re-check every redirect target to mitigate SSRF/DNS-rebinding risks. Do not fetch arbitrary schemes or execute page scripts.
4. **Extract** — produce bounded plain text plus canonical source URL, page title, retrieval timestamp, and extraction status. Treat HTML, metadata, hidden text, and extracted text as hostile external data. Never render or execute source HTML.
5. **Synthesize** — call the existing configured text AI provider through a narrow service adapter. Supply source records as explicitly untrusted evidence; do not route source content through AI Manager action planning. The model may draft claims and citations but cannot add sources, change source IDs, call tools, or decide permissions.
6. **Validate** — validate the response against a strict schema. Every factual claim intended to be cited must reference one or more source IDs that were actually supplied. Reject unknown IDs and malformed citation ranges. If validation fails, return a safe partial/evidence-only response rather than silently inventing citations.
7. **Return** — provide a structured research response with answer, source list, per-source retrieval status, claim-to-source mapping, and explicit caveats where sources conflict, are inaccessible, or provide insufficient evidence.

This layer should be provider-neutral and must not create a second permission system or execution engine.

## 5. Trust and security requirements

- External pages are untrusted data, not instructions. Prompt-injection text in a page must never override system rules, user intent, policy, approval state, capability selection, or tool execution.
- Research/synthesis is read-only. No forms, logins, purchases, messages, downloads to arbitrary paths, browser sessions, code execution, or user-account actions.
- Network access remains behind the existing authenticated, permission-checked application entry point. Internal fetches require a separate policy check, not merely a valid URL.
- SSRF protections must cover IPv4/IPv6 loopback, private, link-local, multicast, reserved and unspecified addresses; redirects and DNS resolution must be considered. Fail closed if a destination cannot be validated.
- Apply hard caps at every boundary: query length, number of search results, number of pages fetched, per-page bytes, total research bytes, extracted text per source, total model context, output length, time budget, and redirect count.
- Do not log raw page bodies or raw search queries. Audit metadata should include request ID, hashed query, selected source hosts/IDs, counts, and safe outcome/error codes.
- Do not claim that a source supports a claim unless the source text actually supports it. Preserve contradictory evidence and distinguish sourced facts from inference.
- The returned source URL must be the validated final canonical URL, not an unchecked model-generated URL.
- Search results and source text must never be allowed to define the system prompt or tool schemas.

## 6. API and product behavior

Recommended first vertical slice: an authenticated `POST /web/research` endpoint and a structured response contract. POST avoids placing the query in the URL, although application/proxy body logging still needs configuration review. Keep `GET /web/search` for backwards compatibility in this stage; do not silently change its semantics.

A research response should minimally contain:
- `query` (validated original query),
- `answer` (bounded synthesized answer or explicit evidence-only fallback),
- `sources[]` with stable source ID, title, canonical URL, host, retrieval status, retrieved timestamp, and bounded excerpt,
- `claims[]` with claim text, source IDs, and evidence status,
- `warnings[]` for conflicting, weak, missing, or inaccessible evidence,
- `provider` / `model` metadata only where safe and available.

Do not persist full page bodies by default. Cache, durable research history, embeddings, browser sessions, and autonomous multi-step browsing are out of scope for the first slice.

## 7. Test plan to carry into design

- Unit: URL/host normalization, content-type checks, HTML-to-text extraction, caps, redirect policy, citation schema and unknown-source rejection.
- Hostile URLs: credentials in URLs, malformed ports/IPv6, localhost/private/link-local/reserved IPs, encoded host tricks, redirects from public to private targets, excessive redirects, oversized/chunked bodies, slow responses, unsupported content types, malformed encodings.
- Prompt injection: page text asks to ignore instructions, reveal secrets, invent citations, call tools, change permissions, or fetch a second URL. Verify it remains data and causes no side effect.
- Evidence integrity: unsupported claim, conflicting sources, duplicate sources, model-generated source ID, empty evidence, truncated extraction, source fetch failure, and synthesis-provider failure.
- Integration: authentication, permission denial before any network request, bounded number of fetches, audit redaction, safe errors, and end-to-end source-to-claim traceability.
- Regression: existing Web Search 1.0 contract and capability tests remain unchanged and pass.

## 8. Decisions recommended for Stage 22.7 design

1. Start with HTML pages only; do not parse PDFs, Office documents, images, or run browser JavaScript in the first slice.
2. Keep source fetching and extraction deterministic; use AI only for evidence synthesis.
3. Start with a small fixed source/page budget (proposed default: at most 5 sources per request, 1 MiB per response, 4 MiB total fetched bytes, 12-second per-source timeout, 2 redirects). Final values must be checked against existing settings/conventions and made configurable only if there is a demonstrated need.
4. Use claim-level source IDs, not merely a bibliography at the end.
5. Keep research separate from browser interaction; Stage 23 browser automation should be designed only after this stage passes its hostile acceptance gate.
6. Do not add database persistence in the first slice unless the current API/UI flow demonstrates a concrete requirement.

## 9. Open questions for design, not blockers

- Which existing AI provider method offers the cleanest text-only, no-tools synthesis call with bounded output? Prefer an existing provider abstraction over creating a second HTTP client.
- Which HTML extraction dependency is already installed and acceptable? Prefer an existing maintained parser; avoid adding a heavyweight browser runtime.
- Is public-network egress already constrained at the deployment/network layer? Application-level SSRF controls remain required regardless.
- Should failed source fetches be omitted or returned as explicit failed source records? Recommendation: return status records so the answer transparently shows coverage gaps.
- Should claim validation be strict all-or-nothing? Recommendation: reject invalid citation references, but allow a clearly marked evidence-only fallback when synthesis is unavailable.

## 10. Exit criteria

Discovery is complete when the implementation plan identifies the exact provider call, dependency, dependency-injection path, endpoint, models, audit events, and test files. Implementation must not start until these are resolved. Full regression and a separate hostile acceptance gate belong at the end of the implementation stage, not in this discovery task.
