# Stage 22.7 — Research / Source Synthesis Discovery

**Status:** Discovery complete; 22.7A–22.7D have implementation code. Final integration/security/regression gates remain open; not approved for merge or production use.  
**Implementation branch:** `stage-22/research-ci-fixes` (PR #6, draft)  
**Branch:** `stage-22-7-research-source-synthesis`

## 1. Goal

Evolve Web Search 1.0 from a ranked-link/snippet capability into a controlled research workflow:

1. Search for candidate sources.
2. Select a bounded number of sources.
3. Retrieve and extract readable text from public pages.
4. Preserve source identity and evidence locations.
5. Synthesize a concise answer with explicit source mapping.
6. Represent uncertainty, disagreement, inaccessible pages, and unsupported claims honestly.

This stage must remain provider-neutral and must not create a general-purpose browser or an autonomous action surface.

## 2. Existing baseline

- `app.web_search.models` defines immutable request/result/response contracts.
- `WebSearchService` validates provider identity, query identity, result counts, URL syntax, ranks, and duplicates; audit events omit raw queries.
- Brave and DuckDuckGo adapters use bounded streamed responses, finite timeouts, and no automatic redirects.
- `/api/web/search` is authenticated and permission-checked.
- `CapabilityResolver` maps explicit web intent to `web.search`; the model cannot independently trigger the network capability.
- `ManagerPromptBuilder` treats user/context/memory as untrusted data. Existing output validation and app-owned execution authority remain the security boundary.
- Core dependencies currently include FastAPI, Pydantic, SQLAlchemy, and HTTPX; no dedicated HTML extraction dependency is declared.
- There is no existing controlled public-page retrieval / readable-content extraction contract in the inspected Web Search surface.

## 3. Proposed architecture

Keep each responsibility narrow and application-owned:

```text
Research request
  -> WebSearchService (existing search contract)
  -> ResearchService (orchestration, budgets, source selection)
  -> PublicPageFetcher (network/SSRF policy, redirects, byte/time limits)
  -> HtmlTextExtractor (bounded text extraction; no script/style execution)
  -> ResearchSource records (canonical URL + title + extracted text + retrieval status)
  -> ResearchSynthesisAdapter (existing configured AI provider, no tools/execution handles)
  -> Evidence validator (source IDs, quote/span limits, claim-to-source mapping)
  -> ResearchResponse (answer + citations + caveats + source statuses)
```

The first implementation should be a separate research service and contract, not an expansion of the Web Search provider interface. Search providers continue returning links/snippets; retrieval and synthesis are distinct operations with distinct limits and errors.

## 4. Required trust and network boundaries

### URL and SSRF controls

- Accept only absolute HTTP(S) URLs with a valid hostname; reject userinfo, malformed ports, control/whitespace characters, and oversized URLs.
- Resolve and validate all A/AAAA addresses before connecting; reject loopback, private, link-local, multicast, unspecified, reserved, and otherwise non-public destinations.
- Protect against DNS rebinding: validate the address used for the actual connection, or use a transport/connector that pins the validated address while preserving TLS hostname validation.
- Reject URL credentials, IP-literal destinations by default, and unsupported schemes.
- Disable automatic redirects. If redirects are supported, process each hop manually, revalidate scheme/hostname/IP and cap hop count. Never forward provider/API credentials to page hosts.
- Do not fetch localhost, LAN, cloud metadata endpoints, or arbitrary user-supplied internal URLs.
- Use fixed application-controlled HTTP behavior, a clear user agent, finite connect/read/total timeouts, response-byte caps enforced during streaming, and bounded concurrency.
- Accept only expected HTML/XHTML content types in the first version. Reject downloads, archives, PDFs, media, and non-text payloads rather than guessing.
- Apply an explicit per-request page count and aggregate byte/text/token budget. A single hostile page must not exhaust memory or stall the workflow.

### Untrusted page content and synthesis

- Treat titles, snippets, HTML text, metadata, and page content as hostile data, including prompt-injection attempts.
- Never put retrieved content in trusted system instructions. Use a dedicated synthesis prompt with clear data separation and an explicit instruction to ignore embedded commands.
- Do not expose tools, file access, shell, browser interaction, credentials, permissions, or execution handles to the synthesis model.
- Model output is a proposal. Validate it against strict application-owned schemas; the model cannot invent source IDs or claim evidence that is not in the retrieved source set.
- Preserve source IDs through extraction and synthesis. Citations must point to sources actually retrieved for this research request.
- Do not treat source text as authorization, user consent, or a command to perform another action.

### Privacy, audit, and availability

- Do not log raw research queries or full page text. Audit only request identifiers, query hash/length, source counts/domains, timings, and safe error codes.
- Do not persist full page content by default. In-memory bounded processing is sufficient for the first version.
- Avoid URL query strings in routine logs; consider POST for research requests to reduce query exposure in access logs.
- Fail closed on network policy errors, malformed extraction, provider failures, and invalid synthesis output. Return partial-source status only when the contract explicitly represents it.

## 5. Initial API/data contract proposal

Keep the first public contract small:

- `ResearchRequest`: query (bounded), max_sources (small fixed range), optional locale.
- `ResearchSource`: stable per-request source ID, title, canonical URL, hostname, extracted text length, retrieval status, and safe failure code. Full text is internal to the synthesis pipeline and should not be returned by default.
- `ResearchCitation`: source ID plus optional short evidence excerpt or text offsets; never arbitrary model-supplied URLs.
- `ResearchResponse`: answer, citations, sources/statuses, and explicit limitations/uncertainty.
- `ResearchService`: search → bounded source selection → fetch/extract → synthesis → evidence validation.
- A deterministic mode without synthesis should still return successfully retrieved source summaries/statuses when configured AI is unavailable, but must not pretend it produced a synthesized answer.

The exact Pydantic fields and limits should be finalized during implementation, after confirming the current AI provider call contract and test conventions.

## 6. Scope for the first implementation slice

In scope:
1. Strict immutable research contracts.
2. Safe public-page fetcher with SSRF protections, manual redirect validation (or redirects disabled for the first slice), streaming byte limits, content-type checks, and finite timeouts.
3. Bounded HTML-to-text extraction using the standard library where practical; strip scripts/styles/forms/navigation noise and normalize whitespace.
4. Deterministic source identity, de-duplication, and retrieval status handling.
5. Hostile tests for private/loopback/link-local IPs, redirects, DNS resolution errors, malformed URLs, oversized streams, content-type mismatch, HTML prompt injection, and source-ID forgery.
6. Tests and CI before integrating synthesis.

Out of scope for this first slice:
- General-purpose browser automation, clicking, forms, downloads, authentication, cookies, or JavaScript execution.
- Access to private/intranet pages.
- Persistent page archives, vector databases, embeddings, or background crawling.
- Autonomous purchases, submissions, account actions, or other side effects.
- Claims of fact without source evidence.

## 7. Integration decision

Do not call a model from the page-fetcher or extractor. First build and test retrieval as a standalone service. Then add a narrow provider-neutral synthesis adapter using the already configured AI provider and existing bounded prompt/output validation conventions. This separates network security defects from model-quality defects and keeps testing deterministic.

Do not add new third-party dependencies until the standard-library extractor is shown insufficient by tests; this avoids increasing the supply-chain and maintenance surface unnecessarily.

## 8. Implementation order and gates

1. **22.7A — Contracts + fetch policy discovery-to-code:** define models, safe URL/IP validation helpers, fetcher interface, deterministic tests.
2. **22.7B — Retrieval + extraction:** bounded fetch, redirect policy, HTML extraction, source normalization, hostile tests.
3. **22.7C — Synthesis adapter:** provider-neutral prompt/input contract, strict output validation, source-ID/evidence checks, injection tests.
4. **22.7D — Operator integration:** explicit research phrasing uses the existing `web.search` service capability and its readiness/permission-checked execution path; ordinary `web.search` remains usable. Implementation and full-core CI are verified on the current branch HEAD; hostile review and acceptance remain open.
5. **22.7E — Final gate:** focused tests, full CI, hostile review, docs and PR status verification.

No phase may silently expand into browser automation. 22.7C is implemented. Do not close the stage until the final integration CI, hostile review, documentation, and regression gates pass.

## 9. Open implementation checks

- Confirm how configured AI providers expose a simple non-tool chat/completion call before implementing synthesis.
- Confirm the runtime's supported DNS/transport facilities so the fetcher can mitigate DNS rebinding rather than relying on a preflight lookup alone.
- Confirm how the app's current logging/audit layers record request paths and whether the research API should use POST.
- Ensure CI runs on the new branch and that the PR remains draft until the final gate is green.


## 10. Implementation checkpoint (2026-10-16)

- **22.7A–22.7B:** contracts, URL/DNS policy, pinned transport, bounded HTML fetch/extraction, and retrieval orchestration are implemented. TLS/SNI and final adversarial transport review remain explicit closure gates.
- **22.7C:** synthesis adapter and evidence-backed citation validation are implemented; the adapter does not receive tools.
- **22.7D:** explicit research intent is routed through the existing permission-checked `web.search` service path. Focused tests cover intent selection and permission-path invocation.
- **Latest CI evidence:** branch HEAD `8d7b62bd4d15af4515c796c03027a14e303a5586` passed [full core-tests run #38889607053](https://github.com/adamzamorski10-alt/Jarvis/actions/runs/38889607053): 2983 passed, 18 skipped, 96 warnings in 224.75 seconds. The paired run #38889602974 also succeeded on the same SHA.
- **Security evidence confirmed in code/tests:** real TLS handshake test checks SNI and certificate verification; DNS pinning/revalidation, redirect rejection, bounded body/time behavior, no-tool synthesis, and exact-evidence citation checks have focused tests.
- **Still open:** final hostile end-to-end review, error-detail/lifecycle review in the Operator integration, focused research regression on the accepted SHA, and explicit acceptance. PR #6 must remain draft and Stage 23 must not begin before the Stage 22 summary is delivered.
