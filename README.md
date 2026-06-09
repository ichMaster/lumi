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

**0.11.0 — v0.11 Face variants & mood themes.** Лілі's image face stops repeating and **dresses
for the day** — several pictures per emotion, and a themed outfit chosen by her mood.

- **Variants** — each emotion is a *folder* of images; the viewer shows a **random** one (no
  immediate repeat), re-picked each turn, so she isn't predictable (LUMI-042).
- **Themes** — each theme is a wardrobe pack (`faces/<theme>/<emotion>/…`); an editable
  `faces/themes.md` manifest + auto-discovered folders (LUMI-043). The **daily mood (v0.6) picks the
  theme** that fits the day (`MoodState.theme`, cached per local day); the core writes
  `<theme> <emotion> <intensity>` to the face signal (LUMI-044).
- **Graceful + backward-compatible** — fallback chain `theme/emotion → theme/calm → default → flat
  v0.7`; with no themes it behaves exactly like v0.7. **No contract change** (reuses the locked
  emotion channel, the v0.7 signal, and the v0.6 mood).
- **Ten "Honest Moods" themes** authored (3am / day-after / furious / … / calm-before): `day-after`
  & `quiet-collapse` ship full 9-emotion packs; the other eight ship a themed `calm` portrait.

_(Previous: **0.10.1 — Date-based memory, leaner prompt, more config** — see RELEASE.txt.)_

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
