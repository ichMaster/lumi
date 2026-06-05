# Lumi

Lumi is a private text persona named **Лілі (Lili)** — a companion with a stable
character ("canon"), layered memory, and a structured emotion channel. An
interface-independent **core** binds two axes: Лілі's growing capabilities and a
growing interface (an in-process TUI first, a client/server platform later).

See [specification/](specification/) for the design (MISSION, ARCHITECTURE,
ROADMAP, EMOTION). This is a **spec-first** repository built version by version.

## Current version

**v0.1 — Skeleton and canon.** The interface-independent `core`, a thin
`LLMClient` seam over Claude Haiku, Лілі's authored canon as the system prompt, a
local `Repository`, and a Textual TUI that holds a dialogue.

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
