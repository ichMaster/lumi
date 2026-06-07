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

**0.6.0 — v0.6 Mood of the day.** Лілі now has a **daily temperament** — a model-generated,
horoscope-flavoured mood that colors her tone and the emotions she leans toward, **never her
competence**. Computed once per local day from her natal chart; an experiment for daily
variation, not an astrological claim. (A real-ephemeris test settled the design: the model
writes a vivid daily reading but can't compute precise transits — and variation, not precision,
is the point.)

- **Daily mood engine** — once per local day a mood call writes a reading from `core/natal.md`
  + today's date; the **full reading is logged**, only a short **resolution** is held; cached,
  recomputed at local midnight, on by default (`LUMI_MOOD`) (LUMI-025).
- **Prominent in the prompt** — only the resolution rides in the system prompt, as a prioritized
  block that biases her tone + emotion, never competence (LUMI-026).
- **`/mood`** — see her mood of the day on demand (LUMI-027).

See [RELEASE.txt](RELEASE.txt) for the full changelog.

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
