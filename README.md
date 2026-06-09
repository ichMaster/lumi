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

**0.9.0 — v0.9 Richer short memory (recent detail + days at a glance).** Лілі now recalls
**recent conversations in detail and the past few days at a glance**, without ballooning the prompt.

- **Two-tier session summary** — at session close, **one** call writes both a **detailed** summary
  and a one-line **gist** (`ShortSummary` gains `gist`; old records migrate to `""`) (LUMI-034…036).
- **Per-day digests** — each day's gists are consolidated into **one cohesive ≤4-sentence day
  summary** (`DaySummary{user_id, date, summary, count, ts}`, a new `day_summaries` store section),
  **regenerated lazily at prompt time, count-based** — a day refreshes only when its session count
  changes (today as it accrues; a past day only when it gains sessions).
- **Prompt order** — the day digests ("Памʼять про розмови в останні дні") come **first**, then the
  **last 5 conversations in detail**. No raw per-session gists are injected (the gist is only the
  input to the day consolidation). Long-term facts untouched; per-user isolation holds.

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
