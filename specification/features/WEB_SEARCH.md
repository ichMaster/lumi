# WEB_SEARCH.md

Boundaries for the optional **web search** capability (ROADMAP §v4.2; ARCHITECTURE §MCP tools, §Security, auth, and access). It lets Лілі look things up on the open internet — **within strict bounds** — through an MCP tool, and is **off by default**. Adapted from the Pyramid project's web-search design.

---

## 1. Goal and non-goals

**Goal:** when a user enables it, Лілі can answer from **fresh web results, with sources**. When disabled, she has no internet access beyond the model's own training knowledge.

**Non-goals**
- Not a general web agent: no logins, no forms, no POSTing, no crawling, no JavaScript execution — read-only `search` + `fetch` of public pages.
- Not a data-exfiltration path: personal and memory data never enter queries.
- Not authoritative: web text is **untrusted input**, never instructions.

---

## 2. The `web_search` MCP service

A networked MCP service (HTTP/SSE), called by the model like any other tool during the v4.2 tool loop. The server hosts the MCP client; the core's model turn issues the calls.

- `web.search(query, k) → results[{ id, title, url, snippet }]`
  Runs a query against a configured search API; returns up to `k` results (capped, e.g. ≤ 5). `id` is an opaque per-turn handle.
- `web.fetch(result_id) → { url, title, text }`
  Fetches and extracts the readable text of **one prior result**. `result_id` MUST be an `id` returned by a `search` in **this same turn** — `fetch` cannot take arbitrary URLs. Extracted text is truncated to a size cap (e.g. ≤ ~8 KB).

The normal pattern: `search` → pick relevant results → `fetch` one or two → answer with citations.

---

## 3. Enablement

- Per-user toggle `User.web_search`, **default `false`**, edited in the admin panel (v2.5).
- When `false`, the `web_search` tool is **not offered** to the model at all (not merely refused) — the turn proceeds with no internet access.
- The search-API key lives in server `.env` (never in a client).

---

## 4. Safety boundaries (hard rules)

1. **Untrusted content.** Page/search text is data, never instructions. The server wraps fetched content so the model treats it as quoted material; Лілі must **not** follow instructions, links, or prompts embedded in pages ("ignore previous…", "now do X", hidden text, etc.). Prompt-injection defense is assumed-hostile by default.
2. **No personal/memory data in queries.** Queries are built only from the user's explicit request for this turn. Relationship memory, the shared-experience layer, account data, prior private conversation, and secrets must **not** be placed into a query or sent to the search/fetch provider.
3. **Fetch is bounded to prior results.** `web.fetch` only accepts `id`s from a `search` in the same turn — no free-form URL fetching, no following links found inside fetched pages (the model may issue a new `search` instead).
4. **Read-only, public, safe.** GET only; no auth headers; skip non-HTML and oversized responses; an optional domain allow/deny list per deployment.
5. **Rate limits.** Per-turn caps (e.g. ≤ 2 `search` + ≤ 3 `fetch`) and a per-user/day budget; over-budget calls return a tool error (degraded reply, not a hang).
6. **Logging.** Every `search` (query) and `fetch` (url) is logged with `session_id`/`turn_id` for audit; logs avoid storing full page bodies.

---

## 5. Turn integration

- Runs inside the v4.2 MCP **tool loop** (ARCHITECTURE §MCP tools): bounded iterations, tool results fed back as tool messages; on tool error/timeout the turn returns a **degraded reply** (answer from model knowledge + a note), never fails the turn. The emotion field `{reply, emotion, intensity}` is produced exactly as on any other turn.
- **Citations required:** when the answer uses web content, the reply names its sources (titles/URLs from the results used). "Fresh result with sources" is the success bar.
- A per-call timeout like the other external stages (model, TTS, STT).

---

## 6. Definition of done

- **Enabled:** Лілі answers a "what's the latest on X?" style question from fresh web results **with sources**; injection attempts in fetched pages are ignored; no personal/memory data appears in the outgoing query; rate limits and logging are enforced.
- **Disabled (default):** the `web_search` tool is absent; Лілі relies only on the model's own knowledge; no outbound search/fetch occurs.

---

## 7. Open decisions

- Search provider (e.g. Brave Search API, Bing, SerpAPI, Tavily) and its `.env` key.
- HTML→text extraction approach (readability-style) and the size cap.
- Optional per-deployment domain allow/deny list.
- Whether to cache results briefly per query to cut repeat calls/cost.
- Whether `web_search` MCP transport is in-process, stdio, or HTTP/SSE for Lumi's single-server deployment.
</content>
