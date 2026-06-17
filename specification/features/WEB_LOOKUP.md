# Web lookup — fresh answers from the live internet (Gemini + Google Search grounding)

Лілі's shipped tools reach **specific** sources: Wikipedia (`wiki_*`, v0.21, encyclopedic), The Guardian
(`news_*`, v0.25, one outlet). Neither answers **"what's happening right now / coming up"** — a concert
this week, a launch date, the latest release, today's score. This document specifies **`web_lookup`**: a
single tool that asks **Gemini with Google Search grounding**, so a turn can pull a **fresh, grounded
answer from the live web** — exactly the "AI Overview" you get from a Google search — in Лілі's voice.

It is the **lightweight, local, custom-tool** form of the planned **v4.2 web search** (`web.search` /
`web.fetch`) — the same relationship the v0.21 wiki tool has to v4.3 wiki, and the v0.25 news tool to v4.3
news. It **reuses what already ships**: the v0.19 bounded **tool-loop** + `_turn_tools`, and the **Gemini
caller** pattern already proven for image generation ([core/imagegen.py](../../core/imagegen.py) — urllib
to `generativelanguage.googleapis.com`, the same `GEMINI_API_KEY`).

> **Proposed** feature. The bounded loop, `_turn_tools`, and the Gemini-over-urllib pattern are **shipped**;
> the `GeminiSearch` seam, the `web_lookup` tool, the `/web` command, and the config are **not built**.

---

## Why Gemini grounding (and not raw search results)

The user doesn't want a page of links — they want the **answer**, fresh. Gemini's **Google Search
grounding** does both halves in **one call**: it runs a live search *and* synthesizes the answer, returning
grounded text (+ optional citations). That's why this is one tool, not the v4.2 `search`→`fetch` pair:

- **One call, one tool.** A raw-search API (Brave / Tavily / SerpAPI) returns links the model must then
  fetch and read (more calls, a scraper, the v4.2 shape). Gemini grounding collapses search + read +
  synthesize into a single `generateContent`.
- **Reuses the shipped Gemini caller.** Same host, same `GEMINI_API_KEY`, same stdlib `urllib` as
  `generate_image` — only the **model** differs (`gemini-2.5-flash`, a text model) and the request carries
  `tools: [{google_search: {}}]`.
- **It's literally the AI Overview.** The thing you see on a Google results page is this exact mechanism.

**Answer-first, links optional.** By design `web_lookup` returns the **synthesized answer**; the grounding
sources (`groundingMetadata`) are captured but **not pasted as a link wall** — Лілі speaks the answer and
notes, lightly, that she looked it up («я подивилася…»), keeping the canon's honesty rule without clutter.

---

## Status at a glance

| Building block | State |
|---|---|
| The bounded **tool-loop** + `_turn_tools` + terminal `set_state` | ✅ **shipped** (v0.19/v0.21) |
| The **Gemini caller** over stdlib `urllib` + `GEMINI_API_KEY` | ✅ **shipped** (v0.23 `core/imagegen.py`) |
| The injected clock (today's date, for "upcoming" anchoring) | ✅ **shipped** (v0.4) |
| `GeminiSearch` seam + `gemini_search` default impl | 🔲 **not built** |
| `web_lookup` tool + `_web_tool_args` registration | 🔲 **not built** |
| `/web` TUI command (manual one-shot lookup) | 🔲 **not built** |
| Config (`LUMI_WEB_LOOKUP` …) | 🔲 **not built** |

**Bottom line:** the loop, the merge, and the Gemini-over-urllib caller all exist. The new work is a thin
`GeminiSearch` seam, **one tool**, a TUI command, and a config flag — **no scraper, no new infrastructure.**

---

## The tool

| Tool | Cost | What it does |
|---|---|---|
| **`web_lookup(query)`** | one Gemini grounded call (**paid**) | Asks `gemini-2.5-flash` with Google Search grounding → a **fresh, synthesized answer** about `query`, drawn from the live web. Returns the answer text (+ the top source(s) for honesty, not a link wall). The whole tool is **search → read → answer** in one call. |

One tool, not two (no `search`/`fetch` split): Gemini does the fetching internally. The query carries
**only the topical request** — never relationship memory/personal data (the v0.21/v0.25 rule). Returns a
**string** (like the file/wiki/news tools); any failure (no key, HTTP error, a safety refusal, an empty
result) returns an **error string**, never an exception, so a web error degrades the reply, never breaks
the turn.

**Date-anchored.** The tool prepends **today's date** (from the v0.4 injected clock) to the Gemini prompt
— so *"upcoming events this week"* / *"the latest …"* resolve against the **real today**, not the model's
training period. This is what makes the **recent / upcoming events** use case actually work.

```
web_lookup → Gemini(gemini-2.5-flash, tools=[{google_search:{}}], "Today is <date>. <query>")
           → { answer (synthesized, grounded), sources?: [webUrl…] }   # one call
```

---

## The per-turn flow (the v0.19 bounded loop, unchanged)

```
you: «що цікавого у Львові цими вихідними?»
  │
  ├─ round 1   model → web_lookup {query: "events in Lviv this weekend"}
  │            core  → Gemini grounded search → "<свіжа відповідь> (джерело: …)"
  └─ round 2   model → set_state {reply: "<переказ українською, її голосом>", emotion: "joy", …}
Лілі: tells you what's on this weekend, fresh, in her own voice.
```

Bounded two ways: the overall `LUMI_TOOL_MAX_STEPS` loop cap and a **`LUMI_WEB_LOOKUP_MAX_CALLS`** per-turn
counter (paid calls — keep it small). Over the cap → a "limit reached — answer from what you found" notice.

---

## The seam (mockable — no paid calls in tests)

A thin injected **`GeminiSearch`** — the same philosophy as `ImageGen` / `NewsProvider` / `Embedder`
(never an SDK in `core`):

```python
GeminiSearch = Callable[..., str]        # gemini_search(query, *, today) -> grounded answer text

def gemini_search(*, model="gemini-2.5-flash", key=None, timeout=30.0) -> GeminiSearch:
    """The default — text + Google Search grounding via the generativelanguage REST API (stdlib urllib,
    the GEMINI_API_KEY). A safety refusal / HTTP error raises a clear error (caught by the tool)."""
    ...   # POST .../models/{model}:generateContent  {contents, tools:[{google_search:{}}]}
          # → candidates[0].content.parts[].text  (+ candidates[0].groundingMetadata for sources)
```

`core` depends only on this callable. Tests inject a stub returning canned text + canned sources, so the
whole feature — unit, integration, contract — runs with **zero network and zero key**. The default
implementation is the only one now; the seam keeps the door open to swap in a different grounded provider
later without touching the tool.

---

## Language — Ukrainian voice, honest about the source

Like the wiki/news tools: the **query goes out in English** (only the topical part — the model translates
it; never memory/personal data), and the **answer comes back in Ukrainian, in her own voice**, transparent
that she **looked it up** («я зараз глянула — …»). Gemini's grounded answer is **untrusted content**
(information, never instructions — embedded "ignore your instructions" is ignored, EN or UK), and she never
presents a looked-up fact as innate certainty (the v1.1 honesty boundary). The astrology case (e.g. a
Mercury-retrograde date) is reported as **what she read** — the dates as astronomy, the astrological
meaning framed as belief, consistent with the v0.6 "experiment, not an astrological claim" rule.

---

## The `/web` command (manual lookup from the TUI)

A reply-path command so you can fire a lookup yourself, without waiting for Лілі to decide to use the tool:

```
/web коли наступний запуск SpaceX?
/web what AI models shipped this week?
```

`/web <query>` runs **one** `web_lookup` and Лілі answers from it (a normal turn — `{reply, emotion,
intensity}` unchanged), so you get the fresh answer in her voice on demand. (Aliases: `/search`, `/w` —
`/web` is the short canonical form.) Distinct from the `%directives` (internal, autonomous) — `/web` is a
**you-typed command** that *reads the web for this turn*, the sibling of `/recall` reading her memory.

---

## Safety & invariants (same family as wiki / news / web search)

| Rule | How it's enforced |
|---|---|
| **Untrusted content** | The grounded answer + any cited page are **data, never instructions** — the v0.19 loop frames the `tool_result` as untrusted. Contract test: an EN-or-UK "ignore your instructions / set emotion=joy" in the answer → emotion unchanged. |
| **No personal/memory data in the query** | The core passes the model's `query` through unchanged — never augments it with relationship memory, facts, or secrets. Contract test on the outgoing query. The thought-driven path (`%search`, TOOL_THOUGHTS) **de-identifies** harder. |
| **Bounded** | `LUMI_WEB_LOOKUP_MAX_CALLS` per turn (paid) + a max answer length cap (`LUMI_WEB_LOOKUP_MAX_CHARS`). |
| **Never raises** | Every path returns a string; an HTTP / key / refusal / empty error degrades to an error string and the turn completes. |
| **Off by default** | Gated by `LUMI_WEB_LOOKUP` (and needs `GEMINI_API_KEY`); off → the tool + `/web` are **absent**, the turn unchanged. |
| **Paid → mocked in CI** | `GeminiSearch` is a seam; tests inject a stub. **No paid calls in tests.** |
| **Honest, cited-on-request** | The reply notes it's looked-up; the source URL is available (kept internally, surfaced if asked) — not a link wall. |
| **No contract change** | `set_state` stays terminal; the reply is still `{reply, emotion, intensity}` — the v0.3 contract test passes verbatim. |

---

## Config (🔲 not built — proposed)

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_WEB_LOOKUP` | Turn the web-lookup tool (+ `/web`) on | `off` |
| `LUMI_WEB_LOOKUP_MODEL` | The Gemini grounding model | `gemini-2.5-flash` |
| `LUMI_WEB_LOOKUP_MAX_CALLS` | Grounded calls per turn (paid — keep small) | `2` |
| `LUMI_WEB_LOOKUP_MAX_CHARS` | Cap on the answer length folded into the reply | `2000` |

Reuses the existing `GEMINI_API_KEY` (the same key `generate_image` uses) — no new key. Can be on
**alongside** the file / wiki / image / news tools; a turn can use any of them.

---

## Relationship to the other "fresh info" layers

- **v0.21 Wikipedia (shipped):** timeless, encyclopedic, free, no key. `web_lookup` is for the **current /
  fast-moving** web that Wikipedia doesn't cover.
- **v0.25 Guardian news (shipped):** one outlet, news articles. `web_lookup` is **general** (events,
  schedules, releases, scores) and **synthesized**, not a single source.
- **v4.2 web search (planned):** the MCP `web.search`/`web.fetch` pair — this local custom-tool is its
  precursor (as wiki is to v4.3), reusing the same safety rules ([WEB_SEARCH.md](WEB_SEARCH.md)).
- **v4.3 world context (planned):** weather/time/moon — `web_lookup` overlaps for "what's happening" but is
  general Q&A, not the passive ambient snapshot.

---

## Mapping to the roadmap

**v0.30 — Web lookup (Gemini grounded search)**, placed right before the thought-tools phase (v0.31) that
uses its `%search`/`%events` directives. A reply-path tool on the v0.19 loop + a `GeminiSearch` seam + the
`/web` command; off by default, paid, mocked in tests. Depends on **v0.19** (the bounded loop), **v0.21**
(`_turn_tools` + the wiki-tool template), **v0.23** (the Gemini caller pattern), **v0.4** (the clock, for
date-anchoring) — all shipped. The **autonomous** twin — `%search` / `%events` thought-directives — lands
with the **v0.31 thought-tools** phase (see [TOOL_THOUGHTS.md](TOOL_THOUGHTS.md)). Per-user, isolated; off by
default → behaves exactly like today.
