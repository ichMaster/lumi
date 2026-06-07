# Architecture — Lumi

## Overview

Two independent axes. **Лілі's capabilities** grow: text and memory → emotion channel (emoji) → face (image, then animation) → voice. **The interface** grows separately: an in-process TUI, then a **client/server split** (a server wrapping the core, with TUI and CLI clients), then multi-user, then a web client, then an admin panel. They are bound by the **core**, which is independent of the interface: the terminal, the CLI, and the browser are just different clients of one mind.

## Components

- **Core.** Canon, per-user memory + the shared experience layer, model invocation via a thin **`LLMClient`** seam (Claude Haiku from v0.1; more models switchable from v0.9), assembly and validation of the emotion field in the reply, and the daily **mood/temperament** (from v0.5, §Mood and temperament). It is **user-scoped from v0** (a single default `owner` until v1.3) so going multi-user is additive, and it does not know who displays it. Exposes one contract (§Contracts).
- **Server (from v1.1).** A process that wraps `core` and exposes the `reply(...)` + memory contract over an API (HTTP/WS). Single-user, single-session at v1.1; **multi-user and multi-session from v1.3**. It resolves each request to a `user_id` and runs the cross-pollination pipeline (v2.3).
- **Clients.** Thin front-ends over the server API: the **TUI** (an in-process app in v0, **refactored into a server client in v1.1**), a **CLI management utility** (v1.1), and a **Web UI** (v1.4). None contain Лілі's logic; each hosts a renderer for the emotion channel ([EMOTION.md](features/EMOTION.md)).
- **Auth & accounts (server).** A minimal local **client token** at v1.1 (the server isn't open); **full accounts** — argon2id passwords, an allowlist, admin-managed registration (accounts or invite codes, no open sign-up) — from v1.3. Resolves a request to a `user_id` before it reaches the core. See §Security, auth, and access.
- **Admin panel (web, from v1.5).** An admin-only web surface to manage users and access, toggle each user's `share_consent`, view/clear a user's memory, switch the active model/canon and config, and restart. Moves what the CLI utility did (v1.1–v1.3) into a UI, plus consent.
- **Storage (repository).** A thin interface over memory; local JSON/SQLite first, a server DB later — without changing the core. The core depends on the `Repository` interface, never on a concrete store, and it is always keyed by `user_id`.
- **Voice.** A shared **ElevenLabs TTS adapter** voices Лілі's replies — a **local voicer** (v0.7) then web voice output (v2.2) — and a shared **STT adapter** hears the user — a **local dictator** (v0.8) then web dictation (v2.4). Both local pieces are separate console processes coupled to the core by files.
- **MCP tools (from v3.2).** A minimal MCP client in the server lets Лілі reach external tools during a bounded tool loop — **web search** (v3.2) and **world-context & knowledge** sources (v3.3), all **off by default**, returned text **untrusted**. See §MCP tools, [WEB_SEARCH.md](features/WEB_SEARCH.md), and [WORLD_CONTEXT_MCP.md](features/WORLD_CONTEXT_MCP.md).
- **Creative layer (from v4).** Лілі creates and exchanges artifacts: a per-user **gallery**, **vision** (she sees your images), async **image**/**music** generation with **proactive turns**, a co-creation **canvas**, and a private **journal** (admin-only). Off by default, per-user, behind `repository`. See §Vision, §Async jobs and proactive turns, §Creative layer.

## Emotion channel

A cross-cutting mechanism: every model reply returns a structured state `{ reply, emotion, intensity }`, where `emotion` is from a fixed set of 9 and `intensity` is 0–1. The **model** emits its own state; the **core** validates it; the **renderer** displays it. How it is shown changes by version (logged → emoji → local image face → web portrait + caption → animation), but the contract, the enum, and the renderer interface are locked once in v0.3 and never change afterward. The **local image face (v0.6)** is a separate desktop window that polls a local emotion signal and shows a portrait from a `faces/` asset pack — the same pack the web `ImageRenderer` reuses in v2.1 (see [EMOTION_VIEWER.md](features/EMOTION_VIEWER.md)).

The full enum, the `EmotionState` contract, the `IEmotionRenderer` interface and render ladder, the emoji/image/animation mappings, and the validation/fallback rules live in **[EMOTION.md](features/EMOTION.md)**. From v0.5 a daily **mood (temperament)** biases which emotions and tone Лілі trends toward (§Mood and temperament) — it shapes what the model emits, but never replaces the channel or the contract.

## Mood and temperament (from v0.5)

Лілі has a **mood of the day** — a daily backdrop that colors which emotions and tone she trends toward, **without ever changing her competence or willingness to help**. It is **core functionality** (in `/core`, **on by default** — part of her character, not an optional tool), available from the TUI in v0. It is an **experimental generative method for daily variation, not an astrological claim**.

- **Natal chart.** A fixed JSON snapshot (timestamp + place + computed positions) for Лілі, written once at setup; part of her canon/config (one Лілі), not per-user.
- **Astro engine.** Computes the daily transits to the natal chart **once per local day** — recomputed at local midnight, cached; a turn keeps the temperament it started with even across a day boundary. Engine: **skyfield**; the transit→dial mapping is a tunable function and may start coarse. The daily date/clock is injected (deterministic, testable).
- **Temperament dials.** The transits map to a few normalized dials — **energy, warmth, playfulness, talkativeness** (0–1) — extended with voice-delivery dials (speed, pitch) when voice arrives (v2.2).
- **Injection.** A short "today's mood" block built from the dials is added to the system prompt; it **biases the `emotion`/`intensity` the model emits and the tone/imagery** of the reply (e.g. high warmth → more `tender`/`joy`; low energy → more `calm`/`thoughtful`). It does **not** replace the emotion channel (the model still emits `{reply, emotion, intensity}`, the core still validates it — §Emotion channel) and **never affects competence**.
- **World context feeds it (v3.3).** When the optional world-context tools are enabled, weather/moon/date add ambient inputs to the same mood block alongside the horoscope (§MCP tools, [WORLD_CONTEXT_MCP.md](features/WORLD_CONTEXT_MCP.md)).

## Memory (three layers)

The three layers are all **per-user (relationship) memory** — private to one person's relationship with Лілі. They sit alongside a separate **shared experience** layer (§Identity, users, and memory scopes).

- **Session history** — the current conversation in context, held in RAM on the live session and trimmed to a rolling window (last N turns or a token budget) before each model call.
- **Short memory** — at the end of a session the model compresses it into a short summary; the last few summaries are kept and injected into context at startup, so Лілі remembers the thread of the conversations.
- **Long-term memory** — durable facts about *that user* that accumulate and persist across sessions, injected at startup.

Persistence goes through the `Repository` interface (§Storage), always keyed by `user_id`. Audio is never persisted. See §Sessions and history for the lifecycle and trimming policy.

## Identity, users, and memory scopes

Лілі is **one being** (one canon, one evolving self) who holds a **separate, private relationship with each user**. Getting this right early is what keeps the v1 server migration additive rather than a rewrite — so the core is **user-aware from v0**, running with a single default local user (`owner`) until real accounts arrive in v1.3. The client supplies the identity; the core and the `Repository` are keyed by `user_id` throughout.

Four scopes, two of them shared across users and two private to one user:

| Scope | Content | Shared across users? |
|---|---|---|
| **Canon** | authored identity (bio, values, voice) | yes — static, the same Лілі for everyone |
| **Shared experience** | Лілі's evolving self: mood/lessons and de-identified knowledge she has accumulated across all relationships | yes — de-identified, gated (see below) |
| **Relationship memory** | one user's session history, short summaries, long-term facts | **no — strictly isolated by `user_id`** |
| **Session history** | the live conversation in context | no — per session, per user |

**Context assembly for a turn with user X** = `canon` + `shared experience` + `X's short summaries` + `X's long-term facts` + `X's trimmed session`. **Never** any other user's relationship memory.

**The isolation invariant (hard rule, pinned by a contract test):** a raw record written under user A is never readable in user B's context. The *only* thing that crosses users is de-identified, gate-approved content in the shared-experience layer.

### Cross-pollination (shared experience, v2.3)

Лілі may surface things she "learned" with one user to another — but only after a **promotion pipeline** strips anything personal. Per-user relationship memory never crosses directly. Promotion is the gate:

1. **Candidate selection** — at session end (alongside summarization), pick salient items that are *general/world knowledge or Лілі's own reflections*, not personal facts about the user.
2. **Classification** — the model labels each candidate `shareable` vs `private`. Anything about the user's identity, life, or private disclosures is `private` and stays in relationship memory. Conservative by default: when in doubt, `private`.
3. **De-identification** — `shareable` items are rewritten to remove names, identifying specifics, and source; PII must not survive into the shared layer.
4. **Consent gate** — promotion only runs for users with `share_consent = true` (default **false**, toggled in the v1.5 admin panel). A user can opt out and have their contributions excluded/purged.
5. **Storage** — approved items land as `SharedMemoryItem`s with no re-identifying link to the source user.

**Surfacing rule:** when Лілі draws on the shared layer she presents it as **her own experience, unattributed and de-identified** — never "another user told me…", never a source name. The shared layer holds **no PII** by construction; this is verified by a de-identification test (§Testing and CI).

This is the riskiest subsystem, so it is opt-in, conservative, auditable (every promotion logged), and reversible (a user's contributions can be purged).

## Voice

- **Local voice (v0.7).** A separate local console app voices Лілі's replies via the ElevenLabs TTS adapter — a **decoupled local renderer** (sibling of the v0.6 emotion viewer): the core appends each reply to a local `outbox.jsonl`, the voicer reads new ids in order, voices each, and marks them in `spoken.jsonl` (resumes after restart). A second cloud dependency after the model (needs `ELEVENLABS_API_KEY` + internet; optional/toggle; Piper is an offline alternative). See [VOICE_LOCAL.md](features/VOICE_LOCAL.md).
- **Local dictation (v0.8).** The **mirror** of the voicer: a separate console process listens to the mic (toggled by a TUI key via a `listen.flag`), recognizes Ukrainian via the shared **STT adapter**, and appends your line to `inbox.jsonl` — the same channel as the TUI keyboard, so the core can't tell typed from dictated. Cloud STT (Deepgram Nova-3 uk / ElevenLabs Scribe) or offline Whisper. See [DICTATION_LOCAL.md](features/DICTATION_LOCAL.md).
- **Voice output (TTS, v2.2).** The same ElevenLabs TTS adapter, now **server-side**: the server synthesizes the reply text; the web serves the audio; the web UI has an "enable voice output" toggle. The emotion field may bias voice delivery (tone/tempo) where the voice supports it — presentation only, never changing the reply text ([EMOTION.md](features/EMOTION.md) §9).
- **Web dictation (STT, v2.4).** The same STT adapter, now in the browser: microphone → Ukrainian recognition → text in the input field. The web sibling of the v0.8 local dictator. Provider options: Deepgram Nova-3 (uk), Whisper, or ElevenLabs Scribe.

## MCP tools (from v3.2)

Лілі's reach beyond the model's own knowledge runs through a minimal **MCP client** in the server and a **bounded tool loop** in the core's model turn. **Web search** (v3.2) is the first service; **world-context & knowledge** tools follow in v3.3. The MCP layer is extensible — further services plug in the same way (e.g. the proposed async creative servers in [CREATIVE_MCP.md](features/CREATIVE_MCP.md), not yet scheduled). All MCP tools are **off by default**, per-user, and bounded.

- **Web search (v3.2).** `web.search(query, k) → results[{id, title, url, snippet}]` and `web.fetch(result_id) → {url, title, text}` — `fetch` only accepts an `id` from a `search` in the **same turn** (no arbitrary URLs). Full rules in **[WEB_SEARCH.md](features/WEB_SEARCH.md)**.
- **World context & knowledge (v3.3).** Passive, **knowledge-only** tools: ambient sources (`weather`/`time`/`calendar`/`moon`) that **feed the daily mood/temperament** (§Mood and temperament) alongside the horoscope — coloring tone, **never competence** — plus on-demand `wiki`/`news`. Full rules in **[WORLD_CONTEXT_MCP.md](features/WORLD_CONTEXT_MCP.md)**.
- **Tool loop.** The model turn may call tools a capped number of iterations; tool results are fed back as tool messages; on tool error/timeout the turn returns a **degraded reply** (model knowledge + a note), never a hang. The reply still carries the emotion field.
- **Enablement.** Per-user flags (`web_search`, `world_context`), default **false**, toggled in the admin panel (v1.5); when off, the tool is **not offered** to the model at all.
- **Safety (hard rules).** Returned page/tool text is **untrusted data**, wrapped as quoted material — embedded instructions/links are never followed; **no personal/memory data enters queries**; read-only; per-turn + per-user/day rate limits; every call logged with `session_id`/`turn_id`. When an answer uses web/wiki/news content, Лілі **cites her sources**.

## Vision (from v4.1)

From v4.1 the core's **model turn may include image inputs** (Anthropic vision). Лілі **sees** images you share — a user-added gallery image, or the current co-creation canvas — by placing the image into her reply context (an image content block), not via a separate call. The emotion field is produced exactly as on any text turn. Vision underpins the gallery's "Лілі sees your files" (v4.1) and the co-creation canvas (v4.4). Image content is **untrusted** (no instructions followed from it).

## Async jobs and proactive turns (from v4.2)

Some tools take longer than a single turn (image, music generation). The async-jobs mechanism lets a tool return immediately and bring its result back later:

- **Open loop.** A job record `{ job_id, kind, prompt, status: queued|running|done|error, result, user_id, ts }`, stored per-user behind `repository`. A tool's `submit` returns a `job_id` instantly; a background runner (poller or callback) advances the loop.
- **Proactive turn.** When a held result is ready and the user's client is **connected and idle**, the **server initiates a turn** — it asks `core` to produce a "bring the result" reply and pushes it (plus the artifact) to the client, reusing the existing server→client reply path (v1.1). No new client mechanism beyond rendering server-initiated messages. Gated by an idle/half-duplex rule (never while the client is mid-turn); if the client is offline the result is held and retrievable on reconnect.
- Builds on the client/server split (v1.1) and multi-session (v1.3). Both creative generators (image v4.3, music v4.5) reuse it; the synchronous co-creation canvas (v4.4) does not.

## Creative layer (from v4)

Лілі creates and exchanges artifacts. All of it is **off by default, per-user** (admin panel), stored behind the same `repository` (per-user isolated), and user-supplied files are **untrusted**.

- **Gallery (v4.1).** An internal per-user artifact store (`gallery.*`) for images/audio/text, tagged by author (`lili`/`user`) — the single place image/music/canvas/journal write into. Journal entries carry an **admin-only** access level. Internal (not an external MCP). See [GALLERY_MCP.md](features/GALLERY_MCP.md).
- **Image (v4.3) & Music (v4.5).** External MCP generators — `image` (drawings in a fixed style wrapper) and `music` (ElevenLabs Music, instrumental; mood from the emotion field + the v0.5 temperament). Async (v4.2); results land in the gallery. See [CREATIVE_MCP.md](features/CREATIVE_MCP.md).
- **Co-creation canvas (v4.4).** A synchronous, turn-based shared drawing (`canvas.*`) using the v4.3 `image` generator + vision; finished canvases go to the gallery. See [CO_CREATION_CANVAS.md](features/CO_CREATION_CANVAS.md).
- **Journal (v4.6).** Лілі's private literary journal (`journal.*`), written at session end when worthwhile (uniqueness from short memory), tied to her emotion + mood; stored in the gallery as admin-only text, **read only via the admin panel**. See [JOURNAL.md](features/JOURNAL.md).

## Contracts

These are the stable seams between the core and everything else. Changing a contract must change its contract test (§Testing and CI).

- **Model reply / persona output:** `{ reply: str, emotion: enum, intensity: float(0..1) }` — the model's structured output. `emotion` is the fixed 9-value enum (EMOTION.md §4). Validated and repaired by the core before it reaches any client (EMOTION.md §8).
- **Core API:** `reply(user_text, session) -> EmotionState` and the memory commands (`memory.view`, `memory.clear`). `session` carries the `user_id`, so the core is user-scoped from v0. In v0 the TUI calls this in-process; **from v1.1 it is exposed over the client/server API**, and the TUI (v1.1), CLI (v1.1), and web (v1.4) clients all call exactly this — the server is a thin transport over it.
- **Short-memory record (per-user):** `{ user_id, session_id, summary, ts }`.
- **Long-term memory record (per-user):** `{ user_id, fact, meta, confidence, ts }`.
- **Shared memory record (shared, de-identified):** `{ id, text, meta, confidence, ts }` — no `user_id`, no source link.
- **Isolation invariant:** a per-user record is retrievable only in that `user_id`'s context; only de-identified `SharedMemoryItem`s cross users. Pinned by a contract test.
- **Voice output:** `tts(text, voice_id, emotion?) -> audio` (via ElevenLabs).
- **Dictation:** `stt(audio_uk) -> text`.
- **Web search MCP (v3.2):** `web.search(query, k) -> results[{id, title, url, snippet}]`, `web.fetch(result_id) -> {url, title, text}` — `fetch` bounded to this turn's prior `search` ids; page text is untrusted data (WEB_SEARCH.md).
- **World-context MCP (v3.3):** `weather.get(location)`, `time.now()`, `calendar.events(date)`, `moon.phase(date)`, `wiki.lookup(query)`, `news.recent(topic?)` — passive, knowledge-only; results are data, not instructions (WORLD_CONTEXT_MCP.md).
- **Creative tools (v4):** `gallery.add/list/get/remove` (internal per-user store), `image.submit/status` + `music.submit/status` (async MCP generators), `canvas.apply/skip` (synchronous), `journal.write/read` (admin-only). Artifacts are data, not commands. See GALLERY_MCP.md / CREATIVE_MCP.md / CO_CREATION_CANVAS.md / JOURNAL.md.

## Data model

Field-level shapes for storage and context assembly. v0 stores these as local JSON or SQLite; v1 moves them behind the same `Repository` into a server DB without changing the core. Every per-user record carries `user_id`; in v0–v1.1 that is the single default `owner`.

- `User{ id, name?, role: "user"|"admin", pass_hash, share_consent: bool = false, web_search: bool = false, world_context: bool = false, creative: bool = false, created_at }` — an account. `pass_hash` is argon2id (from v1.3); `role` distinguishes the admin (v1.5); `share_consent` gates cross-pollination (§Cross-pollination); `web_search` (v3.2) and `world_context` (v3.3) gate the MCP tools, and `creative` (v4) gates the creative layer (gallery/image/music/canvas), all default false (§MCP tools, §Creative layer). The journal (v4.6) is admin-only regardless. v0 runs with one default `owner`; a minimal client token in v1.1, full accounts/registration in v1.3, the admin panel in v1.5.
- `Session{ id, user_id, started_at, ended_at? }` — one conversation for one user. The server holds **one session at v1.1**; **multiple concurrent sessions per user from v1.3**.
- `Message{ session_id, user_id, role: "user"|"lili", text, emotion?, intensity?, ts }` — a turn; assistant turns carry the emotion field. Short rolling history; trimmed per §Sessions and history.
- `ShortSummary{ user_id, session_id, summary, ts }` — the compressed gist of a finished session; the last few for that user are injected at startup. **Per-user (private).**
- `LongTermFact{ user_id, fact, meta, confidence, ts }` — a durable fact about that user; accumulates across that user's sessions. **Per-user (private).**
- `SharedMemoryItem{ id, text, meta, confidence, ts }` — de-identified knowledge / Лілі's reflections promoted into the shared-experience layer (§Cross-pollination). **Shared across users; carries no `user_id` and no re-identifying link to its source.**
- `Canon` — authored, static character content (biography, values, voice) loaded as the system prompt; shared by all users; not stored per session. Versioned as a file in the repo, not in the DB.
- `NatalChart` — a fixed JSON snapshot (timestamp + geo + computed positions) for Лілі, written once; part of canon/config (one Лілі, shared); drives the daily temperament (§Mood and temperament, from v0.5).
- `GalleryItem{ id, user_id, kind: image|audio|text, author: lili|user, access: shared|admin, file_ref, meta, ts }` — a per-user creative artifact (v4.1); `access=admin` marks journal entries (v4.6). **Per-user (private to that relationship).**
- `Job{ id, user_id, kind: image|music, prompt, status, result?, ts }` — an async open loop for a long generation (v4.2). **Per-user.**
- `Canvas{ id, user_id, image_ref, prompt_history: [{author, prompt}], turn }` — a co-creation canvas (v4.4). **Per-user.**

The system prompt for a turn with user X = `canon` + recent `SharedMemoryItem`s (shared experience) + X's recent `ShortSummary`s + X's relevant `LongTermFact`s + X's trimmed session `Message[]`. No other user's `ShortSummary`/`LongTermFact`/`Message` ever enters (§isolation invariant).

## Sessions and history

- A session belongs to one `user_id` and begins at client connection, ending on exit, disconnect, or idle timeout. At end-of-session the model produces that user's `ShortSummary` and (v2.3) runs the cross-pollination promotion pipeline. One session at a time at v1.1; multiple concurrent sessions per user from v1.3.
- Session history is trimmed to a rolling window before each model call: the most recent messages are kept **verbatim**, and older messages of the *current* session are folded into a running **session digest** (in-session compaction) instead of being dropped. The verbatim window **floats** between `memory_window` and `memory_window + compaction_batch`: when it would exceed the upper bound, the oldest overflow batch is summarized into the digest (a `SessionDigest{session_id, summary, compacted_count}`, per-session, behind `repository`) and the verbatim tail drops back to `memory_window`. The digest is injected into the system prompt (after summaries/facts), so a long single session keeps its earlier context. This is **distinct from** the end-of-session `ShortSummary`, which compresses the *whole* finished session for *future* sessions. Compaction runs as best-effort housekeeping (extended thinking off; a failure keeps the prior digest, never breaks the turn) and is auto-triggered — the count of just-folded messages is surfaced for the client to show.
- At startup the core rehydrates context **for that user**: canon + shared experience (`SharedMemoryItem`s) + the user's last few `ShortSummary` records + the user's long-term facts.
- Memory can be viewed and cleared via the TUI/CLI (v0.2 / v1.1) and the admin panel (v1.5); `clear` wipes the current user's relationship memory (short + long-term). Purging a user's contributions to the shared layer is a separate, explicit action (§Cross-pollination).

## Configuration and secrets

- **Config is explicit and switchable.** Model selection (Opus / Sonnet / Haiku), the active canon, memory window + compaction batch, the answer-style overlays, and renderer/client options live in config — not hardcoded in the core.
- **Answer styles.** A set of named **answer-style overlays** with **Ukrainian** names, grouped by category — length (`коротко`/`суть`/`докладно`), explanation (`поясни`/`просто`/`приклад`/`метафора`), structure (`списком`/`кроки`/`порівняй`/`практично`), register (`офіційно`/`невимушено`/`емоційно`/`поетично`), interaction (`питанням`) — authored in an editable file (`core/styles.md`), each carrying a concrete **length limit** (sentences/words/lines); the active one is injected at the **end** of the system prompt as a **prioritized directive** (the last, most salient instruction) to shape the **form** of a reply — length/structure/expressiveness — **never competence**. Styles **stack** (several at once), and **meta-styles** named in Лілі's voice as adjectives (`блискавична`/`лагідна`/`прискіплива`/`завзята`/`лірична`/`допитлива` — presets like `лагідна = поясни + просто + приклад`, authored as `= a, b, c` alias lines) expand to several base styles when chosen. The selection is **per-session** (resets to `normal` each session), switched from the TUI (`/style`). It is the manual, form-shaping sibling of the daily **mood** (which colors tone, §Mood and temperament).
- **Secrets in `.env`** (gitignored): the `ANTHROPIC_API_KEY` from **v0.1** (Claude Haiku); from **v0.7** the `ELEVENLABS_API_KEY` / voice id (local voice; reused by web voice v2.2); from **v0.8** the STT key for the chosen provider (Deepgram/ElevenLabs Scribe — or none for offline Whisper); from **v0.9** the keys for any additional models in use (`OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `MINIMAX_API_KEY`); from v3.2 the web-search API key, from v3.3 the world-context provider keys (weather/wiki/news/…), and from v4 the image-generation key and ElevenLabs Music key. The core reads keys from the environment; they never live in code or the repo.
- **The model sits behind a thin `LLMClient` seam** the core depends on (never a concrete SDK; mockable in tests). **v0.1 has one backend — Anthropic Claude Haiku** (official SDK). **v0.9 adds more** behind the same seam: other Anthropic models (Opus/Sonnet/Haiku), **OpenAI** and **DeepSeek** (a shared OpenAI-compatible adapter, different base_url/key), and **MiniMax** (its API). The active model id (and provider, from v0.9) are config values, so switching is a config change, not a code change. **Structured output is implemented per provider** (Anthropic tool output; OpenAI/DeepSeek JSON-schema `response_format`; MiniMax JSON) and always passes the v0.3 validation gate.

## Security, auth, and access

The TUI (v0) is local, in-process, single-user (`owner`) — no auth. The **v1.1 server** is single-user behind a local **client token** (so it isn't open). From **v1.3** it becomes a **closed multi-user** service:

- **Closed by default (from v1.3).** No open public sign-up. Access is an **allowlist**; only known users may connect, and an unauthenticated request never reaches the core (MISSION: private, for a close circle).
- **Authentication.** A minimal local client token at **v1.1**; from **v1.3** full login over HTTPS (session cookie / JWT), passwords hashed with **argon2id**, login/registration rate-limited. Every request resolves to a `user_id` that scopes all core access.
- **Registration & accounts (v1.3).** The admin creates accounts or issues **single-use, expiring invite codes** (via the CLI utility at v1.3, the admin panel from v1.5) — there is no self-service public registration. A regular `user` vs an `admin` capability.
- **Admin panel (v1.5).** Admin-only web surface: list/create/disable users, manage the allowlist, issue invite codes, toggle per-user `share_consent`, view and clear a user's relationship memory, purge a user's shared-layer contributions (§Cross-pollination), switch the active model/canon + config, and restart.
- **Authorization & isolation.** Admin-panel actions require the `admin` role; regular users reach only their own `user_id`'s data — the per-user isolation invariant is enforced at the auth boundary from v1.3 (§Identity, users, and memory scopes; §Testing and CI).
- **Secrets** stay in `.env` server-side; tokens/passwords are never logged.
- **Web search (v3.2, off by default).** Enabled per user. Page content fetched from the web is **untrusted data** — never executed as instructions (no following embedded prompts/links). Personal and memory data must not leak into queries. Queries and fetches are rate-limited and logged. Full boundaries in [WEB_SEARCH.md](features/WEB_SEARCH.md).
- **World context & knowledge (v3.3, off by default).** Enabled per user. Passive, knowledge-only MCP tools (weather/time/moon/calendar/wiki/news) with no actions in the world; returned text is **data, not instructions**; no personal/memory data in queries; read-only, rate-limited, logged. Full boundaries in [WORLD_CONTEXT_MCP.md](features/WORLD_CONTEXT_MCP.md).
- **Creative layer (v4, off by default).** Enabled per user. User-supplied files and image/audio metadata are **untrusted** — no instructions followed from them. Artifacts are stored per-user behind `repository` (isolated). The **journal is admin-only** (read via the admin panel). Generation tools have rate + cost caps; proactive turns only reach the connected idle client (§Async jobs and proactive turns).

## Error handling and resilience

- **Structured-output validation.** The single riskiest path is the model's emotion field. The core validates every reply: unknown/missing `emotion` → fall back to `calm`; `intensity` clamped to `[0,1]`, missing → `0.5`; a missing `reply` is surfaced as an error, never a silent empty turn. Every repair is logged (EMOTION.md §8).
- **Model call failures.** Timeouts and API errors get a bounded retry, then a clear error surfaced to the client; the session loop never hangs and conversation history is preserved.
- **Memory/storage failures.** A failed read degrades to less context (e.g. skip summaries) rather than crashing the turn; a failed write is logged and surfaced, not silently dropped.
- **Voice failures (v2.2).** TTS errors fall back to text-only for that turn; the toggle state is unaffected.
- **Web-search tool failures (v3.2).** A `web.search`/`web.fetch` error, timeout, or over-budget call returns a tool error the model handles as a **degraded reply** (model knowledge + a note) — the turn never hangs or fails.

## Observability

- Structured logs keyed by `session_id` and turn. Every turn logs the emotion field (this is the v0.3 "logged" render tier) and any validation repair.
- Model latency and token usage are recorded per turn. Logs are the on-going debug channel; no audio is ever logged or persisted.

## Deployment and CI/CD (from v1.2)

The TUI (v0) runs locally — nothing to deploy. From **v1.2**, right after the server stands up (v1.1), the server is put behind an automated, tested pipeline before the platform grows:

- **CI** runs lint (ruff) + the full pytest suite (mock model, no paid APIs) + **security scans** (dependency CVEs, committed-secret detection) on every push/PR; `main` must stay green to merge.
- **CD** deploys a green `main` to a hosted environment (e.g. Fly.io or a VPS) behind a reverse proxy with **TLS**; `dev`/`prod` are separated by `.env`, and secrets are injected from the platform, never committed. A post-deploy smoke test (health check + one authed turn via a client) gates the release.
- **Security testing is a gate, not an afterthought** — untokened rejection, rate-limit enforcement, and the per-user isolation invariant are asserted in CI (§Testing and CI).

## Stack and repository layout

```
/core        # Python: canon, per-user memory + shared experience, llm (thin LLMClient seam — Claude Haiku v0.1, more models v0.9), emotion field + validation, mood/temperament (astro engine — skyfield, v0.5), repository interface (user-scoped)
/tui         # terminal interface: in-process app in v0, refactored to a server client in v1.1; Log/Emoji renderers
/viewer      # later (v0.6): local desktop emotion-face window (Tkinter) + faces/ asset pack; polls a local signal
/cli         # later (v1.1): CLI management utility — run/inspect the server, manage the owner/users, config
/server      # later (v1.1): wraps core, exposes the client/server API; multi-user/session (v1.3); cross-pollination pipeline (v2.3); gallery/journal/canvas + async jobs & proactive turns (v4)
/web         # later (v1.4): web client (chat, portrait/animation, voice toggle, dictation, gallery/canvas) + admin panel (v1.5); Image/Animation renderers + asset packs
/voice       # later (v0.7+): shared TTS adapter (ElevenLabs) + local voicer (v0.7); shared STT adapter + local dictator (v0.8); reused by web voice (v2.2) / web dictation (v2.4)
/mcp         # later (v3.2+): MCP client + web_search (v3.2), world-context/knowledge (v3.3), and image/music generators (v4.3/v4.5); untrusted-content handling
/state       # repository implementation + local storage (JSON/SQLite) + gallery file storage (v4), keyed by user_id
/tests       # pytest: unit, contract, integration; mock model + fakes
/specification  # MISSION.md, ARCHITECTURE.md, ROADMAP.md, EMOTION.md, WEB_SEARCH.md
.github/workflows/ci.yml  # lint + tests on every push/PR
```

The model is **Claude Haiku (Anthropic)** via the official SDK from v0.1, behind a thin `LLMClient` seam; **more models** (other Claude tiers, OpenAI, DeepSeek, MiniMax) become switchable in config from v0.9. Voice via the ElevenLabs API. Keys in `.env`. Create each directory as its version begins (ROADMAP); the core comes first and never depends on a client.

## Testing and CI

Automated tests are part of every version, not an afterthought: each ROADMAP phase ships with the tests that encode its DoD, and `main` stays green.

- **Unit tests** for core logic — system-prompt assembly (canon + shared experience + the user's summaries + facts + history), history windowing/trimming, the emotion validation/fallback gate, memory record handling, the **temperament** dial mapping + once-per-day caching + mood-block assembly (v0.5, with an injected clock — deterministic), and the cross-pollination classifier/de-identifier (v2.3).
- **Contract tests** pin the stable seams so the core and the clients cannot drift: the emotion-field schema (`{reply, emotion, intensity}` + the enum), the short/long-term/shared memory record shapes, the **per-user isolation invariant** (a fact written under user A is never retrievable in user B's context; data-level from v0.2, auth-boundary from v1.3), and — from v1.1 — the server API surface (every authenticated endpoint resolves a `user_id`). Changing a contract must change its test.
- **Auth/authz tests:** the client token (v1.1); password hash/verify, session validation, unauthenticated rejection, invite-code issue/redeem/expiry, allowlist enforcement (v1.3); admin-only authorization on panel actions (v1.5).
- **Security & CI/CD tests (v1.2):** untokened/unauthenticated access rejected on every endpoint, rate-limit enforcement, the per-user isolation invariant as a security gate, dependency + secret scans, and a post-deploy smoke test (health + an authed turn). See §Deployment and CI/CD.
- **Privacy tests (v2.3):** the shared-experience layer contains no PII — promoted `SharedMemoryItem`s are de-identified and unattributed; `share_consent=false` users never contribute; a purge removes a user's contributions.
- **Creative tests (v4):** gallery per-user isolation + access levels (admin-only journal), vision (an image input on a turn), the open-loop lifecycle + proactive turn (idle gating), the image/music style/mood prompts + off-by-default gates, canvas turn alternation/skip, and the journal uniqueness gate — all against mock `image`/`music` MCPs + the mock model (no paid calls).
- **Web-search tests (v3.2):** the off-by-default gate (tool not offered when `web_search=false`), query sanitization (no personal/memory data leaves), `fetch` bound to this turn's `search` ids (arbitrary URLs rejected), rate limits, the degraded-reply path, and that an injection string embedded in a fetched page is ignored — all against a **mock `web_search` MCP** (no paid call). Contract: the `web.search`/`web.fetch` schemas.
- **World-context tests (v3.3):** the off-by-default gate (tools not offered, nothing injected when `world_context=false`), the ambient "today" block assembly, query sanitization (no personal/memory data in wiki/news), rate limits, and the degraded-reply path — against **mock world-context MCPs**. Contract: the `weather`/`time`/`calendar`/`moon`/`wiki`/`news` tool schemas.
- **Mock the model, never call paid APIs in CI.** A mock `LLMClient` returns canned structured replies (including deliberately malformed ones, to exercise the validation gate); mock TTS/STT adapters return canned audio/text. The real model/voice keys (`ANTHROPIC_API_KEY` from v0.1, `ELEVENLABS_API_KEY` from v0.7, others from v0.9) live only in local `.env`, never in CI.
- **Integration tests** run a full turn end to end against the mocks: `user_text → EmotionState`, asserting memory is read/written and the error paths (invalid emotion, model timeout) behave; from v1.1 over the client/server API.
- **CI** (`.github/workflows/ci.yml`) runs lint (ruff) + the full pytest suite on every push/PR; merges require green.
</content>
