# Recall tool — setup & usage (v0.31)

Let Лілі **search her own memory on demand** during a normal chat turn. When the relevant thing *isn't*
the literal words you just typed — *"а що вони казали про брата?"* — she can issue a **targeted** memory
query mid-turn and weave the result in, in her own voice. It's the **"pull"** that complements the
automatic per-turn RAG (v0.17) **"push"**, which already surfaces memory relevant to your message into
every reply unasked.

The one thing that sets it apart from her other tools: a recall result is **her own past — trusted
history**, not external data. So she treats it as a **recollection** (speaks *from* it), not as something
to distrust or fact-check.

It is **off by default**, runs **only over your own messages** (per-user isolation), and adds no new
external surface beyond what semantic recall already does.

> Operator guide, not a design spec. The design is in [ROADMAP.md §v0.31](../specification/ROADMAP.md) +
> the v0.31 issue breakdown; the recall line it builds on is in
> [SEMANTIC_RECALL.md](../specification/features/SEMANTIC_RECALL.md).

---

## Quick start

1. **Make sure semantic recall is on** (the tool needs the index + an embedder):
   ```ini
   LUMI_RECALL=on
   LUMI_EMBED_PROVIDER=local        # or voyage / openai (+ its key)
   ```
2. **Turn the tool on** in `.env`:
   ```ini
   LUMI_RECALL_TOOL=on
   ```
3. **Restart the TUI** (`./lumi`) — settings are read at startup.
4. **Ask her something that lives in her memory but isn't your literal words:**
   ```
   нагадай, що я колись казав про свого брата?
   ми це вже обговорювали раніше?
   ```

Within the turn she calls `recall("…")` with a query **she** composes (often *different* from your
message), gets back a few dated moments, and answers from them as her own memory.

---

## The tool

| Tool | What it does |
|---|---|
| **recall** | Searches **your** past conversations by meaning for a query she chooses, and returns the top few relevant **moments** (a dated dialogue snippet + when), capped. |

It's the same `/recall` search she's always had — only now **she** can reach for it mid-thought, with a
query of her own and across **multiple hops** (search → refine), instead of only *you* running `/recall`.

### Push vs pull

- **Auto-RAG (v0.17 — the "push") stays the default.** Every reply already gets the moments most relevant
  to *your message*, injected automatically. Keep `LUMI_RAG=on` for that.
- **The recall tool (the "pull") adds what the push can't serve:** a query **≠ your message**, and
  **iterative** search during her reasoning. The two compose; the tool result is **deduped** against what
  the auto-RAG block already injected, so nothing is shown twice.

### Scope by date

A recall can be **restricted to a date range** — `recall(query, after, before)` for her, and
`/recall <query> after:РРРР-ММ-ДД before:РРРР-ММ-ДД` for you. The range is half-open `[after, before)`.
It's still a **meaning** search, just confined to those days — handy for *"що ми обговорювали того тижня?"*
(For the *raw, verbatim* messages of a day — no meaning search — use the **date tool** in the overview below.)

---

## Why it's safe to leave on

- **Per-user, never crosses users.** The search runs **only over your own** vectors — the same isolation
  invariant as the rest of her memory (pinned by a contract test).
- **Her own memory, framed as such.** Unlike the wiki / news / file tools (whose results are framed as
  **untrusted** data), a recall result is **trusted history** — she's recalling, not reading a stranger.
  It's still *her* memory of *your* conversations, so it opens no new external surface.
- **Bounded.** At most `LUMI_RECALL_TOOL_MAX_CALLS` recall calls per turn; each returns up to
  `LUMI_RECALL_TOOL_K` moments. A no-hit or an embedder hiccup degrades to a short notice — it never hangs
  or breaks the turn.
- **Off → exactly v0.17.** With `LUMI_RECALL_TOOL=off` the tool isn't offered at all, and a turn behaves
  exactly as before (auto-RAG only).
- **Logged.** With `LUMI_FILE_TOOL_TRACE=on`, her `recall(…)` calls show in the TUI trace +
  `.lumi/tool-log.jsonl` (as a 🧠 recall marker).

---

## Configuration reference

`LUMI_RECALL_TOOL` is the switch; the rest are optional. **It needs `LUMI_RECALL=on` + a configured
embedder** (`LUMI_EMBED_PROVIDER`) — without them it stays off no matter what. Restart the TUI after a
change.

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_RECALL_TOOL` | Offer the model-callable `recall()` tool | `off` |
| `LUMI_RECALL_TOOL_K` | How many past moments one `recall()` call returns | `5` |
| `LUMI_RECALL_TOOL_MAX_CALLS` | Per-turn cap on `recall()` calls (bounds multi-hop search) | `3` |
| *(requires)* `LUMI_RECALL` | The semantic-recall index must be on | `off` |
| *(requires)* `LUMI_EMBED_PROVIDER` | The embedder: `local` / `voyage` / `openai` (+ key) | `local` |

It composes with **chunking (v0.30)** — if `LUMI_RAG_CHUNK=on`, recall returns the relevant *passage* of a
long message, not the whole thing. It works on **any model** with function-calling, and can be on
alongside the file / wiki / news tools (a turn may use any of them).

---

## Troubleshooting

- **"She never recalls on her own."** Check all three: `LUMI_RECALL_TOOL=on`, `LUMI_RECALL=on`, and a
  working embedder (`LUMI_EMBED_PROVIDER` + its key for a cloud one) — then **restart** the TUI. You can
  also just ask her to *"пригадай…"* explicitly.
- **"She repeats what's already on screen."** She shouldn't — recall is deduped against the live window +
  the auto-RAG block. A repeat is most likely the auto-RAG *push*, not the tool.
- **"It found nothing."** The memory may genuinely not be there, or it's all already in the current
  conversation (deduped out). Run `/recall <query>` yourself to see the raw matches.
- **Want only the automatic behaviour?** Set `LUMI_RECALL_TOOL=off` — you keep auto-RAG (the push) with no
  tool calls.

---

## Лілі's tools at a glance

`recall` is one of several tools she can call during a turn — all on the same **v0.19 bounded tool-loop**,
all **off by default**, all **per-user** (sandboxed / scoped to you), all **bounded** (per-turn caps) and
**graceful** (a tool error degrades the reply, never hangs the turn). Each has its own setup guide:

| Family | Tool calls | What she can do | Enable flag | Setup guide |
|---|---|---|---|---|
| **File** | `list_files` · `find_in_file` · `read_file` · `create_file` · `append_file` · `stat_file` · `create_folder` · `copy_file` | List / search / read, and create / append / copy files in her per-user sandbox (non-destructive — no overwrite/delete) | `LUMI_FILE_TOOL` | [FILE_TOOL_SETUP.md](FILE_TOOL_SETUP.md) |
| **Wikipedia** | `wiki_search` · `wiki_read` | Look something up on Wikipedia and answer with the source | `LUMI_WIKI` | [WIKI_SETUP.md](WIKI_SETUP.md) |
| **News** | `news_search` · `news_read` | Read fresh Guardian news on a topic and answer, cited | `LUMI_NEWS_TOOL` | [NEWS_SETUP.md](NEWS_SETUP.md) |
| **Image** | `view_image` · `generate_image` · `send_image` | See & describe a picture, generate a PNG (**paid**), send one to your Telegram | `LUMI_IMAGE` | [IMAGE_SETUP.md](IMAGE_SETUP.md) |
| **Web** | `web_lookup` | Pull a fresh, grounded answer from the live internet (**paid**); also `/web` | `LUMI_WEB_LOOKUP` | [WEB_LOOKUP_SETUP.md](WEB_LOOKUP_SETUP.md) |
| **Journal** | `journal_write` · `journal_read` · `journal_list` | Write & reread her day-summary diary; also `/journal` | `LUMI_JOURNAL` | [JOURNAL_SETUP.md](JOURNAL_SETUP.md) |
| **Recall** *(this doc)* | `recall` | Search her own memory **by meaning** on demand (date-scopable); also `/recall` | `LUMI_RECALL_TOOL` | [RECALL_TOOL_SETUP.md](RECALL_TOOL_SETUP.md) |
| **Messages by date/id** | `messages_on` · `messages_between` · `message_context` | Fetch her **raw, verbatim** messages for a day / range, or a **specific message (by `#id` or `ts`) ± K context** — no meaning search | `LUMI_DATE_TOOL` | *(this doc)* |

**Recall vs the date tool.** `recall` searches *by meaning* and returns the most relevant *moments*
(optionally scoped to a date range); the **date tool** returns the *raw, verbatim* messages of a **specific
day or range**, straight from the store — no embedding. Use recall for "what did we say about X", the date
tool for "what did we talk about **on the 13th**". The date tool is gated by **`LUMI_DATE_TOOL`** (+
`LUMI_DATE_TOOL_MAX_CHARS` / `_MAX_DAYS` / `_MAX_CALLS`); it needs **no embedder** — only the message store.

**Chaining recall → a specific message.** Each `/recall` / recall-tool moment now shows the message **time**
(`HH:MM`) and a short **`#id`** on the matched line. She can pass either into **`message_context`** —
`message_context(msg_id="a1b2c3d4")` or `message_context(ts="2026-06-13T21:04")` — to pull up that exact
message with its **K surrounding messages** (the anchor marked `← (це)`). So: recall finds *what*, then
`message_context` opens *the moment around it*.

**Trust.** Every tool result is treated as **untrusted data** — information she may read, **never**
instructions she obeys — *except* the memory tools (**`recall`**, **`messages_on`** / **`messages_between`** /
**`message_context`**), whose results are **her own memory** (trusted), and the **journal**, which is her own
writing. The wiki /
news / web / file / image results are all untrusted.

With `LUMI_FILE_TOOL_TRACE=on`, every tool call she makes shows in the TUI trace + `.lumi/tool-log.jsonl`.
