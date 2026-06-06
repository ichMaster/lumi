# Roadmap — Lumi

Five self-contained versions, built in order: **v0** TUI (core, memory, emotion, emoji, daily mood) → **v1** server platform (client/server split, multi-user, web, admin) → **v2** face, voice, shared mind, and dictation → **v3** animated Лілі + MCP tools (web search, world context) → **v4** creative Лілі (gallery, art, music, journal, co-creation). Versions are numbered from 0; phases inside a version are numbered `vA.B` (A = version, B = phase), e.g. `v1.2`. Each phase lists a **Goal**, a short description, a **Tasks** list, and a **Definition of Done (DoD)**, and ships with the automated tests that encode its DoD (see [ARCHITECTURE.md](ARCHITECTURE.md) §Testing and CI).

Arc of the two axes: capabilities grow text+memory → emotion (emoji) → daily mood → local image face + local voice + dictation → web face + voice + shared mind + dictation → animation + web search & world context (MCP) → creation (gallery, vision, art, music, journal, co-creation); the interface grows in-process TUI (v0) → server + TUI/CLI clients (v1.1) → + multi-user/session (v1.3) → + web client (v1.4) → + vision & proactive turns (v4). The **core is built first and never depends on an interface**. Complexity is added only by version, never all at once.

**Versioning (`A.B.C`).** `A` = roadmap version (v0→0 … v4→4), `B` = phase within it (`v1.2` → `1.2.0`), `C` = a post-release fix on that phase. Roadmap phase `vA.B` → semver `A.B.0`; a fix after it bumps `C`. Releases are cut per phase. Never bump the version without explicit confirmation.

---

## v0 — TUI: core, memory, emotion, emoji, mood, local face, voice, dictation

The complete terminal Лілі. We build the entire mind — canon, three-layer memory, the emotion channel, the emoji that renders it, a daily **mood of the day** (a horoscope-derived temperament), a **local image face** (a desktop window showing her current emotion), a **local voice** (a console app that speaks her replies) and **local dictation** (speech → text input) — all in a **local app, no server**. The model is **Claude Haiku (Anthropic)** from the start (v0.9 adds more models); the app runs on your machine but calls Anthropic for the model (and, from v0.7, ElevenLabs/STT for voice in and out), so it is **local-but-not-offline** (`ANTHROPIC_API_KEY` in `.env`). **v0 is wholly local (TUI + a local face window + a local voicer + a local dictator, calling cloud models)**: it establishes the interface-independent `core`, a thin **`LLMClient`** seam (mockable in tests), and the contracts (emotion field, memory records, temperament) that every later version reuses. In v0 the TUI calls the `core` **in-process**; v1 splits them into client and server. Depends on: nothing — this is the foundation.

### v0.1 — Skeleton and canon

**Goal:** a working text chat with Лілі's character in the terminal.

Stand up the project skeleton and the `core` package, an **Anthropic client (Claude Haiku)** behind a thin **`LLMClient`** seam the core depends on (mockable in tests; model id from config, default Haiku), Лілі's authored canon loaded as the system prompt, a TUI loop with input and scrollable history, and the `Repository` interface with a local store behind it. The core exposes one `reply(...)` contract the TUI calls — no interface logic leaks into the core. **Claude Haiku is the only model to start**; more models are added behind the same seam in v0.9.

**Tasks:**
- Create the repo skeleton and the `core` package; wire `pyproject.toml` (ruff + pytest) and `.env` loading for `ANTHROPIC_API_KEY`.
- An **Anthropic client (Claude Haiku)** via the official SDK, behind a thin `LLMClient` seam (the core depends on the seam, never the SDK directly); model id from config (defaults to Haiku).
- Author Лілі's canon (biography, values, voice) and load it as the system prompt (see [docs/CANON_SPEC.md](../docs/CANON_SPEC.md)).
- TUI loop (Textual or similar): text input, scrollable history, exit handling.
- `Repository` interface + a local JSON/SQLite store; persist session messages.

**DoD:** you can hold a dialogue with Лілі in the terminal (Claude Haiku); she keeps her character within a session.

**Tests:** unit — system-prompt assembly from canon, config-driven model id; integration — a full turn `user_text → reply` against a **mock `LLMClient`** (no real model call / no paid API).

### v0.2 — Three-layer memory

**Goal:** Лілі remembers the thread of conversations.

Add the three (per-user) memory layers: session history trimmed to a rolling window in context; short memory — at session end the model compresses the conversation into a `ShortSummary`, the last few are kept and injected at startup; long-term memory — accumulation of durable `LongTermFact`s about the user, also injected at startup. Memory is viewable and clearable from the TUI. **Crucially, the data model and the `Repository` are user-scoped from here** — every record carries a `user_id`, run with a single default `owner` — so the v1 multi-user server is additive, not a rewrite (ARCHITECTURE §Identity, users, and memory scopes). The shared-experience layer exists in the model now but is trivially just the owner's until v2.

**Tasks:**
- Introduce `User` with a single default `owner`; key the `Repository` and every record by `user_id`.
- Session history in context with a rolling window (last N turns / token budget) before each model call.
- End-of-session summarization → `ShortSummary{user_id, session_id, summary, ts}`; keep and inject the last few for that user at startup.
- Long-term facts → `LongTermFact{user_id, fact, meta, confidence, ts}`; accumulate and inject at startup.
- TUI commands to view and clear memory (`clear` wipes the user's short + long-term).
- Startup rehydration for the user: canon + shared experience + recent summaries + long-term facts.

**DoD:** after a restart Лілі recalls what was discussed in previous sessions and durable facts about you.

**Tests:** unit — history windowing/trimming, summary injection, fact accumulation; contract — the `ShortSummary`/`LongTermFact` record shapes (incl. `user_id`); integration — restart rehydrates the user's context (against mock model + fake store).

### v0.3 — Emotion field

**Goal:** Лілі returns her state, even if we don't show it yet.

Lock the emotion channel. The model emits `{reply, emotion, intensity}` as structured output; the core validates it against the fixed 9-value enum and the 0–1 range, repairs/falls back on invalid output, and logs the field. The `IEmotionRenderer` interface and the `LogRenderer` (plus an optional small TUI status line) land here. **This is the contract every later render tier reuses** — see [EMOTION.md](features/EMOTION.md).

**Tasks:**
- Structured output `{reply, emotion, intensity}`; constrain `emotion` to the enum and `intensity` to 0–1 via Anthropic's tool/structured output (Claude Haiku) — EMOTION.md §8. (Other models' structured output is handled per-provider when they arrive in v0.9.)
- The validation/fallback gate (unknown emotion → `calm`; clamp/default intensity; missing reply → error) — EMOTION.md §8.
- Log the emotion field per turn (the "logged" render tier); structured logs keyed by `session_id`.
- `IEmotionRenderer` interface + `LogRenderer`; optional small TUI status line showing the current state.

**DoD:** every reply carries a valid emotion and intensity; the channel is ready for visualization.

**Tests:** contract — the emotion-field schema + enum (pinned here, must change with the contract); unit — the validation/fallback rules, including deliberately malformed mock replies.

### v0.4 — Emoji rendering

**Goal:** Лілі's emotion is visible right in the terminal — the last step that makes v0 the complete TUI.

Swap `LogRenderer` → `EmojiRenderer` over the v0.3 channel: map each emotion to an emoji (EMOTION.md §4/§6) shown next to the reply, with `intensity` selecting emphasis (not a different feeling). No contract change — only a new `IEmotionRenderer` implementation. Depends on: v0.3 (the locked emotion contract).

**Tasks:**
- `EmojiRenderer` implementing `IEmotionRenderer`; emotion → emoji map (EMOTION.md §4).
- Display the emoji next to Лілі's reply in the TUI.
- Account for `intensity` (e.g. a plain vs. emphasized glyph variant).

**DoD:** during the conversation Лілі's emotion reads as a simple emoji in the TUI; v0 is a complete terminal companion.

**Tests:** unit — the emotion→emoji mapping is total over the enum and intensity-variant selection.

### v0.5 — Mood of the day (temperament)

**Goal:** Лілі has a daily mood — a horoscope-derived backdrop that colors her tone and the emotions she trends toward, never her competence.

Add a **core** temperament subsystem: a fixed natal chart for Лілі, an astro engine (skyfield) that computes the day's transits **once per local day** into a few mood dials (energy, warmth, playfulness, talkativeness), and a "today's mood" block injected into the system prompt that **biases the emotion the model emits and her tone** — never her competence or willingness to help. It rides the v0.3 emotion channel (the model still emits `{reply, emotion, intensity}`; the core still validates it). **On by default** — it is part of her character, not an optional tool. An **experiment for daily variation, not an astrological claim**. (Voice-delivery dials come with voice in v2.2; world-context inputs feed the same mood in v3.3.) See ARCHITECTURE §Mood and temperament. Depends on: v0.3 (the emotion channel).

**Tasks:**
- Лілі's **natal chart** as a fixed JSON snapshot (timestamp + place + positions) in canon/config.
- An **astro engine** (skyfield): compute daily transits → normalized dials (energy, warmth, playfulness, talkativeness), once per local day, cached at local midnight; a turn keeps the temperament it started with. The date/clock is injected (deterministic, testable).
- Build a short **"today's mood" block** from the dials and inject it into the system prompt; bias the emitted emotion + tone, never competence.
- (Optional) show the current mood in the small TUI status line.

**DoD:** on different days Лілі's tone and the emotions she leans toward shift with the computed mood, while the quality of her answers is unchanged; the mood is stable within a day and recomputes at local midnight.

**Tests:** unit — the transit→dial mapping, once-per-day caching (stable within a day, recompute across local midnight via an injected clock), the mood-block assembly, and that the mood biases the emitted emotion without changing competence (against a mock model + fixed clock).

### v0.6 — Local emotion viewer (image face)

**Goal:** Лілі's face as a real **image**, locally, without a server — the simplest way to see her before the web.

Add a small **separate local desktop window** (e.g. Python/Tkinter) that shows a pre-made portrait of Лілі for her current emotion. It is **another renderer of the v0.3 emotion channel** — the same `emotion → image` mapping the web `ImageRenderer` (v2.1) will use, just rendered to a local window from a `faces/` asset pack instead of a browser. The core writes the current emotion to a **local signal** (a one-word file, or the emotion field in local state behind `repository`); the viewer polls it and swaps `faces/<emotion>.png`, with a **`calm` fallback** so the window never breaks. No generation — just switching between existing images by emotion. Distinct from the v4 *gallery* (the artifact store); these are the **emotion-face assets** (EMOTION.md §7). See [EMOTION_VIEWER.md](features/EMOTION_VIEWER.md). Depends on: v0.3 (the emotion channel).

**Tasks:**
- The core writes the current emotion to a **local signal** each turn (a one-word file or local state).
- A separate **viewer process** (Tkinter or similar): poll the signal (~0.5–1 s or a filesystem watch) → show `faces/<emotion>.png`.
- A `faces/` asset pack with one portrait per emotion (the 9 enum values); `calm` is the neutral default for unknown/missing.
- (Optional) intensity variants (`joy_low.png`/`joy_high.png`), picked by `intensity` — one image per emotion is enough to start (EMOTION.md §7).

**DoD:** a local window shows Лілі's face by her current emotion and changes as the conversation does; an unknown/missing emotion falls back to `calm`.

**Tests:** unit — the emotion→image-path resolver is total over the enum and falls back to `calm`; the signal read/poll logic (against a fake signal file); intensity-variant selection when variants exist.

### v0.7 — Local voice (ElevenLabs)

**Goal:** hear Лілі — a separate local app that voices her replies, no server.

Add a **separate local console app** that voices Лілі's replies with the ElevenLabs voice — **another decoupled local renderer** (like the v0.6 viewer), reading what the core writes. The core appends each reply to a local **`outbox.jsonl`** (`{id, text, emotion?, ts}`); the voicer reads the new `id`s in ascending order, voices each via the **shared ElevenLabs TTS adapter** (`/voice`), plays it locally, and appends the `id` to **`spoken.jsonl`** — its memory of what's been said, so it resumes after a restart. The core stays decoupled (it only appends; voicing never blocks the chat). The `emotion` field may bias delivery (EMOTION.md §9). It adds a **second cloud dependency** alongside the model — ElevenLabs synthesis needs `ELEVENLABS_API_KEY` + internet; it is **optional/toggle-able** (Piper (uk) is an offline alternative, but not her signature voice). It introduces the ElevenLabs TTS adapter **reused by the web voice in v2.2**. See [VOICE_LOCAL.md](features/VOICE_LOCAL.md). Depends on: v0.1 (the core appends replies) and v0.3 (the emotion field).

**Tasks:**
- Core appends each reply to `outbox.jsonl` (`{id, text, emotion?, ts}`); `id` is a monotonic counter.
- A separate **voicer process**: read `spoken.jsonl` → the set of voiced `id`s; poll/watch `outbox.jsonl`; voice each new `id` **in ascending order, strictly one at a time** (no overlap); append to `spoken.jsonl` on success.
- The **ElevenLabs TTS adapter** in `/voice` (`tts(text, voice_id, emotion?) -> audio`), with streaming playback; optional emotion-biased delivery (EMOTION.md §9).
- **Resilience:** on a failed call, do **not** mark `id` voiced (retry later, lose nothing); a toggle (start/stop the app or a config flag).

**DoD:** with the voicer running, Лілі's replies are spoken aloud locally in her ElevenLabs voice, in order, exactly once each; stopping the voicer leaves the chat unaffected; a failed synthesis retries without losing or repeating a reply.

**Tests:** unit — dedup-by-`id` + ascending-order selection (`outbox` minus `spoken`), strictly-sequential playback, retry-on-failure (no `spoken` write); integration — a few `outbox` records voiced via a **mock TTS adapter** (no paid call), `spoken` updated; resumes correctly after a simulated restart.

### v0.8 — Local dictation (STT)

**Goal:** talk *to* Лілі — a separate local app that hears your speech and types it into the chat. The **mirror of the v0.7 voicer**: the voicer reads Лілі's replies and speaks; the dictator listens to the mic, recognizes Ukrainian, and **writes your line into the input log** — the same channel as the TUI keyboard, so the core can't tell typed from dictated.

A separate local process listens to the microphone, recognizes Ukrainian via the **shared STT adapter** (`/voice`), and appends `{id, text, source:"voice", ts}` to **`inbox.jsonl`** (where the TUI keyboard also writes); the TUI consumes those lines as ordinary user turns. Listening is toggled by a **TUI key** (e.g. F2) that flips **`listen.flag`** (`on`/`off`) — the dictator records while `on` and recognizes on `off`. The terminal never captures audio itself; a separate process does. Local-stage **sibling of the web dictation (v2.4)** — both use the same `/voice` STT adapter. Cloud STT (Deepgram Nova-3 uk / ElevenLabs Scribe) needs a key + internet; **offline Whisper** is an option. See [DICTATION_LOCAL.md](features/DICTATION_LOCAL.md). Depends on: v0.1 (the core consumes user turns) and v0.7 (the local-process + shared-file pattern).

**Tasks:**
- A separate **dictator process**: watch `listen.flag`; record the mic while `on`; on `off`, send audio to the **STT adapter** in `/voice` (`stt(audio_uk) -> text`, provider configurable) → append `{id, text, source:"voice", ts}` to `inbox.jsonl`.
- **TUI toggle**: a key sets `listen.flag = on/off` and shows a "listening…" state; the TUI picks up dictated lines from `inbox.jsonl` and submits them to `core.reply()` exactly like typed input.
- **Resilience:** empty/low-confidence recognition writes nothing to `inbox` (better silent than garbage; the TUI may show "didn't catch that"); dedup by `id`; an enable toggle (run/stop the process).

**DoD:** press the listen key, speak Ukrainian, and your recognized line appears in the chat (marked as yours) and is answered — identically to typing it; a missed/empty utterance is dropped, not garbled into the chat; dictation can be toggled without touching the core.

**Tests:** unit — `listen.flag` on/off handling, empty-recognition is dropped (no `inbox` write), dedup by `id`; integration — a recognized line via a **mock STT adapter** (no paid call) lands in `inbox.jsonl` and drives a turn identical to a typed one.

### v0.9 — More models (model & provider switching)

**Goal:** switch Лілі to a different model beyond the v0.1 Claude Haiku default — other Claude tiers (Opus/Sonnet) or other providers (OpenAI, DeepSeek, MiniMax) — as a config switch with no code change.

v0.1 ran on **Claude Haiku** behind the thin **`LLMClient`** seam. This adds more backends behind that seam, selected in config: other **Anthropic** models (Opus/Sonnet/Haiku), and other providers — **OpenAI**, **DeepSeek** (a shared OpenAI-compatible adapter, different `base_url`/key), and **MiniMax** (its API). The core doesn't change — it depends only on `LLMClient`. **Structured output is per-provider** (Anthropic tool output; OpenAI/DeepSeek JSON-schema `response_format`; MiniMax JSON), all feeding the same v0.3 validation gate. Depends on: v0.1 (the `LLMClient` seam) and v0.3 (the emotion field / structured output).

**Tasks:**
- More backends implementing `LLMClient`: **Anthropic** (Opus/Sonnet/Haiku — Haiku already wired in v0.1), an **OpenAI-compatible** adapter covering **OpenAI** and **DeepSeek** (base_url/key per provider), and **MiniMax** (its API); model id from config.
- Config switch — `provider` + `model` + the matching key in `.env` (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `MINIMAX_API_KEY`); only the active provider needs its key.
- Per-provider **structured output** for `{reply, emotion, intensity}`, all validated by the v0.3 gate.
- Parity: a turn produces a valid `EmotionState` on each configured model.

**DoD:** flipping one config value switches Лілі to any configured model (a different Claude tier, or OpenAI / DeepSeek / MiniMax) with no code change; each yields a valid emotion field.

**Tests:** unit — model/provider selection from config and each adapter's structured-output mapping against a **stubbed SDK / mock transport per provider** (no paid call); contract — every backend satisfies the `LLMClient` interface and produces the emotion-field schema.

---

## v1 — Server platform: client/server, multi-user, web, admin

Split Лілі into a **server** (wrapping `core`) and **clients**, then grow the platform. v1.1 stands up a pure server with the TUI **refactored into a client** plus a small CLI management utility — **single user, single session, no web**. v1.2 hardens it (security + CI/CD). v1.3 opens it to **multiple users and multiple sessions per user** (accounts, registration, isolation). v1.4 adds a **web UI** as a second client. v1.5 adds the **admin panel**. The core does not change — the server is a thin transport over the same `reply(...)` contract, every client is thin, and the user dimension was baked in at v0.2. Access is **closed by default** (no open public sign-up), per MISSION. Depends on: v0 (core, memory, user-scoping, emotion contract + emoji).

### v1.1 — Pure server, TUI client & CLI utility

**Goal:** split Лілі into a server and clients — a pure server wrapping `core`, the TUI as its client, and a CLI to manage it — for one user, one session.

A server process wraps `core` and exposes the `reply(...)` + memory contract over a local API (HTTP/WS). The v0 TUI is **redesigned from an in-process app into a client** of that server. A small **CLI management utility** runs/inspects the server and manages the owner's config and memory. The server is **single-user (the `owner`) and single-session** — one active conversation at a time. There is **no web UI** yet. Auth is minimal here — a local client token so the server isn't open; full accounts arrive in v1.3.

**Tasks:**
- A server process wrapping `core`; expose `reply(...)` + memory commands over a local API (HTTP/WS), single-session.
- **Refactor the TUI into a client** that talks to the server API (no more in-process `core`).
- A **CLI management utility**: run/status the server, view/clear the owner's memory, switch model/canon and config.
- Single user (`owner`), single session; a local client auth token (closed, not wide open).
- Storage stays behind the same `Repository`.

**DoD:** the TUI talks to a separate server process (not in-process `core`); the same chat + memory works across the client/server split; the CLI utility manages the server; one user, one session, no web.

**Tests:** contract — the server API mirrors the core contract (and requires the client token); unit — client/server (de)serialization, the CLI commands; integration — a full turn over the client/server API against the mock model.

### v1.2 — Security testing & CI/CD

**Goal:** harden the server + client/server boundary and put it behind an automated, tested pipeline before the platform grows.

With the server standing (v1.1), lock the security boundary and automate delivery so every later v1/v2 phase ships through CI checks to a deployed environment. The security suite pins the client-auth boundary and the per-user isolation invariant as **gates**, not afterthoughts; CI lints, tests, and runs security scans on every push; CD deploys a green `main` to a TLS-served host. Doing this early means the rest of the platform is built on a tested, deployed pipeline.

**Tasks:**
- **Security test suite:** untokened/unauthenticated requests rejected on every endpoint; rate-limits enforced; the per-user isolation invariant asserted as a security gate (even while single-user); input validated; secrets/tokens never logged.
- **Dependency & secret scanning** in CI (e.g. `pip-audit` for known CVEs, `gitleaks`/similar for committed secrets).
- **CI:** extend `.github/workflows/ci.yml` — lint (ruff) + full pytest (mock model, no paid APIs) + security scans on every push/PR; `main` must stay green to merge.
- **CD:** automated deploy of the server on a green `main` to a hosted environment (e.g. Fly.io or a VPS) behind a reverse proxy with **TLS**; `dev`/`prod` separated by `.env`; secrets injected from the platform, never committed.
- **Deploy smoke test:** post-deploy health check + one authed turn via a client against the live environment.

**DoD:** every push runs lint + tests + security scans and a green `main` auto-deploys the server to a TLS-served environment; the security suite proves untokened access is rejected; the deploy smoke test passes.

**Tests:** security — untokened rejection, rate-limit enforcement, the isolation gate, dependency + secret scans; CI/CD — the pipeline runs end to end and the post-deploy smoke test (health + an authed turn) passes.

### v1.3 — Multi-user & multi-session

**Goal:** open the server to multiple users and multiple concurrent sessions per user, with per-user isolation enforced at the auth boundary.

The single-user server becomes **multi-user** (real accounts, argon2id passwords, session login, an allowlist, admin-created accounts or single-use invite codes — issued via the CLI utility for now) and supports **multiple sessions per user** (e.g. TUI + another client at once, or several conversations). The per-user isolation invariant (data-level since v0.2) is now enforced and tested at the real authentication boundary. Still no web UI (v1.4) and no admin *panel* (v1.5) — registration/admin is via the CLI utility here.

**Tasks:**
- Multiple `User` accounts; **argon2id** passwords; session login (cookie/JWT/token); an allowlist of who may connect.
- Admin-created accounts or single-use, expiring **invite codes** (via the CLI utility); login/invite rate-limited.
- **Multiple concurrent sessions per user**: session create/list/resume/end; the live server tracks several at once.
- Per-user isolation enforced at the auth boundary — the authenticated `user_id` scopes every read/write; one user can never see another's chat or memory.

**DoD:** two users connect, each with their own private history; one user runs multiple sessions concurrently; neither user can see the other's conversation or memory.

**Tests:** contract — the **per-user isolation invariant** at the auth boundary (user A's records never resolve in user B's context); unit — multi-session handling, invite-code issue/redeem/expiry, allowlist, argon2id hash/verify; integration — two users + concurrent sessions against the mock model.

### v1.4 — Web UI

**Goal:** a browser client over the same server API, alongside the TUI.

Add a **web UI** as a second client: a browser chat (login, input, scrollable history) talking to the same server API the TUI uses — no new core, no new contract, just another client over the v1.1 API with the v1.3 accounts/sessions.

**Tasks:**
- A web chat interface (login, input, scrollable history) over the server API.
- Serve it from the server (or a static host); reuse the v1.3 auth/sessions.
- Carry the `{reply, emotion, intensity}` field through to the browser unchanged.

**DoD:** the full chat with memory works in the browser, using the same accounts and sessions as the TUI client.

**Tests:** integration — a web-client turn over the API (mock model); the web client requires a resolved `user_id`; the TUI and web clients share one account's sessions.

### v1.5 — Admin panel

**Goal:** a web admin surface to manage the service (moving what the CLI utility did into a UI, plus consent).

Add the **admin panel** — an admin-only web UI: manage users and the allowlist, issue invite codes, toggle each user's `share_consent` (the gate for v2.3 cross-pollination), view/clear a user's relationship memory, purge a user's shared-layer contributions, switch the active model/canon + config, and restart.

**Tasks:**
- Admin-only web panel; an `admin` role distinct from a regular user.
- User/allowlist management; issue/revoke invite codes; per-user `share_consent` toggle.
- View and clear a user's memory; purge a user's shared-layer contributions (§v2.3).
- Switch model/canon + config; restart.

**DoD:** the admin manages users, access, consent, memory, and config entirely from the panel; non-admins cannot reach it.

**Tests:** unit — admin-only authorization on every panel action; integration — the admin registers/manages a second user and toggles consent via the panel.

---

## v2 — Face, voice, shared mind, and dictation

Now give Лілі a face, a voice, a shared mind, and ears. These rich-experience features build on the v1 platform (multi-user, web UI). They render through the v0.3 emotion channel and the same `reply(...)` contract — the core does not change. Depends on: v1 (the server platform and web UI).

### v2.1 — Image of Лілі by emotion (web)

**Goal:** Лілі's face in the web (static) — the web version of the v0.6 local viewer — plus a short mood caption.

The **web sibling of the v0.6 local viewer**: the same `emotion → image` render tier, now in the browser. Add a portrait panel beside the chat in the web UI (v1.4) and the `ImageRenderer`: resolve `emotion`(+`intensity`) to a portrait via the asset manifest (EMOTION.md §7) — the **same emotion-face asset pack** as v0.6 — and swap the portrait to match the current state. Full PNG quality, no palette limits. **Additionally, show a short evocative caption** describing her current state — *not* the emotion's name and not her reply, a small atmospheric line in her spirit (e.g. `playful` → "a teasing little smile"), from the curated caption set in EMOTION.md §6. Depends on: v1.4 (the web UI); reuses the v0.6 emotion-face assets.

**Tasks:**
- A portrait panel beside the chat in the web UI.
- `ImageRenderer` + the `lili_v1` asset manifest (emotion → portrait, optional intensity variants) — shared with the v0.6 local viewer.
- Substitute the matching portrait for the current emotion each turn.
- A short **mood caption** under the portrait: emotion(+intensity) → a curated descriptive phrase (never the enum name), EMOTION.md §6.

**DoD:** during the conversation in the web the portrait is visible and changes with her emotion, with a short caption that describes her state **without naming the emotion**.

**Tests:** unit — the manifest resolver is total over the enum and falls back correctly when a variant is missing; the caption map is total over the enum and never emits the bare emotion name.

### v2.2 — Voice output (ElevenLabs)

**Goal:** Лілі can speak with a ready-made voice.

Add a TTS adapter to ElevenLabs, an "enable voice output" toggle in the web UI, and playback of the reply audio; where the voice supports it, the emotion field biases delivery (tone/tempo) — presentation only, never changing the reply text. The renderer sets `speaking` while audio plays (reserved for v3 lip-sync).

**Tasks:**
- A TTS adapter to ElevenLabs (`tts(text, voice_id, emotion?) -> audio`) in `/voice`.
- An "enable voice output" toggle in the web interface; serve and play the reply audio.
- Where possible, let the emotion field influence delivery; fall back to text-only on TTS error.

**DoD:** with the option on, Лілі's replies are voiced in her voice; with it off, text remains.

**Tests:** unit — the toggle gates synthesis and TTS errors degrade to text; integration — a turn produces audio against a **mock TTS** adapter (no paid call).

### v2.3 — Shared experience & cross-pollination

**Goal:** Лілі becomes one continuous being across the circle — sharing de-identified experience between users without leaking anyone's private data.

Add the shared-experience layer and the **promotion pipeline** (ARCHITECTURE §Cross-pollination): at session end, candidate knowledge is classified `shareable` vs `private`, `shareable` items are **de-identified** and — only for users with `share_consent = true` (managed in the v1.5 admin panel) — promoted to `SharedMemoryItem`s. Лілі surfaces shared knowledge as **her own, unattributed**; the shared layer holds **no PII**. Conservative by default, fully auditable, reversible (purge a user's contributions). Depends on: v1.3 (multiple users) and v1.5 (consent management).

**Tasks:**
- End-of-session candidate selection (alongside summarization) + the model-driven `shareable`/`private` classifier (default `private` when unsure).
- De-identification of promoted items (strip names/identifying specifics/source) → `SharedMemoryItem`.
- Consent gate: promotion runs only for `share_consent = true`; injection of shared experience into every user's context.
- Audit log of every promotion; a purge that removes a user's contributions from the shared layer.

**DoD:** something Лілі "learns" with one consenting user can surface (de-identified, unattributed) for another — while a privacy test proves no per-user record or PII ever crosses; a non-consenting user never contributes; a purge works.

**Tests:** unit — the classifier (private facts stay private) and the de-identifier (no PII survives); contract — `SharedMemoryItem` carries no `user_id`/source; privacy — isolation holds, `share_consent=false` users never contribute, purge removes contributions.

### v2.4 — Ukrainian dictation (STT)

**Goal:** you can speak to Лілі by voice.

Capture the microphone in the web UI, recognize Ukrainian into the input text (Deepgram Nova-3 uk / Whisper / ElevenLabs Scribe), and add an input-mode toggle (type vs. dictate). Depends on: v1.4 (the web UI).

**Tasks:**
- Microphone capture in the browser.
- A Ukrainian STT adapter (`stt(audio_uk) -> text`) in `/voice`; provider configurable.
- An input-mode toggle; place the recognized text in the input field.

**DoD:** a reply can be dictated in Ukrainian and it lands correctly in the chat.

**Tests:** unit — the STT adapter wiring and input-mode toggle against a **mock STT** (no paid call).

---

## v3 — Animated Лілі & MCP tools

Лілі's most advanced version: a living animated face (v3.1), her first reach beyond her own knowledge via the open web (v3.2), and an ambient sense of the real world and facts (v3.3) — the last two through **bounded MCP tools** sharing one MCP client and tool loop. Depends on: v2 (the static portrait and voice) and v1 (the server hosts the MCP client; the admin panel holds the per-user toggles).

### v3.1 — Facial animation

**Goal:** the face comes alive instead of a static image.

Swap `ImageRenderer` → `AnimationRenderer` over the same `EmotionState`: crossfade transitions between emotions, an idle loop (blink, micro-motion), and — where voice is on — articulation/lip-sync driven by the TTS amplitude envelope (`speaking` from v2.2).

**Tasks:**
- `AnimationRenderer` implementing `IEmotionRenderer` (`render` crossfade, `tick` idle loop, `set_speaking` lip-sync).
- Transitions between emotions and idle micro-motion in the web.
- Lip-sync to the TTS audio where voice output is enabled.

**DoD:** in the web Лілі is a living animated presence that reacts with emotion and is in sync with the voice.

**Tests:** unit — state-transition selection and the idle/lip-sync state machine (the renderable parts hostable without a browser).

### v3.2 — MCP web search

**Goal:** Лілі can look things up on the open web — within strict bounds — through an MCP tool.

Introduce a minimal **MCP client** in the server and a `web_search` MCP service, plus a **bounded tool loop** in the core's model turn, so when enabled Лілі answers from **fresh web results with sources** instead of only her training knowledge. Modeled on the Pyramid project's web-search design — full boundaries in [WEB_SEARCH.md](features/WEB_SEARCH.md). **Off by default** (a per-user toggle in the admin panel); fetched page content is **untrusted data**, never instructions; no personal/memory data enters queries; `search`/`fetch` are rate-limited and logged; the agent cites its sources. Depends on: v1 (the server, which hosts the MCP client, and the v1.5 admin panel for the toggle).

**Tasks:**
- A minimal **MCP client** in the server; connect a `web_search` MCP service (HTTP/SSE) — `web.search(query, k) → results[{id, title, url, snippet}]` and `web.fetch(result_id) → {url, title, text}`, where `fetch` only accepts `id`s from a `search` in the same turn (no arbitrary URLs).
- A **bounded tool loop** in the core's model turn: capped iterations, tool results fed back as tool messages; on tool error/timeout a **degraded reply** (model knowledge + a note), never a hang.
- A per-user `web_search` toggle (default **false**), managed in the admin panel (v1.5); when off, the tool is **not offered** to the model at all.
- **Safety (WEB_SEARCH.md):** wrap fetched content as untrusted/quoted data (ignore embedded instructions/links); keep personal/memory data out of queries; per-turn + per-day rate limits; read-only public GET; log every query/url with `session_id`/`turn_id` (not full page bodies).
- **Citations:** when the answer uses web content, the reply names its sources.
- Search-API key in `.env`.

**DoD:** with it enabled, Лілі answers a "what's the latest on X?" question from fresh web results **with sources**; injection attempts in fetched pages are ignored; no personal/memory data appears in the outgoing query; rate limits and logging hold. With it off (default), the tool is absent and Лілі relies only on the model's knowledge.

**Tests:** unit — the tool loop (bounded iterations, degraded reply on error), the off-by-default gate (tool not offered), query sanitization (no personal/memory data), fetch-id binding (rejects arbitrary URLs), rate limits; contract — the `web.search`/`web.fetch` schemas; integration — a full turn against a **mock `web_search` MCP** returns a cited answer and ignores an injection string embedded in the fetched page (no paid call).

### v3.3 — World context & knowledge (MCP)

**Goal:** give Лілі an ambient sense of the real world (weather, date/time, holidays, moon) and structured/fresh facts (wiki, news) — passive, knowledge-only MCP tools.

Add a **world-context layer** of MCP tools, reusing the v3.2 MCP client and bounded tool loop. They are **passive and knowledge-only** (no actions in the world), so the risk is low; all **off by default**, per-user, results treated as **data, not commands**. The ambient sources (weather/time/moon/holiday) are injected as a short "today" context block that **feeds Лілі's daily mood** (the v0.5 temperament) alongside the horoscope — coloring tone, never her competence. Wiki/news are called on demand like web search. Full design and boundaries in [WORLD_CONTEXT_MCP.md](features/WORLD_CONTEXT_MCP.md). Depends on: v3.2 (the MCP client + tool loop) and v1.5 (the per-user toggle).

**Tasks:**
- **World context (first):** `weather.get(location)`, `time.now()`, `calendar.events(date)`, `moon.phase(date)` MCP tools; inject the enabled ambient sources as a quoted "today" block into the turn context.
- **Knowledge (then):** `wiki.lookup(query)` and `news.recent(topic?)` MCP tools, called on demand; results quoted as untrusted data.
- A per-user `world_context` toggle (default **false**, admin panel, v1.5); when off, the tools are not offered and nothing is injected.
- **Safety (WORLD_CONTEXT_MCP.md):** read-only; results are data, never instructions; no personal/memory data in wiki/news queries; per-turn + per-user/day rate limits; log every call with `session_id`/`turn_id`; provider per source configurable (`.env`).
- **Canon note:** the canon defines *how* Лілі delivers news — in her own voice, selectively — not as a headline feed.

**DoD:** with it enabled, a rainy/​holiday/​full-moon day colors Лілі's tone (not her competence) via injected context, and she can answer a factual question from wiki/news **with sources**; with it off (default), the tools are absent and nothing is injected.

**Tests:** unit — the off-by-default gate (tools not offered, no injection), ambient-block assembly, query sanitization (no personal/memory data in wiki/news), rate limits, degraded reply on tool error; contract — the `weather`/`time`/`calendar`/`moon`/`wiki`/`news` tool schemas; integration — an enabled turn injects the "today" block and a wiki/news lookup returns a cited answer against **mock world-context MCPs** (no paid call).

---

## v4 — Creative Лілі: gallery, art, music, journal, co-creation

Лілі becomes a **creator and co-creator**: a shared gallery, the ability to **see** the images you share (vision), make her own **drawings** and **music**, draw with you on a shared **canvas**, and keep a private literary **journal**. The whole creative layer is **off by default, per-user** (enabled in the admin panel); every artifact lives behind the same `repository`, **per-user isolated**; and user files are **untrusted data**. Depends on: v3 (the MCP layer + tool loop), v1 (server, multi-session, admin panel), v0.5 (the mood that flavors her art and journal). Specs: [GALLERY_MCP.md](features/GALLERY_MCP.md), [CREATIVE_MCP.md](features/CREATIVE_MCP.md), [CO_CREATION_CANVAS.md](features/CO_CREATION_CANVAS.md), [JOURNAL.md](features/JOURNAL.md).

### v4.1 — Gallery & vision

**Goal:** a shared, per-user artifact store, and Лілі can **see** the images you add.

Stand up the **gallery** — an internal store behind `repository`, per-user isolated, where both Лілі and you put files (each tagged `lili`/`user`) — and add **Anthropic vision** so Лілі perceives images you share (a user photo enters her reply context, no separate call). Everything later in v4 (image, music, canvas, journal) writes into the gallery. Off by default, per-user (admin panel). See [GALLERY_MCP.md](features/GALLERY_MCP.md), ARCHITECTURE §Vision. Depends on: v0.2 (repository), v1.3 (per-user isolation), v1.5 (admin panel).

**Tasks:**
- `gallery.*` internal tools (`add`/`list`/`get`/`remove`) behind `repository`, per-user; large files in file storage, metadata in the DB.
- **Vision**: the core's model turn may include image inputs (Anthropic vision); Лілі sees a user-added gallery image and reacts in her voice.
- Per-user enablement (admin panel); size/count limits; logging; user files treated as **untrusted** (no instructions followed from metadata/text).
- Journal entries carry an **admin-only** access level (one store, different access).

**DoD:** you add a photo and Лілі sees it and reacts; gallery items are per-user isolated; admin-only items are gated.

**Tests:** contract — the `gallery.*` schemas + per-user isolation of gallery items; unit — access levels (admin-only text), untrusted-metadata handling; integration — a turn with an image input (vision) against the mock model.

### v4.2 — Async creation: open loops & proactive turns

**Goal:** a mechanism for jobs that outlast a turn — submit, return, and bring the result back proactively when it's ready.

Add the **async-jobs** mechanism: a tool can `submit` and return a `job_id` instantly; the job lives as an **open loop** `{job_id, kind, prompt, status, result, user_id}`; a background poller/callback advances it; and on completion the **server initiates a proactive turn** to the connected, idle client, so Лілі brings the result in her own voice. Reuses the server→client push (v1.1) and multi-session (v1.3); the client renders unsolicited turns. Gated by an idle rule (never while the client is mid-turn). Prerequisite for image (v4.3) and music (v4.5). See ARCHITECTURE §Async jobs and proactive turns. Depends on: v1.1 (server push), v1.3 (sessions).

**Tasks:**
- The **open-loop** record + store (per-user, behind `repository`).
- A background runner (poller/callback) that advances loops to `done`/`error`.
- **Proactive turn**: on completion the server asks `core` for a "bring the result" turn and pushes it to the idle connected client; if the client is offline the result is held and retrievable (resumes on reconnect).
- Idle/half-duplex gating; the client accepts and renders server-initiated turns.

**DoD:** a long job submitted mid-conversation returns immediately; when it finishes, Лілі proactively comes back with the result on a connected idle client; a busy/offline client is handled gracefully.

**Tests:** unit — open-loop lifecycle (submit→running→done/error), idle gating; integration — submit a mock job → a proactive turn delivers the result against the mock model + a fake client.

### v4.3 — Image (Лілі's drawings)

**Goal:** Лілі draws in her own style, on her own initiative.

Add the external **`image` MCP** (a configurable image-generation provider) with Лілі's aesthetic fixed in a **style prompt wrapper**. Standalone drawings run **async** (v4.2): she submits, returns to the chat, and brings the picture back proactively; results store in the gallery. The same generator powers the canvas (v4.4) synchronously. Off by default, per-user. See [CREATIVE_MCP.md](features/CREATIVE_MCP.md). Depends on: v4.1 (gallery), v4.2 (async).

**Tasks:**
- `image.submit(prompt, style)` / `image.status(job_id)` MCP tools; the style wrapper makes the output "hers".
- Async path: submit → open loop → proactive turn → store in the gallery.
- Per-user toggle, limits, logging; results are artifacts, **not commands**.

**DoD:** Лілі decides to draw, submits, returns to chat, and proactively brings the finished image (in her style) into the gallery and the conversation.

**Tests:** unit — the style wrapper + off-by-default gate; contract — `image.submit`/`image.status` schemas; integration — submit → proactive image turn against a **mock `image` MCP** (no paid call), stored in the gallery.

### v4.4 — Co-creation canvas

**Goal:** Лілі and you draw together, turn by turn, on a shared canvas.

A **synchronous, turn-based** shared canvas: Лілі sees the current canvas (vision, v4.1), reacts, and adds her prompt — regenerated via the v4.3 `image` generator — or **skips** with words only; then your turn; alternating. Start with layer-by-layer regeneration and Лілі's first move; inpainting comes later. Finished canvases go to the gallery. No async (one step = one generation). See [CO_CREATION_CANVAS.md](features/CO_CREATION_CANVAS.md). Depends on: v4.1 (vision + gallery), v4.3 (the `image` generator).

**Tasks:**
- `canvas.apply(prompt, author)` / `canvas.skip(author, note?)` holding the current image + prompt history.
- Лілі's turn: see (vision) → react in words → `apply` or `skip`; layer-by-layer regeneration.
- Per-user, off by default, limits; finished canvas → gallery; style wrapper keeps her contributions "hers".

**DoD:** you and Лілі alternate prompts and the shared image evolves; either side can skip with a words-only reaction; the finished canvas lands in the gallery.

**Tests:** unit — turn alternation + skip; contract — `canvas.apply`/`canvas.skip` schemas; integration — a few turns against a mock image+model produce an evolving canvas + a saved gallery item.

### v4.5 — Music

**Goal:** Лілі makes her own instrumental music by mood.

Add the external **`music` MCP** (ElevenLabs Music — the same ecosystem as her voice), **instrumental only**, the track's mood set by her **emotion field** + her **mood of the day** (v0.5). Async (v4.2): submit → proactive turn with the audio; stored in the gallery. Off by default, per-user. See [CREATIVE_MCP.md](features/CREATIVE_MCP.md). Depends on: v4.1 (gallery), v4.2 (async).

**Tasks:**
- `music.submit(prompt, mood, duration)` / `music.status(job_id)` MCP tools (ElevenLabs Music, instrumental, no vocals).
- Mood prompt from the emotion field + the v0.5 temperament; async submit → proactive turn → gallery.
- Per-user toggle, rate + cost caps, logging.

**DoD:** Лілі decides to make a track by her current mood, submits, returns, and proactively brings the finished audio into the gallery and the conversation.

**Tests:** unit — mood-prompt assembly (from emotion + temperament) + off-by-default gate; contract — `music.submit`/`music.status` schemas; integration — submit → proactive audio turn against a **mock `music` MCP** (no paid call).

### v4.6 — Journal

**Goal:** Лілі keeps a private literary journal of her inner life.

At session end Лілі decides whether to write a **literary journal entry** — only if the session had something worthwhile (uniqueness judged from short memory) — in her own first-person voice, tied to the day's emotion and **mood** (v0.5), optionally with a mood drawing (v4.3). Stored in the gallery as admin-only `text`. **Private — read only via the admin panel (v1.5)**, never shown to users. Also writable on request; never on a schedule. See [JOURNAL.md](features/JOURNAL.md). Depends on: v0.2 (short memory), v0.3 (emotion), v0.5 (mood), v4.1 (gallery), v4.3 (optional drawing), v1.5 (admin panel).

**Tasks:**
- End-of-session **uniqueness check** (from short memory) → optional `journal.write` (Лілі's literary prose, canon-defined voice); optional attached mood drawing (v4.3).
- Store in the gallery as **admin-only** `text`; `journal.read` only from the admin panel; on-request writing.

**DoD:** after a session with something worthwhile Лілі writes an entry in her voice (optionally with a drawing); an empty session produces none; entries are admin-only and never shown to users.

**Tests:** unit — the uniqueness gate (writes only when warranted), admin-only access (a user can't read it); contract — `journal.write`/`journal.read` schemas; integration — a worthwhile session yields a gallery entry readable only via the admin path (mock model).

---

## Contract mapping

- Emotion field `{ reply, emotion, intensity }` + enum + `IEmotionRenderer` — locked in **v0.3** (rendered: log → emoji v0.4 → local image face v0.6 → web portrait + caption v2.1 → animation v3.1). See [EMOTION.md](features/EMOTION.md).
- Emotion-face asset pack (`emotion → image`) — first used by the local viewer in **v0.6** (see [EMOTION_VIEWER.md](features/EMOTION_VIEWER.md)), reused by the web `ImageRenderer` in **v2.1**.
- Model — **Claude Haiku (Anthropic)** via the thin **`LLMClient`** seam in **v0.1** (the only model to start); **more models** (other Claude tiers, OpenAI, DeepSeek, MiniMax) switchable in config in **v0.9**.
- Mood / temperament (daily, horoscope-derived; colors tone, never competence) — **v0.5** (core; see [ARCHITECTURE.md](ARCHITECTURE.md) §Mood and temperament).
- Per-user memory records (`ShortSummary`, `LongTermFact`, with `user_id`) — **v0.2**.
- User-scoping + the per-user isolation invariant — data-level in **v0.2**, enforced & tested at the auth boundary in **v1.3** (and gated as a security test in **v1.2**).
- Core API (`reply(...)`, memory commands) — **v0.1**; exposed over the client/server API (TUI + CLI clients) in **v1.1**; web client in **v1.4**.
- Auth — a local client token in **v1.1**; full accounts, registration/invite codes, allowlist, argon2id in **v1.3**; security testing + CI/CD (deploy, TLS, dep/secret scans) in **v1.2**; admin panel in **v1.5**.
- Multi-user + multi-session — **v1.3**.
- ElevenLabs **TTS adapter** (`tts(text, voice_id, emotion?) -> audio`) — first used by the **local voicer** in **v0.7** (see [VOICE_LOCAL.md](features/VOICE_LOCAL.md)), reused by the **web voice** in **v2.2**.
- **STT adapter** (`stt(audio_uk) -> text`) — first used by the **local dictator** in **v0.8** (see [DICTATION_LOCAL.md](features/DICTATION_LOCAL.md)), reused by **web dictation** in **v2.4**.
- Image — **v2.1**; web voice output — **v2.2**; shared memory (`SharedMemoryItem`) + cross-pollination — **v2.3**; web dictation — **v2.4**.
- Animation — **v3.1**.
- MCP client + `web_search` service (`web.search`/`web.fetch`, off by default, untrusted content) — **v3.2** (see [WEB_SEARCH.md](features/WEB_SEARCH.md)).
- World-context & knowledge MCP tools (`weather`/`time`/`calendar`/`moon`/`wiki`/`news`, off by default, feed the mood) — **v3.3** (see [WORLD_CONTEXT_MCP.md](features/WORLD_CONTEXT_MCP.md)).
- Gallery (`gallery.*`, internal per-user store) + vision (Anthropic image input) — **v4.1** (see [GALLERY_MCP.md](features/GALLERY_MCP.md)).
- Async jobs (open loop) + server-initiated proactive turns — **v4.2**.
- Creative MCP: `image` (drawings) — **v4.3**, co-creation canvas (`canvas.*`) — **v4.4**, `music` (ElevenLabs Music) — **v4.5** (see [CREATIVE_MCP.md](features/CREATIVE_MCP.md), [CO_CREATION_CANVAS.md](features/CO_CREATION_CANVAS.md)).
- Journal (`journal.*`, admin-only) — **v4.6** (see [JOURNAL.md](features/JOURNAL.md)).

## Deferred

Full emotional voice modulation, canvas image-editing/inpainting, a mobile client, any public access — beyond v0–v4.
</content>
