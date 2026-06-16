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

**0.23.0 — Image generation (text → PNG).** Лілі can now **make a picture** during a turn: ask her to
draw and she calls the new **`generate_image`** tool — a PNG rendered by **Gemini** (`gemini-2.5-flash-image`)
behind an injected **`ImageGen`** seam, saved **create-only** into her per-user sandbox (`art/`) and shown
per `LUMI_IMAGE_SHOW`. **Paid** (needs `GEMINI_API_KEY`), **bounded** per turn (`LUMI_IMAGE_MAX_GEN`),
**non-destructive** (never overwrites/deletes), with **no personal/memory data** in the prompt; **off by
default** (`LUMI_IMAGE`). **No SDK in `core`** (the generator is mocked in every test — no paid calls), no
emotion-contract change. See **[docs/IMAGE_SETUP.md](docs/IMAGE_SETUP.md)**.

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
