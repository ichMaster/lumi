# Lumi

Lumi is a private text persona named **Лілі (Lili)** — a companion with a stable
character ("canon"), layered memory, and a structured emotion channel. An
interface-independent **core** binds two axes: Лілі's growing capabilities and a
growing interface (an in-process TUI first, a client/server platform later).

See [specification/](specification/) for the design (MISSION, ARCHITECTURE,
ROADMAP, EMOTION). This is a **spec-first** repository built version by version.

## Current version

**0.2.0 — v0.2 Three-layer memory.** User-scoped core + `Repository` (per-user
isolation), a rolling session window, end-of-session `ShortSummary` + accumulated
`LongTermFact` rehydrated at startup, and TUI `/memory` / `/forget` commands — so
Лілі recalls past sessions and durable facts about you across restarts.

## Layout

```
core/    canon, config, llm seam, repository interface, the reply() turn
tui/     the Textual terminal client (in-process in v0)
state/   repository implementation + local storage (keyed by user_id)
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
uv run python -m tui         # run the TUI (needs ANTHROPIC_API_KEY)
```
