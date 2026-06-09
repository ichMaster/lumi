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

**0.10.0 — v0.10 Closeness (relationship level).** Лілі grows **closer to or cooler with each
person over time** — a per-user closeness level that modulates how *open* she is, **never her
competence**.

- **Relational read** — each turn the model scores *your* message on `warmth / vulnerability /
  playful / harm / manipulation` (0–1), emitted **alongside** `{reply, emotion, intensity}` —
  **additive**; the locked v0.3 emotion contract is untouched (LUMI-037…041).
- **Closeness engine** — a weighted delta moves a per-user value (0–100), **decays toward a
  baseline over days of silence** (injected clock + `last_ts`), and re-buckets into **5 levels with
  inertia** (no turn-to-turn flapping). `Closeness{user_id, value, level, last_ts}`, per-user isolated.
- **Authored levels + guardrail** — an editable `core/closeness.md` (Ввічлива → Найрідніша); the
  active level injects a behavior block (warmth/openness/initiative). **Hard rule (like the mood):
  it never changes her competence or willingness to help** — a low level / harm turn never refuses.
- **See it** — `/closeness` shows the level **by name** (raw scores stay internal).

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
