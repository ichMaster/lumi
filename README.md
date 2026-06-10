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

**0.12.1 — v0.12 Thought-stream (+ follow-ups).** Лілі's mind **acts on its own**: between and
around your messages she muses into a private **dated diary**, mostly silently, and only
occasionally says one aloud. The `.1` adds: **proactive thinking wired into the idle timer** (she
thinks on her own when you go quiet — mostly silent, occasionally speaks first; `nudges.md`
`%`-lines become a topic menu), a **`LUMI_THOUGHTS_CONTEXT=lean|full`** toggle (full = the whole
reply backdrop), a **`/theme <name>` / `/theme auto`** manual face override, and a fix to keep
`<think>` reasoning out of recorded thoughts.

- **`%directives`** — her mind *acts* (internal, never typed): **`%think`** (everyday musing) +
  **`%wonder`** (curiosity), over one reusable **mental-act engine** (`trigger → seed → generate →
  record → maybe surface`). Distinct from `/commands` that *read* state and plain chat she speaks.
- **A global, dated diary** — a `Thought` store (her one mind, **not** per-user); the **last 24h**
  of dated thoughts feed back into the prompt (`# Що в мене на думці…`) so she **remembers her
  day**, and softly color the daily mood.
- **Proactive nudge** — on the idle timer she thinks **mostly silently** (paced: interval + quiet
  hours + a per-session cap); a configurable fraction **graduate to a spoken turn**.
- **Manual + placeholders** — type `%think[!] [about] {topic}` (`!` shows the raw `💭` thought);
  `{last_thought}` / `{mood}` / … resolve in the topic. A **`/thoughts`** command shows the diary.
- **Isolation + invariants** — a thought sparked with user A **never** surfaces to B (contract
  test); logged, **never** written to long-term memory; **never competence**. **No contract change.**

_(Previous: **0.11.0 — v0.11 Face variants & mood themes** — see RELEASE.txt.)_

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
