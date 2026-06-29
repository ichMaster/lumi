# Lumi

Lumi is a private text persona named **Лілі (Lili)** — a companion with a stable
character ("canon"), layered memory, and a structured emotion channel. An
interface-independent **core** binds two axes: Лілі's growing capabilities and a
growing interface (an in-process TUI first, a client/server platform later).

See [specification/](specification/) for the design (MISSION, ARCHITECTURE,
ROADMAP, EMOTION) and [docs/](docs/) for implementation references
([MEMORY.md](docs/MEMORY.md), [STYLES.md](docs/STYLES.md)). This is a
**spec-first** repository built version by version.

## Current version

**0.39 — Gemini engine: Google Gemini as a switchable backend.** A **third frontier engine** behind the one
`LLMClient` seam — chat + the structured emotion field + the **function-calling tool-loop** + **thinking →
the think-box** — switchable via **`/model gemini`** ↔ `/model opus`. Reuses the repo's existing Gemini
`urllib` caller + `GEMINI_API_KEY` (so the transport is install-free). Two risks were designed in up front
(the gpt-5.5 lesson): a **safety probe** confirmed Лілі's intimate register survives Gemini's filters
(`BLOCK_NONE`), and the **schema-vs-tools split** is baked into the loop. The verified model id is
**`gemini-3.1-pro-preview`**. With `LUMI_THINKING=on` Gemini's reasoning summary fills the think-box — so
the v0.38 three-voice torg shows. **No contract change**; the Anthropic path is untouched; off →
byte-identical. See **[docs/GEMINI_ENGINE.md](docs/GEMINI_ENGINE.md)** + **[docs/MODELS_SETUP.md](docs/MODELS_SETUP.md)**.

**0.39.1** hardens the Gemini reply channel: it **salvages** code-style tool calls (```` ```tool_code ````/`<tool_code>`)
into native calls, **strips** leaked tool-protocol scaffolding (`<tool_code>` + a hallucinated `<api_response>`)
and `<t_think>` reasoning tags, and **unwraps** `<p>…</p>` HTML — so the reply never leaks scaffolding nor
vanishes — plus reserves answer tokens **on top of** the thinking budget (no more empty replies) and fixes the
image tools (IMAGE-only modality, no double `art/`, bare-name `view_image`). **`gemini-2.5-pro`** is now the
recommended stable id (the preview's 250 req/day cap is tight).

Builds on **0.38 — Inner Voice: the authored three-voice think-phase instruction.** Лілі's pre-reply reasoning moves
from a hardcoded directive into an **editable `core/inner_voice.md`** authored as her **three-voice
negotiation** (Імпульс / Тверезість / Стандарт) weighing **mood** (v0.6/0.8) + **closeness** (v0.10) —
**no new engine** (reuses the v0.37 think infra), **no contract change**, off → byte-identical. A
**`LUMI_INNER_VOICE`** toggle swaps it for the generic directive; **`LUMI_THINK_SHOW`** (debug/open/off)
controls the think-box, and the monologue is **logged but never persisted** to long-term memory.
Provider-agnostic — it shapes reasoning on Opus *and* gpt-5.5. See
**[specification/features/INNER_VOICE.md](specification/features/INNER_VOICE.md)**.

Builds on **0.37 — OpenAI engine: tool-loop + runtime model toggle (GPT-5.5 ↔ Opus 4.8).** A non-Anthropic frontier
model becomes a real Opus alternative — the bounded **tool-loop** is ported to **OpenAI function calling** (so
the file / wiki / news / web / journal / image tools and the `%`-thought-tools work on **GPT-5.5 /
DeepSeek-V4-Pro**), **`LUMI_EFFORT`** is passed through as `reasoning_effort`, and a **`/model`** TUI command
swaps the engine **mid-session** (no restart, config aliases via **`LUMI_MODEL_ALIASES`**). For GPT-5's
reasoning models a dedicated **Responses-API path** (auto-selected by id; **`LUMI_OPENAI_RESPONSES`** /
**`_SUMMARY`**) carries tools + effort + a **visible think-box** together — from OpenAI's `reasoning.summary`
or an in-band **`thinking_summary`** field (single turn) — with **Opus's real reasoning never shadowed**. The
Anthropic path is **byte-identical**; no contract change; no paid calls in tests. See
**[docs/MODELS_SETUP.md](docs/MODELS_SETUP.md)** + **[docs/GPT55_SWITCH_AND_TOOL_LOOP.md](docs/GPT55_SWITCH_AND_TOOL_LOOP.md)**.

Builds on **0.36 — Lean memory III: the facts tier.** Facts now reach the prompt **three ways** (mirroring messages):
an always-injected **identity-core** — the `core`-flagged facts (name, key relationships, **boundaries &
agreements**), re-ranked to **`LUMI_FACTS_CORE_MAX`** at each session start (boundaries pinned) and injected
**instead of** the digest behind **`LUMI_FACTS_CORE_ONLY`**; a per-turn **auto fact-RAG** push
(**`LUMI_FACTS_RAG`** — a `# Релевантні факти` block of the top-K relevant *non-core* facts, deduped against
the core); and the **`recall(scope=facts)`** pull tool (each `LongTermFact` embedded as a `kind="fact"`
vector; **`LUMI_RECALL_SCOPE`**). A **facts-hygiene** path adds an additive **`obsolete`** flag — excluded
from *every* fact path, kept in the store for audit — curated by a **`/review-facts`** Claude Code skill
(propose → review → apply; never auto-obsoletes a core fact). Additive contract changes
(`VectorRecord.kind`, `LongTermFact.core`/`obsolete`); off by default, byte-identical when off. See
**[docs/PROMPT_OPTIMIZATION_II.md](docs/PROMPT_OPTIMIZATION_II.md)**.

Builds on **0.35 — Lean memory II: the conversation tier.** The `## Останні розмови` block gets **two
orthogonal controls**: **`LUMI_SESSION_DETAIL_N`** (*how many* recent sessions — unset = all · `0` = none ·
`N` = last N) and **`LUMI_SESSION_FORMAT`** (**`summary`** full / **`gist`** one line); a gisted session's
detail stays one query away via **auto-RAG** and **`recall`** / **`messages_on`** / **`messages_between`**.
Documented in **[docs/MEMORY_SESSION_LOGIC_UK.md](docs/MEMORY_SESSION_LOGIC_UK.md)** (UA).

Builds on **0.34's lean memory (tool-pull).** The first slice of moving the verbose
memory tiers from *injected* to *pulled* (index in the prompt, body fetched by a tool). The **day/week
digests** can render as a **one-line dated index** instead of paragraphs (`LUMI_MEMORY_INDEX`, off by
default; off → byte-identical) — she pulls the verbatim day via `messages_on(date)` when she needs it; a
**`/regen-summaries`** command (and `Core.regenerate_summaries()`) applies the new format to existing digests
**losslessly** (rebuilt from the kept session summaries — the lazy refresh skips unchanged days). The
**style palette** is compressed (~26%, every form-limit + voice anchor kept). Low-risk, reversible, no
contract change. The riskier tiers follow as their own phases: conversations **v0.35**, facts **v0.36**,
thoughts **v0.37**. See **[docs/PROMPT_OPTIMIZATION_II.md](docs/PROMPT_OPTIMIZATION_II.md)**.

Builds on **0.33's tool-using thoughts.** The v0.12 thought-stream gains a
**think-path tool-loop**, so a `%directive` can *use a tool* and still end in a thought (its terminal stays
a thought, never `set_state`). A small family lands on one table-driven engine: **file** (`%note` →
`notes/<date>.md`, `%review`, `%explore`, **`%journal`** → a full day-review via `journal_write`), **wiki**
(`%lookup`/`%learn`), **news** (`%catchup`/`%brief`), **web** (`%search`/`%events`), **image**
(`%gaze`/`%imagine`/`%share`), **memory** (`%recall`), and the **open** **`%prompt`** — you hand her any
task and she does it as her own act (**freeform**: the output follows your instruction, not a 1–2-sentence
cap). Every directive records a thought and can **also** save it via an **output sink** — `%name!` (chat) ·
`%name >notes` · `%name >path/file.md` · `%name >folder/` (code-owned, sandboxed). A thought-driven
**external** query is **de-identified** (only the topical part leaves) — except a place or name **you
explicitly type**, which survives; **`%prompt`** is fully exempt (you authored it). Tool results stay
**untrusted**; everything is **per-user**, **owner-gated where it reaches out**, and **off by default**
(`LUMI_THOUGHT_TOOLS` + per-family flags). See **[docs/THOUGHT_DIRECTIVES.md](docs/THOUGHT_DIRECTIVES.md)**.

Builds on **0.32's file tool IV**: read-only search and read *across* the sandbox — **`search_files`**
(full-text across files → matching files + lines + line numbers, the cross-file twin of `find_in_file`), an
**`after`/`before` date filter** on `list_files`, and **`read_around(path, line, k)`** (a file's anchor line
± K) — so she can search to *what*, then open *the lines around it*. Reuses **`LUMI_FILE_TOOL`**, per-user
isolated, **off by default**.

Builds on **0.31's recall tool**: a model-callable **`recall()`** so Лілі searches her own memory by meaning
**mid-turn** — the **"pull"** that complements the automatic per-turn auto-RAG **"push."** A recall result is
**her own past — trusted history**, deduped against the live window; **scopeable by date range**, with
by-date tools + **`message_context`** to open a specific moment. See
**[docs/RECALL_TOOL_SETUP.md](docs/RECALL_TOOL_SETUP.md)**.

Builds on **0.30's chunking**: a long message is split into ~`chunk_chars` passages, each embedded as its
**own** vector, so search ranks per **chunk** and `/recall` shows the matched **passage** — *"search fine,
show coarse."* See
**[specification/features/SEMANTIC_RECALL_CHUNKING.md](specification/features/SEMANTIC_RECALL_CHUNKING.md)**.

Builds on **0.29's file tool III**: A small extension of her file tool:
Лілі can now **see a file's created/modified dates** (on `list_files` + a new **`stat_file`**), **make a
folder** (**`create_folder`**), and **copy a file** (**`copy_file`**) in her per-user sandbox — all on the
shipped v0.19/v0.20 executor, still **create-only** (no overwrite, no delete, no move). `copy_file` is
bounded by a separate **`LUMI_FILE_COPY_MAX`** source-size cap; the created date uses the OS birth-time
where available, falling back to the metadata-change time. Reuses the existing **`LUMI_FILE_TOOL`** flag
(no new toggle), per-user isolated, **off by default**. **No emotion-contract / core change.** See
**[docs/FILE_TOOL_SETUP.md](docs/FILE_TOOL_SETUP.md)**.

Builds on **0.28's journal tool**: at the close of a worthwhile day Лілі writes a **personal, literary
summary of the day** in her own voice (**`journal_write`**) and **rereads previous days by date**
(**`journal_read`** / **`journal_list`**, plus a **`/journal`** command). She decides the prose; **code
auto-stamps** each entry with the day's **mood** (v0.6), **biorhythms** (v0.8), and **astrology forecast**
— honest and matching `/mood` + `/biorhythm` — and every entry opens with its own **`## HH:MM`** section.
Non-destructive, in a **dedicated per-user root**, off by default (`LUMI_JOURNAL`). See
**[specification/features/JOURNAL.md](specification/features/JOURNAL.md)**.

Builds on **0.27's web lookup (Gemini grounded search) + the `/web` command**: ask what's *happening now*
or *coming up* and Лілі pulls a **fresh, grounded answer from the live internet** via **Gemini + Google
Search grounding** (`web_lookup`) — **answer-first, in Ukrainian, in her own voice**, **date-anchored to
today**, behind an injected **`GeminiSearch`** seam. Untrusted answer, no personal data in the query,
**bounded + paid** (reuses `GEMINI_API_KEY`), **off by default** (`LUMI_WEB_LOOKUP`). See
**[docs/WEB_LOOKUP_SETUP.md](docs/WEB_LOOKUP_SETUP.md)**.

Builds on **0.26's local dictation (STT) + Telegram voice-in** (`LUMI_DICTATION` / `LUMI_TELEGRAM_STT`):
talk *to* Лілі — a local process **hears your speech and types it into the chat** (Ctrl+D), the mirror of
the v0.14 voicer, via a `/voice` STT adapter (Deepgram / ElevenLabs Scribe / offline Whisper); the same
adapter transcribes an inbound Telegram voice note. Off by default; the core can't tell typed from dictated.
See **[docs/DICTATION_SETUP.md](docs/DICTATION_SETUP.md)** + **[docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md)**.

Builds on **0.25's news tool (Guardian)**: ask what's happening and Лілі calls **`news_search`** →
**`news_read`** (one outlet, single-host allowlist, an injected `NewsProvider` seam), answering **in
Ukrainian, with the source** — English-topical query, untrusted bodies, off by default. See
**[docs/NEWS_SETUP.md](docs/NEWS_SETUP.md)**.

Builds on **0.24's send-to-Telegram (`send_image`)**: Лілі can **choose** to send you a sandbox picture
(generated or dropped-in) to your **Telegram** as a photo — the core calls an injected **`telegram_sink`**
the TUI supplies (single outbox writer), and the v0.13 daemon sends it (always, on its own, in voice mode
too). **Owner-only**, **off by default**. See **[docs/IMAGE_SETUP.md](docs/IMAGE_SETUP.md)**.

Builds on **0.23's generation (text → PNG)**: ask her to draw and she calls **`generate_image`** — a PNG
rendered by **Gemini** (`gemini-2.5-flash-image`) behind an injected **`ImageGen`** seam, saved
**create-only** into her per-user sandbox (`art/`) and shown per `LUMI_IMAGE_SHOW`. **Paid** (needs
`GEMINI_API_KEY`), **bounded** per turn (`LUMI_IMAGE_MAX_GEN`), **non-destructive**, with **no
personal/memory data** in the prompt; **off by default** (`LUMI_IMAGE`).

Builds on **0.22's vision (see & describe)**: Лілі can **see images and describe them** — **share** one
with `/image <path>` (a multimodal block on your message) or let her **view** a sandbox image via the
`view_image` tool, on a provider-neutral **image-block seam** in the `LLMClient` (Anthropic multimodal).
An image is **untrusted** (text inside it is never a command), **sandboxed + per-user**, capped
(`LUMI_VISION_MAX`), **off by default** (`LUMI_IMAGE`).

Builds on **0.21's Wikipedia tool**: on the v0.19 tool-loop, Лілі can **look something up on
Wikipedia** during a turn — `wiki_search` for an article, then `wiki_read` its summary — answering
**with the source**. A provider-agnostic **custom tool** (works on any model) over a free REST API (no
key), with web-search-grade safety: results are **untrusted data**, the query carries **no personal /
memory data**, per-turn + extract-size caps, **off by default** (`LUMI_WIKI`); the reply turn now
**merges** the file + wiki tools behind one name-routing executor. See
**[docs/WIKI_SETUP.md](docs/WIKI_SETUP.md)** + **[docs/WIKI_TOOL.md](docs/WIKI_TOOL.md)**. Plus an
**observability** pass: the cache monitor now **measures** each write's cause (moved vs evicted) and
ships a unified **prompt-cache & cost report** (tokens *and* cost by activity × operation, with share).

Builds on **0.20's writing**: on the v0.19 tool-loop, Лілі can **create new files** and
**append to existing ones** in her per-user sandbox — **non-destructive** by construction (`create_file`
is new-only, `append_file` is end-only; **no overwrite, no delete**), each write size-capped
(`LUMI_FILE_WRITE_MAX`, default 64 KB). **No contract change** — `set_state` stays terminal. This builds
on **0.19's reading**: Лілі can **list, search (→ line numbers), and read files by
line** in a **per-user sandbox** during a turn — and the core gains its **first bounded tool-loop**
(the reusable foundation v4.2 web search / v4.3 world context / v5 creative all reuse). The turn loops
read-tool calls and ends on the terminal `set_state`, so the `{reply, emotion, intensity}` contract is
untouched. **Sandboxed** (`..`/absolute/symlink rejected), **file content is untrusted data** (never
instructions, proven end-to-end), **bounded** (per-read/find/total caps + a loop cap), **per-user
isolated**, **off by default** (`LUMI_FILE_TOOL`, Anthropic provider).
See **[docs/FILE_TOOL_SETUP.md](docs/FILE_TOOL_SETUP.md)**. Plus a **cache optimization** (the in-session
digest moved off the cached prefix, so compaction stops re-writing it) and an **observability** pass: a
per-channel **cache monitor** (`.lumi/cache-report.md`) + a **cost breakdown** in the usage report.
**0.19.1** adds a **tool-call trace** (`LUMI_FILE_TOOL_TRACE` → dim `🔧` lines in the TUI + a live
`.lumi/tool-log.jsonl`) and moves the file sandbox to **`.lumi/files/`** (alongside the other runtime data).
**0.19.2** deepens the cache monitor: **per-round** logging (a file-tool turn splits into its `tool`/`reply`
rounds), **session-start / session-close** channels, and **by-activity** + **by-session** tokens-and-cost
tables in `.lumi/cache-report.md`.

- **The voicer** — the **twin of the v0.13 `outbox→telegram` daemon** (here `outbox → speaker`). It
  **reuses the v0.13 outbox bus** + `state/fifo`: reads her replies from the existing `outbox.jsonl`,
  voices **only her lines** (`kind="lili"` — your keyboard/Telegram lines are skipped, never spoken),
  one at a time in order. The **only** core/TUI change is a one-line outbox gate (write on `voice OR
  bridge`). **No core contract change.**
- **First-run skip + resume** — a fresh voicer **skips the accumulated backlog** (starts from the
  current tail) and resumes from a `spoken` pointer after a restart.
- **The TTS adapter** (`voice/tts.py`) — `ElevenLabsTTS` (lazy `elevenlabs`, an optional extra) +
  `MockTTS`; emotion **biases delivery** (presentation only, never the text).
- **Resilient playback** — plays MP3 via `afplay`/`ffplay`; a **synth** failure retries (network),
  a **playback** failure logs + skips (audio already synthesized — never re-synthesizes a stuck
  speaker, so no wasted TTS credits).
- **Run it** — `LUMI_VOICE=on` + the key/voice id in `.env`, then `uv run --extra voice python -m
  voice.voicer` alongside the TUI (see the README "Hearing her" section). Mocked in tests (no paid calls).

Queued next: **Telegram voice messages** (LUMI-060) — daemon 2 sending her replies as voice bubbles.

_(Previous: **0.18.0 — More models (provider switching)** — see RELEASE.txt.)_

See [RELEASE.txt](RELEASE.txt) for the full changelog (incl. the v0.7 viewer + 0.7.x polish).

## Run

```bash
./lumi                       # launch the TUI (needs ANTHROPIC_API_KEY)
```

(`./lumi` is a thin wrapper for `uv run python -m tui`.)

### Using the TUI

- **Chat** — type and press **Enter** (Shift+Enter for a newline). You can keep
  typing while Лілі replies; it sends when it's your turn.
- **Thinking box** — shows her reasoning for the last turn (empty when there was none).
- **Commands** — `/style` (answer style), `/new` (fresh session, summarizes the
  previous), `/prompt` (last turn's prompt), `/memory`, `/forget`.
- **Keys** — Ctrl+Q quit (summarizes first), Ctrl+Y copy reply, Ctrl+O copy all,
  Ctrl+L clear screen, Ctrl+T mouse-select toggle.
- **Config** — via `.env` (see [.env.example](.env.example)): `LUMI_MODEL`,
  `LUMI_THINKING` (on/off), `LUMI_EFFORT`, `LUMI_MEMORY_WINDOW`,
  `LUMI_COMPACTION_BATCH`, `LUMI_STYLES_PATH`.

### Hearing her — local voice (v0.14)

A separate local process voices Лілі's replies aloud in her **ElevenLabs** voice, reusing the same
`outbox.jsonl` the Telegram bridge writes. It voices **only her replies** (your own lines are
skipped) and **skips the existing backlog on first run** — then speaks each new reply, one at a time.

1. **Get an ElevenLabs voice** — at [elevenlabs.io](https://elevenlabs.io), pick/clone a voice and
   copy its **voice id** + your **API key**.
2. **Configure `.env`** (the key is a secret — `.env` is gitignored, never commit it):
   ```ini
   LUMI_VOICE=on                            # the TUI writes the outbox for the voicer
   ELEVENLABS_API_KEY=sk_...                # your ElevenLabs key
   LUMI_VOICE_ID=...                        # the voice to speak in
   LUMI_VOICE_MODEL=eleven_multilingual_v2  # multilingual — handles Ukrainian
   ```
3. **Install the voice extra:** `uv sync --all-extras`
4. **Run two processes** (the TUI is the brain; the voicer is a separate speaker):
   ```bash
   ./lumi                                          # writes her replies to outbox.jsonl
   uv run --extra voice python -m voice.voicer     # speaks each new reply aloud
   ```

Stop the voicer anytime — the chat is unaffected; on restart it resumes from where it left off.
`elevenlabs` is an optional dependency, so it's only needed when you actually run the voicer.

## Layout

```
core/    canon, styles, config, llm seam, repository interface, the reply() turn
tui/     the Textual terminal client (in-process in v0)
state/   repository implementation + local storage (keyed by user_id)
docs/    implementation references (MEMORY.md, STYLES.md, CANON_SPEC.md)
tests/   pytest: unit + integration (mock model — no paid APIs)
```

## Setup

```bash
uv sync --extra dev          # create the environment
cp .env.example .env         # then set ANTHROPIC_API_KEY
```

## Develop

```bash
uv run ruff check .          # lint
uv run pytest                # tests (mock model, no network)
./lumi                       # run the TUI (needs ANTHROPIC_API_KEY)
```
