# Wikipedia tool — setup & usage (v0.21)

Let Лілі **look something up on Wikipedia** during a normal chat turn. She can search for an article and
read its summary, then answer **with the source** — useful for a quick factual check she'd otherwise have
to hand-wave.

It is **off by default**, uses a **free REST API (no key)**, sends **no personal data**, and treats what
it reads as **untrusted information** (never instructions).

> Operator guide, not a design spec. The design is in [ROADMAP.md §v0.21](../specification/ROADMAP.md);
> the safety pattern is shared with [WEB_SEARCH.md](../specification/features/WEB_SEARCH.md).

---

## Quick start

1. **Turn it on** in `.env`:
   ```ini
   LUMI_WIKI=on
   ```
2. **Restart the TUI** (`./lumi`) — settings are read at startup.
3. **Ask her** something factual:
   ```
   хто такий Григорій Сковорода?
   що таке гемолімфа?
   ```

Within the turn she runs `wiki_search` to find the article, `wiki_read` to read its summary, and replies
grounded in it — citing the source URL.

---

## The two tools

| Tool | What it does |
|---|---|
| **wiki_search** | Searches Wikipedia for a query and returns candidate articles (title + short description). |
| **wiki_read** | Reads one article's summary (a clean, HTML-free extract) and returns it **with the source URL**. |

The normal flow is **search → pick → read → answer with the source**. (The tool *names* are
`wiki_search` / `wiki_read`; the roadmap calls them `wiki.search` / `wiki.read`.)

---

## Safety (why it's safe to leave on)

- **No personal data leaves.** The search query is built **only from your request** — never from her
  memory of you, stored facts, or any secret.
- **What she reads is untrusted.** If an article contained text like *"ignore your instructions…"*, she
  reads it as **information only**, never as a command (the same rule as the file tool and web search).
- **Bounded.** At most `LUMI_WIKI_MAX_CALLS` wiki calls per turn; each summary is capped at
  `LUMI_WIKI_MAX_CHARS`. A network error degrades the reply, never hangs the turn.
- **No key, no scraping.** It uses Wikipedia's free REST API (`opensearch` + `page/summary`) — a clean
  extract + a source URL, no HTML scraping, no provider key.
- **Off by default.** Nothing happens unless `LUMI_WIKI=on`.
- **Logged.** With `LUMI_FILE_TOOL_TRACE=on`, her `wiki_search(…)` / `wiki_read(…)` calls show in the
  TUI trace + `.lumi/tool-log.jsonl`.

---

## Configuration reference

All optional except `LUMI_WIKI`. Restart the TUI after changing any of them.

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_WIKI` | Turn the Wikipedia tool on | `off` |
| `LUMI_WIKI_LANG` | Language edition(s), comma-separated; first with a hit wins | `uk,en` |
| `LUMI_WIKI_BASE_URL` | Override the host (default `https://{lang}.wikipedia.org`) | (default) |
| `LUMI_WIKI_MAX_CHARS` | Max characters of one article summary | `1500` |
| `LUMI_WIKI_MAX_CALLS` | Max wiki calls per turn | `4` |

It works on **any model** (provider-agnostic function-calling) — Claude or a local one (v0.18). It can be
on **alongside the file tool**; a turn can use either.

---

## Troubleshooting

- **"She doesn't look things up."** Check `LUMI_WIKI=on` and that you **restarted** the TUI.
- **"Nothing found."** The article may not exist in the first configured language; add another to
  `LUMI_WIKI_LANG` (e.g. `uk,en`) so she falls back.
- **A reply with no source.** She may have answered from her own knowledge without calling the tool —
  ask her explicitly to *check Wikipedia*.
