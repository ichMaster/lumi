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

**0.7.2 — v0.7 Local emotion viewer.** Лілі's face as a real **image**, locally, without a
server: a separate desktop window shows a portrait for her current emotion and changes as the
conversation does. Another **renderer of the locked v0.3 emotion channel** (alongside the v0.5
emoji) — no contract change.

- **Emotion signal** — the core writes her current emotion to `.lumi/face.txt` each turn; the
  viewer is linked only through that file (LUMI-028).
- **Face resolver** — `emotion → faces/<emotion>.png`, total over the enum, `calm` fallback,
  optional `_low`/`_high` intensity variants (LUMI-029).
- **The window** — a pygame face window (`./lumi-viewer`) + a placeholder pack so it runs before
  art; drop your own `viewer/faces/*.png` in (prompts in `viewer/faces/PROMPTS.md`) (LUMI-030).

_0.7.1 fixes:_ the face **relaxes to `calm` after an idle period** (`LUMI_FACE_IDLE_SECONDS`,
default 120s; the next emotion wakes it); the signal carries **date+time** so every turn's line is
unique; the **TUI input box locks while Лілі replies** and re-enables on your turn.

_0.7.2 fixes:_ **TUI send/receive sound** — a blip on send + receive (macOS `afplay`; off by
default, **Ctrl+S** toggles, status shows `sound:on/off`; never the idle nudge); and **auto-style** —
Лілі now **chooses her own answer style** each turn (prefers "mega"/meta-styles) and declares it,
**`/style` is a recommendation** not a switch, and the status bar shows the style **+ who picked
it** (`(Лілі)` / `(ти)`).

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
