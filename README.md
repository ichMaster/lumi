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

**0.13.0 — v0.13 Telegram bot (the bridge).** Reach Лілі from **Telegram** — the same mind, a new
window — without giving up the TUI.

- **The bridge** — the **TUI stays the only brain** (the one process calling `core.reply`); Telegram
  is a **file bus** (`inbox.jsonl`/`outbox.jsonl`, append-only **FIFO** with id pointers, `state/fifo.py`)
  plus **two dumb daemons** (`telegram→inbox`, `outbox→telegram`). **No core change.**
- **Symmetric mirror, echo-free** — a Telegram message shows in the TUI (`📱`); a keyboard turn shows
  on the phone (`💻`); a Telegram-originated line never re-enters the outbox (no echo, by construction).
- **Single-owner, allowlist-gated** — only your Telegram id is served (a non-owner never reaches the
  core); spoken **proactive thoughts (v0.12) push** to the phone — she reaches out first.
- **Daemon 1** buffers a burst → 2 s flush → one turn, **ack-after-flush** (no buffer file); **daemon 2**
  sends FIFO, **N-batched** (bounds a backlog), with a **catch-up cap** + first-run backlog skip, emoji,
  optional face photo (length-guarded).
- **Operability** — `python -m telegram.check` (pre-flight `getMe`), `python -m telegram.monitor`
  (live queue + log health), daemon logging + crash-resilience, and a full **setup & monitoring guide**
  ([docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md)). `aiogram` is an optional extra; mocked in tests.

Follow-ups this release: nudge + proactive-think now **run together** (decoupled timers) split into
**two seed files** (`nudges.md` openers / `think_seeds.md` `%think` seeds, chosen randomly) with
**independent quiet hours** (`LUMI_THOUGHTS_QUIET_HOURS`) and a `LUMI_THOUGHTS_MAX_LINES` knob; her
default voice is now **1–2 sentences** (long is the exception, structured via mega-styles); test
isolation so tests never touch the real bus; and **Local voice moved to v0.14** (next).

_(Previous: **0.12.1 — v0.12 Thought-stream (+ follow-ups)** — see RELEASE.txt.)_

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
