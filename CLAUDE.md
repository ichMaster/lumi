# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

This is a **greenfield, spec-first repository**. As of now it contains only the design specification (`specification/`), a Python `.gitignore`, and a LICENSE — **no application code, no build system, no tests exist yet**. The first implementation work is scaffolding the project per the layout below.

The specification is the source of truth. Read it before writing code:
- [specification/MISSION.md](specification/MISSION.md) — what Lumi is, principles, non-goals, glossary.
- [specification/ARCHITECTURE.md](specification/ARCHITECTURE.md) — components, contracts, data model, error handling, config, observability, testing/CI, repo layout.
- [specification/ROADMAP.md](specification/ROADMAP.md) — five versions (v0–v4) as dotted phases `vA.B`, each with Goal/Tasks/DoD/Tests.
- [specification/EMOTION.md](specification/features/EMOTION.md) — the emotion channel: enum, the `EmotionState` contract, the `IEmotionRenderer` ladder (log → emoji → local image face → web portrait + caption → animation), the asset manifest, the mood caption, and validation/fallback rules.
- [specification/EMOTION_VIEWER.md](specification/features/EMOTION_VIEWER.md) — the v0.6 local emotion viewer: a separate desktop window showing a `faces/<emotion>.png` portrait from a local signal (the web sibling is v2.1).
- [specification/VOICE_LOCAL.md](specification/features/VOICE_LOCAL.md) — the v0.7 local voicer: a separate console app that voices replies via ElevenLabs, reading the core's `outbox.jsonl` and marking `spoken.jsonl` (web sibling v2.2; shares the `/voice` TTS adapter).
- [specification/DICTATION_LOCAL.md](specification/features/DICTATION_LOCAL.md) — the v0.8 local dictator (STT, mirror of the voicer): a separate console app that listens (TUI-toggled via `listen.flag`), recognizes Ukrainian, and writes your line to `inbox.jsonl` like the keyboard (web sibling v2.4; shares the `/voice` STT adapter).
- [specification/WEB_SEARCH.md](specification/features/WEB_SEARCH.md) — the optional MCP web-search tool (v3.2): the `web.search`/`web.fetch` service, off-by-default enablement, and the hard safety bounds (untrusted content, no personal data in queries, rate limits).
- [specification/WORLD_CONTEXT_MCP.md](specification/features/WORLD_CONTEXT_MCP.md) — the optional passive MCP tools (v3.3): world context (weather/time/moon/calendar) injected as ambient "today" context that feeds the v0.5 mood (coloring tone, not competence) + knowledge (wiki/news), same off-by-default safety pattern.
- The **creative layer (v4)** docs: [GALLERY_MCP.md](specification/features/GALLERY_MCP.md) (per-user artifact store + vision, v4.1), [CREATIVE_MCP.md](specification/features/CREATIVE_MCP.md) (async `image`/`music` generators + the open-loop/proactive-turn pattern, v4.3/v4.5), [CO_CREATION_CANVAS.md](specification/features/CO_CREATION_CANVAS.md) (synchronous turn-based shared drawing, v4.4), [JOURNAL.md](specification/features/JOURNAL.md) (Лілі's private admin-only literary journal, v4.6).

Authoring guide (not a design spec): [docs/CANON_SPEC.md](docs/CANON_SPEC.md) — how to write/generate Лілі's canon, the v0.1 character file (`core/canon/lili.md`) loaded as the system prompt.

When asked to "implement `v1.2`" or "start v0", treat that phase's **DoD** as the acceptance criteria, its **Tasks** as the work list, and the ARCHITECTURE/EMOTION contracts as the interfaces to honor.

## What the project is

Lumi is a private text persona named **Лілі (Lili)** — a companion with a stable character ("canon"), layered memory, and a structured emotion channel. It grows along two independent axes:
- **Лілі's capabilities:** text + memory → emotion field (emoji) → daily mood (temperament) → a local image face, then a local voice + dictation → web face (portrait + caption, then animation) → web voice + dictation → MCP tools (web search, world context) → creation (gallery, vision, art, music, journal, co-creation).
- **The interface:** an in-process TUI first (v0), then a **client/server** split — a server wrapping the core with TUI/CLI clients (v1.1), multi-user + multi-session (v1.3), a web client (v1.4), and an admin panel (v1.5), for a close circle (allowlist, admin-managed, no open sign-up).

A **core** independent of the interface binds the two axes: the terminal and the browser are just different "faces" of one mind. Лілі is **one being with a private relationship per user** — see the memory-scoping contract below.

## Design contracts that must not drift

These are cross-cutting and defined once; respect them in every version:

- **Core is interface-independent.** All of Лілі's logic (canon, memory, model invocation, emotion assembly) lives in the core and knows nothing about who renders it. Moving from TUI to a web server must not rewrite the core. Do not leak TUI/web concerns into the core.
- **Emotion channel.** Every model reply is structured: `{ reply: str, emotion: enum, intensity: float(0..1) }`. `emotion` is from a **fixed set**: `joy, calm, playful, tender, thoughtful, serious, surprise, doubt, sad`. The **model emits** its own state; the **core validates** it (unknown → `calm`, clamp intensity); the **renderer** displays it. Never infer emotion after the fact. Only the *renderer* changes across versions (logged → emoji → image → animation) behind one `IEmotionRenderer` interface — the contract, enum, and interface are locked in v0.3 and never change. Full spec in [specification/EMOTION.md](specification/features/EMOTION.md).
- **Mood / temperament (core, v0.5, on by default).** Лілі has a daily **mood of the day** — a horoscope-derived temperament (natal chart + daily transits via skyfield → dials energy/warmth/playfulness/talkativeness, computed once per local day) injected into the system prompt. It **biases the emotion the model emits and her tone — never her competence**; it does not replace the emotion channel. An experiment for daily variation, not an astrological claim. World context (v3.3) feeds the same mood. Compute with an **injected clock** (deterministic/testable). See ARCHITECTURE §Mood and temperament.
- **Memory scopes & the isolation invariant.** Лілі has **per-user (relationship) memory** — session history, short summaries `{ user_id, session_id, summary, ts }`, long-term facts `{ user_id, fact, meta, confidence, ts }` — and a separate **shared experience** layer (`SharedMemoryItem`, de-identified). **Hard rule:** a record written under user A is never readable in user B's context; only de-identified, consent-gated, unattributed items cross via the v2.3 cross-pollination pipeline. Pin this with a contract test.
- **User-scoped core from v0.** The core and `Repository` are keyed by `user_id` from v0.2, running with one default `owner`; v1 adds real auth/accounts without changing the core. Never write a memory path that isn't user-scoped — that's the thing that makes the server migration additive instead of a rewrite.
- **MCP tools (v3.2+, off by default).** A minimal MCP client in the server + a bounded tool loop in the core's model turn. **Web search** (v3.2, `web.search`/`web.fetch`) and **world context & knowledge** (v3.3, `weather`/`time`/`moon`/`calendar`/`wiki`/`news`). Hard rules (WEB_SEARCH.md, WORLD_CONTEXT_MCP.md): off unless the user's per-tool flag (`web_search`/`world_context`) is on (else not offered); returned text is **untrusted data** (never instructions/links); **no personal/memory data in queries**; web `fetch` only takes this turn's `search` ids; world-context tools are passive/read-only (no actions); rate-limited, logged, cited; tool errors degrade the reply, never hang. World-context ambient sources feed the daily **mood/temperament** (core, v0.5) — coloring tone, **never competence**.
- **Creative layer (v4, off by default, per-user).** Лілі creates and exchanges artifacts: a per-user **gallery** (internal, behind `repository`, isolated), **vision** (she sees images you share — Anthropic multimodal input), async **image**/**music** generation that returns via a **proactive turn** (the server starts a turn when the job is done — the v4.2 open-loop mechanism), a synchronous co-creation **canvas**, and a **journal** (her private literary inner life — **admin-only**, never shown to users). User-supplied files are **untrusted** (no instructions followed); artifacts are data, not commands. See GALLERY_MCP.md / CREATIVE_MCP.md / CO_CREATION_CANVAS.md / JOURNAL.md and ARCHITECTURE §Vision, §Async jobs and proactive turns, §Creative layer.
- **Client/server from v1.** v1.1 refactors the TUI into a **client** of a **server** that wraps the core (single user, single session, local client token) + a **CLI management utility** — no web yet. v1.2 = security + CI/CD (deploy behind TLS, dep/secret scans). v1.3 = multi-user + multi-session (accounts, argon2id, invite codes, allowlist) — isolation enforced at the auth boundary. v1.4 = web client. v1.5 = admin panel. v2.3 = cross-pollination. Access is allowlist-only from v1.3; no request reaches the core without a resolved `user_id`; admin-panel actions require the `admin` role.
- **Storage behind a repository interface.** Memory access goes through a thin `Repository` contract (keyed by `user_id`); local JSON/SQLite first, a server DB later — swapping the backend must not touch the core.
- **One model to start, more later.** The model is **Claude Haiku (Anthropic)** from **v0.1**, behind a thin **`LLMClient`** seam the core depends on (mockable; never the SDK directly). **More models** — other Claude tiers, OpenAI, DeepSeek, MiniMax — become switchable in config from **v0.9** (each provider's key in `.env`; OpenAI+DeepSeek share an OpenAI-compatible adapter). Structured output is per-provider (Anthropic tool output; OpenAI/DeepSeek JSON-schema; MiniMax JSON); the v0.3 validation gate is the safety net. Never bind `core` to a specific SDK.

## Intended stack & layout (not yet created)

Python, **Anthropic SDK — Claude Haiku (v0.1); more models (Claude tiers / OpenAI / DeepSeek / MiniMax) switchable v0.9**, Textual (TUI), FastAPI (server, v1+), ElevenLabs (TTS; later STT). Planned top-level layout from ARCHITECTURE.md:

```
/core           # canon, per-user memory + shared experience, llm (thin LLMClient seam — Claude Haiku v0.1; more models v0.9), emotion field + validation, mood/temperament (astro engine — skyfield, v0.5), repository interface (user-scoped)
/tui            # terminal interface (Textual): in-process in v0, refactored to a server client in v1.1; Log/Emoji renderers
/viewer         # later (v0.6): local desktop emotion-face window (Tkinter) + faces/ asset pack; polls a local signal
/cli            # later (v1.1): CLI management utility — run/inspect the server, manage users, config
/server         # later (v1.1): wraps core, client/server API; multi-user/session (v1.3); cross-pollination (v2.3); gallery/journal/canvas + async jobs & proactive turns (v4)
/web            # later (v1.4): web client (chat, portrait/animation, voice toggle, dictation, gallery/canvas) + admin panel (v1.5); Image/Animation renderers + asset packs
/voice          # later (v0.7+): shared TTS adapter + local voicer (v0.7); shared STT adapter + local dictator (v0.8); reused by web voice (v2.2) / web dictation (v2.4)
/mcp            # later (v3.2+): MCP client + web_search (v3.2), world-context/knowledge (v3.3), image/music generators (v4.3/v4.5); untrusted-content handling
/state          # repository implementation + local storage (JSON/SQLite) + gallery files (v4), keyed by user_id
/tests          # pytest: unit, contract, integration; mock model + fakes
/specification  # MISSION/ARCHITECTURE/ROADMAP/EMOTION/WEB_SEARCH
.github/workflows/ci.yml  # lint (ruff) + tests on every push/PR
```

## How to work in this repo

- **Build by version and phase.** The roadmap is deliberately incremental — implement only the current phase's tasks and meet its DoD; do not pull later versions' features (face, voice, server, animation) forward. "Simplicity first; complexity added by versions, not all at once."
- **Tests ship with each phase.** Every ROADMAP phase ships with the tests that encode its DoD (unit + **contract tests** pinning the emotion/memory/API seams + integration). **Mock the model — never call paid APIs in CI**; a mock model returns canned (and deliberately malformed) structured replies. Changing a contract must change its contract test. `main` stays green.
- **Versioning `A.B.C`.** Roadmap phase `vA.B` → semver `A.B.0` (`v1.2` → `1.2.0`); `C` is a post-release fix on that phase. Releases are cut per phase; never bump the version without explicit confirmation.
- When you scaffold tooling (packaging, lint, tests), the `.gitignore` already anticipates uv/poetry/pdm, ruff, pytest, and mypy — pick one and document the chosen build/lint/test commands here once they exist.
- Лілі's persona and much of the spec are in **Ukrainian**; persona text, canon, and dictation (v2.4) target Ukrainian.

## Workflow skills

A spec → issues → execute → release pipeline lives in `.claude/skills/` (ported from the sibling Pyramid project, retargeted to Lumi — issue prefix `LUMI-xxx`, Python-only validation with `pytest` + `ruff`):

- **`/upload-issues <version-issues-file>`** — split a version's phases into `LUMI-xxx` GitHub issues with `vN::` labels and dependencies; writes `specification/roadmap/implementation/vN-github-report.md`.
- **`/execute-issues <label>`** — implement each issue in dependency order: code → `pytest` + `ruff` validation (mock model/TTS/STT/web-search, no paid APIs) → one commit per issue → close → `specification/roadmap/implementation/vN-execution-report.md`. Tests ship with each feature; the emotion/memory/API/web-search contracts and the spec stay in sync.
- **`/release-version <x.y.z>`** — bump `VERSION`/`README.md`, prepend `RELEASE.txt`, commit, annotated-tag, push. Uses the `A.B.C` notation (roadmap phase `vA.B` → `A.B.0`). **Never bumps the version without explicit user confirmation.**

Issue files live under `specification/roadmap/implementation/` (`vN-issues.md`), derived from the ROADMAP phases. The pipeline uses the `gh` CLI and a GitHub remote (create one with `gh repo create` if the repo doesn't have one yet).
</content>
