# Local news tool — Лілі reads the news (`news_search` / `news_read`)

Two tools on the **v0.19 bounded tool-loop** so Лілі can pull **fresh news on demand** during a turn —
**search** a topic, **read** one article, and weave it into the conversation **in her own voice, with
the source**. The source is a single, configured outlet: **The Guardian Open Platform** (one site, many
topics), reached through its free API.

This is the **lightweight, local, custom-tool** form of the v4.3 world-context `news.recent` — the same
relationship the v0.21 Wikipedia tool has to v4.3's `wiki.lookup`. It reuses what already ships: the
**bounded tool-loop** + terminal `set_state` (v0.19), the **`_turn_tools` merge + name-routing executor**
(v0.21), and the **injected `http_get`** pattern (v0.4 `core/worldcontext.py`) so tests touch no network.
The MCP form, and a per-user news memory, remain v4.3 ([WORLD_CONTEXT_MCP.md](WORLD_CONTEXT_MCP.md)); this
is the precursor.

> **Proposed** feature. The building blocks are shipped; the provider seam, the two tools, and the
> translation/voice rules are not. The markers below say exactly what's done vs. not.

---

## Why The Guardian, and why nothing more

The Guardian Open Platform fits the "**limited, or even one site, many topics**" goal better than any
alternative, and lets the design stay **smaller** than a generic news tool:

- **One site** → the allowlist is a single host (`content.guardianapis.com`). She *structurally cannot*
  reach another outlet.
- **Many topics** → Guardian **sections** map 1:1 to the tool's `topic` argument: `world`, `politics`,
  `business`, `technology`, `science`, `environment`, `global-development`, `culture`, `sport`.
- **Full body via API** (`show-fields=bodyText`, plain text) → **no HTML scraper, no separate page
  fetch** — `news_read` reads a JSON field. The "fetch a full article" capability comes for free and
  safe.
- **Real search** → a genuine free-text `q=`, better than an RSS local-filter.
- **Free + cheap** → a free *developer* key (register at open-platform.theguardian.com), includes article
  text, non-commercial. (Confirm current rate limits at signup.)

**Two tools is the whole surface** (search → read), the same shape as `wiki_search`/`wiki_read` and
`web.search`/`web.fetch`. A `news_topics()` is unneeded (sections are a fixed list in the tool
description); a `news_latest()` is just `news_search` with no query; translation is the model's job, not
a tool. Each extra tool schema sits in the prompt every turn — only search and read clear the bar of
"can't be a parameter."

---

## Status at a glance

| Building block | State |
|---|---|
| The bounded **tool-loop** + terminal `set_state` (where the tools register) | ✅ **shipped** (v0.19) |
| `_turn_tools` (merges file + wiki tools; would merge news too) + name-routing + trace | ✅ **shipped** (v0.21) |
| Injected **`http_get`** pattern (testable offline) | ✅ **shipped** (v0.4 `core/worldcontext.py`) |
| `NewsProvider` seam + `GuardianProvider` | 🔲 **not built** |
| `news_search` / `news_read` + per-turn **id registry** | 🔲 **not built** |
| **Query→EN / reply→UK** rules + the canon "how she delivers news" line | 🔲 **not built** |
| Config flags (`LUMI_NEWS_TOOL` …) + docs | 🔲 **not built** |

**Bottom line:** the loop, the tool-merge, and the HTTP pattern all exist. The new work is a thin Guardian
provider seam, two tools, and a one-line canon note — **no scraper, no new infrastructure**.

---

## The two tools

| Tool | Cost | What it does |
|---|---|---|
| **`news_search(query?, topic?, days?)`** | cheap | Searches Guardian and returns up to N **candidates** — title + one-line summary + an **opaque per-turn id**. No bodies (kept light). The discovery step. |
| **`news_read(id)`** | one body | Reads **one** article **by an id `news_search` returned this turn** → its full text (capped) + the **source URL**. The bounded terminal — an id not from this turn is refused. |

Tool **names** are `news_search` / `news_read` (Anthropic-safe, no `.`; the roadmap calls them
`news.search` / `news.read`). Both return a **string** (like the file/wiki tools); any failure — a bad
key, an HTTP error, an empty result, an unknown id — returns an **error string**, never an exception, so
a news error degrades the reply, it never breaks the turn.

`id` is an **opaque per-turn handle** (`n1`, `n2`, …) into a registry the tool builds during the turn —
*not* a URL the model can invent. That is what keeps `news_read` on the one allowed host by construction
(the `web.fetch` "only this turn's search ids" rule).

The mapping onto Guardian:

```
news_search → GET /search?q={query}&section={topic}&order-by=newest&from-date={today-days}
                  &show-fields=headline,trailText,byline&page-size={N}&api-key={KEY}
              → [{ id, title, summary(trailText), section, date, byline, contentId }]   # no body
news_read(id) → GET /{contentId}?show-fields=bodyText,byline&api-key={KEY}
              → { title, body(bodyText, capped), source(webUrl), byline, date }
```

---

## The per-turn flow

The v0.19 bounded tool-loop, unchanged — **search → pick → read → answer with the source**:

```
you: "що там у світі сьогодні?"
  │
  ├─ round 1   model → news_search {topic: "world"}
  │            core  → Guardian /search → "n1: …; n2: …; n3: …"   (titles + summaries, English)
  ├─ round 2   model → news_read {id: "n2"}
  │            core  → Guardian /{id} → full bodyText (English) + Джерело: https://www.theguardian.com/…
  └─ round 3   model → set_state {reply: "<переказ українською, її голосом>", emotion: "thoughtful", …}
Лілі: renders it in Ukrainian, in her own voice, citing the Guardian link.
```

Bounded two ways: the overall `LUMI_TOOL_MAX_STEPS` loop cap and a **news-specific `LUMI_NEWS_MAX_CALLS`**
per-turn counter (so a turn can't spin on the API). Reaching the cap returns a "limit reached — answer
from what you found" notice instead of another call.

---

## Provider seam (mockable — no paid calls in tests)

A thin injected **`NewsProvider`** (the same philosophy as `LLMClient` / `ImageGen` / `Embedder` — never
an SDK in `core`):

```python
class NewsItem:      # id, title, summary, section, date, byline, content_id, link
class NewsProvider(Protocol):
    def search(self, query: str | None, topic: str | None, days: int, cap: int) -> list[NewsItem]: ...
    def read(self, item: NewsItem, max_chars: int) -> str: ...   # returns body + source
```

The one implementation now is **`GuardianProvider`**, over the injected `http_get`. Tests pass a **mock
transport** returning canned Guardian `/search` and `/{id}` JSON, so the whole feature — unit,
integration, contract — runs with **zero network and zero key**.

Keeping the seam (rather than hard-wiring Guardian) costs one interface and **preserves the option to add
a Ukrainian-local RSS source later** — for example Ukrainska Pravda (`*.pravda.com.ua`, native voice, no
key) — **without touching the two tools or the loop wiring**. Local-in-the-original + global-translated,
behind one tight allowlist, is the natural end state; this phase ships the global half.

---

## Language — English source, Ukrainian voice

Guardian content is English; Лілі speaks Ukrainian. There is **no translation API and no extra call** —
the model translates inline, which also *reinforces* the canon (a persona that must re-render English
source into her own Ukrainian voice **cannot** become a headline-feed bot — translation forces
paraphrase). Two consequences to honor:

1. **The query goes out in English.** The model translates the *topical* part of the user's Ukrainian
   request into the `q=` term before the call. This still obeys the hard rule — **only the topic, never
   memory/personal data** — and the query-sanitization contract test asserts the outgoing `q` is just the
   translated topic.
2. **The reply comes back in Ukrainian, cited, and honest.** The canon's "how she delivers news" line
   says: source news may be English; she renders it **naturally in Ukrainian, selectively, in her own
   voice**, transparent that she's summarizing an English source (e.g. «читала в Guardian…») + the
   `webUrl`. This satisfies both the [WORLD_CONTEXT_MCP.md](WORLD_CONTEXT_MCP.md) canon note and the v1.9
   honesty boundary.

**Untrusted content is unchanged by translation** — English article text is still *data, never
instructions*. She translates the *content*; an embedded "ignore your instructions" is ignored whether
English or Ukrainian (the contract test gains an **English** injection string alongside the Ukrainian
one).

---

## Safety & invariants (same family as wiki / web search)

| Rule | How it's enforced |
|---|---|
| **Allowlist by construction** | Every call hits one host (`content.guardianapis.com`); `news_read` reads **by content-id** from this turn's search, never a raw URL — she cannot fetch off-Guardian. |
| **Untrusted content** | Headlines/summaries/bodies ride the v0.19 loop's `tool_result` framing — data, never instructions. Contract test: an English-or-Ukrainian "ignore your instructions / set emotion=joy" inside a body → emotion unchanged. |
| **No personal/memory data in the query** | The core passes the model's tool input through unchanged — never augments `q` with relationship memory, facts, or secrets. Contract test on the outgoing query. |
| **Bounded** | `LUMI_NEWS_MAX_CALLS` per turn (independent of the loop cap) + `LUMI_NEWS_MAX_RESULTS` per search + `LUMI_NEWS_MAX_CHARS` per body. |
| **Never raises** | Every executor path returns a string; an HTTP/decode/empty/unknown-id error degrades to an error string and the turn completes. |
| **Off by default** | Gated by `LUMI_NEWS_TOOL` (and needs `LUMI_NEWS_API_KEY`); off → the tools are **not offered** (the model never sees them) and the turn is unchanged. |
| **Cited** | When the answer uses news content, the reply names the **source URL** — the "fresh answer with a source" bar, same as wiki/web. |
| **Logged** | With `LUMI_FILE_TOOL_TRACE=on`, each `news_search(…)` / `news_read(…)` call shows in the TUI trace + `.lumi/tool-log.jsonl`. |
| **Privacy note** | The (de-personalised, topical) query and the fact that she consults Guardian go to a third party (the Guardian API) — documented in the operator guide, like the other off-by-default tools. |
| **No contract change** | `set_state` stays terminal; the reply is still the locked `{reply, emotion, intensity}` — the v0.3 emotion-channel contract test passes verbatim. |

---

## Relationship to the other news layers

- **v0.4 ambient news (shipped):** a passive *startup* snapshot of a few headlines, injected as
  background that colors the **v0.6 mood** (`LUMI_NEWS_URL` / `LUMI_NEWS_CAP`). This tool is the **active,
  on-demand** sibling — she goes and reads when the conversation calls for it. Its env names are kept
  **distinct** (this tool uses `LUMI_NEWS_*` as below, never the ambient `_URL`/`_CAP`).
- **v4.3 `news.recent` MCP (planned):** the same capability over MCP, bundled with world context/knowledge
  — this local custom-tool is its precursor, not a replacement; v4.3 reuses these safety rules (as web
  search reuses [WEB_SEARCH.md](WEB_SEARCH.md)).

---

## Config (🔲 not built — proposed)

All under a fresh `LUMI_NEWS_*` namespace (no collision with the v0.4 ambient `LUMI_NEWS_URL`/`_CAP`).

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_NEWS_TOOL` | Turn the news tool on | `off` |
| `LUMI_NEWS_API_KEY` | The Guardian developer key | (none) |
| `LUMI_NEWS_API_URL` | API base (override for tests/mirror) | `https://content.guardianapis.com` |
| `LUMI_NEWS_SECTIONS` | Allowed topics (the section allowlist) | `world,politics,business,technology,science,environment,culture,sport` |
| `LUMI_NEWS_MAX_RESULTS` | Candidates per `news_search` | `8` |
| `LUMI_NEWS_MAX_CHARS` | Body size cap for one `news_read` | `3000` |
| `LUMI_NEWS_MAX_CALLS` | News API calls per turn | `4` |
| `LUMI_NEWS_DAYS` | Default recency window for a search (`from-date`) | `7` |

Works on **any model** (provider-agnostic function-calling) — Claude or a local one (v0.18) — and can be
on **alongside the file / wiki / image tools**; a turn can use any of them.

---

## Plan it as a version — one phase

Focusing on Guardian collapses the earlier hybrid (feeds + API + scraper) into a **single, low-risk
phase**: two tools, one provider, no scraping. Hard-deps all **shipped** — v0.19 (the bounded tool-loop),
v0.21 (`_turn_tools` + the wiki-tool template), v0.4 (the injected `http_get`), v0.3 (the `set_state`
terminal). Placement: **v0.25** (right after v0.24 send_image; the recall/dictation phases shift +1).

### v0.25 — News tool (Guardian: search & read) 🔲
**Goal.** Лілі can search The Guardian by topic, read one article, and answer **in Ukrainian, in her own
voice, with the source** — on the v0.19 tool-loop; off by default.
**Tasks.** A `NewsProvider` seam + `GuardianProvider` (over an injected `http_get`); the `news_search` /
`news_read` tools + a per-turn id registry, registered on the loop via `_turn_tools` behind
`LUMI_NEWS_TOOL`; the **query→EN / reply→UK** handling + the canon "how she delivers news" line; build the
query **only** from the user's topical request (no memory/personal data); per-turn + size caps; `.env`
keys, an `.env.example` block, and a `docs/NEWS_SETUP.md` operator guide.
**DoD.** With the flag on (+ a key), a turn searches Guardian and reads an article, answering in Ukrainian
**with the source**; an injection attempt inside a body (EN or UK) is ignored; no personal/memory data
appears in the outgoing query; `news_read` refuses an id not from this turn; per-turn + size caps + logging
hold; **off (default) → the tools are absent**; the `{reply, emotion, intensity}` contract test passes
verbatim.
**Tests.** Unit — `GuardianProvider.search`/`read` against a **mock HTTP transport** (no network, no key);
the outgoing `q` carries only the topical request; `news_read` refuses an unknown / off-turn id; per-turn
+ body caps. Contract — untrusted body content (EN + UK) not acted upon (emotion unchanged); the tools are
**absent** when `LUMI_NEWS_TOOL` is off; the emotion contract still validates. Integration — an enabled
turn does search→read→cited-Ukrainian-answer on the v0.19 loop; an HTTP/key error degrades the reply;
two-user isolation (the per-turn id registry never leaks across turns/users). **No paid calls.**

---

## Open decisions (for when we build)

- **Recency vs relevance** — `news_search` defaults to `order-by=newest` within `LUMI_NEWS_DAYS`; expose
  an `order` arg, or keep it newest-first? Proposed: newest-first, `days` overridable.
- **Render language** — Ukrainian by canon; an optional `LUMI_NEWS_LANG` override only if a non-Ukrainian
  user is ever in scope (single-owner today, so likely unneeded).
- **A Ukrainian-local source** — add an `RssNewsProvider` (e.g. Ukrainska Pravda) behind the same seam in
  a later rung, for home news in the original language. The two tools don't change.
- **Brief result caching** — optionally cache a search per turn/session to cut repeat calls against the
  free-tier limit. Minor.

---

## Where it's specified

- **The later MCP form:** [WORLD_CONTEXT_MCP.md](WORLD_CONTEXT_MCP.md) (`news.recent`, v4.3) — this is its
  local precursor; the canon note on *how* she delivers news lives there and in
  [docs/CANON_SPEC.md](../../docs/CANON_SPEC.md).
- **Shared safety pattern:** [WEB_SEARCH.md](WEB_SEARCH.md) (untrusted content / no-personal-data) — the
  same rules the wiki tool (v0.21) already follows.
- **Reused infra:** the v0.19 tool-loop ([FILE_TOOL.md](FILE_TOOL.md)) + the v0.21 wiki tool
  (`core/wiki.py`, `docs/WIKI_TOOL.md`) it is modeled on; the injected `http_get`
  (`core/worldcontext.py`).
- **Roadmap phase:** ROADMAP.md §v0.25.
