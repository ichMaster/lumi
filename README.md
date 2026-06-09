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

**0.10.1 — Date-based memory, leaner prompt, more config.** A post-v0.10 round refining how Лілі
remembers and how the system prompt reads.

- **Date-based 3-layer short memory** — recall shifts from session-count to **date windows**
  (cumulative, coarse→fine): every session summary from the **last 2 days** (detail), per-day
  digests for the **last 7 days**, and a **new per Mon–Sun week digest** (`WeekSummary`) for the
  **last 14 days**. Day **and** week digests consolidate from the **session summaries**, lazily +
  count-based. All windows `.env`-tunable.
- **Structured prompt** — the appended overlays are now **markdown sections** (`# Як відповідати`,
  `# Зараз`, `# Памʼять про цю людину` grouping the memory tiers, `# Настрій дня`, `# Близькість`,
  `# Стиль відповіді`); the canon stays natural prose.
- **Leaner style palette** — the prompt now offers only the **mega-styles, each with a concise
  description** (not the full 16 base styles) — shorter, but richer per-style.
- **More `.env` config** — short-memory windows and closeness (on/off + engine tuning + levels
  path) are now all tunable from `.env`.

_(Previous: **0.10.0 — v0.10 Closeness**, the per-user relationship level — see RELEASE.txt.)_

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
