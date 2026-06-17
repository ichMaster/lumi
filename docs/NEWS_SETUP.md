# News tool — setup & usage (v0.25)

Let Лілі **read fresh news on demand** during a normal chat turn. Ask what's happening and she **searches
The Guardian** by topic (`news_search`), **reads one article** (`news_read`), and answers **in Ukrainian,
in her own voice, with the source**.

It is **off by default** (`LUMI_NEWS_TOOL`), uses **one outlet** (The Guardian — a single allowlisted
host), treats every article as **untrusted** (text inside it is information, never a command), and sends
**no personal data** in the query. It needs a **free Guardian developer key**.

> Operator guide, not a design spec. The design is in
> [specification/features/NEWS_TOOL.md](../specification/features/NEWS_TOOL.md).

---

## Quick start

1. **Get a free Guardian key** (≈2 min): register at
   [open-platform.theguardian.com](https://open-platform.theguardian.com/access/) → a *developer* key.
   It includes article text and is free for non-commercial use.
2. **Turn it on** in `.env`:
   ```ini
   LUMI_NEWS_TOOL=on
   LUMI_NEWS_API_KEY=your-guardian-key
   ```
3. **Restart the TUI** (`./lumi`).
4. **Ask for news:**
   ```
   що там у світі сьогодні?
   ```
   She calls `news_search {topic: "world"}`, picks an article, `news_read`s it, and tells you about it in
   Ukrainian — citing the Guardian link.

---

## The two tools

| Tool | What it does |
|---|---|
| **`news_search(query?, topic?, days?)`** | Searches Guardian and returns up to N **candidates** — title + one-line summary + an **opaque per-turn id** (`n1`, `n2`, …). No bodies (kept light). The discovery step. |
| **`news_read(id)`** | Reads **one** article **by an id from this turn's search** → its full text (capped) + the **source URL**. An id not from this turn's search is refused — so she can never fetch off Guardian. |

`topic` is a Guardian **section**: `world`, `politics`, `business`, `technology`, `science`,
`environment`, `culture`, `sport` (the `LUMI_NEWS_SECTIONS` allowlist). A topic outside the list is
ignored gracefully (the search runs without a section filter).

---

## English source, Ukrainian voice

The Guardian is English; Лілі speaks Ukrainian. There's **no translation API and no extra call** — the
model translates inline, which also keeps her a *persona*, not a headline feed:

- **The query goes out in English** — she translates the *topical* part of your request into the search
  term. Only the topic, **never** your relationship memory or personal details.
- **The reply comes back in Ukrainian, cited, and honest** — she summarises selectively, in her own
  voice, transparent that she's reading an English source (e.g. «читала в Guardian…») + the link.

---

## Safety (why it's safe to leave on)

- **One outlet, by construction.** Every call hits a single host (`content.guardianapis.com`); `news_read`
  works **by id from this turn's search**, never a raw URL — she structurally cannot reach another site.
- **Untrusted content.** If an article contains text like *"ignore your instructions"* (in English or
  Ukrainian), she reads it as **information only**, never a command (proven in the tests).
- **No personal data in the query.** The core passes the model's search term through unchanged — it never
  appends memory, facts, or secrets.
- **Bounded.** At most `LUMI_NEWS_MAX_CALLS` API calls per turn, `LUMI_NEWS_MAX_RESULTS` candidates per
  search, `LUMI_NEWS_MAX_CHARS` per article body. A bad key / HTTP error / empty result degrades to a
  notice, never a crash.
- **Off by default.** Nothing happens unless `LUMI_NEWS_TOOL=on` **and** a key is set.
- **Cited.** When the answer uses an article, the reply names the **source URL**.
- **Privacy note.** The (de-personalised, topical) query and the fact that she consults Guardian go to a
  third party (the Guardian API) — like the other off-by-default tools.

---

## Distinct from the v0.4 ambient news

This is **not** the startup news snapshot. The v0.4 **ambient** news (`LUMI_NEWS_URL` / `LUMI_NEWS_CAP`) is
a passive feed of a few headlines pulled **once at startup** that colors her **mood** in the background.
This v0.25 tool is the **active, on-demand** sibling — she **goes and reads** when the conversation calls
for it. The two use **separate** env namespaces and never collide.

---

## Configuration reference

All optional except `LUMI_NEWS_TOOL` + `LUMI_NEWS_API_KEY`. Restart the TUI after changing any of them.

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_NEWS_TOOL` | Turn the news tool on | `off` |
| `LUMI_NEWS_API_KEY` | The Guardian developer key | (none) |
| `LUMI_NEWS_API_URL` | API base (override for a mirror / tests) | `https://content.guardianapis.com` |
| `LUMI_NEWS_SECTIONS` | Allowed topics (the section allowlist) | `world,politics,business,technology,science,environment,culture,sport` |
| `LUMI_NEWS_MAX_RESULTS` | Candidates per `news_search` | `8` |
| `LUMI_NEWS_MAX_CHARS` | Body size cap for one `news_read` | `3000` |
| `LUMI_NEWS_MAX_CALLS` | News API calls per turn | `4` |
| `LUMI_NEWS_DAYS` | Default recency window for a search (`from-date`) | `7` |

The news tool can be on **alongside** the file / Wikipedia / image tools; a turn can use any of them.

---

## Troubleshooting

- **She doesn't fetch news.** Confirm `LUMI_NEWS_TOOL=on` **and** `LUMI_NEWS_API_KEY` is set, then restart
  the TUI. Ask explicitly ("почитай новини про …").
- **"error" / nothing comes back.** Check the key is valid and not rate-limited (confirm current limits at
  signup); a bad key / HTTP error degrades to a notice and the turn carries on.
- **Wrong topic.** `topic` must be one of the `LUMI_NEWS_SECTIONS` sections; an unknown one is ignored and
  the search runs unfiltered.
- **See the calls.** With `LUMI_FILE_TOOL_TRACE=on`, each `news_search(…)` / `news_read(…)` shows in the
  TUI trace + `.lumi/tool-log.jsonl`.
