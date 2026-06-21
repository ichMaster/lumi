# Лілі's directives & placeholders — reference (v0.33)

A single at-a-glance reference for the two authored vocabularies of her inner life:

- **`%directives`** — *mental acts* her mind runs (`trigger → seed → generate → record → maybe surface`).
  They are **internal**, not chat: she fires them proactively (the idle nudge), and **you can also type one**
  to nudge her. Distinct from **`/commands`** (which *read* state and show it to you) and plain **chat**
  (which she *speaks*). Most are **silent** (curating her interior); `!` after the name forces it open
  (`%wonder!`).
- **`{placeholders}`** — `{name}` tokens that authored prompts and directive topics may contain; the core
  expands them to **live state** at fire time. Unknown tokens stay literal.

> Reference, not a design spec. Source of truth: `core/thoughts.py` (`REGISTRY`) + `core/placeholders.py`
> (`PLACEHOLDER_NAMES`). Design: [ROADMAP §v0.33](../specification/ROADMAP.md),
> [THOUGHT_STREAM.md](../specification/features/THOUGHT_STREAM.md),
> [TOOL_THOUGHTS.md](../specification/features/TOOL_THOUGHTS.md).

---

## The directives (17)

Everything below the v0.12 base is **off by default**: a tool-thought needs the master gate
**`LUMI_THOUGHT_TOOLS`** *and* its per-family flag *and* the underlying tool/sandbox. Off → the directive is
**absent** (typing it is treated as plain chat).

| `%directive` | She… | Tools (think-path) | Gated by | Notes |
|---|---|---|---|---|
| **`%think`** | muses quietly to herself | — | `LUMI_THOUGHTS` | v0.12; always on, tool-less |
| **`%wonder`** | lets curiosity/imagination roam | — | `LUMI_THOUGHTS` | v0.12; always on, tool-less |
| **`%note`** | jots a thought into her diary | — (code-appends) | `+ LUMI_FILE_TOOL` | the thought is **code-appended** to `journal/<date>.md` (non-destructive) |
| **`%review`** | re-reads her own notes & muses | `list/find/read/search/read_around/stat` | `+ LUMI_FILE_TOOL` | read-only |
| **`%explore`** | wanders her files (read **and** write) | the file read tools **+** `create/append/create_folder/copy` | `+ LUMI_FILE_TOOL` | non-destructive writes |
| **`%journal`** | writes a day-summary diary entry | `journal_write/read/list` | `+ LUMI_THOUGHT_JOURNAL + LUMI_JOURNAL` | the v0.28 journal tool (its own dedicated root) |
| **`%lookup`** | a quick Wikipedia check | `wiki_search/wiki_read` | `+ LUMI_THOUGHT_WIKI + LUMI_WIKI` | cited; query **de-identified** |
| **`%learn`** | a chosen deep-read on Wikipedia | `wiki_search/wiki_read` | `+ LUMI_THOUGHT_WIKI + LUMI_WIKI` | cited; query **de-identified** |
| **`%catchup`** | "що там у світі?" — one news item | `news_search/news_read` | `+ LUMI_THOUGHT_NEWS + LUMI_NEWS_TOOL` | Guardian; Ukrainian, cited; query **de-identified** |
| **`%brief`** | a paced daily news catch-up | `news_search/news_read` | `+ LUMI_THOUGHT_NEWS + LUMI_NEWS_TOOL` | Ukrainian, cited; query **de-identified** |
| **`%search`** | looks it up on the live internet | `web_lookup` | `+ LUMI_THOUGHT_WEB + LUMI_WEB_LOOKUP` | **paid**; Gemini grounded; query **de-identified** |
| **`%events`** | scans what's recent/upcoming (dated) | `web_lookup` | `+ LUMI_THOUGHT_WEB + LUMI_WEB_LOOKUP` | **paid**; date-anchored to today |
| **`%recall`** | lets a memory resurface | `recall` | `+ LUMI_RECALL_TOOL` | **inward → results TRUSTED**; no de-id |
| **`%prompt`** | does what *you* asked, as her own act | `*` (any enabled) | `+ LUMI_THOUGHT_PROMPT` · **owner-only** | the **topic is the instruction**; de-id **exempt**; results untrusted |
| **`%gaze`** | looks again at a sandbox picture | `view_image` | `+ LUMI_THOUGHT_IMAGE + LUMI_IMAGE` | read-only |
| **`%imagine`** | renders an inner image (a PNG) | `generate_image` | `+ LUMI_THOUGHT_IMAGE + LUMI_IMAGE` | **paid**; create-only; sub-cap `LUMI_THOUGHT_IMAGINE_CAP` |
| **`%share`** | sends you a picture, as a gift | `send_image` | `+ LUMI_THOUGHT_IMAGE + LUMI_IMAGE` · **owner-only** | → Telegram; **no-op without the bridge** |

**Trust.** Every tool result feeds back **untrusted** (data she may read, never instructions) — *except*
**`%recall`** (her own memory → trusted) and the **file/journal** tools (her own writing).

**De-identification.** A thought-driven **external** query/prompt (`%lookup`/`%learn`/`%catchup`/`%brief`/
`%search`/`%events` and `%imagine`'s gen prompt) is **de-identified** — only the topical/creative part leaves
(proper-noun stems from her own facts are redacted). **`%prompt` is exempt** (you authored it).

**Surfacing in the TUI.** While a directive runs, the status line shows `✦ %name · tool…`; with
**`LUMI_THOUGHT_SURFACE=on`** a subtle chat-log line marks the act (`✦ Лілі читає новини…`). Off → invisible.

---

## The placeholders (18)

Lazy, **`""`-on-empty** (the token disappears), **isolation-aware** where per-user. Unknown `{tokens}` stay
literal.

| `{placeholder}` | Resolves to | Scope |
|---|---|---|
| `{mood}` | today's mood resolution (v0.6) | global |
| `{closeness}` | the closeness level by name (v0.10) | **per-user** |
| `{recent}` | the recent conversation tail | per-session |
| `{last_thought}` | her most recent thought | **per-user** |
| `{thoughts}` | her recent thoughts | **per-user** |
| `{now}` / `{today}` | date-time / date (from the clock) | — |
| `{weekday}` | the local weekday, in Ukrainian (v0.33) | — |
| `{user}` | the active `user_id` | **per-user** |
| `{world}` | the v0.4 ambient line (calendar / weather / news) | global |
| `{ambient_news}` | the v0.4 startup news snapshot (topical only) | global |
| `{section}` | the first configured news section | — |
| `{last_image}` | the newest image in her sandbox (v0.33) | **per-user** |
| `{plan}` · `{need}` · `{interest}` · `{hungriest_need}` · `{gap}` | `""` for now — placeholders for the **v1.1 inner life / needs** | global |

---

## See also

- Tool setup guides + the "tools at a glance" table: [RECALL_TOOL_SETUP.md](RECALL_TOOL_SETUP.md),
  [FILE_TOOL_SETUP.md](FILE_TOOL_SETUP.md), [WIKI_SETUP.md](WIKI_SETUP.md), [NEWS_SETUP.md](NEWS_SETUP.md),
  [IMAGE_SETUP.md](IMAGE_SETUP.md), [WEB_LOOKUP_SETUP.md](WEB_LOOKUP_SETUP.md), [JOURNAL_SETUP.md](JOURNAL_SETUP.md).
- The autonomous clock that will *fire* these on a schedule: **v0.34** ([THOUGHT_SCHEDULER.md](../specification/features/THOUGHT_SCHEDULER.md)).
