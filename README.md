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

**0.8.0 — v0.8 Biorhythms (a second daily-mood layer).** Лілі's daily temperament gains a
**computed** layer beside the v0.6 horoscope: three biorhythm cycles — and a hormonal cycle —
blended into the **same daily mood**, so her resolution reflects all of them at once. Computed in
code (exact, deterministic), they color her **tone, energy and sensitivity — never her competence**.

- **Biorhythms** — physical (23 d) / emotional (28 d) / intellectual (33 d) as
  `sin(2π·days_since_birth/period)` from her natal birth date, with
  `high/low/rising/falling/critical` labels; fed into the v0.6 mood call so the reading blends
  horoscope + cycles (LUMI-031…033).
- **Hormonal cycle** — a phased rhythm (менструація → фолікулярна → овуляція → лютеїнова → ПМС)
  from an authored anchor in `core/natal.md`, merged into the same mood under a shared
  "integrate these body rhythms" directive.
- **See them** — `/biorhythm` shows today's cycles + phase; `/mood` shows the blended resolution.
  Toggle with `LUMI_BIORHYTHMS` / `LUMI_CYCLE` (both on by default).

_Also:_ a leading `[date-stamp]` is now stripped from replies even when the model drops the
closing `]`; the roadmap moved the face wardrobe after closeness (v0.9 → v0.11).

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
