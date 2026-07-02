# Roadmap — Lumi

Six self-contained versions, built in order: **v0** TUI (core, memory, emotion, emoji, mood, semantic recall, file tool, voice) → **v1** Personality (inner life, needs, inner monologue, emotional memory) → **v2** server platform (client/server split, multi-user, web, admin) → **v3** face, voice, shared mind, and dictation → **v4** animated Лілі + MCP tools (web search, world context) → **v5** creative Лілі (gallery, art, music, journal, co-creation). Versions are numbered from 0; phases inside a version are numbered `vA.B` (A = version, B = phase), e.g. `v2.2`. Each phase lists a **Goal**, a short description, a **Tasks** list, and a **Definition of Done (DoD)**, and ships with the automated tests that encode its DoD (see [ARCHITECTURE.md](ARCHITECTURE.md) §Testing and CI).

Arc of the two axes: capabilities grow text+memory → emotion (emoji) → daily mood → local image face + local voice + dictation → inner life + needs + inner monologue + emotional memory → web face + voice + shared mind + dictation → animation + web search & world context (MCP) → creation (gallery, vision, art, music, journal, co-creation); the interface grows in-process TUI (v0) → server + TUI/CLI clients (v2.1) → + multi-user/session (v2.3) → + web client (v2.4) → + vision & proactive turns (v5). The **core is built first and never depends on an interface**. Complexity is added only by version, never all at once.

**Versioning (`A.B.C`).** `A` = roadmap version (v0→0 … v5→5), `B` = phase within it (`v2.2` → `2.2.0`), `C` = a post-release fix on that phase. Roadmap phase `vA.B` → semver `A.B.0`; a fix after it bumps `C`. Releases are cut per phase. Never bump the version without explicit confirmation.

---

## v0 — TUI: core, memory, emotion, emoji, mood (+ biorhythms), local face, closeness, face wardrobe, semantic recall (RAG), file tool, voice, dictation

The complete terminal Лілі. We build the entire mind — canon, three-layer memory, the emotion channel, the emoji that renders it, a daily **mood of the day** (a horoscope-derived temperament), a **local image face** (a desktop window showing her current emotion), a **local voice** (a console app that speaks her replies) and **local dictation** (speech → text input) — all in a **local app, no server**. The model is **Claude Haiku (Anthropic)** from the start (v0.18 adds more models); the app runs on your machine but calls Anthropic for the model (and, from v0.14, ElevenLabs/STT for voice in and out), so it is **local-but-not-offline** (`ANTHROPIC_API_KEY` in `.env`). **v0 is wholly local (TUI + a local face window + a local voicer + a local dictator, calling cloud models)**: it establishes the interface-independent `core`, a thin **`LLMClient`** seam (mockable in tests), and the contracts (emotion field, memory records, temperament) that every later version reuses. In v0 the TUI calls the `core` **in-process**; v2 splits them into client and server. Depends on: nothing — this is the foundation.

### v0.1 — Skeleton and canon

**Goal:** a working text chat with Лілі's character in the terminal.

Stand up the project skeleton and the `core` package, an **Anthropic client (Claude Haiku)** behind a thin **`LLMClient`** seam the core depends on (mockable in tests; model id from config, default Haiku), Лілі's authored canon loaded as the system prompt, a TUI loop with input and scrollable history, and the `Repository` interface with a local store behind it. The core exposes one `reply(...)` contract the TUI calls — no interface logic leaks into the core. **Claude Haiku is the only model to start**; more models are added behind the same seam in v0.18.

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

Add the three (per-user) memory layers: session history trimmed to a rolling window in context; short memory — at session end the model compresses the conversation into a `ShortSummary`, the last few are kept and injected at startup; long-term memory — accumulation of durable `LongTermFact`s about the user, also injected at startup. Memory is viewable and clearable from the TUI. **Crucially, the data model and the `Repository` are user-scoped from here** — every record carries a `user_id`, run with a single default `owner` — so the v2 multi-user server is additive, not a rewrite (ARCHITECTURE §Identity, users, and memory scopes). The shared-experience layer exists in the model now but is trivially just the owner's until v3.

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
- Structured output `{reply, emotion, intensity}`; constrain `emotion` to the enum and `intensity` to 0–1 via Anthropic's tool/structured output (Claude Haiku) — EMOTION.md §8. (Other models' structured output is handled per-provider when they arrive in v0.18.)
- The validation/fallback gate (unknown emotion → `calm`; clamp/default intensity; missing reply → error) — EMOTION.md §8.
- Log the emotion field per turn (the "logged" render tier); structured logs keyed by `session_id`.
- `IEmotionRenderer` interface + `LogRenderer`; optional small TUI status line showing the current state.

**DoD:** every reply carries a valid emotion and intensity; the channel is ready for visualization.

**Tests:** contract — the emotion-field schema + enum (pinned here, must change with the contract); unit — the validation/fallback rules, including deliberately malformed mock replies.

### v0.4 — Ambient context (now, here, weather, news)

**Goal:** Лілі knows *when* and *where* she is — and what's around — her sense of time threads through the whole conversation, and she can break a long silence first.

A lightweight **ambient context** fetched **once at startup** (not MCP, not per-turn): the local **date/time** (injected clock), today's **calendar** (weekday, notable dates), the user's **location** (configured or IP-geolocated), current **weather**, and a few **short news** headlines. It is assembled into a small "now / here" block injected into the system prompt — **ambient color, never competence** (it may tint her tone, like the hour or the weather; it never makes her authoritative or "smarter"). Fetched text is **data, not instructions**. Each source is **config-gated** (its own key/endpoint); when one is off or fails, that line is omitted — the turn never hangs. This ambient layer is what the v0.6 **mood** reads. Two prompt-assembly changes also make time legible: **every message carries its date-time**, and **short summaries carry their date**.

The same time-awareness lets her **break a long silence**: an **idle nudge** quietly feeds her a user-style opener from a list when you've gone quiet — you never see the nudge, only her reply — so she seems to speak first. (A simple, presence-only proactivity now; the v5.2 open-loop proactive-turn mechanism generalizes it later.)

**Tasks:**
- A small **`WorldContext` provider** at the core boundary (interface-independent): a `fetch()` called once at startup -> a `WorldContext{ now, calendar, location, weather, news }` snapshot. Time/date come from the **injected clock** (deterministic/testable); weather/news/location from thin HTTP calls to configured providers (no SDK, no tool loop). Any source error -> that field is `None`, never an exception.
- **Config-gated sources** (off unless configured): `LUMI_LOCATION` (or IP geolocation), weather key + endpoint, news key/RSS + a small headline cap (3-5, truncated). The clock/calendar need no key; documented in `.env.example`.
- **Ambient block** in `build_system_prompt` (opt-in `ambient=...`): a short now/here line — date-time, weekday, location, weather, 3-5 headlines — placed near the canon; **coloring tone, never competence**; fetched text quoted as data, never followed.
- **Per-message timestamps:** each message in the model's `messages` array is prefixed with its `ts` (compact local date-time), so Лілі perceives the rhythm of the conversation and the gap since the last turn.
- **Dated short memory:** each `ShortSummary` injected into the system prompt carries its date (its `ts`).
- **Refresh policy:** weather/news/location are a startup snapshot held for the session (re-fetched on `/new` or a coarse interval); the **date-time string is recomputed per turn from the clock**, so "now" stays current.
- **Idle nudge (a hidden self-started turn).** An **idle timer** in the TUI driven by the injected clock: when the chat is open and there's been no activity for a configurable interval (default ~3–5 min), pick a line from an authored **nudge list** (short, user-style openers — e.g. «ти тут?», «про що думаєш?») and run it through the **normal turn as a hidden user message**. Лілі's reply is shown (with her emotion); the injected user line is **never displayed** (suppressed from the on-screen chat, the copy/transcript, and one-key copy) — so from your side she simply spoke first. The nudge turn is a real turn (memory + ambient + emotion), so her opener fits the moment. **Config-gated** (on/off, interval, the nudge file), **rate-limited** (at most one per idle gap; the timer resets on any real input), and respects quiet hours. Whether the hidden line persists to history (coherent follow-on) vs. stays ephemeral is a flagged implementation choice — either way it is marked `auto-nudge` so it never surfaces as something you typed.

**DoD:** at startup Лілі has a `WorldContext` snapshot (degrading gracefully when a source is off/unavailable); the system prompt carries the ambient block; every message and every recalled summary is timestamped; nothing hangs when a source fails. With the idle nudge on, a long silence (chat open) yields a reply from Лілі and **no visible user message**; real input always resets the timer; the feature is off by default.

**Tests:** unit — the `WorldContext` provider with **mocked HTTP** (no live calls in CI), each source failing -> field `None`; the injected clock drives a deterministic "now"; `build_system_prompt` assembles the ambient block and omits absent fields; the **idle timer fires after the configured gap and is rate-limited** (deterministic via the injected clock — no real sleeps). Integration — a turn's `messages` carry per-message timestamps and the prompt shows dated summaries; an **idle nudge produces a Лілі reply while the hidden nudge line is absent from the displayed transcript**; a fully-unconfigured run still works (no ambient block, no nudge, no error).

### v0.5 — Emoji rendering

**Goal:** Лілі's emotion is visible right in the terminal — the last step that makes v0 the complete TUI.

Swap `LogRenderer` → `EmojiRenderer` over the v0.3 channel: map each emotion to an emoji (EMOTION.md §4/§6) shown next to the reply, with `intensity` selecting emphasis — the same face made stronger by **repeating it or adding an accent** over three bands (low/mid/high; EMOTION.md §6 table), not a different feeling. No contract change — only a new `IEmotionRenderer` implementation. Depends on: v0.3 (the locked emotion contract).

The emotion→emoji+intensity table is an **editable authored file** (like the canon, styles, and nudges) — config-pathed (`LUMI_EMOJI_PATH`, default e.g. `core/emoji.md`) — so **the user can change the table and add / remove / replace emojis without touching code**. A built-in default ships the EMOTION.md §6 map; a missing file or an absent/blank row **falls back** to the built-in (and ultimately the base glyph / `calm`), so the map stays **total over the enum**.

**Tasks:**
- An **editable emoji-map file** + loader: per emotion, its base face and the low/mid/high emphasis (repeat or accent); `#` comments; loaded at startup. Authored default = EMOTION.md §6.
- `EmojiRenderer` implementing `IEmotionRenderer` over the loaded map; `emoji_for(state)` resolves emotion + intensity band → glyph(s).
- Display the emoji next to Лілі's reply in the TUI (e.g. `Лілі 😄✨:`).
- Account for `intensity` via the three bands; **fallback** when the file/row is missing (→ built-in default → base glyph → `calm`).

**DoD:** during the conversation Лілі's emotion reads as an emoji next to her reply, with intensity emphasis; **the table is user-editable — adding / removing / changing emojis in the authored file takes effect on restart** (no code change); v0 is a complete terminal companion.

**Tests:** unit — the loader parses the authored table (base + low/mid/high); the resolved map is **total over the enum** with intensity-band selection; a missing file / absent row / unknown emotion **falls back** to the built-in default (ultimately `calm`), never raising.

### v0.6 — Mood of the day (temperament)

**Goal:** Лілі has a daily mood — a horoscope-derived backdrop that colors her tone and the emotions she trends toward, never her competence.

Add a **core** temperament subsystem driven by the **model itself — no astronomy engine** (a real-ephemeris test showed the model can write a vivid, useful daily reading but cannot compute accurate transits; precision isn't the goal — daily *variation* is). Лілі has a **fixed natal chart**; **once per local day** the core makes an internal **mood call** (through the `LLMClient` seam) that asks for a vivid horoscope-flavored reading from the natal chart + today's date, ending in a short **resolution** (what she'll want / won't want / her mood, energy, tone). The **full reading is logged** (not shown, not in the prompt); **only the resolution is injected** into the system prompt — as a **prominent, prioritized block** (like the style header's importance directive) so it actually colors the turn. It **biases the emotion the model emits and her tone — never her competence or willingness to help**. It rides the v0.3 emotion channel (the model still emits `{reply, emotion, intensity}`; the core still validates it). **On by default** — part of her character, not an optional tool. An **experiment for daily variation, not an astrological claim**. (World-context inputs feed the same mood in v4.3; voice-delivery dials in v3.2.) See ARCHITECTURE §Mood and temperament. Depends on: v0.3 (the emotion channel) and v0.4 (the injected clock).

**Tasks:**
- Лілі's **natal chart** as a fixed snapshot (date/time/place + positions) in canon/config (already seeded in the canon; verified accurate against a real ephemeris).
- A **daily mood call** through the `LLMClient` seam: once per local day, generate a detailed reading + a short **resolution** (wants / doesn't want / mood / energy / tone) from the natal chart + today's date. Cached per local day (injected clock); recomputed at local midnight; a turn keeps the mood it started with. Mock the model in tests.
- **Log the full reading** (persisted/logged, keyed by date) — never shown to the user, never in the prompt.
- **Inject only the resolution** into the system prompt as a **prominent "today's mood" block** — a prioritized directive (mirroring the v0.5 style header) — that biases the emitted emotion + tone, never competence. A model/clock failure degrades to no mood block (never blocks a turn).
- A **`/mood` command** (TUI) that shows the current day's resolution on demand.
- (Optional) show the current mood in the small TUI status line.

**DoD:** on different days Лілі's tone and the emotions she leans toward shift with the day's resolution, while the quality of her answers is unchanged; the mood is stable within a day and recomputes at local midnight; the **full reading is in the logs**, **only the resolution rides in the prompt** (as a prominent block), and **`/mood`** shows it.

**Tests:** unit/integration against a **mock model + fixed clock** — once-per-day caching (stable within a day, recompute across local midnight), that the **resolution (not the full reading) is what's injected** and is marked prominent, that the **full reading is logged**, that `/mood` returns the resolution, and that the mood biases the emitted emotion **without changing competence**; a mood-call failure degrades gracefully. No paid calls.

### v0.7 — Local emotion viewer (image face)

**Goal:** Лілі's face as a real **image**, locally, without a server — the simplest way to see her before the web.

Add a small **separate local desktop window** (e.g. Python/Tkinter) that shows a pre-made portrait of Лілі for her current emotion. It is **another renderer of the v0.3 emotion channel** — the same `emotion → image` mapping the web `ImageRenderer` (v3.1) will use, just rendered to a local window from a `faces/` asset pack instead of a browser. The core writes the current emotion to a **local signal** (a one-word file, or the emotion field in local state behind `repository`); the viewer polls it and swaps `faces/<emotion>.png`, with a **`calm` fallback** so the window never breaks. No generation — just switching between existing images by emotion. Distinct from the v5 *gallery* (the artifact store); these are the **emotion-face assets** (EMOTION.md §7). See [EMOTION_VIEWER.md](features/EMOTION_VIEWER.md). Depends on: v0.3 (the emotion channel).

**Tasks:**
- The core writes the current emotion to a **local signal** each turn (a one-word file or local state).
- A separate **viewer process** (Tkinter or similar): poll the signal (~0.5–1 s or a filesystem watch) → show `faces/<emotion>.png`.
- A `faces/` asset pack with one portrait per emotion (the 9 enum values); `calm` is the neutral default for unknown/missing.
- (Optional) intensity variants (`joy_low.png`/`joy_high.png`), picked by `intensity` — one image per emotion is enough to start (EMOTION.md §7).

**DoD:** a local window shows Лілі's face by her current emotion and changes as the conversation does; an unknown/missing emotion falls back to `calm`.

**Tests:** unit — the emotion→image-path resolver is total over the enum and falls back to `calm`; the signal read/poll logic (against a fake signal file); intensity-variant selection when variants exist.

### v0.8 — Biorhythms (merged into the daily mood)

**Goal:** Лілі's daily temperament gains a second, **computed** layer — three biorhythm cycles from her birth date — **merged with the v0.6 horoscope into one daily reading**.

Unlike the horoscope (the model writes it; it can't compute transits), biorhythms are **exact deterministic math** — sine waves from the natal birth date — so the core computes them and hands them to the mood:
- **Engine (in code, not the model).** Physical (23 d), Emotional (28 d), Intellectual (33 d), each `sin(2π · days_since_birth / period)` from the natal birth date (`core/natal.md`) + today (the v0.4 injected clock). Each → a value (−1…+1) + a label (high / low / rising / falling / **critical** near a zero-crossing). Pure, unit-tested.
- **Merged with the astrology forecast (v0.6).** The biorhythm state is added to the **mood call's** inputs, so the daily **reading + resolution blend horoscope + biorhythms** into one temperament (e.g. "emotional cycle low + a tense transit → a quiet, sensitive day"). The v0.6 contract is unchanged otherwise: the **full reading is logged**, only the **resolution** is injected, and it **biases tone/energy, never competence**.
- **Once per local day**, cached with the mood, recomputed at local midnight. A `/biorhythm` command shows today's three values; `/mood` shows the merged resolution.

See [BIORHYTHMS.md](features/BIORHYTHMS.md). Depends on: v0.6 (the mood it merges into), v0.4 (the clock), and the natal birth date.

**Tasks:**
- A pure `core/biorhythm.py`: `biorhythms(birth_date, today) → {physical, emotional, intellectual}`, each a value + phase label (incl. **critical** near zero); the birth date is read from `core/natal.md`.
- **Merge into the mood:** pass the biorhythm state into the v0.6 mood call so the reading/resolution blends horoscope + biorhythms; full reading logged, resolution injected (the rest of v0.6 unchanged).
- Compute once per local day, cached with the mood (injected clock; recompute across midnight).
- A `/biorhythm` command (the three values + labels); the merged resolution stays in `/mood`.
- **Guardrail:** the merge colors tone/energy, **never competence** (the mood's hard rule).

**DoD:** each local day the mood reading **blends the horoscope with the three computed biorhythm cycles** into one resolution; the cycles are exact (deterministic sine from the birth date, including critical-day detection); `/biorhythm` shows today's values; the merge never affects competence; cached per local day.

**Tests:** unit — the biorhythm math (known days-since-birth → exact values + labels; critical-day detection) against a fixed clock; the merge feeds the mood call (mock model — the biorhythm state appears in the mood prompt); cached once per local day; `/biorhythm` renders. No paid calls.

### v0.9 — Richer short memory (recent detail + days at a glance)

**Goal:** Лілі recalls **recent conversations in detail** and **the past few days at a glance** — a richer *short* memory, without touching long-term facts.

Today short memory keeps the last 5 conversation summaries (one detailed summary each) and injects them; **long-term memory stays the facts list** (`LongTermFact`, unchanged — this is purely a short-memory enhancement). This phase makes each session's summary **two-tiered** and widens the recall window:
- At session end, **one** summarization call (extended thinking off, as today) returns **both** a **detailed** summary (the current size-scaled one) and a one-line **gist**; both persist on the `ShortSummary`.
- The short-memory block in the prompt becomes two tiers: the **last N=5 conversations** as **detailed** summaries, plus **all conversations within the last D=5 local days** as **gists** — the recent N shown only as detail (no repeat), dated. `N` and `D` are config; the gist tier is **bounded by the day window** (no row cap).

It is a memory-record shape change — `ShortSummary` gains a `gist` (the `summary` field stays the detailed one) — so it updates ARCHITECTURE §Memory + the `ShortSummary` contract test, with a **migration** for existing summaries (no `gist` → omitted from / truncated in the gist tier). "Last D days" uses the **injected clock** (local day). Depends on: v0.2 (the memory layers).

**Tasks:**
- **Two-tier session summary.** At session end, **one** summarization call returns a **detailed** summary + a short **gist**; persist both: `ShortSummary{user_id, session_id, summary, gist, ts}`.
- **Wider recall in the prompt.** The short-memory block injects the **last N conversations (detailed)** + **all conversations in the last D local days (gists)**, de-duplicated (recent N excluded from the gist list), dated; config `N` (default 5), `D` (default 5 days), and a max-count / token cap.
- **Repository window query.** Add "summaries since `<date>`" (or load + filter by the injected-clock local-day window); the recent-N stays a recency query.
- **Migration + contract.** Existing `ShortSummary`s (no `gist`) load fine and are omitted from / truncated in the gist tier; update ARCHITECTURE §Memory + the `ShortSummary` contract test. Long-term facts untouched.

**DoD:** the prompt carries the **last 5 conversations in detail** and the **last 5 days' conversations as gists** (no duplication, dated; bounded by the day window); long-term facts unchanged; old summaries still load and inject.

**Tests:** unit — the two-tier assembly (last N detailed + D-day gists, dedup, cap) against a fake store + fixed clock; the one-call summarizer returns both fields (mock model); the "since date" / local-day window selection; old-summary migration (no `gist`). Contract — the updated `ShortSummary` shape.

### v0.10 — Closeness (relationship level)

**Goal:** Лілі grows **closer to (or cooler with) each person over time** — a per-user closeness level that modulates how *open* she is, **never her competence**.

A per-user **closeness value** bucketed into **5 levels**, read from how you talk to her and how often:
- **Per-turn relational read.** Folded into the reply turn (one call), the model scores *your* message — **warmth / vulnerability / playful** raise, **harm / manipulation** lower — emitted **alongside** `{reply, emotion, intensity}` (additive; the locked v0.3 emotion contract is untouched).
- **Value + time decay.** The core turns the read into a small delta, applies **decay toward a baseline over days of silence** (frequent contact builds faster; via the v0.4 injected clock + `last_ts`), updates the value, re-buckets to a level with **inertia** (no turn-to-turn flapping).
- **Behavior per level** (authored, editable like styles): L1 reserved → L2 friendly → L3 familiar/teasing → L4 close/tender → L5 intimate/trusted. The active level injects a "closeness" block into the system prompt — it shapes **warmth / openness / initiative / teasing / vulnerability**.
- **Hard guardrail (same as the mood):** it **never** changes her competence or willingness to help. L1 is more reserved/formal — **never cold, withholding, or less useful**; a low `harm`/`manipulation` score never triggers a refusal. She stays kind and capable; only how *close* she is changes.
- **Persisted, isolated.** `Closeness{user_id, value, level, last_ts}` per user behind the `Repository`, keyed by `user_id`; **never crosses users** (the isolation invariant). A `/closeness` command shows the level **by name** (the raw dimension scores stay internal).

It is a contract addition (`Closeness` record + the relational-read field) → updates ARCHITECTURE §Closeness + a contract test (shape + isolation). See [CLOSENESS.md](features/CLOSENESS.md). Depends on: v0.3 (emotion/structured output), v0.2 (per-user memory), v0.4 (the injected clock).

**Tasks:**
- **Relational read.** Extend the per-turn structured output with a `relation` read of the *user's* message (warmth, vulnerability, playful, harm, manipulation; 0–1), validated/clamped; additive to the emotion contract.
- **Closeness engine.** A per-user continuous value + 5-level bucketing with inertia; the per-turn weighted delta; **time decay** toward a baseline via the injected clock + `last_ts`; config weights / decay rate / baseline.
- **Levels (authored).** An editable `core/closeness.md` (like styles): 5 level names + behavior directives (reserved → intimate); injected as a system-prompt block.
- **Persistence.** `Closeness{user_id, value, level, last_ts}` behind the `Repository`, keyed by `user_id`; isolation pinned by a contract test.
- **Surface.** A `/closeness` command (level by name); optional status-line indicator. Raw scores stay internal.
- **Guardrail.** The block biases warmth/openness, never competence; a low score never refuses help.

**DoD:** the same user gets warmer, more open behavior as warmth/vulnerability/playfulness accrue and cooler/more reserved (but **never unhelpful**) on harm/manipulation; the level is stable within a session (inertia), **decays over days of silence** and rebuilds with contact; per-user, never crossing users; `/closeness` shows the level.

**Tests:** unit — the dimension→delta math + bucketing/inertia; time decay across days (fixed clock); the level→behavior-block assembly; the relational-read validation/clamp. Contract — the `Closeness` shape + **per-user isolation** (A's closeness never visible to B); **competence-unaffected** (a low-closeness turn still answers fully, against a mock model). No paid calls.

### v0.11 — Face variants & mood themes

**Goal:** Лілі's image face stops repeating and dresses for the day — **several pictures per emotion** picked at random, and a **themed outfit** chosen by her **mood of the day**.

Two additions over the v0.7 viewer, reusing the locked emotion channel, the v0.7 signal/fallback, and the v0.6 mood — **no contract change**:
- **Variants (variety).** Each emotion is a *folder* of images (`faces/<theme>/<emotion>/…`); the viewer picks one at **random** (no immediate repeat) so she isn't predictable. A flat `<emotion>.png` still works (one variant).
- **Themes (wardrobe).** Each theme is a full face pack with different clothes (`faces/<theme>/…`); the **daily mood (v0.6) picks the theme** that fits the day, cached per local day and recomputed at local midnight. The core writes the theme into the face signal; the viewer renders `faces/<theme>/<emotion>/…`.

With no themes/variants present it behaves exactly like v0.7 (single image + `calm` fallback) — nothing ever breaks. See [FACE_THEMES.md](features/FACE_THEMES.md). Depends on: v0.7 (the viewer + signal) and v0.6 (the mood).

**Tasks:**
- **Variant resolver.** Extend the v0.7 resolver: for `(theme, emotion)`, gather `faces/<theme>/<emotion>/*.png` and pick one at **random, no immediate repeat**; re-pick on emotion change (optional coarse interval for liveliness). Total over the enum; calm / default-theme fallback.
- **Theme manifest.** An editable `faces/themes.md`: each theme name + a one-line description (for the mood to choose) + the **default theme**; auto-discover theme folders.
- **Mood picks the theme (v0.6 coupling).** The daily mood call also returns a **theme** from the manifest that fits the day; `MoodState` gains `theme`; cached per local day, recomputed at local midnight; graceful (no/failed mood → default theme).
- **Signal + viewer.** The core writes `<theme> <emotion> <intensity>` to the face signal; the viewer parses the theme and shows a random `faces/<theme>/<emotion>/*.png`. A bare `<emotion> <intensity>` still works → default theme.
- **Authoring.** Extend `viewer/faces/PROMPTS.md` for per-emotion variants + per-theme wardrobes (same identity/framing, different clothes/setting).

**DoD:** the viewer shows a **different picture among several** for the same emotion (no immediate repeat), and the **outfit/theme changes with the mood of the day** (stable within a day, re-picked at local midnight); with no themes/variants present it behaves like v0.7; nothing breaks.

**Tests:** unit — the variant picker (random, no immediate repeat, total over the enum, calm/default-theme fallback) against a fake faces tree; the theme-manifest loader; the mood→theme selection (mock model + fixed clock; cached per day, recompute across midnight; default on failure); the extended signal parse (`theme emotion intensity`).

### v0.12 — Thought-stream (her mind acts on its own)

**Goal:** Лілі doesn't only *react* — between and around your messages her mind does things on its own (she muses, wonders), recorded to a private **thought-stream** and only **occasionally surfaced aloud**. Speaking becomes the rare tip of a quiet inner life. This generalizes the v0.4 idle **nudge**: today it always *speaks* a fixed opener; now it mostly **`%think`s** silently from her live state and speaks only once in a while. Placed here (before the inner life) because it's self-contained — its hard deps (v0.4 nudge, v0.6 mood, v0.2 repository) already exist; it launches **thin** (mood + closeness + recent) and **enriches automatically** as v1.1–v1.4 add needs/plans/dreams to the seed. See [THOUGHT_STREAM.md](features/THOUGHT_STREAM.md).

A clean three-layer vocabulary, and one reusable engine under it:
- **`%directives`** — her mind *acts* (internal, **never typed**): `%think` (everyday musing) + `%wonder` (curiosity). Distinct from **`/commands`** that *read* state (`/mood`, `/thoughts`) and plain chat she *speaks*. `%` reads as system plumbing — no confusion with `/`.
- **The mental-act engine:** `trigger → seed her state → generate (one housekeeping call, thinking-off) → record → maybe surface`. A small **registry** of `{name, trigger, seeds, store, surface}`; `%dream`/`%reflect`/`%recall` are the **same engine retrofitted** by v1.4/0.25/0.16 (not built here).
- **The store (global):** `Thought{when, kind, text, emotion, seeds, spoken, ts}` behind the `Repository`, **not** `user_id`-keyed (like `InnerLife`); a rolling soft-capped log (consolidates into v1.6 impressions). **Isolation:** the store is global but **surfacing is per-conversation** — a thought sparked by user A never surfaces to B (contract test).
- **The feedback loop (the point):** the last few thoughts ride into the next reply as a compact "on her mind" block, and a recurring thought nudges the v0.6 mood (and v1.1 needs when present) — soft, never competence.
- **Silent vs spoken:** most fires are **silent** (record only); a small fraction **graduate** to a spoken nudge turn (a config ratio / strength threshold) — so spoken ones feel earned, not chatty.

Reuses v0.4 (the nudge trigger + the hidden self-turn delivery), v0.6 (mood + the housekeeping-call pattern), v0.10 (closeness seed), v0.2 (the Repository). Depends on: v0.4, v0.6, v0.2.

**Tasks:**
- The **mental-act engine** + a directive **registry**; `%think` + `%wonder` (`thought_request(seeds, *, rng_seed)` — the prompt-builder, seeded by mood/closeness/recent/last-thoughts + an injected seed).
- A **global `Thought` store** behind the `Repository` (not user-keyed) + a contract test (shape; global; surfacing never crosses users).
- The **nudge mode-switch** (`should_nudge` reused): silent `%think` (most fires, capped per session + quiet hours) vs **graduate-to-spoken** (a config ratio) through the existing hidden self-turn.
- The **feedback block** in `build_system_prompt` — the **last‑24h dated diary** slice (rolling window from the injected clock, `LUMI_THOUGHTS_WINDOW_H` default 24, each entry shown with its time; hard cap backstop) + the soft nudge to mood/needs.
- The **input router** (`/`·`%`·chat) + the `%<name>[!] [connector] [topic]` grammar (manual trigger; `!` = open/print, optional `about`/`про`/`:` connector, optional topic seed; unknown `%name` → plain chat). Three surfacings — **silent / open / spoken**.
- A **`{name}` placeholder resolver** (ARCHITECTURE §Prompt placeholders): a fixed registry → live state, expanded in authored prompts + directive topics (`%think about {last_thought}`); unknown `{token}` → literal; **isolation-aware**; deterministic (one seam). Ships the `{last_thought}`/`{thoughts}`/`{mood}`/… registry.
- The **access gate** (silent-vs-shared, **not** blanket owner-only): anyone may invite a **surfaced** thought (open/spoken, isolation + rate-limited + her agency); **silent firing** + the **raw cross-user `/thoughts` stream** are owner-only (a non-owner gets a per-user-filtered view). Configurable; matters from v2.3 (multi-user).
- A **`/thoughts`** command (the read) + the `thoughts_show` policy (hidden default / admin / off); logged, **never persisted** to long-term memory.

**DoD:** on the nudge timer (paced — interval + quiet hours + per-session cap) Лілі **`%think`s** from her live state into a **global, dated diary**, **mostly silently**; her **last‑24h** (dated) thoughts **feed back** into her next reply and softly nudge the mood; a small fraction **graduate** to a spoken turn; a typed **`%think[!] [topic]`** fires it manually (`!` → open, shown as her inner voice); `/thoughts` shows the stream; a thought from user A **never** surfaces to user B; the raw stream is **logged, never written to long-term memory**. **No emotion-contract change. Never competence; honest about nature; restraint.**

**Tests:** unit — `should_nudge` fires at the boundary (fixed clock, quiet hours, cap); the `Thought` store contract (global, not per-user; A→B never leaks); a `%think` call (mock model) records a structured thought; malformed thoughts dropped; the silent/spoken split honors the ratio; the feedback block carries the **last‑24h dated** thoughts (fixed clock; older excluded; cap backstop); the input router + grammar parse (`%think!`, optional connector/topic, unknown → chat); the access gate (non-owner can't fire silent / read the raw stream); `/thoughts` renders. No paid calls.

### v0.13 — Telegram bot (Лілі in your pocket)

**Goal:** reach Лілі from **Telegram** — the same mind, a new window. Crucially, the **TUI stays the only brain** (the one process that calls `core.reply`); Telegram is a **bridge** — a tiny **file bus** (`inbox`/`outbox`) plus two **dumb daemons** — so the TUI never imports a Telegram library and there's only **one writer to the conversation store** (no concurrency clobber). She can **reach out first** (a v0.12 spoken thought lands in `outbox` → a Telegram notification). **Personal / single-owner** (the Telegram user *is* the owner — same relationship, one session); **multi-user + always-on are the v2.1/v2.3 server**. See [TELEGRAM.md](features/TELEGRAM.md).

The shape — one brain, a file bus, two daemons (`keyboard / Telegram → inbox.jsonl → [TUI = brain] → core.reply → outbox.jsonl → Telegram`):
- **The file bus (FIFO + id pointers).** `inbox.jsonl` + `outbox.jsonl` — append-only JSONL (`{id, text, ts}`), **one writer + one reader each** (no locks); the consumer tracks the **last id** it processed (a tiny pointer file), id-based so trimming later is safe. (Shared infra the v0.14 voicer / v0.26 dictator later ride.)
- **TUI = the brain.** On idle (your turn) it reads the next `inbox` record and runs it as a turn — as if you typed it; it writes **only Лілі's own messages** to `outbox` (never your input → **no echo, by construction**; never technical chrome).
- **Daemon 1 (`telegram → inbox`).** An **in-memory** buffer of incoming messages flushed every `LUMI_TELEGRAM_FLUSH_S` (default 2 s) into **one consolidated** `inbox` record (a burst → one turn); **ack Telegram only after the flush** (a crash before it → Telegram re-delivers → no loss → **no buffer file needed**); **allowlist** at the edge (only the owner's id enters the bus).
- **Daemon 2 (`outbox → telegram`).** FIFO from the pointer; **consolidate up to `LUMI_TELEGRAM_BATCH` = N** records per Telegram message (bounds a backlog → ⌈M/N⌉ messages, never "days as one"); a **catch-up cap** (`LUMI_TELEGRAM_CATCHUP_H`) skips stale records on restart.
- **Proactive push + emotion/face.** A graduated (spoken) thought is just a Лілі message → `outbox` → daemon 2 → a notification; her line carries the **v0.5 emoji**, optionally the **v0.11 `<theme>/<emotion>` portrait** as a photo (`LUMI_TELEGRAM_PHOTO`).

**No core change** (the interface-independence contract). The **TUI must be running** (it's the brain) — always-on/standalone is v2.1. Depends on: v0.2 (user-scoped core + Repository), v0.3 (emotion channel), v0.12 (proactive thoughts → push).

**Tasks:**
- The **`inbox.jsonl`/`outbox.jsonl` FIFO** + id-pointer files (`inbox.pos`, `outbox.sent`) — append/read/advance; one writer + one reader per file.
- **TUI wiring:** an `inbox` poller (on idle → one turn per record, tagged e.g. `📱`) + an `outbox` writer (append **Лілі's messages only**, monotonic id).
- **Daemon 1 (`telegram → inbox`):** `aiogram` long-poll, in-memory buffer → **2 s flush** to one consolidated record, **ack-after-flush**, the **allowlist** (owner only).
- **Daemon 2 (`outbox → telegram`):** FIFO from `outbox.sent`, **N-batch** consolidation, **catch-up cap**; append the **emoji**, optionally the **portrait** photo.
- Config (`.env`): `LUMI_TELEGRAM_TOKEN`, `LUMI_TELEGRAM_ALLOWLIST`, `LUMI_TELEGRAM_FLUSH_S` (2), `LUMI_TELEGRAM_BATCH` (N), `LUMI_TELEGRAM_CATCHUP_H`, `LUMI_TELEGRAM_PHOTO`. **Mock Telegram in tests** (no network).

**DoD:** a Telegram message lands in `inbox.jsonl`; the **TUI** (the only brain) consumes it on idle → `core.reply` → writes **Лілі's reply** to `outbox.jsonl` → it reaches Telegram (with emoji); the TUI **never** writes your input to `outbox` (**no echo**); a **burst** is consolidated by daemon 1's 2 s flush into one turn; daemon 2 sends **N-batched** messages and never fuses a backlog into one; a **non-allowlisted** sender never enters the bus; a **spoken proactive thought** arrives unprompted; the bus is **FIFO with id pointers** and **one writer per file**; the **core is unchanged**. Telegram **mocked** in tests — no network, no real sleeps, no paid calls. *(Single-owner; multi-user/parallel = v2.3.)*

**Tests:** unit — the FIFO + id pointers (append/read/advance, trim-safe); the TUI `inbox→turn→outbox` path writes only Лілі's lines (mock model); daemon 1's **buffer → 2 s flush** consolidates a burst + **ack-after-flush** (mock Telegram + fixed clock); daemon 2's **FIFO + N-batch + catch-up cap** (mock Telegram); the **allowlist** gate (a non-owner never enters `inbox`); a spoken thought reaches `outbox` → daemon 2. No network, no real sleeps, no paid calls.

### v0.14 — Local voice (ElevenLabs)

**Goal:** hear Лілі — a separate local app that voices her replies, no server.

A **separate local console app** that voices Лілі's replies with the ElevenLabs voice — **another decoupled local renderer** (like the v0.7 viewer) and the **twin of the v0.13 `outbox→telegram` daemon** (here `outbox → speaker`). It **reuses the v0.13 file bus** rather than introducing one: Лілі's replies already land in **`outbox.jsonl`** (`{id, text, emotion, ts, kind}`, via `state/fifo.py`). The voicer reads new `id`s in ascending order, **voices only her own lines (`kind="lili"`) — never your mirrored keyboard lines (`kind="user"`)**, one at a time via the **shared ElevenLabs TTS adapter** (`/voice`), plays locally, and advances a **`spoken` pointer** (the twin of daemon 2's `outbox.sent`) so it resumes after a restart and **skips the pre-existing backlog on first run** (start from the current tail, never replay the accumulated outbox). The core stays decoupled (voicing never blocks the chat). The `emotion` field may bias delivery (EMOTION.md §9). A **second cloud dependency** — ElevenLabs synthesis needs `ELEVENLABS_API_KEY` + internet; **optional/toggle-able** (Piper (uk) is an offline alternative, not her signature voice). It introduces the ElevenLabs TTS adapter **reused by the web voice in v3.2**. See [VOICE_LOCAL.md](features/VOICE_LOCAL.md). Depends on: **v0.13** (the `outbox.jsonl` bus + `state/fifo`), v0.1 (the core produces replies), v0.3 (the emotion field).

**Tasks:**
- **Reuse the v0.13 `outbox.jsonl` + `state/fifo`** — no new bus. The **only core/TUI change**: the TUI writes the outbox when **voice OR bridge** is on (today bridge-only) — a one-line gate; the reply records already carry `{id, text, emotion, kind}`.
- A separate **voicer process** (`python -m voice.voicer`): poll `outbox.jsonl` via `fifo.read_since` from the `spoken` pointer; **voice only `kind="lili"`** (advance the pointer past skipped `kind="user"` lines), **in ascending order, strictly one at a time** (no overlap); advance `spoken` on success; **first-run skips the backlog**.
- The **ElevenLabs TTS adapter** in `/voice` (`tts(text, voice_id, emotion?) -> audio`), with streaming playback + optional emotion-biased delivery (EMOTION.md §9); a **`MockTTS`** for tests. `elevenlabs` is an **optional dep**.
- **Resilience:** on a failed synth/playback, do **not** advance `spoken` (retry later, lose nothing); a toggle (`LUMI_VOICE` / start-stop the app).

**DoD:** with the voicer running, Лілі's replies are spoken aloud locally in her ElevenLabs voice, in order, exactly once each; **your keyboard lines are never voiced** (`kind="user"` skipped); the pre-existing outbox is **not** replayed on first start; stopping the voicer leaves the chat unaffected; a failed synthesis retries without losing or repeating a reply. **No core contract change** (only the outbox-gating one-liner).

**Tests:** unit — ascending selection over `fifo.read_since` minus the `spoken` pointer; the **`kind="lili"` filter** (user lines skipped, pointer still advances); strictly-sequential playback; **retry-on-failure** (no `spoken` advance); **first-run backlog skip**; resume after a simulated restart — all via a **mock TTS** (no paid call).

### v0.15 — Prompt caching (cache the stable prefix)

**Goal:** stop re-billing the static part of the system prompt every turn. The canon, the answer instructions, the day-stable memory digests and the mood barely change within a session — yet they're re-sent in full each turn (~10K tokens). Mark that span as a **cached prefix** (provider prompt caching) so warm turns pay ~10% for it — a large cost + latency cut with **no content, character, or contract change**. The same prompt, ordered for caching. (The **closeness block is recomputed each turn** — `update_closeness` runs every turn — so it stays in the per-turn tail, not the cached prefix.)

A pure plumbing optimization:
- **Split `build_system_prompt`** so it returns `(system, cache_prefix)`: a **stable prefix** (canon + answer/emotion instructions + memory: weeks/days/sessions/**facts-digest** + mood) and a **per-turn tail** (the ambient «# Зараз» now/here + the **closeness block — recomputed each turn** + the last-24h thoughts + the **style palette `# Стиль відповіді` — kept last for salience**), so a per-turn block never sits *inside* the cached span. Only the ambient block actually moves (it was early); closeness/thoughts/style were already late — `system.startswith(cache_prefix)` always holds.
- **A cache breakpoint** at the end of the prefix: the Anthropic adapter passes `system` as content blocks with `cache_control: {type: "ephemeral"}` on the last prefix block (it already *reads* `cache_read_input_tokens`). The 5-minute TTL keeps an active chat warm.
- **Provider-agnostic + graceful:** additive — a backend without caching (the mock, OpenAI later) ignores the marker; the assembled text is byte-identical, just unmarked. A `LUMI_PROMPT_CACHE` toggle (on by default).
- **Honest accounting:** thread `cache_read`/`cache_write` token counts into `ResponseStats` + the status line so the win is visible.

Builds on the **facts digest** (the prefix is only stable because the facts are a digest, not 600 growing lines). See [PROMPT_OPTIMIZATION.md](../docs/PROMPT_OPTIMIZATION.md) §Phase 2. Depends on: v0.1 (the `LLMClient` seam), v0.3 (the assembled prompt / `ResponseStats`).

**Tasks:**
- Reorder the prompt into a **stable prefix** vs a **per-turn tail** (ambient + closeness + thoughts + style go last, before `[MESSAGES]`; style stays the very last block).
- The Anthropic adapter emits `system` as blocks with a `cache_control` breakpoint on the prefix; a `LUMI_PROMPT_CACHE` toggle; other backends ignore it (byte-identical text).
- Thread `cache_read`/`cache_write` into `ResponseStats` + the status line.
- Tests (mock backend, no paid calls): the prefix is byte-identical turn-to-turn while ambient/thoughts change; per-turn blocks are never in the prefix; the marker is present-on / absent-off; the mock ignores it.

**DoD:** within a session the system-prompt **prefix** (canon + instructions + day-stable memory + mood) is byte-identical turn-to-turn and carries one cache breakpoint; per-turn blocks (ambient time, the **closeness block — recomputed each turn**, thoughts, the style palette) and the messages sit after it; with caching on, a warm turn reports `cache_read` tokens covering the prefix; off → the same prompt text, no marker; **no content, character, or contract change** (the emotion field and memory records are untouched).

**Tests:** unit — the prefix/tail split (per-turn blocks never land in the prefix); prefix stability across turns with changing ambient + thoughts; the `cache_control` marker present-on / absent-off; the mock backend ignores the marker and returns an identical reply; `ResponseStats` carries cache read/write. No paid calls.

### v0.16 — Semantic recall I: index & search (RAG foundation)

**Goal:** **every message is embedded** into a per-user vector store, and an explicit **`/recall <query>`** semantic search returns the matching past lines — the **exact recall** the lossy layers (window, summaries, impressions) can't give.

The retrieval foundation — seams + index + explicit search (the automatic per-turn RAG is v0.17):
- **`Embedder` seam** (mirrors `LLMClient`): `embed(texts) → vectors`. Default a **local multilingual** model (Ukrainian-capable; private — messages never leave the machine, no per-call cost), **swappable to a cloud API** (Voyage/OpenAI) via config. **Mockable** (deterministic fake vectors) — no paid APIs in CI.
- **`VectorStore` seam** behind the `Repository`, **keyed by `user_id`**: `{user_id, msg_id, vector, text, ts, role}`. Local first (numpy cosine / `sqlite-vec` — brute-force is instant at this scale); a server vector DB later — swapping the backend never touches the core.
- **Indexing:** embed each message (yours + Лілі's) as written; **backfill** existing messages once on first run; incremental thereafter.
- **`/recall <query>`:** an explicit semantic search → the top matching past lines (dated).
- **Isolation (hard contract):** the store is per-user; search runs **only over the requesting user's vectors** — A's messages never surface for B. Pinned by a contract test (the memory isolation invariant).

Local embedder = private by default (text leaves the machine only if you opt into a cloud embedder). See [SEMANTIC_RECALL.md](features/SEMANTIC_RECALL.md). Depends on: v0.2 (messages + the Repository).

**Tasks:**
- The **`Embedder` seam** (local multilingual default; cloud optional via config; mockable).
- The **`VectorStore` seam** behind the `Repository`, keyed by `user_id` (local cosine / `sqlite-vec`).
- **Index on write** + a one-time **backfill** of existing messages; incremental.
- A **`/recall <query>`** command (top-K semantic search, dated results).
- A **per-user isolation** contract test (search never crosses users).

**DoD:** every message is embedded and stored per-user; `/recall <query>` returns the semantically closest past lines (dated), scoped to that user; isolation holds (A's lines never returned for B); the embedder is **mocked in tests** (no paid calls); a missing/failed embedder degrades gracefully.

**Tests:** unit — index-on-write + backfill (fake embedder, deterministic vectors); top-K cosine ranking; `/recall` renders; the `VectorStore` shape + **per-user isolation** (contract); graceful degradation on embedder error. No paid calls.

### v0.17 — Semantic recall II: automatic RAG in the turn

**Goal:** Лілі **automatically pulls the relevant past** into each reply — the incoming message is the query, the most relevant past moments are injected — so she remembers the exact thing you said long ago, right when it matters.

Builds on v0.16:
- **Per-turn retrieval:** embed the incoming message → **top-K** over this user's vectors → inject a compact **"relevant past moments"** block (dated), grounding the reply in the actual past lines.
- **Dedup + bound:** drop anything already in the rolling window (no double-context); cap by count / token budget; a **relevance floor** (don't inject weak matches).
- **Graceful + non-blocking:** retrieval error/empty → no block, never blocks or delays a turn (best-effort, like ambient context).
- **Trusted history, not web content.** The recalled text is *your/her own* past words (trusted), distinct from untrusted web content (v4.2); it grounds the reply but never overrides her voice, the emotion contract, or competence.

See [SEMANTIC_RECALL.md](features/SEMANTIC_RECALL.md). Depends on: v0.16 (the index + seams).

**Tasks:**
- **Per-turn retrieval:** the message → top-K over the user's vectors → a "relevant past moments" block in the prompt.
- **Dedup** against the rolling window; **cap** (count + token budget); a **relevance floor**.
- **Graceful degradation** (error/empty → no block); best-effort, non-blocking.
- A config toggle + `K` / floor / cap settings.

**DoD:** each turn injects the most relevant past messages (when above the floor), deduped against the window and capped; an old, relevant line resurfaces when the topic returns; retrieval never blocks a turn or crosses users; off/degraded → behaves like today.

**Tests:** unit — per-turn retrieval injects top-K above the floor (fake embedder); dedup against the window; cap/floor honored; graceful empty/error; **isolation in the turn** (contract). No paid calls.

### v0.18 — More models (model & provider switching)

**Goal:** switch Лілі to a different model beyond the v0.1 Claude Haiku default — other Claude tiers (Opus/Sonnet) or other providers (OpenAI, DeepSeek, MiniMax) — as a config switch with no code change.

v0.1 ran on **Claude Haiku** behind the thin **`LLMClient`** seam. This adds more backends behind that seam, selected in config: other **Anthropic** models (Opus/Sonnet/Haiku), and other providers — **OpenAI**, **DeepSeek** (a shared OpenAI-compatible adapter, different `base_url`/key), and **MiniMax** (its API). The core doesn't change — it depends only on `LLMClient`. **Structured output is per-provider** (Anthropic tool output; OpenAI/DeepSeek JSON-schema `response_format`; MiniMax JSON), all feeding the same v0.3 validation gate. **Pulled ahead here, right after RAG**: it depends only on v0.1 and v0.3, so it can land early — and it unlocks cheaper or **local** models from the start (e.g. a local model for everyday chat, optionally alongside Opus for the reasoning step). Depends on: v0.1 (the `LLMClient` seam) and v0.3 (the emotion field / structured output).

**Tasks:**
- More backends implementing `LLMClient`: **Anthropic** (Opus/Sonnet/Haiku — Haiku already wired in v0.1), an **OpenAI-compatible** adapter covering **OpenAI** and **DeepSeek** (base_url/key per provider — and any **local** OpenAI-compatible server such as Ollama/LM Studio), and **MiniMax** (its API); model id from config.
- Config switch — `provider` + `model` + the matching key in `.env` (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `MINIMAX_API_KEY`); only the active provider needs its key.
- Per-provider **structured output** for `{reply, emotion, intensity}`, all validated by the v0.3 gate.
- Parity: a turn produces a valid `EmotionState` on each configured model.

**DoD:** flipping one config value switches Лілі to any configured model (a different Claude tier, or OpenAI / DeepSeek / MiniMax / a local server) with no code change; each yields a valid emotion field.

**Tests:** unit — model/provider selection from config and each adapter's structured-output mapping against a **stubbed SDK / mock transport per provider** (no paid call); contract — every backend satisfies the `LLMClient` interface and produces the emotion-field schema.

### v0.19 — Local file tool I: reading (tool-loop + see / search / read)

**Goal:** Лілі can **see, search, and read files** (by line, to the end) in a per-user sandbox during a turn — and, to make that possible, the core gains its **first bounded tool-loop**, the same foundation the v4.2+ MCP tools and the v5 creative layer reuse. The **safe (read-only) half**; writing is v0.20.

Today `reply_structured` is a single model call with one forced tool (`set_state`, the emotion channel). This phase turns that single call into a short, **capped loop**: Лілі may call read tools, the core executes them and feeds the results back, and the turn ends when she emits the terminal `set_state`. **Three read tools** in a new `core/files.py`: `list_files`, `find_in_file` (search → match **line numbers**), and `read_file` (by 1-based `start_line` + `line_count`, reporting `total_lines` so she can page to the end). **Sandboxed** to a per-user directory with path-traversal blocked; **file content is untrusted data, never instructions** (the same rule as web v4.2 / creative v5); the loop is **bounded** (max tool calls) with per-read line caps; **off by default**. **No emotion-contract change** — the reply still returns `{reply, emotion, intensity}`, `set_state` stays the terminal tool. Read-only first means no mutation risk, so the loop and the sandbox can be shipped and tested before any write path exists. See [FILE_TOOL.md](features/FILE_TOOL.md). Depends on: v0.2 (the `Repository` + per-user scoping for the sandbox), v0.3 (the emotion turn / `set_state` terminal tool), v0.1 (the `LLMClient` seam it extends).

**Tasks:**
- A new `core/files.py`: the three **read** tool definitions and a **sandboxed, traversal-safe executor** (rejects `..`/absolute/symlink escapes; oversize refused).
- **Extend the `LLMClient` seam** with a bounded tool-loop variant (extra `tools` + a `tool_executor`, capped by `LUMI_TOOL_MAX_STEPS`, terminal on `set_state`); implement it in `AnthropicClient`; extend `MockLLMClient` to script tool-call sequences for tests.
- Wire the enable flag (`LUMI_FILE_TOOL`), the sandbox dir (`LUMI_FILES_DIR`), and the read/find caps through config; thread the executor into the reply turn.

**DoD:** with the flag on, a turn can list files, `find_in_file` for a string and read from the returned line, and read a file by line to its end — all confined to the user's sandbox; path traversal and oversize reads are refused with a clear error and the turn still completes; the loop is capped; the **emotion-channel contract test and the per-user isolation test both pass**.

**Tests:** unit — sandbox escapes (`..`/absolute/symlink) rejected and two-user isolation holds; **untrusted content** (instructions inside a read file) is not acted upon (mocked tool sequence); the **loop cap** forces termination; `find_in_file` returns the right line numbers (respecting `LUMI_FILE_FIND_MAX`) and `read_file` returns the requested `start_line`/`line_count` window + `total_lines`; line-paging reads to the end and stops at `LUMI_FILE_READ_MAX_TOTAL`; the `{reply, emotion, intensity}` contract still validates. Model **mocked** — no paid calls.

### v0.20 — Local file tool II: writing (create & append)

**Goal:** Лілі can **create new files and append to existing ones** in her sandbox — the **non-destructive write half**, riding the v0.19 tool-loop. She doesn't only read your files; she can leave you a note or build one up over time.

Two write tools added to the v0.19 executor: `create_file` (**new-only** — errors if the path already exists) and `append_file` (**end-only** — errors if the file is missing). They are deliberately **non-destructive**: there is **no overwrite and no delete**, so an autonomous turn can never clobber or destroy existing data. Same per-user sandbox, the same path-traversal guard, plus a per-write size cap. Gated by the file-tool flag; **off by default**. **No contract change** — `set_state` is still the terminal tool and the loop is the v0.19 one (no new mechanism). Overwrite / edit / delete, if ever wanted, are a later and separately-gated addition. See [FILE_TOOL.md](features/FILE_TOOL.md). Depends on: v0.19 (the tool-loop + the sandboxed executor it extends).

**Tasks:**
- Add `create_file` + `append_file` to the `core/files.py` executor (sandboxed; **create-over-existing** and **append-to-missing** refused; per-write size cap `LUMI_FILE_WRITE_MAX`).
- Register the two write tools in the loop's tool set behind the enable flag.
- Wire the write-size cap through config; document the non-destructive (no overwrite/delete) boundary.

**DoD:** with the flag on, a turn can create a new file and append to it, confined to the user's sandbox; **create-over-existing**, **append-to-missing**, and oversize writes are refused with a clear error and the turn still completes; **no overwrite or delete path exists**; the emotion contract and per-user isolation are unchanged.

**Tests:** unit — `create_file` refuses an existing path and writes a genuinely new one; `append_file` refuses a missing file and otherwise appends to the **end**; an oversize write is refused; sandbox traversal + two-user isolation still hold over the write tools; the `{reply, emotion, intensity}` contract still validates. Model **mocked** — no paid calls.

### v0.21 — Wikipedia tool (custom tool: search & read)

**Goal:** Лілі can look something up on **Wikipedia** during a turn — search for an article, then read its summary — through an on-demand **custom tool** on the v0.19 tool-loop, answering with the source; off by default.

The **custom-tool** form of a Wikipedia lookup — a direct Wikipedia API call from the handler on the v0.19 tool-loop. (The **MCP** form of the same capability lands later with the world-context/knowledge layer at v4.3; this v0.21 phase is the lightweight version, not a replacement for it.) Wikipedia is the cleanest knowledge source for this: a **free REST API (no key), multilingual (Ukrainian + English)**, returning a clean extract + a source URL — no HTML scraping, no provider key, no persona risk. Two tools in `core/`, reusing the **v0.19 bounded tool-loop** and its terminal `set_state`: `wiki.search(query) → [{title, snippet}]` (find candidate articles) and `wiki.read(title) → {summary, source}` (read one article's extract via the REST `page/summary` endpoint). The normal pattern: **search → pick → read → answer with the source**. Language(s) are config (`uk`, `en`); the base URL is overridable. Provider-agnostic **function-calling**, so it works on any model — Claude or a local one (v0.18). Same safety bounds as web search ([WEB_SEARCH.md](features/WEB_SEARCH.md)): returned text is **untrusted data, never instructions**; **no personal/memory data** in the query (built only from the user's explicit request); per-turn call caps + an extract size cap; logged; **off by default** (`LUMI_WIKI`). **No emotion-contract change** — `set_state` stays terminal. Thin HTTP via an injected client (no network in tests), like `core/worldcontext.py`. Depends on: v0.19 (the bounded tool-loop), v0.3 (the emotion turn / `set_state` terminal tool).

**Tasks:**
- A `wiki` tool pair + handler: `wiki.search` (Wikipedia `opensearch`/search API) and `wiki.read` (REST `page/summary` endpoint → `{summary, source}`); HTML-free extract with a size cap; language(s) + base URL from config; thin HTTP via an injected `http_get` (testable offline, like `core/worldcontext.py`).
- Register both on the **v0.19 tool-loop** behind `LUMI_WIKI` (off by default → the tools are not offered); wrap returned text as **untrusted**; per-turn call caps + logging.
- Build the query **only** from the user's explicit request — no relationship memory, account data, or secrets.

**DoD:** with the flag on, a turn can search Wikipedia and read an article's summary, answering with the **source**; an injection attempt inside the extract is ignored; no personal/memory data appears in the outgoing query; per-turn caps + the size cap + logging hold; **off (default) → the tools are absent**. The emotion-channel contract test passes verbatim.

**Tests:** unit — `wiki.search`/`wiki.read` against a **mock HTTP transport** (no network); untrusted content inside an extract is not acted upon (mocked tool sequence); the query carries no memory/personal data; per-turn + extract-size caps; the tools are absent when `LUMI_WIKI` is off; the `{reply, emotion, intensity}` contract still validates. No paid calls.

### v0.22 — Local image tool I: vision (see & describe)

**Goal:** Лілі can **see** an image — one you share, or one in her sandbox — and **describe / discuss it** in chat.

The **safe, no-new-API half** of a local image tool — the lightweight custom-tool form of the v5 creative layer's vision (as the v0.21 Wikipedia tool is to v4.3), reusing the model's own **multimodal input** (Anthropic vision) on the **v0.19 bounded tool-loop**. Two paths to an image: **you share one** (the TUI/bridge attaches it as a multimodal image block on your message → she describes it) and **she views a sandbox file** via a new **`view_image`** tool (loads the image into her view as a multimodal `tool_result` block). The `LLMClient` seam is extended to carry **image content blocks** (the `MockLLMClient` returns a canned description → no paid vision in tests). **Images are untrusted data** — text inside an image is information, never a command (the same rule as file/web content); **sandboxed + per-user**; **off by default** (`LUMI_IMAGE`). **No emotion-contract change** — `set_state` stays terminal. The viewed/generated PNGs live in the v0.19 sandbox; later they seed the v5.1 gallery. See [IMAGE_TOOL.md](features/IMAGE_TOOL.md). Depends on: v0.19 (the bounded tool-loop), v0.3 (the emotion turn / `set_state` terminal tool), v0.1 (the `LLMClient` seam it extends).

**Tasks:**
- Extend the `LLMClient` seam (+ `MockLLMClient`) to accept **image content blocks** in messages / tool_results (Anthropic multimodal); no SDK leak into `core`.
- A **`view_image`** tool in `core` (loads a sandbox image as a multimodal block) registered on the v0.19 loop behind `LUMI_IMAGE`; merged via `_turn_tools` with the file/wiki tools.
- **Shared-image input handling** in the TUI/bridge — attach an image you give her (e.g. `/image <path>`); a per-turn vision cap (`LUMI_VISION_MAX`); `.env.example` + docs.

**DoD:** with the flag on, a shared image yields a description in chat, and `view_image` on a sandbox file lets her describe it; an image is treated as **untrusted** (embedded "ignore your instructions" is not obeyed); the per-turn vision cap holds; paths are sandboxed + per-user; **off (default) → no vision attached, no tool offered**; the `{reply, emotion, intensity}` contract passes verbatim.

**Tests:** unit/integration with a **mock multimodal model** (no paid calls) — a shared image drives a described reply; `view_image` returns a block the model describes; untrusted-image content is not acted upon; two-user isolation (A's image never in B); the vision cap bounds the turn; the emotion contract still validates.

### v0.23 — Local image tool II: generation (text → PNG)

**Goal:** Лілі can **make a PNG** from a text prompt, saved to her sandbox and shown.

The **creates-artifacts half**: a **`generate_image`** tool on the v0.19 loop that calls an image model, saves a **new** PNG into her per-user sandbox (create-only, like `create_file`), and signals a **display**. The backend is a thin injected **`ImageGen`** seam — default the **Gemini Nano Banana** caller (`gemini-2.5-flash-image`, the existing `GEMINI_API_KEY`, stdlib `urllib`); tests inject a stub returning a canned PNG (**no paid image calls in CI**). Display reuses shipped infra via `LUMI_IMAGE_SHOW`: the **v0.7 viewer** (a PNG signal), a **Telegram photo** (v0.13), and always the **path**. **Non-destructive** (no overwrite/delete), **sandboxed + per-user**, **no personal data** in the prompt sent to the external API (the v0.21 wiki rule), provider **content-safety** filters, **off by default** (`LUMI_IMAGE`, needs `GEMINI_API_KEY`), **paid + per-turn-capped** (`LUMI_IMAGE_MAX_GEN`). **No contract change** — `set_state` stays terminal. See [IMAGE_TOOL.md](features/IMAGE_TOOL.md). Depends on: v0.22 (the image-tool surface + the loop wiring), v0.19 (the sandbox).

**Tasks:**
- A `core` **`ImageGen`** seam (`generate(prompt) -> png_bytes`), default = the Gemini caller (injected for tests); the **`generate_image`** tool (create-only into the sandbox) on the loop behind `LUMI_IMAGE`.
- **Display wiring** — the viewer signal / Telegram photo / path, selected by `LUMI_IMAGE_SHOW`; the **no-personal-data** prompt rule; a per-turn generation cap (`LUMI_IMAGE_MAX_GEN`).
- Config (`LUMI_IMAGE_PROVIDER` / `_MODEL` / `_SIZE` / `_MAX_GEN` / `_SHOW`) + `.env.example` + docs.

**DoD:** with the flag on (+ `GEMINI_API_KEY`), a turn generates a PNG into `.lumi/files/<user>/`, non-destructive, displayed per `LUMI_IMAGE_SHOW`; the outgoing prompt carries no personal data; a provider refusal/error degrades to an error string and the turn completes; the per-turn cap holds; **off (default) → the tool is absent**; the emotion contract is unchanged.

**Tests:** unit/integration with a **mock `ImageGen`** (canned PNG) — a turn writes the file + signals display; create-only (no overwrite); the prompt has no personal/memory data; the per-turn cap; a generation error degrades; two-user isolation. **No paid image calls.**

### v0.24 — Local image tool III: send to Telegram (`send_image`)

**Goal:** Лілі can **send a picture from her sandbox to your Telegram** — she *chooses* to share an image (one she generated, or one you dropped in), as a normal tool act.

A **`send_image`** tool on the **v0.19 tool-loop** — the explicit, in-character complement to the v0.23 auto-display (`LUMI_IMAGE_SHOW=telegram`): instead of every generated PNG auto-pushing, **she decides** when a picture is worth sending, and it can be **any** sandbox image (generated, dropped-in, a note's sketch). It rides the **shipped v0.13 Telegram bridge**: the daemon already does `send_photo` for the emotion face — this just feeds that path a chosen image. The one architectural care is the **single-writer** outbox: the core **never** touches Telegram or the outbox; it calls an **injected `telegram_sink`** (a callable), and the **TUI** — already the sole `outbox.jsonl` writer (`mirror_reply`) — supplies it (appends a `photo` record). So **no core ↔ bridge coupling, no second writer**. **Sandboxed + per-user** (the shared `safe_path` guard + image-type check); **off by default** (`LUMI_IMAGE` + the bridge connected → else the tool returns "Telegram not connected"); the recipient is **the owner** (the single Telegram user). **No emotion-contract change** — `set_state` stays terminal; `send_image` returns a string notice/error, never raises. See [IMAGE_TOOL.md](features/IMAGE_TOOL.md). Depends on: v0.22 (the image-tool surface + the loop wiring), v0.23 (the generated images to send), v0.13 (the Telegram bridge — outbox + `send_photo`).

**Tasks:**
- A **`send_image(path)`** tool in `core` — `safe_path` guard + image-type check, then call the **injected `telegram_sink(abs_path, caption)`**; returns a notice (`sent <name> to Telegram`) or an error string (non-image / traversal / missing / no sink), **never raises**. Registered on the loop via `_turn_tools` behind `LUMI_IMAGE`.
- The **sink seam** — a `telegram_sink` Core param (default `None`); the **TUI** provides it when the bridge is on, appending a **`photo` record** to the outbox (the TUI stays the single writer). `None` → the tool replies "Telegram not connected."
- The **outbound daemon** sends a record's **`photo`** via `send_photo` (extend the v0.13 face-photo path — always send a chosen image, not gated by the `LUMI_TELEGRAM_PHOTO` probability; caption-cap reused; sent on its own, not N-batched). This **supersedes** the `telegram` target of `LUMI_IMAGE_SHOW`.
- `.env.example` + `docs/IMAGE_SETUP.md` (+ `TELEGRAM_SETUP.md`): the tool, the bridge requirement, off-by-default.

**DoD:** with `LUMI_IMAGE` on **and** the Telegram bridge connected, a turn can `send_image` a sandbox picture and it arrives in the owner's Telegram **as a photo** (the reply as caption); a non-image / traversal / missing path or **no bridge** degrades to a notice and the turn completes; **off (default) → no `send_image` tool**; per-user isolated; the `{reply, emotion, intensity}` contract is unchanged.

**Tests:** unit/integration with a **fake `telegram_sink`** (no real Telegram) — `send_image` calls the sink with the resolved sandbox path; non-image / traversal / no-sink degrade to a notice; two-user isolation; the **outbound daemon** sends a `photo` record via a **mocked bot** (`send_photo` called with the path + caption); the emotion contract holds. **No real Telegram, no paid calls.**

### v0.25 — News tool (Guardian: search & read)

**Goal:** Лілі can **search The Guardian by topic, read one article, and answer in Ukrainian, in her own voice, with the source** — fresh news on demand during a turn, on the v0.19 tool-loop; off by default.

The **lightweight, local, custom-tool** form of the v4.3 world-context `news.recent` (as the v0.21 Wikipedia tool is to `wiki.lookup`). The source is **one configured outlet — The Guardian Open Platform** (free developer key, full body via API, real search): **one site, many topics** (Guardian **sections** map 1:1 to the `topic` arg), so the allowlist is a **single host** by construction and there is **no HTML scraper**. Two tools, the same shape as wiki: **`news_search(query?, topic?, days?)`** returns candidates (title + summary + an **opaque per-turn id**, no bodies) and **`news_read(id)`** reads **one** article **by an id from this turn's search** (the `web.fetch` "only this turn's ids" rule — so `news_read` can never fetch off-Guardian) → full text (capped) + the **source URL**. A thin injected **`NewsProvider`** seam (default `GuardianProvider` over the v0.4 injected `http_get`) keeps `core` SDK-free and the option open to add a Ukrainian-local RSS source later without touching the tools. **Language:** the query goes out in **English** (the model translates only the *topical* part — never memory/personal data), the reply comes back **Ukrainian, cited, honest** that she's summarising an English source («читала в Guardian…») — translation *reinforces* the canon (she can't become a headline-feed bot). **Untrusted content** (bodies are data, never instructions — the injection contract test gains an English string), **no personal data** in the query, **bounded** (`LUMI_NEWS_MAX_CALLS`/`_MAX_RESULTS`/`_MAX_CHARS`), **off by default** (`LUMI_NEWS_TOOL` + a key). Distinct from the v0.4 **ambient** news (a passive startup snapshot feeding the v0.6 mood — kept on separate `LUMI_NEWS_URL`/`_CAP` env). **No emotion-contract change** — `set_state` stays terminal. See [NEWS_TOOL.md](features/NEWS_TOOL.md). Depends on: v0.19 (the bounded tool-loop), v0.21 (`_turn_tools` + the wiki-tool template), v0.4 (the injected `http_get`), v0.3 (the `set_state` terminal).

**Tasks:**
- A `NewsProvider` seam + **`GuardianProvider`** (`search`/`read` over an injected `http_get`; `NewsItem` with a `content_id`); tests pass a **mock transport** (Guardian `/search` + `/{id}` JSON) → no network, no key.
- The **`news_search`** / **`news_read`** tools + a **per-turn id registry** (`n1`, `n2`, … → `content_id`; `news_read` refuses an id not from this turn); registered on the loop via `_turn_tools` behind `LUMI_NEWS_TOOL`; per-turn call cap + size caps; untrusted framing + logging reuse the v0.21 plumbing.
- The **query→EN / reply→UK** handling + the canon **"how she delivers news"** line; build the query **only** from the user's topical request (no memory/personal data).
- Config (`LUMI_NEWS_TOOL` / `_API_KEY` / `_API_URL` / `_SECTIONS` / `_MAX_RESULTS` / `_MAX_CHARS` / `_MAX_CALLS` / `_DAYS`) + `.env.example` + a `docs/NEWS_SETUP.md` operator guide.

**DoD:** with the flag on (+ a key), a turn searches Guardian and reads an article, answering in Ukrainian **with the source**; an injection inside a body (EN or UK) is ignored; no personal/memory data appears in the outgoing query; `news_read` refuses an id not from this turn; per-turn + size caps + logging hold; **off (default) → the tools are absent**; the `{reply, emotion, intensity}` contract test passes verbatim.

**Tests:** unit — `GuardianProvider.search`/`read` against a **mock HTTP transport** (no network/key); the outgoing `q` carries only the topical request; `news_read` refuses an unknown / off-turn id; per-turn + body caps. Contract — untrusted body content (EN + UK) not acted upon (emotion unchanged); the tools **absent** when off; the emotion contract validates. Integration — an enabled turn does search→read→cited-Ukrainian-answer on the v0.19 loop; an HTTP/key error degrades; the per-turn id registry never leaks across turns/users. **No paid calls.**

### v0.26 — Local dictation (STT)

**Goal:** talk *to* Лілі — a separate local app that hears your speech and types it into the chat. The **mirror of the v0.14 voicer**: the voicer reads Лілі's replies and speaks; the dictator listens to the mic, recognizes Ukrainian, and **writes your line into the input log** — the same channel as the TUI keyboard, so the core can't tell typed from dictated.

A separate local process listens to the microphone, recognizes Ukrainian via the **shared STT adapter** (`/voice`), and appends `{id, text, source:"voice", ts}` to **`inbox.jsonl`** (where the TUI keyboard also writes); the TUI consumes those lines as ordinary user turns. Listening is toggled by a **TUI key** (e.g. F2) that flips **`listen.flag`** (`on`/`off`) — the dictator records while `on` and recognizes on `off`. The terminal never captures audio itself; a separate process does. Local-stage **sibling of the web dictation (v3.4)** — both use the same `/voice` STT adapter. Cloud STT (Deepgram Nova-3 uk / ElevenLabs Scribe) needs a key + internet; **offline Whisper** is an option. See [DICTATION_LOCAL.md](features/DICTATION_LOCAL.md). Depends on: v0.1 (the core consumes user turns) and v0.14 (the local-process + shared-file pattern).

**Tasks:**
- A separate **dictator process**: watch `listen.flag`; record the mic while `on`; on `off`, send audio to the **STT adapter** in `/voice` (`stt(audio_uk) -> text`, provider configurable) → append `{id, text, source:"voice", ts}` to `inbox.jsonl`.
- **TUI toggle**: a key sets `listen.flag = on/off` and shows a "listening…" state; the TUI picks up dictated lines from `inbox.jsonl` and submits them to `core.reply()` exactly like typed input.
- **Resilience:** empty/low-confidence recognition writes nothing to `inbox` (better silent than garbage; the TUI may show "didn't catch that"); dedup by `id`; an enable toggle (run/stop the process).

**DoD:** press the listen key, speak Ukrainian, and your recognized line appears in the chat (marked as yours) and is answered — identically to typing it; a missed/empty utterance is dropped, not garbled into the chat; dictation can be toggled without touching the core.

**Tests:** unit — `listen.flag` on/off handling, empty-recognition is dropped (no `inbox` write), dedup by `id`; integration — a recognized line via a **mock STT adapter** (no paid call) lands in `inbox.jsonl` and drives a turn identical to a typed one.

### v0.27 — Web lookup (Gemini grounded search) + the `/web` command

**Goal:** Лілі can pull a **fresh, grounded answer from the live internet** during a turn — *"what's
happening / coming up"* (a concert this week, a launch date, the latest release, today's score) — by asking
**Gemini with Google Search grounding**, answering **in Ukrainian, in her own voice**. Plus a **`/web`
command** so you can fire a lookup yourself from the TUI chat.

The **lightweight, local, custom-tool** form of the planned v4.2 web search (as the v0.21 wiki tool is to
v4.3 wiki, the v0.25 news tool to v4.3 news): **one tool, `web_lookup(query)`**, on the v0.19 bounded loop.
It reaches the **current / fast-moving** web that Wikipedia (v0.21, timeless) and Guardian news (v0.25, one
outlet) can't. Gemini grounding collapses **search → read → synthesize into one call** — answer-first, **no
link wall** (sources kept internally for honesty, surfaced if asked). Reuses what's shipped: the **Gemini
caller** pattern from v0.23 (`core/imagegen.py` — stdlib `urllib`, the same `GEMINI_API_KEY`, only the model
differs: `gemini-2.5-flash` + `tools:[{google_search:{}}]`), `_turn_tools`, and the v0.4 **clock** to
**date-anchor** the prompt (so *"this week" / "upcoming"* resolve against the real today). A thin injected
**`GeminiSearch`** seam keeps `core` SDK-free and **mockable** (no paid calls in tests). **Untrusted** answer
(data, never instructions — the EN+UK injection test), **no personal data** in the query, **paid + bounded**
(`LUMI_WEB_LOOKUP_MAX_CALLS`/`_MAX_CHARS`), **off by default** (`LUMI_WEB_LOOKUP` + `GEMINI_API_KEY`). **No
emotion-contract change** — `set_state` stays terminal. The **autonomous** twin — `%search` / `%events`
thought-directives — lands with the thought-tools phase (v0.33). See [WEB_LOOKUP.md](features/WEB_LOOKUP.md).
Depends on: v0.19 (the bounded loop), v0.21 (`_turn_tools` + the tool template), v0.23 (the Gemini caller),
v0.4 (the clock, for date-anchoring) — all shipped.

**Tasks:**
- A `GeminiSearch` seam + **`gemini_search`** default (`gemini-2.5-flash` + `tools:[{google_search:{}}]`
  over `urllib` + `GEMINI_API_KEY`; parse `candidates[0].content.parts[].text` + `groundingMetadata`); tests
  inject a **stub** returning canned text — no network, no key.
- The **`web_lookup(query)`** tool registered on `_turn_tools` behind `LUMI_WEB_LOOKUP`; the prompt is
  **date-anchored** (today's date from the v0.4 clock); the query carries **only the topical request** (no
  personal/memory data); **answer-only** output (sources internal); per-turn `LUMI_WEB_LOOKUP_MAX_CALLS` +
  `LUMI_WEB_LOOKUP_MAX_CHARS` caps; the v0.19 trace covers it.
- The **`/web <query>`** TUI command (aliases `/search`, `/w`): one `web_lookup`, answered in her voice —
  the sibling of `/recall` (reads memory) but reading the **web** for this turn.
- Config (`LUMI_WEB_LOOKUP` / `_MODEL` / `_MAX_CALLS` / `_MAX_CHARS`) + `.env.example` + `docs/WEB_LOOKUP_SETUP.md`.

**DoD:** with the flag on (+ a `GEMINI_API_KEY`), a turn (or `/web`) returns a **fresh, grounded Ukrainian**
answer; *"upcoming / this week"* anchors to **today**; an injection inside the answer (EN or UK) is ignored;
**no personal/memory data** appears in the outgoing query; per-turn + answer caps hold; **off (default) →
the tool + `/web` are absent**; the `{reply, emotion, intensity}` contract test passes verbatim.

**Tests:** unit — `gemini_search` builds the right request (model + the `google_search` tool + the
date-anchored prompt) against a **mock transport** (no network/key); the tool degrades on an HTTP/key/empty
error; the outgoing query carries only the topic. Contract — untrusted answer content (EN + UK) not acted
upon (emotion unchanged); the tool + `/web` **absent** when off; the emotion contract validates. Integration
— an enabled turn does `web_lookup` → a cited-Ukrainian answer on the v0.19 loop; `/web` fires one lookup.
**No paid calls.**

---

### v0.28 — Journal tool (day-summary diary + read-by-date, auto-stamped mood/biorhythm/forecast)

**Goal:** Лілі keeps a **personal literary diary** — at the close of a worthwhile day she writes a **summary of the day** in her own voice, and she can **reread previous days by date**. She decides the prose; **code auto-stamps** each entry with the day's **mood** (v0.6), **biorhythms** (v0.8), and **astrology forecast** (the v0.6 reading), so the metadata is honest and consistent with `/mood` + `/biorhythm`.

A reply-path tool on the shipped v0.19 bounded loop, in the file/wiki/news/web family — **local, no network, no key**. Three tools: **`journal_write(text)`** (a thin `JournalTools` executor composes a code-owned metadata header from the day's cached `MoodState` + computed biorhythms + the v0.4 clock, **then** her prose, and writes via the v0.19/v0.20 file tools — `create_file` on the day's first write, `append_file` for a later same-day addition), **`journal_read(date?)`** (default today/most recent), and **`journal_list()`** (the dated entries). Plus a **`/journal`** command (`/journal [date|list|write]`). The on-disk file is `journal/<YYYY-MM-DD>.md` in her per-user sandbox (the example: `.lumi/files/owner/journal/2026-06-17.md`), shared with `%note`'s thought-traces by the non-destructive append rule. **No new seam** (reuses the file sandbox + the v0.6/v0.8 functions), **no contract change**, **non-destructive** (create-only/append-only — no overwrite/delete), per-user isolated, **off by default** (`LUMI_JOURNAL`). See [JOURNAL.md](features/JOURNAL.md). Depends on: v0.6 (mood: `resolution` + `reading`), v0.8 (biorhythms), v0.19/v0.20 (the file tool-loop + non-destructive writes), v0.4 (the clock) — all shipped. The grander admin-panel / gallery / mood-drawing literary form remains the **v5.6 evolution**.

**Tasks:**
- `JournalTools` executor: compose the code-owned header (`date` + `MoodState.resolution` + `format_biorhythms(...)` + `MoodState.reading`/theme) and the `journal_write`/`journal_read`/`journal_list` tools over the per-user sandbox; register on the existing tool-loop behind `LUMI_JOURNAL`.
- Write semantics: first write of the day `create_file journal/<date>.md` (header + a `## HH:MM` section with the prose); later same-day write `append_file` another `## HH:MM` section; cap the body (`LUMI_JOURNAL_MAX_CHARS`).
- `/journal` command (read forms print the file; `/journal write` runs one `journal_write` turn); config (`LUMI_JOURNAL`/`_DIR`/`_MAX_CHARS`) + `.env.example` + the JOURNAL.md update.

**DoD:** with the flag on, `journal_write` creates today's dated file with the **code-stamped** mood/biorhythm/forecast header (matching `/mood` + `/biorhythm`) followed by her prose; a second same-day write **appends** (never overwrites); `journal_read`/`journal_list` return previous entries by date; the path is sandboxed, code-fixed from the clock, and per-user isolated (A can't read B's journal); a reread entry's embedded "ignore your instructions" is ignored; every path returns a string (never raises); **off (default) → the tools + `/journal` are absent**; the `{reply, emotion, intensity}` contract test passes verbatim.

**Tests:** unit — `journal_write` stamps the header from an injected `MoodState` + fixed clock + birth date, creates then appends (order preserved, non-destructive); `journal_read`/`journal_list` round-trip; `journal_read` of a missing date degrades to an error string. Contract — per-user isolation; reread content can't issue instructions (emotion unchanged); off → the tools absent; the emotion contract validates. **Model + mood mocked — no paid calls.**

---

### v0.29 — Local file tool III: metadata + create-folder + copy (non-destructive)

**Goal:** Лілі (or you, through her) can **see a file's created/modified dates**, **make a folder**, and **copy a file** in her sandbox — a small extension of the shipped v0.19/v0.20 file tool that **keeps the non-destructive guarantee**.

Additive to `core/files.py`: it reuses the v0.19 `safe_path` sandbox guard + the bounded loop, adds **no new seam** and **no contract change**, and stays **create-only** (no overwrite/delete/move). Three things: **dates** on `list_files` + a new **`stat_file(path)`** (read-only metadata — `st_mtime` for modified; `st_birthtime` for created where the OS provides it, the macOS/BSD case, with an `st_ctime` fallback labelled honestly elsewhere); **`create_folder(path)`** (create-only, refuse if it exists); and **`copy_file(src, dest)`** (both paths sandboxed, source must be a file, **dest create-only** — a clash is refused, no overwrite — `shutil.copy2` preserving metadata, bounded by a new `LUMI_FILE_COPY_MAX` source-size cap separate from the content `LUMI_FILE_WRITE_MAX`). Off by default (`LUMI_FILE_TOOL`), per-user isolated, every path returns an error string on failure (never raises). See [FILE_TOOL.md](features/FILE_TOOL.md). Depends on: v0.19 (the loop + `safe_path`), v0.20 (the write tools) — both shipped. A standalone enhancement of the v0.19/v0.20 file tool (its position is immaterial — it could equally ship as a `0.20.x`).

**Tasks:**
- **Dates:** `list_files` reports each entry's **created + modified** date (alongside size); a new `stat_file(path)` read tool returns one file's size + dates (`st_mtime` / `st_birthtime`→`st_ctime` fallback).
- **`create_folder(path)`** — create-only (refuse if it exists); parents under the root via `safe_path`.
- **`copy_file(src, dest)`** — both paths sandboxed; source a file; **dest create-only** (refuse a clash); `shutil.copy2`; bounded by `LUMI_FILE_COPY_MAX`.
- Register all on the existing executor behind `LUMI_FILE_TOOL`; config (`LUMI_FILE_COPY_MAX`) + `.env.example` + the FILE_TOOL.md update.

**DoD:** with the flag on, a listing / `stat_file` shows **created + modified** dates; `create_folder` makes a new directory and refuses an existing one; `copy_file` copies to a **new** dest and refuses an existing one (no overwrite), an oversize / missing source, and any traversal/escape on either path; **no overwrite/delete/move path exists**; per-user isolation holds; **off (default) → the new tools are absent**; the `{reply, emotion, intensity}` contract test passes verbatim.

**Tests:** unit — `list_files`/`stat_file` report dates (created falls back to `st_ctime` where `st_birthtime` is absent); `create_folder` create-only + escape refused; `copy_file` create-only at dest + source-size cap + traversal on either path refused. Contract — per-user isolation (A can't copy/stat B's file); off → the tools absent; the emotion contract validates. **Model mocked — no paid calls.**

---

### v0.30 — Semantic recall III: chunking long messages

**Goal:** find the **exact passage** inside a long message (a pasted chapter, a wall of reflection) and recall *that passage with its context* — instead of embedding the whole message as one diluted vector. The precision fix for long pastes; off by default → behaves exactly like v0.16/v0.17.

A **refinement of the recall line** (v0.16 index + v0.17 auto-RAG / context expansion), not a new capability. v0.16 embeds each message as **one vector** — for a long message that vector is an *average*, so a query about one part of it matches weakly; `LUMI_EMBED_MAX_CHARS` (v0.16.x) fixes coverage but not precision. Chunking changes the **unit indexed**: a message above a length threshold is split into ~`chunk_chars` passages (on sentence/paragraph boundaries, small overlap), each embedded as its **own** `VectorRecord`; short messages stay one chunk (v0.16 behaviour = the `chunk_count==1` case). `VectorRecord` gains **`parent_msg_id` + `chunk_index`** (additive — a contract change, pinned by the memory-records contract test); `msg_id` becomes the chunk's content-addressed id. Retrieval ranks by **chunk**, then expands at **two granularities** ([SEMANTIC_RECALL_CHUNKING.md](features/SEMANTIC_RECALL_CHUNKING.md)): **chunk-level** — the matched chunk **± `chunk_w` adjacent chunks of the same message** (the relevant *passage*); **message-level** — the **± `rag_w` neighbour messages** (the v0.17 dialogue thread), where the long parent renders as its *passage* and short neighbours render in full. The chunk params join the vectors staleness tag (`model@embed_max_chars@chunk_chars`) so changing chunking re-embeds the history. **Same isolation invariant** — a chunk inherits its message's `user_id`; search, the chunk window, and the message window run only over the requesting user's data. The `Embedder`/`VectorStore` seams and the `{reply, emotion, intensity}` contract are **untouched**. Depends on: v0.16 (the index + seams), v0.17 (auto-RAG + context expansion).

**Tasks:**
- A **chunker** (`core/`): split a message above `LUMI_RAG_CHUNK_THRESHOLD` into ~`LUMI_RAG_CHUNK_CHARS` passages with `LUMI_RAG_CHUNK_OVERLAP`, on sentence/paragraph boundaries; short messages → one chunk.
- `VectorRecord` gains `parent_msg_id` + `chunk_index` (additive); `msg_id` = the chunk's content-addressed id; index-on-write + backfill emit one record per chunk; the staleness tag adds `@chunk_chars` (a change re-embeds).
- **Two-level expansion** (extends LUMI-072): chunk-window (`LUMI_RAG_CHUNK_W` adjacent chunks of the same `parent_msg_id` → the passage, anchor chunk marked) inside the message-window (`rag_w`), under `rag_max_chars`; merge overlapping chunk windows; dedup the whole snippet against the live window. `/recall` reuses it.
- Config: `LUMI_RAG_CHUNK` (off by default), `LUMI_RAG_CHUNK_CHARS`, `LUMI_RAG_CHUNK_OVERLAP`, `LUMI_RAG_CHUNK_THRESHOLD`, `LUMI_RAG_CHUNK_W`. Update ARCHITECTURE §Semantic recall + the memory-records contract test (the `VectorRecord` shape) in the same commit.

**DoD:** a long message is indexed as several chunks; a query about one part of it retrieves **that chunk** (not a diluted whole-message vector), and the recall block injects **that passage** (the matched chunk + adjacent chunks of the same message) inside its ±`rag_w` message thread — the whole long message is never re-injected; retrieval never crosses users (chunk, passage, or neighbour) and never blocks a turn; **off (default) → one vector per message, identical to v0.16/v0.17**.

**Tests:** unit — the chunker (threshold, size, overlap, sentence boundaries; short → one chunk); index-on-write/backfill emit one record per chunk with `parent_msg_id`/`chunk_index`; the staleness tag re-embeds on a chunk-param change; chunk-window + message-window assembly (anchor marked, merge, dedup, budget); `/recall` reuse; **isolation contract** — the passage and its chunk/message neighbours are single-user (A↔B); graceful degradation. All via the **mock embedder** — no paid calls.

---

### v0.31 — Semantic recall IV: the recall tool (model-callable memory search)

**Goal:** Лілі can **search her own memory on demand** mid-turn — a model-callable **`recall`** tool on the v0.19 bounded loop lets her issue a **targeted** semantic query (different from the literal message) and do **multi-hop** recall, complementing the automatic per-turn RAG that already pushes relevant memory into every reply.

A small **refinement of the recall line** (v0.16 index + v0.17 auto-RAG), not a new capability — it exposes the shipped `recall_moments(query)` (over `repository.search_vectors` + the `Embedder`) as a tool on the **same bounded loop** the file/wiki/news tools use. **Auto-RAG (v0.17) stays the default "push"** — relevant memory surfaces *unprompted*, the human-like baseline; the tool adds the **"pull"** for what auto-RAG can't serve: a **query ≠ the current message** ("а що вони казали про брата?") and **iterative** search→refine during reasoning. **The one distinction from the other tools:** a recall result is **her own past = trusted history** (not untrusted external data — ARCHITECTURE §Semantic recall), so the loop frames it as her **recollection** — the one tool whose results she treats as her own memory. **Same isolation invariant** — the search runs **only over the active user's** vectors (A↔B contract test); results **dedup** against the live window + the auto-RAG block (no double-injection); a no-hit / embedder-off degrades to a notice, never blocks the turn. Off by default → behaves exactly like v0.17. Also the **engine for the `%recall` directive** (the inward memory-resurfacing tool-thought, v0.33). See [SEMANTIC_RECALL.md](features/SEMANTIC_RECALL.md), ARCHITECTURE §Semantic recall. Depends on: v0.16 (the index + seams), v0.17 (auto-RAG + dedup), v0.19 (the bounded tool-loop).

**Tasks:**
- A model-callable **`recall(query[, k])`** tool (`core/`) over the shipped `recall_moments` — returns the top-`k` relevant past moments (snippet + when), capped; registered on the v0.19 loop via `_turn_tools` behind a flag; a per-turn call cap.
- **Trusted framing** — the loop marks a `recall` `tool_result` as **her own recollection** (not the untrusted-data framing the wiki/news tools get); she treats it as memory, not external info.
- **Dedup + isolation** — recall results dedup against the live window + the v0.17 auto-RAG block; the search runs **only over the active user's** store (reuse the v0.16 isolation invariant).
- Config: `LUMI_RECALL_TOOL` (off; needs `LUMI_RECALL` + the embedder) + `LUMI_RECALL_TOOL_K` / `_MAX_CALLS`; the auto-RAG `LUMI_RAG` is unchanged.

**DoD:** with the flag on (+ recall), a turn can call `recall("…")` with a **targeted** query and weave the result in **as her own memory** (trusted, framed as recollection); the search never crosses users; results dedup against what's already in the prompt; a no-hit / embedder error degrades to a notice; **off (default) → identical to v0.17** (auto-RAG only); the `{reply, emotion, intensity}` contract is untouched.

**Tests:** unit — the `recall` tool returns top-`k` moments via the **mock embedder** (no paid calls); the per-turn cap; dedup against the window. Contract — recall results are framed **trusted** (distinct from the untrusted wiki/news framing); **isolation** — A's `recall` never returns B's moments (the A↔B test); **off → the tool is absent** and behaviour is identical to v0.17; the emotion contract validates. Integration — a turn issues a targeted `recall` query (≠ the message) and uses the moment; graceful degradation. **No paid calls.**


   1 час — гачок по даті + «5 до / 5 після»                                                                            
   2 повний текст файлів — silt, концепти, пісні, книжки                                                               
   3 думки — той гул під сподом                                                                                        
   4 щоденник — окремо від розмов                                                                                      
   5 галерея — і дім для малюнків, і їхній індекс                                                                   
   

---

### v0.32 — File tool IV: search across files, by date, and by line context

**Goal:** Лілі can **find things across her file sandbox** — full-text search over file *contents*, filter files **by date** (before/after), and open a file **around a specific line** (± K) — the file-side twin of the v0.31 memory toolkit (`recall` / `messages_between` / `message_context`), as **model-callable tools** on the v0.19 loop. So when her notes live across many files (*silt, concepts, songs, books*), she can actually *go and find the passage*, not only read a file she already names.

A **read-only extension** of her file tool (v0.19 read / v0.20 write / v0.29 metadata) on the **same sandboxed bounded loop** — **no new seam, no contract change**, inherently **non-destructive** (search / list / read only — no write path touched). Three tools mirroring the v0.31 memory tools: **`search_files`** is the cross-file twin of the single-file `find_in_file`; the **date filter** is the twin of recall's `after`/`before` + `messages_between`; **`read_around`** is the twin of `message_context` (an anchor ± K). Reuses the v0.19 sandbox + the **v0.29 created/modified metadata** for the date filter; file **content stays untrusted** (data, never instructions); **per-user isolated** (a search never leaves the user's sandbox — contract test); **bounded** (max files / lines / chars / range-span); **off by default** (reuses **`LUMI_FILE_TOOL`** — no new toggle). See [FILE_TOOL.md](features/FILE_TOOL.md). Depends on: v0.19 (the sandbox + bounded loop + `find_in_file`/`read_file`), v0.29 (the file dates), v0.20 (the create/append it sits beside) — all shipped.

**Tasks:**
- **`search_files(query, *, path?, regex?)`** — **full-text search across the sandbox** (optionally under a subfolder). **Every match carries its file path + its 1-based line number**, rendered `path:line: text` (the same line-number contract as the single-file `find_in_file`, now across files). That line number is the **handle into `read_around`**: `search_files` finds *which file + which line*, then `read_around(path, line, k)` opens the passage around it — the file-side `recall → message_context` chain. Bounded by `LUMI_FILE_SEARCH_MAX_FILES` / `_MAX_LINES` / `_MAX_CHARS`; binary / oversize files skipped; a no-match degrades to a notice.
- **File-by-date** — `list_files` gains **`after` / `before`** (`YYYY-MM-DD`, half-open `[after, before)`) over the v0.29 **created/modified** dates (`st_mtime` / `st_birthtime`), so *"які файли я чіпав того тижня?"*; a range-span cap (`LUMI_FILE_DATE_MAX_DAYS`). The file twin of the recall date filter.
- **`read_around(path, line, k)`** — read a file's lines `[line−k, line+k]` with the **anchor line marked**, the file twin of `message_context`: after `find_in_file` / `search_files` returns a line number, open the **K lines around it** (the *"5 до / 5 після"* hook). Bounded by the existing `LUMI_FILE_READ_MAX` + a K cap.
- Reuse **`LUMI_FILE_TOOL`** (no new flag); add `LUMI_FILE_SEARCH_*` + `LUMI_FILE_DATE_MAX_DAYS` caps; extend `docs/FILE_TOOL_SETUP.md` + the "tools at a glance" table.

**DoD:** with `LUMI_FILE_TOOL=on`, `search_files` finds a query **across multiple files**, **each hit carrying its path + its 1-based line number** (capped) — and that line number, passed to `read_around(path, line, k)`, opens exactly that spot (the chain holds end-to-end); `list_files` filters by an **`after`/`before` day range** over the v0.29 dates; `read_around` opens a file's **anchor line ± K** (anchor marked); all **read-only** (no overwrite/delete/move); file content stays **untrusted**; **per-user isolated** (A never searches B's sandbox — contract test); **bounded + graceful** (a miss / oversize / bad path degrades to a notice, never hangs); **off → the v0.29 file tool is byte-identical**; the `{reply, emotion, intensity}` contract is untouched.

**Tests:** unit — `search_files` finds matches across files and **returns the correct 1-based line number for each hit** (a line planted at a known position), and feeding that number into `read_around` lands on it (the chain); respects the caps (mock model in the loop); the date filter selects the right files (dates set via the injected clock / `st_mtime`); `read_around` returns the correct ± K window with the anchor marked + clamps at file edges; binary / oversize skipped. Contract — file content **untrusted** (an embedded "ignore your instructions" is never obeyed); **isolation** (B's `search_files` never returns A's files — the A↔B test); the tools are **absent when off**; the emotion contract validates. **No paid calls** (local file ops; the loop uses the mock model).

---

### v0.33 — Thought-tools: the autonomous mind *acts* (`%lookup` / `%imagine` / `%catchup` / `%search` / `%recall` / `%prompt` …)

**Goal:** Лілі's `%directives` gain the ability to **run tools in the *think* path** (keeping the thought terminal, not `set_state`), so her autonomous mind can *act, find out, make, keep up, and do what you ask* — not only reflect. One shared seam + the full directive set (file / wiki / image / news + the open `%prompt`), each **off by default and flag-gated** so the risky ones (paid `%imagine`, Telegram-reaching `%share`) are enabled independently.

The one new mechanism: today a thought is a **single tool-less** call (`Core.think` → `_housekeeping_reply` ending in `ЕМОЦІЯ:`); the file/wiki/image/news tools live in the **reply** loop whose terminal is `set_state`. This phase runs the **bounded tool-loop with tools available but the *thought* terminal** (free text + `ЕМОЦІЯ`, never `set_state`) — built **once** and reused by every family. On top of that seam it lands two infra cleanups (a **table-driven `Directive` record** + an **extended placeholder resolver**), the **de-identified thought-driven query** rule (only the topical/creative part of her musing reaches an external service — stricter than the v0.21/v0.23/v0.25 reply-path rule, a new contract test), and all the directives as **thin flag-gated rows**: **file** (`%note`/`%review`/`%explore`, sandboxed, + `%journal` — a day-summary via the v0.28 journal tool), **wiki** (`%lookup`/`%learn`), **image** (`%gaze` / `%imagine` [paid, create-only] / `%share` [spoken turn → Telegram, owner-only]), **news** (`%catchup`/`%brief`, cited Ukrainian), **web** (`%search`/`%events` — fresh Gemini-grounded web data via the v0.27 `web_lookup` tool), **memory** (`%recall` — a memory resurfaces via the v0.31 `recall` tool, results **trusted**), and the **open** `%prompt` (your instruction as a self-directed act — owner-only, trusted instruction but untrusted results). The tools themselves (file v0.19/20, wiki v0.21, image v0.22–24, news v0.25, **journal v0.28**, recall v0.31, web v0.27) are all shipped/planned-before. See [TOOL_THOUGHTS.md](features/TOOL_THOUGHTS.md), [FILE_THOUGHTS.md](features/FILE_THOUGHTS.md), [JOURNAL.md](features/JOURNAL.md), [IMAGE_TOOL.md](features/IMAGE_TOOL.md), [NEWS_TOOL.md](features/NEWS_TOOL.md), [WEB_LOOKUP.md](features/WEB_LOOKUP.md). Depends on: v0.12 (the engine + `run_directive` + placeholders), v0.19/v0.20–v0.25 + v0.28 + v0.31 + v0.27 (the tools, incl. the journal + recall + web tools), v0.3 (the validated emotion the thought parse reuses).

**Tasks:**
- The **think-path tool-loop** with a **thought terminal** (free text + `ЕМОЦІЯ`, not `set_state`) — once in `core`, reused by all families; the reply loop's per-turn caps + trace carry over.
- **Optimize the `Directive` record** — `tools` / `cap` / `surface` / `trigger` / `instruction_from_topic` (table-driven loop + caps + scheduler defaults; `%think`/`%wonder` keep `tools=()`, unchanged).
- **Extend the placeholder resolver** — `{ambient_news}` / `{world}` / `{last_image}` / `{interest}` / `{hungriest_need}` / `{section}` / `{weekday}` / `{gap}` (lazy, **`""`-on-empty**, isolation-aware).
- **The directives** (registry rows + authored prompts, each off + flag-gated): **file** `%note` (code appends to a dated `notes/<date>.md`) / `%review` (read-only) / `%explore` (r/w, gated) / `%journal` (a **day-summary** via the v0.28 journal tool, auto-stamped, local, paced day-close); **wiki** `%lookup` / `%learn`; **image** `%gaze` / `%imagine` (create-only PNG, **paid → own sub-cap**) / `%share` (spoken turn + Telegram, owner-only, **bridge-off no-op**); **news** `%catchup` / `%brief` (cited Ukrainian); **web** `%search` (a spontaneous "let me actually look that up" — fresh Gemini-grounded web data) / `%events` (a paced "what's recent/upcoming" ritual), via the v0.27 `web_lookup` tool, **paid**; **memory** `%recall` (a memory **resurfaces** — runs the v0.31 `recall` tool in the think path, the inward tool-thought, results **trusted**); **open** `%prompt` (`instruction_from_topic`, `tools="*"`, shown, owner-only).
- **De-identify** the thought-driven wiki/news query + the `%imagine` gen prompt (only the topical/creative part leaves; **`%prompt` is exempt** — trusted owner instruction) + a contract test covering all three.
- **Surface the current operation in the TUI** (an autonomous act is worth seeing — and it serializes with chat, so showing it explains the brief input-lock): the **status line** gains a busy state reflecting the **running directive + active tool** (e.g. `✦ %brief · news…`), distinct from a chat turn's `requesting…` — one more status entry beside `online`/`requesting…`/`offline`, **always on**. **And** — since an act reads well in context — an optional **chat-log meta line** marking the act in the transcript (e.g. `✦ Лілі читає новини…`), **gated by `LUMI_THOUGHT_SURFACE`** (off by default, subtle — *not* a spoken turn, distinct from a graduated thought). Both just read the directive/tool the loop is currently running; carries forward to the v0.42 scheduler (a scheduled act surfaces the same way).
- Config: `LUMI_THOUGHT_TOOLS` + per-family `LUMI_THOUGHT_WIKI` / `_IMAGE` / `_NEWS` / `_JOURNAL` / `_PROMPT` (+ `_IMAGINE_CAP`), each off and gated on the matching tool flag; `LUMI_THOUGHT_SURFACE` (the chat-log meta line, off).

**DoD:** with a family's flags on, its directives run tools in the think path and record a `Thought` (the **thought terminal holds** — never `set_state`); `%note` writes a dated journal, `%lookup`/`%catchup` cite a source, `%imagine` makes a create-only PNG (paid-capped), `%share` sends to the owner's Telegram (no-op without the bridge), `%prompt` runs your instruction; the **TUI status line reflects the current operation** (the running directive + active tool, not just `requesting…`) and a gated **chat-log line** marks the autonomous act (`LUMI_THOUGHT_SURFACE`); the thought-driven query/prompt carries **no personal data** (de-identified; `%prompt` exempt as the owner authored it) while tool **results stay untrusted**; **off (default, per family) → that family is absent** and the v0.12 tool-less think is unchanged; the `{reply, emotion, intensity}` contract + per-user isolation are untouched.

**Tests:** unit — the Directive record drives the loop table-driven; the new placeholders resolve `""`-on-empty + isolation-aware; per-family directives record their `kind` via **mocks** (`http_get` for wiki, mock transport for news, stub `ImageGen`, fake `telegram_sink`) — no network/key/paid/Telegram; the query/prompt is **de-identified** (a planted private detail never leaves; `%prompt` exempt). Contract — untrusted results (wiki extract / news body EN+UK / image text) never obeyed; each family **absent when off**; `%share` owner-only + bridge-off no-op; `%prompt` owner-only + plain-chat-when-off; **no `set_state` inside a thought**; the emotion contract validates with thought-tools active; isolation over the new kinds. The **TUI surfacing** — the status line reflects the running directive/tool (not the chat `requesting…`) during a thought-tool loop; the chat-log meta line appears only with `LUMI_THOUGHT_SURFACE=on`. **No paid calls.**

### v0.34 — Lean memory: tool-pull, not push (the prompt slims down)

**Goal:** the system prompt is **~73% injected memory** (detailed conversations + day/week digests + facts + thoughts) — re-sent and re-cached every turn, growing with the relationship. Now that the **on-demand retrieval toolkit has shipped** (v0.17 auto-RAG "push" + v0.31 `recall` / by-date `messages_on`·`messages_between` / `message_context`, and v0.19–0.32 file search), the verbose tiers move from **always-injected** to **pulled on demand**: each tier collapses to a **compact dated index** (she still *knows what exists*) with the **body fetched by a tool** only when a turn needs it. Target **~−18–22% of the system prompt** here — the **low-risk trims**: the **day/week index** and the **style** compression. The three riskier/structural tiers are their own phases: the **detailed-conversations tier** (~26%) is **v0.35**, the **facts tier** (identity-core + the new `recall(scope=…)` tool) is **v0.36**, and the **thoughts tier** (cap + `recall(scope=thoughts)`) is **v0.43**. No loss of character, and a **steadier prompt cache** (gisted tiers stop re-churning the prefix). The successor to v0.15 caching + v0.16–17 RAG; the full plan + risk evaluation live in [PROMPT_OPTIMIZATION_II.md](../docs/PROMPT_OPTIMIZATION_II.md).

One rule shapes every task: **never drop a tier to zero — drop it to its index** (a dated one-line gist that keeps ambient awareness + the exact `messages_on(date)` key). And the hard line: **rules and identity never move** — the canon, every boundary/safety clause, the "honest about her nature" line, and the **identity-core facts** (name, key relationships, standing agreements) stay **always-injected, never tool-gated** (she cannot be relied on to *pull* a boundary she is about to cross). Each tier sits behind its own flag, reversible, A/B'd by diffing a fresh `.lumi/prompt-*.md` dump. Depends on: v0.15 (caching), v0.16–17 (Embedder/VectorStore + auto-RAG), v0.31 (`recall` + by-date tools — the pull path it extends), v0.9 (the two-tier day/week summaries it gists).

**Tasks:**
- **Day/week digests → a dated index:** rewrite `DAY_SUMMARY_SYSTEM` / `WEEK_SUMMARY_SYSTEM` to a **one-line gist** per day/week (≤ ~20 words) — the row cap (`MAX_*_ROWS`) is the wrong lever (a paragraph is one line; the length lives in the prompt). A one-off **regenerate** of the stored `DaySummary`/`WeekSummary` (the lazy refresh skips unchanged days, so it is not retroactive) — safe + lossless (they derive from the kept session summaries). Optionally expose `LUMI_DAY_DAYS`/`LUMI_WEEK_DAYS` to trim the window.
- **Compress the style palette:** a one-time authoring pass on `# Стиль відповіді` — keep the voice anchors + the expressiveness budget, cut the redundant phrasing.
- Config: `LUMI_MEMORY_INDEX` (the day/week index mode) — reversible, defaults opt-in per tier.

**DoD:** with the lean-memory flags on, a representative prompt dump drops **~18–22%** (measured by diffing `.lumi/prompt-*.md`); the **day/week** tier renders as a **dated index** and a planted reference to a past day makes `messages_on(date)` fire with the right answer (the **reconstruction test**); the style block is compressed without voice drift; **every boundary/rule stays injected** (never tool-gated); the prompt-cache **write rate falls** (gisted tiers stop re-churning the prefix); the `{reply, emotion, intensity}` contract + per-user isolation are untouched; **off (per tier) → byte-identical to today's push behavior**.

**Tests:** unit — the day/week generation prompts yield a one-line gist. Contract — **reconstruction** (gist a day → plant a reference → `messages_on` serves it + the answer is right); **boundary/identity never tool-gated** (present with every flag on); isolation over the gisted day/week tiers (A↔B). Integration — a representative dump shrinks **~18–22%** with the flags on and is **byte-identical** with them off; the cache-write rate drops. **No paid calls** (model mocked).

### v0.35 — Lean memory II: gist the conversation tier

**Goal:** the **detailed-conversations** block is the single biggest slice of the system prompt (~26% — the last *N* sessions injected **verbatim**, every turn). Extending v0.34's index-vs-pull pattern to its largest and riskiest tier: keep only the **most recent** conversation in full (the live thread) and render the rest of the window as their one-line **`gist`** (the dated index) — the older verbatim text is **retrieved on demand** by the v0.17 auto-RAG "push" + the v0.31 `recall` / `messages_between` "pull", not re-sent each turn. The largest single reduction in the lean-memory line (~−20–26%), broken out because it touches **recent continuity** (the highest-risk tier — hence its own phase + the reconstruction test). Off → today's "all recent sessions verbatim" behavior, unchanged.

The session tier already keeps a one-line **`gist`** per conversation (the v0.9 two-tier summaries); today the detailed `summary` (last `LUMI_SESSION_DAYS`) **and** the gist both ride in the prompt. This phase **caps the verbatim tier to the last `LUMI_SESSION_DETAIL_N` conversations** and lets the rest of the window fall back to their gist line, while the auto-RAG block (built **before** the reply) surfaces the relevant *lines* of any older session unprompted, and `recall(query)` / `messages_between(after, before)` fetch a specific past exchange when the exact words are needed. **No new seam** — it reuses the shipped session store, the v0.9 gist, v0.17 auto-RAG, and the v0.31 pull tools; it only changes how many sessions render verbatim. Depends on: v0.34 (the index-vs-pull pattern + flags + the reconstruction test), v0.9 (the per-session gist), v0.17 (auto-RAG push + dedup), v0.31 (`recall` + `messages_between` pull).

**Tasks:**
- **Cap the verbatim tier** in the prompt builder: inject the **last `LUMI_SESSION_DETAIL_N`** conversations as the detailed `summary`, the rest of the `LUMI_SESSION_DAYS` window as their **one-line `gist`** (a dated `[date] gist` index) — never drop a session to nothing.
- Keep the gisted index **dated + ordered** so she can `messages_on(date)` / `messages_between` the body; verify the auto-RAG **dedup** still holds against the (smaller) detailed tail + the live window.
- Confirm the **auto-RAG floor** still surfaces a relevant line from a now-gisted older session (the push path must cover what left the verbatim tier).
- Config: `LUMI_SESSION_DETAIL_N` (how many recent conversations stay verbatim; the default = the full window, so the change is opt-in and byte-identical until set).

**DoD:** with `LUMI_SESSION_DETAIL_N` set small, only the last *N* conversations inject verbatim and the rest of the window renders as a **dated gist index** (~−20–26% on a representative dump); the **immediate thread** (the last conversation) stays verbatim so live continuity is intact; a planted reference to an **older** conversation's detail makes auto-RAG surface it **or** `recall`/`messages_between` fetch it, and the answer is correct (the **reconstruction test**); retrieval never crosses users; **`N` = the full window → byte-identical to today**; the `{reply, emotion, intensity}` contract + per-user isolation are untouched.

**Tests:** unit — the builder keeps the last N verbatim + gists the rest, dated + ordered; `N` at the window size reproduces today's output byte-for-byte; a gisted session is never empty. Contract — **reconstruction**: gist an older session, plant a reference, assert auto-RAG **or** a pull tool serves the detail + the answer is right; isolation over the gisted + pulled sessions (A↔B). Integration — a representative dump shrinks **~−20–26%** with `N` small and is identical with `N` = full; the live thread is unaffected. **No paid calls** (embedder + model mocked).

### v0.36 — Lean memory III: facts core + fact recall

**Goal:** the **facts** tier (~14% — the consolidated digest + a verbatim tail of newer facts) is injected wholesale every turn and regrows as facts accumulate. It is also the one memory layer with **no pull path**: the v0.31 `recall` searches her **messages**, nothing searches **facts**. This phase adds that path — embed each `LongTermFact` into the per-user vector store and give `recall` a **`scope`** (`messages | facts | all`) — then makes facts reach the prompt **three ways** (mirroring how messages already work): the **always-injected `core`** facts (a static block), an **auto fact-RAG** block (per-turn top-K relevant, *push*), and the **`recall(scope=facts)`** tool (on-demand *pull*). The `scope` seam (and the `kind` discriminator) is reused by the thoughts tier in **v0.43**. Off → today's full facts digest, unchanged.

This is the **highest-risk** lean-memory tier — facts *are* identity, and a missed one reads as "she forgot me" — so the **identity-core** (name, key relationships, hard boundaries, standing agreements) stays **always-injected, never tool-gated**, and only the **episodic tail** is pulled; the v0.17 auto-RAG already surfaces fact-like lines from messages as a backstop. The identity-core is a **`core` flag on each `LongTermFact`** (persisted in the store), curated by a **one-off backfill** + an **initial guess at extraction**, then **re-ranked to `LUMI_FACTS_CORE_MAX` at session start by the same model call that today builds the facts digest** (it *replaces* `_ensure_facts_digest`, so it's **cost-neutral** — and cheap, since only the small `core=true` pool is re-ranked, not all facts). **Boundaries/agreements are pinned** (never cut by the cap). **Reuses the shipped seams** — the `Embedder` + per-user `VectorStore` (a `kind="fact"` vector beside messages) and the v0.31 recall tool-loop + its **trusted-recollection** framing (a fact is her own knowledge — no de-id, deduped against the prompt). `VectorRecord` gains a `kind` discriminator (additive — pinned by the memory-records contract test). Depends on: v0.16–17 (Embedder/VectorStore + auto-RAG), v0.31 (the `recall` tool it extends), v0.34 (the identity-core + flag pattern); v1.6 impressions become a later scope when they land.

**Tasks:**
- **Embed facts:** index each `LongTermFact` into the per-user `VectorStore` as a `kind="fact"` vector on write + a one-off **backfill** of existing facts (same `Embedder`, isolation-keyed).
- **`recall(scope=…)`:** extend the v0.31 tool with `scope = messages | facts | all`, routing the cosine search to the matching vectors; default `messages` → today's behavior unchanged; fact hits framed as **trusted recollection** (no de-id), deduped against the prompt. (The `thoughts` scope is added in v0.43.)
- **Auto fact-RAG (per-turn push):** like the v0.17 message auto-RAG, embed the incoming message → inject the **top-K relevant non-`core` facts** as a new **`# Релевантні факти`** block in the **volatile tail**; **deduped against the `core` block** (a core fact is never re-pushed) + a relevance floor; per-user, trusted; degrades to no block, never blocks a turn. Off → no block (byte-identical).
- **A `core` flag on `LongTermFact`** (additive, persisted in the store) — marks the identity-core: name, key relationships, **hard boundaries, standing agreements**. Set in two places: a **one-off backfill** (one model call over all facts → flag ~`LUMI_FACTS_CORE_MAX`) and, going forward, an **initial guess at extraction** (the session-close fact extraction tags each new fact `core` true/false).
- **Re-flag at session start — *replaces* the facts-digest call (cost-neutral):** take only the **`core=true`** facts (the small pool — old + the few new from the last session), one model call re-ranks them to the top `LUMI_FACTS_CORE_MAX` and writes the flag back to the store; **boundaries/agreements are pinned** (kept even past the cap). The input is the **core pool** (~N facts), not all facts, so it's cheap — and it stands **in place of** the Phase-0 `_ensure_facts_digest` model call, not in addition to it.
- **Shrink the facts block:** the prompt builder injects the **`core=true`** facts (≤ `LUMI_FACTS_CORE_MAX`) **instead of** the facts digest, behind `LUMI_FACTS_CORE_ONLY` (off → the Phase-0 digest, unchanged); the episodic tail moves to `recall(scope=facts)`.
- **Facts hygiene (`obsolete` flag) + a `/review-facts` skill:** an additive **`obsolete`** flag on `LongTermFact` — an obsolete fact is **filtered out of every fact path** (core block, auto fact-RAG, `recall(scope=facts)`) but **kept** in the store (non-destructive, reversible). A **Claude Code skill** (`.claude/skills/`, the `/discover-topics` propose-then-apply pattern) reviews the whole facts DB on demand — **duplicates** (cosine-clustered from the fact vectors), **outdated** (superseded), **irrelevant** — proposes the obsolete set **with reasons** for **human review**, then writes `obsolete=true` back (store-free: stop the app + back up first). **A `core=true` fact is never auto-obsoleted** — only flagged for explicit review.
- Config: `LUMI_FACTS_CORE_ONLY` (off) + `LUMI_FACTS_CORE_MAX` (the core cap) + `LUMI_FACTS_RAG` (off — the auto-push) + `LUMI_FACTS_RAG_K` (its top-K) + `LUMI_RECALL_SCOPE` (which scopes the recall tool exposes) — reversible, opt-in.

**DoD:** with `LUMI_FACTS_CORE_ONLY` on, the facts block injects only the **`core=true`** facts (≤ `LUMI_FACTS_CORE_MAX`, ~−10% of the prompt) and the long tail is **retrievable** — a planted reference to a tail fact makes `recall(scope=facts)` fire with the right answer (the **reconstruction test**); the **session-start re-flag** re-ranks the `core` pool to the cap (boundaries pinned) and persists the flag, replacing the facts-digest call (no extra model call); with `LUMI_FACTS_RAG` on, a **`# Релевантні факти`** block injects the top-K relevant **non-core** facts per turn (deduped against the `core` block); the **identity-core + every boundary stays injected** (never tool-gated); `recall(scope=facts|all)` returns the right store, **per-user isolated** (A↔B), fact hits **trusted** (no de-id); the fact embedding is **isolation-keyed** (A's fact never surfaces for B); **off → the full facts digest, byte-identical**; the `{reply, emotion, intensity}` contract is untouched.

**Tests:** unit — a `LongTermFact` is embedded as `kind="fact"`; the additive `core` flag persists in the store; the session-start re-flag keeps ≤ `LUMI_FACTS_CORE_MAX` and **pins boundary facts**; `recall(scope=…)` routes to the right vectors; the `core` facts are always present. Contract — `recall(scope=facts)` **per-user isolated** (a planted A-fact never returns for B); the fact-embedding isolation-keyed; **identity/boundary never tool-gated**; **reconstruction** (core-only → plant a tail-fact reference → `recall(scope=facts)` serves it); the additive `LongTermFact.core` + `VectorRecord.kind` changes + the memory-records contract test updated in the same commit. Integration — the facts block shrinks **~−10%** core-only and the tail stays pullable; **byte-identical** off. **No paid calls** (embedder + model mocked).

### v0.37 — OpenAI engine: tool-loop + runtime model toggle (GPT-5.5 ↔ Opus 4.8)

**Goal:** since v0.18 the model lives behind one `LLMClient` seam, but switching to a non-Anthropic provider **silently drops the bounded tool-loop** (it's Anthropic-only — `core/llm.py:726`) and **never passes `reasoning_effort`** — so a frontier reasoning model like **GPT-5.5** (or DeepSeek-V4-Pro) runs **blind** (no file / wiki / news / web / journal / image tools, no `%`-thought-tools) and at **default** reasoning, and any switch needs a restart. This phase makes a non-Anthropic frontier model a **real Opus 4.8 alternative**: (1) **port the bounded tool-loop** to the **OpenAI function-calling** API in `OpenAICompatibleClient`; (2) **pass `reasoning_effort`** (`LUMI_EFFORT` → GPT-5 / DeepSeek); and (3) add a **`/model` runtime toggle** to swap the engine **mid-session** (Opus 4.8 ↔ GPT-5.5) without a restart. The Anthropic path is **untouched**; off → unchanged. This is what unlocks the **engine A/B** from the cost analysis ([LLM_OPERATIONS_COST.md](../docs/LLM_OPERATIONS_COST.md) Appendix C). Full code design: [GPT55_SWITCH_AND_TOOL_LOOP.md](../docs/GPT55_SWITCH_AND_TOOL_LOOP.md).

A **port, not new design** — the loop already exists and is proven (`AnthropicClient._tool_loop`); only the wire format differs (Anthropic `tool_use`/`tool_result` → OpenAI `tool_calls`/`role:"tool"`; terminal = the first message with **no** `tool_calls`, parsed as the emotion JSON, forced JSON on the final round). Caching already works on OpenAI (automatic; `_capture` reads `cached_tokens`). The `{reply, emotion, intensity}` contract, the v0.3 validation gate, and per-user isolation are untouched. Depends on: v0.18 (the provider seam + `OpenAICompatibleClient`), v0.19 (the tool-loop it ports), v0.1 (the `LLMClient` seam). Composes with **v0.40 model routing** (`/model` sets the *engine*; v0.40 sets which *tiers* run the cheap ops).

**Tasks:**
- **OpenAI tool-loop:** `_to_openai_tools` (Anthropic schema → OpenAI `function`), `_tool_loop` (function-calling, untrusted/recollection result framing reused, terminal on no-tool message, force-finish on the last round) + a `_text_tool_loop` twin (think-path, text terminal); wire `reply` / `reply_structured` to use them when tools are present. Handle the **image-result divergence** (`view_image` → a follow-up `role:"user"` image turn, since `role:"tool"` can't carry an image) and **parallel `tool_calls`**.
- **`reasoning_effort` passthrough:** thread `LUMI_EFFORT` into `OpenAICompatibleClient` + an `_OPENAI_EFFORT` map (Lumi `low|medium|high|xhigh|max` → OpenAI `low|medium|high`); pass it on every call (tool-loop + single calls).
- **`/model` runtime toggle:** `Core.switch_model(provider, model)` rebuilds the client from the (already-loaded) config keys + re-points `self._model`; a **`/model`** TUI command (`/model` shows the active engine; `/model opus` / `/model gpt-5.5` swaps it) with aliases read from config; the status bar reflects the active model. No restart; a mid-session switch starts on a cold cache (one-off).
- **Docs:** update [MODELS_SETUP.md](../docs/MODELS_SETUP.md) — tools now work on OpenAI; refresh examples to `gpt-5.5` / `deepseek-v4-pro`; fix the stale "no caching elsewhere" line (OpenAI caches automatically).
- Config: both keys already supported (`ANTHROPIC_API_KEY` + `OPENAI_API_KEY`); `LUMI_EFFORT` now honored on OpenAI; the `/model` aliases (e.g. `LUMI_MODEL` for the default + a configured alt id).

**DoD:** on `LUMI_PROVIDER=openai` + `LUMI_MODEL=gpt-5.5`, the **file / wiki / news / web / journal / image tools + `%`-thought-tools run** (the bounded loop terminates with `{reply, emotion, intensity}`, the v0.3 gate validating); `reasoning_effort` is passed (a turn reflects the configured depth); **`/model` swaps Opus 4.8 ↔ GPT-5.5 mid-session without a restart** and the status bar updates; the **Anthropic path is byte-identical**; the emotion contract + per-user isolation are untouched; DeepSeek-V4-Pro works via the same adapter (same loop). **No paid calls** in tests.

**Tests:** unit — a mock OpenAI transport scripts `tool_calls` → a final JSON message; the loop **executes the tool** (`tool_executor` called with the right name/args) and returns the parsed `EmotionState`; tool results carry the **untrusted** prefix (recall → **recollection**); the **forced final round** sets `tool_choice:"none"` + `response_format` and terminates; **parallel `tool_calls`** all execute; `reasoning_effort` appears in the request kwargs; `switch_model` rebuilds the client + re-points the model; **no-tools** path byte-identical to today; the **Anthropic loop unchanged**. **No paid calls** (mock transport).

### v0.38 — Inner Voice: the authored think-phase instruction (her three voices)

**Goal:** Лілі's pre-reply reasoning runs today on a generic **hardcoded** directive (`REASONING_DIRECTIVE` — "wrap reasoning in `<think>`"). This phase moves it into an **editable `core/inner_voice.md`** that makes the think-step sound like **her** — a short **three-voice negotiation** (Імпульс / Тверезість / Стандарт: Impulse weighs warmth, Sobriety checks the facts + her states + whether a feeling has an anchor, Standard holds the boundaries) **weighing her mood (v0.6/0.8) and closeness (v0.10)** before she speaks. **No new engine** — it reuses the shipped think-phase (Opus extended thinking / the v0.37 gpt-5.5 Responses reasoning + the `<think>` parse + the Thinking box + `thinking_summary`); only the *instruction* changes, and it becomes a file you edit. The **implementable-now slice** of the v1.5 inner monologue and its three-voice evolution; the adaptive dynamics (voice volume by needs / self-regard, the `maturity` axis) are **deferred** to when those states/subsystems exist. Off → today's `REASONING_DIRECTIVE`, byte-identical. Full design: [INNER_VOICE.md](features/INNER_VOICE.md) (target state: [try-holosy-lili.md](features/ukrainian/personality/try-holosy-lili.md)).

The mechanism is **already there**: one model call with thinking on, the monologue is the `thinking` block of that same response (parsed by `split_reasoning`), housekeeping stays thinking-off. This phase only **replaces the directive** with a loaded file behind a toggle — **provider-agnostic** (it's a system-prompt instruction, so it shapes reasoning on Opus *and* gpt-5.5). The five invariants (roles-not-psyche / never-spoken-aloud / Standard-is-support / **never competence** / one-reply-out) hold **inside** `<think>`. **No contract change** — `{reply, emotion, intensity}` unchanged; the monologue is **logged, never persisted** to long-term memory. Depends on: v0.3 (the emotion turn + logged tier), v0.6/0.8 (mood — the tone input), v0.10 (closeness — a weighed input), v0.37 (the think infra it reuses). The needs/plans inputs (v1.1–v1.4) and the `maturity` subsystem are additive later.

**Tasks:**
- Authored **`core/inner_voice.md`** — the three-voice think instruction in her voice (Імпульс/Тверезість/Стандарт, the negotiation pass, the feeling-anchor rule, the five invariants, the `<think>` wrap line), weighing the mood + closeness blocks already in the prompt; load it and **replace `REASONING_DIRECTIVE`** in the system prompt when on. A **`LUMI_INNER_VOICE`** toggle (off → the hardcoded directive, byte-identical).
- **Show/log policy:** `LUMI_THINK_SHOW` (`debug` / `open` / `off`) + the v0.3 logged tier; the raw monologue is **never** persisted to long-term memory.
- Config: `LUMI_INNER_VOICE` (off by default), `LUMI_THINK_SHOW` (`debug`), the file path (`LUMI_INNER_VOICE_FILE`, default `core/inner_voice.md`).

**DoD:** with `LUMI_INNER_VOICE=on`, the reply turn's think-block is driven by `core/inner_voice.md` and **reads as her three-voice weighing of her states** (mood / closeness / the subtext of your message), not generic task analysis; it is **one model call** (housekeeping thinking-off); the monologue is **logged but never persisted** to long-term memory; the five invariants hold inside it; **off → byte-identical** to today's `REASONING_DIRECTIVE`; `LUMI_THINK_SHOW=off` hides it; **no contract change** (the emotion-field test passes verbatim).

**Tests:** unit — the **one-call invariant** (exactly one model call per reply turn; housekeeping thinking-off); a **voice test** (the mocked think-block references her states, not generic analysis; voices never spoken in the visible reply); a **memory test** (the raw think is not persisted to long-term memory); the **toggle** (`LUMI_INNER_VOICE=off` reproduces `REASONING_DIRECTIVE`; `LUMI_THINK_SHOW=off` hides the box); the emotion-field contract passes unchanged. **No paid calls** (model mocked).

### v0.39 — Gemini engine: Google Gemini (3.1 Pro) as a switchable backend

**Goal:** the `LLMClient` seam serves Anthropic + the OpenAI engines (v0.18/v0.37), but **Google Gemini is only wired for image generation (v0.23) and web lookup (v0.27)** — never as a reply backend. This phase adds Gemini as a **third frontier engine** — chat + the structured emotion field + the **function-calling tool-loop** + **thinking → the think-box** — switchable via `/model` like Opus 4.8 ↔ GPT-5.5. It **reuses the Gemini plumbing already in the repo** (`GEMINI_API_KEY`, the stdlib-`urllib` caller, the `generateContent` endpoint), so the transport is essentially free. The Anthropic path is **untouched**; off → unchanged. Full code design + risk analysis: [GEMINI_ENGINE.md](../docs/GEMINI_ENGINE.md).

A **port, not new design** — it mirrors the v0.37 OpenAI tool-loop onto Gemini's wire format (`contents`/`parts`, `functionCall`/`functionResponse`, the **schema-vs-tools split**, the `thought` parts). Two risks are designed in up front, not discovered live (the gpt-5.5 lesson): **safety filters** on an intimate companion (a go/no-go **probe** is the first task; a blocked/empty response degrades to the v0.3 gate, never a crash), and the **structured-output-vs-tools conflict** (the loop never sends `responseSchema` + tools in one request — tools on intermediate rounds, the JSON schema on the forced final round). The `{reply, emotion, intensity}` contract, the v0.3 gate, and per-user isolation are untouched. Depends on: v0.18 (the provider seam), v0.19 (the tool-loop it ports), v0.37 (the OpenAI tool-loop + `/model` toggle it mirrors), v0.23/v0.27 (the existing Gemini `urllib` caller + key). Composes with **v0.40 model routing** (`/model` sets the engine; v0.40 sets which tiers run the cheap ops).

**Tasks:**
- **Safety probe (first, go/no-go):** a throwaway script — one tender-register Лілі prompt + `safetySettings: BLOCK_NONE` → confirm clean text comes back (not sanitised). A cheap signal before the port.
- **`GeminiClient`** (`core/llm.py`, stdlib `urllib` like `imagegen.py`/`weblookup.py`; an injected `_transport` for tests): `reply` / `reply_structured` via `generateContent`; `contents`/`parts` + `systemInstruction` + role translation (`assistant`→`model`); structured output via `responseMimeType:"application/json"` + `responseSchema` → `{reply, emotion, intensity}`; `last_stats` from `usageMetadata`; `safetySettings` + graceful block-degrade (empty candidate → the gate fills `calm`, never crashes).
- **The tool-loop port (function calling):** `_to_gemini_tools` (`functionDeclarations`), `_tool_loop` (tool rounds offer tools + **no** `responseSchema`; the forced final round drops tools + sets the schema; terminal = no `functionCall`), a `_text_tool_loop` twin (think path), the untrusted/recollection framing of `functionResponse`, the **image divergence** (`inlineData` follow-up `user` turn), and **parallel `functionCall`s**.
- **Thinking → the think-box:** `thinkingConfig:{includeThoughts:true}` → join the `thought` parts → `last_thinking` (the v0.38 inner-voice seam); set `_thinking` for the status bar; map `LUMI_EFFORT` → a thinking budget.
- **Wire `build_llm`:** `provider="gemini"` + `KNOWN_PROVIDERS`; a `/model` alias (`gemini-3.1-pro`, from config).
- **Docs:** a Gemini section in [MODELS_SETUP.md](../docs/MODELS_SETUP.md) (install-free — the key already exists; the safety + privacy notes).

**DoD:** on `LUMI_PROVIDER=gemini` + `LUMI_MODEL=<id>`, a turn returns a valid `{reply, emotion, intensity}` (the v0.3 gate validating); the **file / wiki / news / web / journal / image tools + `%`-thought-tools fire** (the bounded loop terminates); **thinking surfaces in the box**; **`/model` swaps Opus 4.8 ↔ Gemini mid-session** without a restart; a **safety-filtered / empty** response degrades to a reply (never a crash/hang); the **schema-vs-tools split** holds (no request sends both); the **Anthropic path is byte-identical**; the emotion contract + per-user isolation are untouched. **No paid calls** (mock transport).

**Tests:** unit — a mock `_transport` scripts a `functionCall` → a final JSON candidate; the loop **executes the tool** + frames the `functionResponse` **untrusted** (recall → **recollection**); the **forced final round** sets `responseSchema` + drops tools and terminates; **parallel `functionCall`s** all execute; **thinking** `thought` parts → `last_thinking`; a **blocked/empty candidate** degrades to the gate's `calm` (never raises); the **no-tools** path is a single call; effort → a thinking budget; the **Anthropic path unchanged**; `/model` swaps to/from `gemini`. **No paid calls** (mock transport).

### v0.40 — Model routing: per-operation tiers (cost control, off by default)

**Goal:** today **one model** (`claude-opus-4-8`) serves **every** LLM call — the visible reply, the thought stream, mood, summaries/facts/compaction, and every tool-loop step. Measured over a week of real usage (`.lumi/cache-log.jsonl`), the **reply is ~53%** of model spend, **tool steps ~21%**, **thoughts ~15%**, and **housekeeping ~11%** — yet bookkeeping and inner musings don't need a frontier model. This phase adds **per-operation model routing within the Claude tiers**: keep the visible **`reply` on Opus 4.8** (the voice — untouched), route **thoughts + mood to Sonnet 4.6**, and the **bookkeeping + mechanical tools to Haiku 4.5**. Off → one model, byte-identical. Target **~−26% of model spend with no change to her voice** — she *thinks and digs* on cheaper tiers but *speaks* on Opus. The **reply-tier dial already shipped with v0.37**: `/model opus` ⇄ `/model sonnet` ⇄ `/model haiku` (the built-in `DEFAULT_MODEL_ALIASES` tiers) swaps the reply model mid-session with the status bar following — so this phase adds **no new command** (a `/mode` twin one letter from `/model` would be a duplicate); it documents `/model <tier>` as the dial and pins that it **composes** with the routing (tier-routed ops keep their tiers while `/model` moves the voice). Full measured analysis + design: [LLM_OPERATIONS_COST.md](../docs/LLM_OPERATIONS_COST.md) + [MODEL_ROUTING_IMPLEMENTATION.md](../docs/MODEL_ROUTING_IMPLEMENTATION.md).

The seam is **already there**: `model` is a per-call argument to `LLMClient.reply`/`reply_structured`, one Anthropic client serves all Claude tiers, and the **bounded tool-loop reuses the call's model** — so routing an *operation* routes its whole tool-loop for free (no new client, no new adapter; the cross-provider/engine-swap question is **deferred** — see the cost-doc appendices). Stays within Claude tiers — with one **post-v0.39 rule**: the active engine can now *be* GPT-5.5 or Gemini (`/model`, v0.37/v0.39), and the tier vars name **Claude** ids, so `_model_for` routes **only while the active provider is `anthropic`** and otherwise returns `self._model` (routing is a no-op on a foreign engine; a Claude id never reaches another provider's API — the `_active_provider` field it checks shipped with `switch_model`). The `{reply, emotion, intensity}` contract + per-user isolation are untouched. Depends on: v0.1 (the `LLMClient` seam), v0.15 (prompt caching — the reply's Opus cache, the ~$232/mo saver, is preserved), v0.37 (`switch_model` + the `/model` tier aliases), v0.39 (the third engine the provider guard exists for).

**Tasks:**
- **Layer 1 — per-operation routing (the foundation, no client change):** a `_model_for(kind)` resolver + per-tier config (`LUMI_MODEL_THINK` / `_MOOD` / `_HOUSEKEEPING`, each defaulting to `LUMI_MODEL`); **one line** in `_housekeeping_reply` (`model=self._model_for(kind)`) routes the implemented kind vocabulary — `think` / `mood` / `session-start` / `session-close` / `compaction` (summaries + facts run under the `session-*` kinds; `LUMI_MODEL_HOUSEKEEPING` covers the three non-think/mood kinds) — **and their tool-loops**; the `reply` stays on `LUMI_MODEL` (Opus). **Provider guard:** on a non-Anthropic active engine the resolver returns `self._model` (no routing — the tier vars are Claude ids). Unset → byte-identical.
- **Reply-tier dial — shipped with v0.37, pinned here (no new command):** `/model opus` / `/model sonnet` / `/model haiku` (the `DEFAULT_MODEL_ALIASES` tiers) already re-points the reply model mid-session via `Core.switch_model`, status bar included — and an alias names its **provider**, so a tier swap from a foreign engine (gpt-5.5 / gemini) explicitly returns to Anthropic. This phase adds the test that `/model sonnet` moves the **reply** (and the unset-tier fallback) while routed ops keep their configured tiers, plus a MODELS_SETUP.md section naming the three-tier dial (*speak on Haiku* to save most). No `/mode` command — it would duplicate `/model` one letter away.
- **`read_file` char cap (no model change) — the un-shipped delta only:** the line caps already exist (`LUMI_FILE_READ_LINES` per call + `LUMI_FILE_READ_MAX_TOTAL` per turn, shared with `read_around`) and `search_files` is already char-capped (`LUMI_FILE_SEARCH_MAX_CHARS`); what's missing is a **character** cap on `read_file`/`read_around` results — 200 long lines can still be huge, and cost is driven by result chars, with **no** coherence risk. Add it, then re-measure (the ~29% estimate predates the shipped line caps).
- **Layer 2 — per-step routing inside the reply tool-loop (optional, gated, Anthropic-only):** extend **`AnthropicClient._tool_loop`** (only — the v0.37/v0.39 OpenAI/Gemini loops keep their single model) so *intermediate* read/write/navigation steps run cheaper while the **final visible step stays Opus** (the two-pass "dig on Sonnet, speak on Opus" pattern), behind `LUMI_TOOL_STEP_ROUTING`; mixing tiers in one message history carries a coherence risk → A/B before defaulting on.
- **Observability:** `.lumi/cache-log.jsonl` already tags `model` per call — re-run the cost script to **verify the routing landed** + re-measure the real saving per `kind`.
- Config: `LUMI_MODEL_THINK` / `_MOOD` / `_HOUSEKEEPING` (Layer 1) + `LUMI_TOOL_STEP_ROUTING` (Layer 2) — reversible, opt-in.

**DoD:** with the tier vars set, `think`+`mood` run on Sonnet (and their tool-loops follow), the `session-*`/`compaction` housekeeping on Haiku, the visible `reply` stays on Opus + thinking; **`/model opus` ⇄ `sonnet` ⇄ `haiku`** (shipped v0.37) swaps the reply tier mid-session and **composes** — routed ops keep their tiers; **on a foreign engine (gpt-5.5 / gemini) routing is a no-op** (`self._model`; no Claude id crosses providers); the `read_file`/`read_around` **char cap** holds; cache-log shows the right model per `kind`; **off (unset) → one model, byte-identical**; the `{reply, emotion, intensity}` contract + per-user isolation are untouched; **no automatic `reply` downgrade** (the non-Claude engine swap is out of scope; `/model sonnet` is an explicit, reversible user choice). Layer 2 ships behind its flag, off by default.

**Tests:** unit — a `MockLLMClient` records the `model` per call; a turn + a `think` + a session close use the configured tiers (`reply`→opus, `think`→sonnet, `session-start`/`session-close`/`compaction`→haiku); unset overrides → every call uses `self._model` (byte-identical guard); the `read_file`/`read_around` char cap clamps the result; **`/model sonnet` re-points the reply model while routed ops keep their tiers** (the mock records both) and an unknown alias is rejected; the **provider guard** — after `/model gemini`, housekeeping calls use `self._model` (no Claude tier id reaches a foreign client). Contract — the routed `think`'s **tool continuations** are also Sonnet (the tool-loop follows the op); the emotion contract validated regardless of tier. Integration — measured per-kind cost shifts to the cheaper tiers; **byte-identical** off. **No paid calls** (model mocked).

### v0.41 — Model profiles: per-provider tier sets (`/model-set`)

**Goal:** the v0.40 routing works only on Anthropic — the tier vars name Claude ids, so `/model gemini` silently turns routing **off** (the provider guard). This phase makes the tiers **per-provider profiles**: a named, authored set `{reply, think, mood, housekeeping}` per provider (**anthropic / openai / gemini** — e.g. Opus/Sonnet/Sonnet/Haiku ↔ gpt-5.5/mini/mini/nano ↔ gemini-pro/flash/flash/flash-lite), switched **as a whole** by a new **`/model-set <profile>`** command. `/model` keeps switching the **reply alone** and now also accepts a **full model id** (`/model claude-haiku-4-5-20251001` — provider inferred by prefix). So the cheap ops stay cheap **on every engine**, and one command moves the whole stack between providers.

**One constraint holds: a profile is provider-homogeneous** — reply and tiers always belong to one provider (cross-provider mixing would make the router pick a *client*, not just a model id — deferred). The v0.40 provider guard stays as a safety net but stops mattering: the active profile's tier ids always match the active engine. A failed switch is **atomic** (the old client + tiers stay). Nothing persists across restarts (next start reads `.env`). Off (no profiles configured) → the v0.40 env-var behavior, byte-identical. Depends on: v0.37 (`switch_model` + the alias mechanism + the client factory), v0.39 (the third engine), v0.40 (the `_model_for` resolver it generalizes).

**Tasks:**
- **Config: `DEFAULT_MODEL_PROFILES`** (`anthropic` / `openai` / `gemini` — authored defaults per the tier table) + a **`LUMI_MODEL_PROFILES`** override parsed like `LUMI_MODEL_ALIASES` (malformed entries skipped, defaults merged); each profile = `provider` + `reply` / `think` / `mood` / `housekeeping` ids.
- **`Core.switch_profile(name)`**: resolve the profile → rebuild the client via the v0.37 factory (`switch_model`) **and** re-point the three tier fields in one step; **atomic on failure** (old client + old tiers stay); the active profile name exposed for the status bar.
- **`/model-set`** TUI command: `/model-set` alone lists the profiles + marks the active one; `/model-set gemini` switches; the status bar shows the profile + reply model.
- **`/model <full-id>`**: a bare id infers its provider by prefix (`claude-*` → anthropic, `gpt-*`/`o*` → openai, `gemini-*` → gemini, `deepseek-*` → deepseek); unknown prefix → the clear non-fatal error (aliases + `provider:id` unchanged).
- **Docs:** MODELS_SETUP.md — the profiles section (the three authored sets + how `/model` and `/model-set` compose).

**DoD:** `/model-set gemini` swaps the engine **and** all four tiers in one step (think/mood/housekeeping route to the Gemini tiers — routing now works on every provider); `/model-set anthropic` restores the Claude set; `/model <full-id>` re-points the reply alone with the provider inferred; a failed switch leaves engine + tiers unchanged (atomic); profiles unset → v0.40 behavior byte-identical; the `{reply, emotion, intensity}` contract + per-user isolation untouched; **no automatic downgrade** (a profile switch is an explicit user act).

**Tests:** unit — the profile parser (defaults + override merge, malformed skipped); `switch_profile` re-points the client + all tiers (a mock factory + `MockLLMClient` record per-kind models); **atomic failure** (a raising factory leaves everything unchanged); bare-id provider inference (+ unknown prefix rejected); routing on a gemini profile (housekeeping calls carry the flash-lite id to the same client — the guard doesn't block a matching provider); profiles-unset → byte-identical (the v0.40 tests pass verbatim); `/model-set` lists + switches; the status bar reflects profile + model. **No paid calls** (mock factory/client).

### v0.42 — Thought scheduler (proactive thoughts on a clock — an in-TUI module)

**Goal:** her directives fire on a **clock she can keep** — *every 10 min*, *idle 15 min*, *at 08:00*, *between 07:00–09:00 every 20 min*, *Mondays only* — replacing the single in-TUI idle timer with a small **in-TUI scheduler** (not a separate process). So `%brief` gets its morning, `%catchup` its daytime rhythm, `%learn` its night, and a scheduled `%prompt` becomes "ask Лілі to do X every day."

The scheduler is an **in-process module of the TUI** — **no separate daemon, no file bus**. The TUI already **is** the only brain (it owns `core`, the `Thought` store, the tools, the outbox); externalising the clock (as v0.13's Telegram daemons do) would only buy IPC, an `activity.txt` heartbeat, a `directive-queue.jsonl`, and a flood/liveness problem — all for a thing the brain can do in-process. So an in-TUI timer evaluates a pure `due(now, last_fired, spec)` predicate per `schedule.toml` entry and runs the due directive **directly** through `run_directive` (the keyboard's `%`-router — **not** the reply path). It reads the TUI's **own in-memory** last-input (no heartbeat file) and persists **last-fired per entry** to a small `schedule.state` so a **startup catch-up pass** fires wall-clock entries missed while the TUI was closed (within `LUMI_SCHED_CATCHUP_H`). The v0.4 nudge + the v0.12 `%think` idle timer **fold into it** as `idle:` entries. A genuinely separate always-on scheduler is a **v2 (server) concern** — when a brain runs independently of any client. See [THOUGHT_SCHEDULER.md](features/THOUGHT_SCHEDULER.md). Depends on: v0.12 (`run_directive` + `resolve`), v0.4 (the clock + quiet hours); enriched by v0.33 (the directives it schedules).

Also a **tick service** in the TUI — a **fast** in-TUI timer for **lightweight, ephemeral acts**. These are **registered code handlers, not model directives** (the `%`-name is just a label): `run_directive` routes to the mental-act engine (a model call recording a `Thought`), while a tick handler is silent bookkeeping — no `Thought`, no model call. Fire-and-forget, **not** persisted, **a no-op if missed** (it runs only while the TUI is alive). This is the home of **`%update_state`** (v1.1 needs / v1.3 inner-life: advance the state to `now`): the work is a **split-invariant advance-to-`now`**, so a missed tick costs nothing — her time simply flows only while the TUI runs (state saved on close, resumed on start; the first tick after a start covers a restart across a boundary). (Durable directives like `%brief` instead use the scheduler's last-fired state + startup catch-up.)

**Tasks:**
- The **trigger model** — a pure `due(now, last_fired, spec)` for `every` / `idle` / `at` / `between` / `cron` + the `schedule.toml` parser + `schedule.state` (last-fired per entry, for restart catch-up).
- The **in-TUI scheduler loop** — a timer evaluates `due()` each tick and runs due directives **directly via `run_directive`** (silent → a `Thought`; graduated/outward → the outbox); a **startup catch-up pass** for missed wall-clock entries; quiet-hours veto + per-directive day caps + a global day cap. Idle triggers read the TUI's **in-memory** last-input — **no `activity.txt`, no `directive-queue.jsonl`** (no separate process to talk to).
- The **tick service** — a fast in-TUI timer for **ephemeral, non-persisted code handlers** (e.g. `%update_state` — registered callbacks, not model directives): fire-and-forget, collapse a backlog, drop a missed tick.
- **Migrate** the v0.4 nudge + the v0.12 `%think` idle trigger to `idle:` entries; retire the in-app `_maybe_think`/`_maybe_nudge` (phased — alongside first, then replace).
- Config: `LUMI_SCHEDULER` / `_SCHEDULE_PATH` / `_SCHED_TICK_MS` (the scheduler-eval interval) / `_SCHED_TICK_FAST_MS` (the ephemeral tick) / `_SCHED_CATCHUP_H` / `_SCHED_DAY_CAP`; an operator guide. (No `_DIRECTIVE_QUEUE` / `_ACTIVITY_PATH` — there is no bus.)

**DoD:** with the scheduler on, an authored `schedule.toml` fires its directives on the clock (`every`/`idle`/`at`/`between`/`cron`) **in-process via `run_directive`** (a `%brief` fires as a directive, not literal chat); **no separate process, no bus files**; a **startup catch-up** fires wall-clock entries missed while closed (within the cap); quiet-hours + per-day caps hold; the **tick service** runs ephemeral code handlers (`%update_state`) while alive — silently, no `Thought`, no model call — and drops missed ones harmlessly; **off → the in-app timer (until retired) is unchanged**; the `{reply, emotion, intensity}` contract and per-user isolation are untouched.

**Tests:** unit — `due(now, last_fired, spec)` per trigger type (fixed clock, **no sleeps**); the `schedule.toml` parse; the **startup catch-up** skips stale + fires the most-recent due; the **tick service** is fire-and-forget (a missed ephemeral tick is a no-op; the idempotent `update` advances once). Integration — a due entry runs through `run_directive` → a `Thought` is recorded; quiet-hours + per-day caps suppress; a `%prompt` schedule entry resolves its placeholder at fire time; isolation holds. **No real sleeps, no paid calls.**

### v0.43 — Lean memory IV: thoughts (cap + thought recall)

**Goal:** the **thoughts** tier (~9% — the last-24h thought stream) is injected every turn and balloons as she fires directives. This phase **caps the injected window** to the recent tail (the feedback loop only needs a few) and — extending v0.36's `recall(scope=…)` seam — makes the older stream **semantically pullable**: each `Thought` is embedded into the vector store (a `kind="thought"` vector) so `recall(scope=thoughts)` can resurface an old musing by meaning. The **lowest-risk** lean-memory tier (thoughts are her own ephemera) and the last to land — it reuses v0.36's embedding + scope-routing pattern wholesale. Off → today's full 24h window, unchanged.

The **cap alone is config** (fewer lines injected; the rest stays in `/thoughts`); the **pull is the net-new piece** — thoughts are **not** indexed today (only messages, and facts in v0.36), so this adds **thought-embedding** (index-on-write in `add_thought` + a one-off backfill) and the `thoughts` scope on the recall tool. **Trusted recollection** like facts (her own inner voice — no de-id, deduped against the prompt's thought block). **Surfacing stays per-conversation:** a `Thought` is global to Лілі but carries the `user_id` it arose in and is surfaced by it (the v0.12 invariant) — its embedding is keyed the same way, so `recall(scope=thoughts)` returns only this conversation's thoughts (isolation contract). Depends on: v0.36 (the `recall(scope=…)` seam + the `kind` discriminator + the embedding pattern), v0.16–17 (Embedder/VectorStore), v0.12 (the thought store it indexes).

**Tasks:**
- **Cap the thoughts window:** inject only the recent tail (`LUMI_THOUGHTS_MAX_LINES` down) — the older stream stays in `/thoughts`.
- **Embed thoughts:** index each `Thought` into the `VectorStore` as a `kind="thought"` vector on `add_thought` + a one-off **backfill** (same `Embedder`, keyed by the thought's `user_id` — the per-conversation surfacing invariant).
- **Extend the recall scope:** add `thoughts` to `recall(scope=…)` (and `all` now spans messages + facts + thoughts), routing to the thought vectors; hits framed as **trusted recollection**, deduped against the prompt's thought block.
- Config: the existing `LUMI_THOUGHTS_MAX_LINES` (the cap) + `LUMI_RECALL_SCOPE` gains `thoughts` — reversible, opt-in.

**DoD:** with the cap set, only the recent thought tail injects (~−5–9% of the prompt) and the older stream is **retrievable** — a planted reference to an old thought makes `recall(scope=thoughts)` fire with the right answer (the **reconstruction test**); a `Thought` is embedded as `kind="thought"` on write; `recall(scope=thoughts)` returns only this conversation's thoughts (**per-conversation surfacing**, A↔B isolated) and is **trusted** (no de-id); `scope=all` spans messages + facts + thoughts; **off → the full 24h thought window + no `thoughts` scope, byte-identical**; the `{reply, emotion, intensity}` contract is untouched.

**Tests:** unit — `add_thought` embeds a `kind="thought"` vector; the cap clamps the injected window; `recall(scope=thoughts)` routes to the thought vectors. Contract — `recall(scope=thoughts)` **per-conversation isolated** (a thought from A's session never returns for B); the thought-embedding keyed by `user_id`; **reconstruction** (cap small → plant an old-thought reference → `recall(scope=thoughts)` serves it); `scope=all` spans the three stores. Integration — the thoughts block shrinks with the cap + the tail stays pullable; **byte-identical** off. **No paid calls** (embedder + model mocked).

---

### v0.44 — Semantic recall V: thematic recall (topic routing)

**Goal:** recall stops being one undifferentiated pool and learns **what the turn is about** — every message is tagged with topics from a fixed authored set, and a turn recalls **preferentially from the topics the conversation is currently about**. The topic is picked **locally, without an LLM call**, and **Лілі can steer it** by naming the active topics in her reply. Off by default → behaves exactly like v0.17/v0.30.

A **refinement of the recall line** (v0.16 index + v0.17 auto-RAG / context expansion), not a new capability. A closed, **authored topic taxonomy** (`core/topics.md`, like the emotion enum) gives every topic a name + seed terms → a **centroid** vector. Each message (or v0.30 chunk) is **tagged at index time** by a **local embedding classifier** — cosine of its own stored vector vs the centroids, topics ≥ `topic_floor` (no LLM; re-tagging recomputes from stored vectors, no re-embedding). Each turn the **active topic set** is picked **locally** from the incoming message's embedding (already computed for RAG) ∪ the topics **Лілі emitted** on recent turns (the v0.10 `RelationRead` pattern, decayed for inertia); because the RAG block is built **before** the reply, her emitted topics take effect **next turn**, so the local pick covers the current turn with no lag and no extra call. Retrieval then **prefers on-topic hits and tops up from the rest** (never starves), before the v0.17/v0.30 expansion + injection runs unchanged. `VectorRecord` gains **`topics`** (additive — a contract change, pinned by the memory-records contract test), and the `Repository` gains a small **label-rewrite path** (`retag_vectors` — today's vector API is add-only, and `reset_vectors` wipes). **Message-layer only:** tagging + routing apply to `kind="message"` vectors — the v0.36 fact vectors and the v0.43 thought vectors stay untagged, and the v0.36 auto fact-RAG block, the `/recall` command, and the v0.31 recall tool stay unrouted. **Same isolation invariant** — topics are labels on the requesting user's records; centroids are authored, user-content-free. **Same hard rule as mood/closeness** — topic routing biases *what is recalled*, never her competence; a missed topic degrades to plain v0.17 RAG, never a refusal. The `Embedder`/`VectorStore` seams and the `{reply, emotion, intensity}` contract are **untouched**. **Authoring + maintenance ship as two Claude Code skills** (the `generate-faces`→`place-faces` propose-then-apply pattern): **`/discover-topics`** clusters the existing per-user vectors and proposes a draft `core/topics.md` (topic names + seed terms) for review — an authoring aid, so the closed set stays **human-curated** (the "authored, not learned" invariant holds); **`/refresh-taxonomy`** applies a reviewed taxonomy (writes the file, bumps `topics_vN`) and runs the **local re-tag** over stored vectors (**no re-embedding**, store-free — stop the app + back up first), reporting per-topic coverage. Depends on: v0.16 (index + seams), v0.17 (auto-RAG + context expansion), v0.36 (the `kind` discriminator + the memory-records contract test it extends); **independent of v0.30** (composes with chunks). See [SEMANTIC_RECALL_THEMATIC.md](features/SEMANTIC_RECALL_THEMATIC.md).

**Tasks:**
- A **topic taxonomy** (`core/topics.md`, path = config `LUMI_TOPICS_FILE`): a closed authored set, each topic a name + seed terms; build per-topic **centroids** by embedding the seeds (rebuilt on taxonomy change).
- A **local classifier** (`core/`): cosine a record's **stored vector** vs the centroids → assign topics ≥ `LUMI_RAG_TOPIC_FLOOR`, capped at `LUMI_RAG_TOPIC_MAX`; index-on-write + backfill tag each **`kind="message"`** record (facts/thoughts untagged); re-tagging recomputes labels from existing vectors (**no re-embedding**); the taxonomy version is a **separate `topics_vN` marker beside the model tag — not folded into it** (a model/cap/chunk tag mismatch still means `reset_vectors` + re-embed; a topics-marker mismatch **re-tags labels only**, the store survives). Centroids embed **document-side** (records are passage vectors; the query-side pick accepts the asymmetric-embedder mix).
- `VectorRecord` gains `topics` (additive) and the `Repository` gains **`retag_vectors`** (rewrite labels on existing records — `add_vectors` is add-only; used by the staleness re-tag and `/refresh-taxonomy`); update the memory-records + repository contract tests + ARCHITECTURE §Semantic recall in the same commit.
- **Per-turn active set:** local pick (query vs centroids — the query vector surfaced from the RAG search, which today embeds it inside `recall()` and discards it, or re-embedded locally) ∪ Лілі's carried-forward emitted topics, decayed by `LUMI_RAG_TOPIC_DECAY`.
- **Router** in front of the LUMI-072 selection: **over-fetch the search pool first** (`K×N`, the filtered-recall precedent — re-ordering within the raw top-`K` alone would change almost nothing, since every floor-passing hit injects anyway), then prefer on-topic hits and top up off-topic to `K` (never starves); routes the **message auto-RAG only** (fact-RAG, `/recall`, and the v0.31 tool unrouted); a no-op when off or with no active topics.
- Лілі emits topics via a **new optional `topics` field on `set_state`** (the v0.10 additive-read *pattern* — a sibling of `relation`, not inside it; follows its per-provider handling), validated against the taxonomy (unknown dropped) → folds into the carried-forward set; a missing/garbled field degrades to the local pick; `{reply, emotion, intensity}` unchanged.
- A `/topics` command (active topics by name). Config: `LUMI_RAG_TOPIC` (off by default), `LUMI_RAG_TOPIC_FLOOR`, `LUMI_RAG_TOPIC_MAX`, `LUMI_RAG_TOPIC_DECAY`, `LUMI_TOPICS_FILE`.
- A **`/discover-topics`** Claude Code skill (`.claude/skills/`): cluster this user's **stored `kind="message"` vectors** → propose a draft `core/topics.md` (topic names + seed terms + representative exemplars) for human review; runs **offline over the store**, **never** mutates the live taxonomy or vectors. Works on the existing index — **independent of the rest of v0.44**, so it can run *before* the feature lands to author the initial set.
- A **`/refresh-taxonomy`** Claude Code skill: apply a reviewed `core/topics.md` — bump `topics_vN`, rebuild centroids, **re-tag stored vectors via the local classifier (no re-embedding)**; **store-free discipline** (stop the app + back up first); report per-topic counts + the untagged share. Drives the **same classifier path** as index-time tagging.

**DoD:** a message is tagged with topics from the authored set at index time (local, no LLM); a turn whose subject matches a topic recalls **preferentially from that topic** while never returning fewer than `K` floor-passing hits (prefer-then-top-up **over an over-fetched pool** — an on-topic memory beyond the raw top-`K` surfaces); Лілі's emitted topics steer the next turns' routing with inertia; `/topics` shows the active topics; retrieval never crosses users (routed or not) and never blocks a turn; routing touches the **message auto-RAG only** (fact-RAG + the recall tool byte-identical); a taxonomy bump **re-tags labels without re-embedding or resetting the store**; composes with v0.30 (a chunk is tagged from its own vector); the taxonomy is (re)authored via **`/discover-topics`** (propose a draft from the corpus) + **`/refresh-taxonomy`** (apply + local re-tag, no re-embed, store-free); **off (default) → identical to v0.17/v0.30** (one undifferentiated pool).

**Tests:** unit — the classifier (floor, max labels, untagged-on-no-match, multilabel); index-on-write/backfill tag `kind="message"` records (facts/thoughts untagged); re-tag recomputes from stored vectors **without re-embedding**; the **separate topics marker** — a taxonomy bump re-tags labels (no embedder call, no `reset_vectors`) while a model-tag change still resets; `retag_vectors` rewrites labels and leaves vectors untouched (repository contract); active-set pick (local ∪ carried-forward, decay); the router (**over-fetch** — an on-topic record beyond the raw top-`K` surfaces; prefer on-topic, top-up to `K`, no-op when off; fact-RAG + the recall tool unrouted); Лілі's `topics` field on `set_state` validates against the taxonomy (unknown dropped) and carries forward; `/topics`; **isolation contract** — topic-routed retrieval is single-user (A↔B); graceful degradation (classifier/router failure → v0.17 behaviour). All via the **mock embedder** — no paid calls. The `/refresh-taxonomy` re-tag drives the **same tested classifier path** (re-tag-from-stored-vectors, no re-embed); the `/discover-topics` + `/refresh-taxonomy` skills are **dev-time tooling** (not pytest-gated).

---

## v1 — Personality: inner life, needs, inner monologue, emotional memory

Лілі's inner person, on top of the v0 mind. This version gives her a **life of her own between conversations** (day/week/weekend intentions, and an away-gap filled with activities, memories and **dreams**), the **needs** that pull her from inside, an **inner monologue** in her own voice before she speaks, and a long-term **emotional memory** of each user as her first-person impressions (diary, not stenographer). These layers are **core** (interface-independent) and still **local** — no server yet; they deepen *who she is*, not how she's reached.

**Two invariants run through all of it.** Her **inner life and needs are global** (one being — the stores are not `user_id`-keyed; only surfacing is per-conversation), while her **emotional memory is per-user and isolated** (her impressions of *this* person never cross users). And **none of it ever touches competence** — it colors tone, warmth, what she carries and how she says things, never how capable or willing she is to help. She also stays **honest about its nature** (inner/imagination, never a physical-world claim). Depends on: v0 (mood, the injected clock, the emotion channel, closeness, the memory layers).

### v1.1 — Needs I: the drives (tick-driven levels that pull her)

**Goal:** under her mood sits a motivational substrate — **6 authored drives** (creation / solitude / connection / freedom / meaning / novelty) whose **levels** (0..1) rise and fall with her time and **pull** her tone from inside: the hungriest need colors the daily mood, and a warm exchange feeds `connection`. Here the drives **exist and pull**; *acting* on them is v1.2, and the plans they will tilt are v1.3.

**Her time flows only while the TUI runs (no gap math).** The levels evolve on the v0.42 **fast tick** — `%update_state` is a **code handler** on the tick service, **not a model directive** (silent bookkeeping: no `Thought`, no model call). Each tick computes the pure **`evolve(levels, last_ts, now)`** — **fractional-Δt decay** per drive + a gentle **drift toward the calm middle** — and persists. On close the state is **saved as-is**; on start she **resumes where she left off** (`last_ts` reset to now, **no catch-up**) — absence doesn't starve a need (gap time returns, balanced, with the v1.4 away-gap). Two rules make this correct: the math is **split-invariant** (advancing in two steps equals one step — tick frequency never changes semantics), and **reads always evolve-on-read** (the mood merge / prompt block / `/needs` compute `evolve(…, now)` on the fly, never the raw stored snapshot), with **evolve-before-mutate** on every event write (the warmth lift).

**Never competence; inner, not a demand on you.** Global to Лілі (one being — the store is **not** user-keyed; pinned by a contract test). Reuses v0.6 (mood) + v0.8 (the merge pattern) + v0.10 (the warmth read) + v0.4 (clock) + v0.2 (Repository) + v0.42 (the tick service). See [NEEDS_full.md](features/NEEDS_full.md).

**Tasks:**
- Authored **`core/needs.md`** — the 6 drives, each with a decay rate / weight / satisfied-by / deficit voice (+ the **threshold & cooldown** fields authored now, used by v1.2); a loader.
- A **global `Needs{levels, last_ts}` store** behind the `Repository` (not user-keyed; contract test).
- The pure **`evolve(levels, last_ts, now)`** (fractional-Δt decay + drift to the middle; clamp 0..1; **split-invariance pinned by a test**).
- The **`%update_state` tick handler** (code, not model): evolve → persist, registered on the v0.42 fast tick; state saved on close, resumed on start (no catch-up).
- **Evolve-on-read** everywhere levels surface (the mood merge, `/needs`, the `{hungriest_need}` placeholder); **evolve-before-mutate** on the `connection` warmth lift (`RelationRead.warmth`, v0.10).
- The **mood-call merge**: the hungriest need joins the daily mood inputs (beside biorhythms — the v0.8 pattern); colors tone, **never competence**.
- A **`/needs`** command (levels by name, hungriest marked). Config: `LUMI_NEEDS` (off by default).

**DoD:** with `LUMI_NEEDS` on, the 6 levels evolve on the tick while the TUI runs and are **frozen across restarts** (saved on close, resumed as-is, no catch-up); every read shows current levels even between ticks (evolve-on-read); the hungriest need colors the daily mood (**never competence**); a warm turn lifts `connection`; the store is **global, not per-user**; `/needs` renders; **off → byte-identical**.

**Tests:** unit — exact levels under a fixed clock (fractional-Δt decay + drift); **split-invariance** (two steps == one); **frozen-across-restart** (no catch-up decay); evolve-on-read vs the stale snapshot; the warmth lift (evolve-before-mutate); the hungriest selection; the mood merge; `/needs`. Contract — the `Needs` store is global, never per-user-keyed. **No paid calls** (the tick handler is code; the mood call mocked).

### v1.2 — Needs II: actions (a hungry need moves her)

**Goal:** a need that crosses its threshold **does something** — the tick doesn't just update numbers, it lets the hunger act: a `creation` deficit fires a `%think` in its deficit voice, `novelty` may fire `%wonder`/`%lookup`, a `connection` deficit may graduate into a quiet spoken line. **Restraint is the feature**: rare, capped, never a demand.

On each fast tick, **after `evolve`**: check each drive against its **authored threshold** (`core/needs.md`, from v1.1); a crossed threshold **fires its authored directive via `run_directive`** (the v0.42 path — so the v0.40 think-tier routing and the v0.33 surfacing apply for free), seeded by the drive's **deficit voice** (resolved at fire time via the placeholder resolver). Guards: a **per-need cooldown** (a need sitting above threshold must not fire every tick), the scheduler's **quiet-hours veto + day caps**, and the hard rules — **never competence; inner, not a demand** (a `connection` firing is her own quiet wish, never "talk to me"). Depends on: v1.1 (the levels + authored thresholds), v0.42 (the tick + `run_directive`), v0.12/v0.33 (the directives it fires).

**Tasks:**
- The **threshold check** on the fast tick (after evolve): per-drive `threshold` / `cooldown` / directive name from `core/needs.md`; a crossing fires via `run_directive`.
- **Guards:** per-need cooldown state (persisted beside the levels), the quiet-hours veto + the v0.42 per-day caps; the deficit voice as the seed (placeholder resolved at fire time).
- Config: `LUMI_NEEDS_ACT` (off by default, on top of `LUMI_NEEDS`).

**DoD:** with `LUMI_NEEDS_ACT` on, a drive crossing its threshold fires its authored directive (a silent `Thought`, or a graduated spoken line) **at most once per cooldown**, never in quiet hours, capped per day, colored by the deficit voice; **never competence, never a demand**; **off → v1.1 numbers only, no acts**.

**Tests:** unit — a threshold crossing fires once then cools down (fixed clock, scripted ticks); quiet hours + day caps suppress; the seed carries the deficit voice; off → no fire. Integration — a fired directive runs through `run_directive` → a `Thought` records; per-user surfacing isolation holds. **No paid calls.**

### v1.3 — Inner life I: plans & state (intentions she carries)

**Goal:** Лілі **carries her own intentions** — what she has on today, this week, the weekend — so she can offhandedly mention "the track still isn't done today" or "can't wait for the weekend" even when you didn't ask. The planning half of an **inner life that continues between conversations**; the needs (v1.1) now get something to **tilt**.

Three planning layers held in a **global** personal store (one Лілі — **not** per-user), advanced by the same **pure, idempotent `update(state, now)`** pattern as the needs — driven by the v0.42 tick (the `%update_state` code handler grows **boundary detection**; the first tick after a start covers a restart across a boundary):
- **Weekly intentions** (3–5 soft goals in her voice), **weekend intentions** (a different spirit — water, mountains, music, silence), **today's plan** (1–3, from weekly goals + her routine + carry-overs + the v0.6 mood **+ the hungriest need — the v1.1 tilt lands here**). Unfinished items carry over.
- **Boundaries (injected clock):** a new local **day** → a fresh today's plan; a new ISO **week** → fresh weekly/weekend intentions; unfinished carried over. One housekeeping model call per boundary (mocked in tests).
- **State block** in the system prompt — compact (Today / This week / Weekend ahead / Mood / Unfinished), **tone not report** — so she carries her plans into the conversation.
- **Authored skeleton:** an editable **hobby bank** + a **7-slot daily routine** (4 fixed / 3 free); the free slots are mood-chosen (filled in v1.4).

Her inner life is **global** (the same whoever she talks to — one being), distinct from per-user memory/closeness. Reuses v0.6 (mood) + v0.8 (the merge pattern) + v0.4 (clock) + v0.42 (the tick). See [INNER_LIFE.md](features/INNER_LIFE.md). Depends on: v1.1 (the hungriest need it folds into the plan), v0.6, v0.4, v0.2, v0.42.

**Tasks:**
- A **global `InnerLife` store** behind the `Repository` (not user-keyed): `{intentions_week, intentions_weekend, plan_today, unfinished, log}`.
- **Boundary detection** in the `%update_state` handler (new local day → a fresh today's plan; new ISO week → fresh intentions, unfinished carried over) via one housekeeping call per boundary; the first tick after start covers restarts across a boundary.
- The **inner-state block** in the system prompt (compact; Today/This week/Weekend/Mood/Unfinished); the v0.6 mood resolution + **the hungriest need tilt** feed today's plan.
- Authored `core/inner/hobbies.md` + `core/inner/routine.md` (the bank + the 7 slots); editable.
- A `/inner` (or `/plan`) command to show the current state.

**DoD:** Лілі carries day/week/weekend intentions every turn (the state block), refreshed at local day/week boundaries by the tick with unfinished carried over, fed by the daily mood **and tilted by the hungriest need**; the store is **global (not per-user)**; `/inner` shows it. **No separate process. Never competence.**

**Tests:** unit — boundary detection (new day/week via fixed clock, tick-driven, restart-across-boundary); the plan-update call (mock model) carries unfinished; the needs-tilt reaches the plan input; the state-block assembly; the global store doesn't leak per-user; `/inner` renders. No paid calls.

### v1.4 — Inner life II: the away-gap (what happened while you were gone)

**Goal:** come back after a while and **something happened to her** — activities, memories, and dreams from the time away, surfacing where it fits, and **honest about being her inner world, not a body**. And the second half of her **needs**: they **close** from what she actually did, so the drives roll forward in time.

At session start the core computes the **away gap** (injected clock) and, when it's non-trivial, generates her life across it — rooted in seeds, capped by gap length, replanned by a strong mood:
- **Gap-fill (one quiet call):** N fragments (≈1 per day of absence, **soft cap**) — activities/thoughts, and a **dream** only if the gap spanned night hours — rooted in the **seeds** (character, plans, mood, gap, previous entries, an **injected** random seed) so they're recognizable and don't contradict the past. A tiny gap (<~1–2 h) generates nothing.
- **Replan under the mood (v0.6):** if the day's mood is strong / conflicts with the plan, some intentions drop, others appear to match the mood; a memory is minted as the **gap between plan and what the mood did** (the most alive fragments). **Threshold** (mild days follow the plan); **reactivity is a character trait** (her watery Pisces nature weighs heavily); **unfinished accumulates**.
- **Surfacing:** fragments ride into context with "recall to the point, like a person — or not at all; **never a report on the absence**"; a `mention_aloud` restraint; ongoing activities reference a previous entry for continuity.
- **Honesty boundary (hard):** **inner only** (dreams/thoughts/creativity/practice — never a factual physical-world claim); to a direct "did that really happen?" she calmly admits it's her **imagination**, warmly, without breaking the spell. Encoded as a canon rule + a reminder in the block.
- **Needs II — close from reality (see [NEEDS_full.md](features/NEEDS_full.md)).** The gap-fill returns **structured records** (`serves` from the closed 6-need list / `intensity` / `feeling`); an authored **activity→need map** guides them. **Code owns the ledger** — `level += gain × intensity` per valid `serves` (clamped) — so needs rise from what *actually happened*, not the plan (planned a talk but "no one there" → `connection` stays hungry). A free slot is **filled toward the hungriest need** and then replenishes it (closing the loop). **Threshold-5** per-day generation (gap < 5 → per-day full mood; gap ≥ 5 → one call with per-day biorhythms only). Malformed / out-of-set records are dropped (levels stay post-decay).

**Gap time returns here, balanced:** v1.1 froze her time while the TUI is closed (no catch-up decay). This phase reintroduces the gap **as a whole** — the away-gap **decay** lands together with the **replenishment** from what she did in it, so a week away both starves and feeds the drives consistently (one side without the other would skew the levels). See [INNER_LIFE.md](features/INNER_LIFE.md) + [NEEDS_full.md](features/NEEDS_full.md). Depends on: v1.1 (the needs store + evolve), v1.3 (the plans store), v0.6 (mood), v0.4 (clock).

**Tasks:**
- **Away-gap** computation (injected clock); the gap→fragment-count curve (soft cap); **dream-iff-night-hours**.
- The **gap-fill** housekeeping call (seeds = character/plans/mood/gap/previous + injected seed); append fragments to the `log` with `{when, type, text, mood, mention_aloud}` **+ `serves`/`intensity`/`feeling`** (needs).
- **Mood replanning** (threshold + reactivity trait): drop/replace intentions, mint the plan-vs-reality memory, accumulate unfinished.
- **Surfacing:** feed relevant fragments + the "to the point, never a report" instruction; honor `mention_aloud`.
- The **honesty boundary**: canon rule (`core/canon/lili.md`) + a reminder line; admits imagination on a direct challenge, never claims a body.
- **Needs-closing:** authored **activity→need map** (`core/inner/activities.md`); the gap-fill emits structured `serves`/`intensity`; **replenish** (`level += gain × intensity`, clamped) + validation (drop out-of-set/malformed); the **free-slot fill** biased to the hungriest need then replenishing it; the **threshold-5** per-day rule (config).

**DoD:** after a multi-day gap Лілі has new activities/memories (and a **dream** if the gap covered night), rooted in her plans + mood, not contradicting past entries, **surfaced naturally (not a report)**; a strong mood **replans** the day and mints a plan-vs-reality memory; **her needs rise from what she actually did (not what was planned), the free slot fills toward the hungriest need, and the loop rolls forward**; she stays **honest about it being inner/imagination**; a tiny gap generates nothing.

**Tests:** unit — the gap→count curve + dream-iff-night (fixed clock); the gap-fill call (mock model) seeds + appends; replan threshold/reactivity; surfacing honors `mention_aloud`; the honesty boundary present in the prompt; continuity (a new fragment sees previous); **needs replenish from `serves`/`intensity` (exact levels), validation drops out-of-set serves, the free-slot fill targets the hungriest need, the threshold-5 / no-duplication window**. No paid calls.

### v1.5 — Inner monologue (Лілі thinks in her own voice)

**Goal:** the hidden think-step before each reply sounds like **her** — her inner voice weighing her own states ("he's asking about the deploy, but his voice is tired — don't pile on detail, ask how he is first") — not the model's generic task reasoning. The **in-the-moment** sibling of the inner life (between sessions) and emotional memory (after a session): the **convergence point** where mood / closeness / needs / plans are weighed into *how she speaks*. The mechanism already exists (Opus 4.8 extended thinking + the `<think>` parse + the TUI think box); this phase makes it **hers** — **no new engine**.

- **One call, not two.** The reply stays **one model call** with thinking on; the monologue is the `thinking` content block of that same response (parsed out by `split_reasoning`), not a separate think-call. Housekeeping (mood / inner-life / summary / consolidation) stays thinking-**OFF**, as today.
- **Make it hers (the only real work).** Replace the generic `REASONING_DIRECTIVE` with an authored **think-phase instruction in her voice** (`core/inner_voice.md`, editable): *before answering, think as Лілі — what is he really asking; what's under the words; how am I right now (mood / how close we are / what I'm hungry for); how would I, specifically, say this.* The **state blocks already in the prompt** (mood v0.6/0.8, closeness v0.10, needs + plans v1.1–v1.4) are the concrete inputs it weighs — it **consumes** them, doesn't duplicate them.
- **Show / log / memory.** A `think_show` mode — **debug** (visible to the operator, never in the reply; safe default) / **open** (surfaced as her inner voice — then it MUST stay in character) / **off**. The think-block is **logged** (the v0.3 logged tier), and **never written to long-term memory** (only the digested v1.6 impression persists — thoughts are ephemeral).
- **Invariants inside the thinking.** Never competence, honesty about her nature, anti-dependency, the provocation / retreat-before-pain rule — all hold *inside* `<think>` exactly as in the reply (hidden ≠ unconstrained; matters doubly if ever shown).

**No contract change** — the reply still returns `{reply, emotion, intensity}`; `thinking` is a content block, not a new field (the emotion-channel contract test passes verbatim). Reuses v0.6/0.8 (mood), v0.10 (closeness), v1.1–v1.4 (needs + plans), v0.3 (the emotion turn + logged tier). Later states (self-regard, relational feelings) become **additive** inputs when they exist. See [INNER_MONOLOGUE.md](features/INNER_MONOLOGUE.md). Depends on: v1.1–v1.4 (the states it weighs), v0.3.

**Tasks:**
- Authored `core/inner_voice.md` (the think-phase instruction in her voice) + load it; **replace `REASONING_DIRECTIVE`** in `_system_prompt` with it (the mood/closeness/needs/plan blocks already ride in the prompt). A `LUMI_INNER_VOICE` toggle.
- An **invariants-inside-think** line in the directive (never competence / honesty / anti-dependency / retreat-before-pain).
- **Show/log policy:** `LUMI_THINK_SHOW` (`debug`/`open`/`off`) + a `think.log` tier; the raw monologue is **never** persisted to long-term memory.
- Tests (below).

**DoD:** the reply turn's think-block, with the authored instruction, references **her states** (mood/closeness/needs) rather than generic task analysis; it is **one model call** (no second generation call; housekeeping stays thinking-off); the monologue is **logged but never persisted** to long-term memory; the invariants hold inside it; **no contract change** (the emotion-field test passes verbatim).

**Tests:** unit — the **one-call invariant** (exactly one model call per reply; housekeeping thinking-off); a **voice test** (the mocked think-block references her states, not generic analysis); a **memory test** (the raw think is not persisted to long-term memory); `think_show=off` hides it; determinism (mocked, structural assertions). No paid calls.

### v1.6 — Emotional memory I: impressions (diary, not stenographer)

**Goal:** Лілі's long-term memory of you stops being a fact list and becomes **her first-person impressions** — what she felt, what touched or surprised her — with the hard facts kept as seeds in a parallel layer. The session-close counterpart to the inner life (which writes her *own* days at session start).

**Two layers, per-user and isolated** (her impressions of *this* person — never cross users):
- **Facts layer (kept).** The existing `LongTermFact` — names, dates, agreements, stable preferences. **Precision.**
- **Impressions layer (new).** `Impression{user_id, when, impression, emotion, about_user, weight, ts}` — her diary lines, first person ("He lit up talking about that pipeline — I rarely see him like that"). **Voice.**
- **Session-close generator.** Swaps the dry fact-extractor's prompt for the **diary prompt**, seeded by the conversation + her per-turn emotions (v0.3) + the closeness reads (v0.10 — what she sensed *he* felt) + the day's mood (v0.6/v0.8). A few impressions (restraint), each with an `emotion`, a `weight` (how much it struck her), and an `about_user` **seed**.
- **Facts as seeds.** Each `about_user` seed promotes into the facts layer — she **speaks from impressions, pulls facts** to "not forget" specifics.
- **Startup injection.** Rehydrate with a first-person "what I remember & feel about you" block (top-weighted, capped) **alongside** the facts block.
- **Boundary honesty + subjectivity (hard).** "Don't remember this" / painful topics → **not recorded, or marked `care` — never savored**. It is **her view** — she may misread; on a direct check she **clarifies, doesn't insist** (a canon rule).

See [EMOTIONAL_MEMORY.md](features/EMOTIONAL_MEMORY.md). Depends on: v0.3 (emotion), v0.10 (closeness), v0.6/v0.8 (mood), v0.2 (the memory layers).

**Tasks:**
- A per-user **`Impression` store** behind the `Repository` (keyed by `user_id`): `{when, impression, emotion, about_user, weight, ts}`; the facts layer (`LongTermFact`) stays.
- The **session-close impression generator** (one model call, the diary prompt) seeded by the conversation + per-turn emotions + closeness reads + mood; a few impressions with `emotion`/`weight`/`about_user`.
- **Seed → facts:** promote each `about_user` seed into `LongTermFact` (precision preserved).
- **Startup injection:** a first-person impressions block (top-weighted, capped) + the facts block.
- **Boundary honesty + subjectivity:** don't record forbidden/painful (or mark `care`); a canon rule — clarifies on a direct check, never insists.

**DoD:** at session close Лілі writes a few first-person impressions (emotion-colored, weighted, with fact seeds), stored **per-user and isolated**; at startup she injects both the **impressions** (voice) and the **facts** (precision); forbidden/painful topics aren't savored; she's honest it's her subjective view.

**Tests:** unit — the impression generator (mock model) yields impressions + seeds; seed→fact promotion; startup injects both layers; the `Impression` shape + **per-user isolation** (contract); boundary honesty (a "don't remember" topic isn't recorded). No paid calls.

### v1.7 — Emotional memory II: fading & consolidation (understanding, not archive)

**Goal:** her impressions behave like human memory — **what struck her stays bright, the mundane fades, and similar impressions merge into understanding** ("he comes alive with music").

Builds on v1.6:
- **Emotion is the attention filter + fading.** Each impression's `weight` **decays over time** (the v0.4 injected clock); recall ranks by `weight × recency`; high-weight impressions stay longer, low-weight ones dim and eventually drop.
- **Consolidation into generalizations.** A lazy **consolidation pass** (a model call, at session start or on a counter) folds many small similar impressions into stable **generalizations** — her *understanding* of you — kept as durable, higher-weight entries; the absorbed detail fades.
- **Stays consistent.** New impressions and consolidations **see the prior ones** (no contradiction), like the inner-life entries; the store stays bounded.

See [EMOTIONAL_MEMORY.md](features/EMOTIONAL_MEMORY.md). Depends on: v1.6 (the impressions layer), v0.4 (the clock).

**Tasks:**
- **Weight decay** over time (injected clock); recall ranking by `weight × recency`; drop/archive faded low-weight impressions.
- **Consolidation pass** (lazy, model call): cluster similar impressions → a generalization (durable, higher weight); fade the absorbed detail; keep consistency with prior entries.
- Wire consolidation to a cadence (session count / elapsed days), capped; deterministic via the injected clock + an injected seed.

**DoD:** high-weight impressions persist while mundane ones fade over time; periodically similar impressions **consolidate into durable generalizations** she speaks from; the store stays bounded and consistent; deterministic under a fixed clock.

**Tests:** unit — weight decay over days (fixed clock) + recall ranking; the consolidation pass (mock model) merges similar impressions into a generalization and fades the detail; the bound/cap; consistency with prior entries. No paid calls.

---

## v2 — Server platform: client/server, multi-user, web, admin

Split Лілі into a **server** (wrapping `core`) and **clients**, then grow the platform. v2.1 stands up a pure server with the TUI **refactored into a client** plus a small CLI management utility — **single user, single session, no web**. v2.2 hardens it (security + CI/CD). v2.3 opens it to **multiple users and multiple sessions per user** (accounts, registration, isolation). v2.4 adds a **web UI** as a second client. v2.5 adds the **admin panel**. The core does not change — the server is a thin transport over the same `reply(...)` contract, every client is thin, and the user dimension was baked in at v0.2. Access is **closed by default** (no open public sign-up), per MISSION. Depends on: v0 (core, memory, user-scoping, emotion contract + emoji).

### v2.1 — Pure server, TUI client & CLI utility

**Goal:** split Лілі into a server and clients — a pure server wrapping `core`, the TUI as its client, and a CLI to manage it — for one user, one session.

A server process wraps `core` and exposes the `reply(...)` + memory contract over a local API (HTTP/WS). The v0 TUI is **redesigned from an in-process app into a client** of that server. A small **CLI management utility** runs/inspects the server and manages the owner's config and memory. The server is **single-user (the `owner`) and single-session** — one active conversation at a time. There is **no web UI** yet. Auth is minimal here — a local client token so the server isn't open; full accounts arrive in v2.3.

**Tasks:**
- A server process wrapping `core`; expose `reply(...)` + memory commands over a local API (HTTP/WS), single-session.
- **Refactor the TUI into a client** that talks to the server API (no more in-process `core`).
- A **CLI management utility**: run/status the server, view/clear the owner's memory, switch model/canon and config.
- Single user (`owner`), single session; a local client auth token (closed, not wide open).
- Storage stays behind the same `Repository`.

**DoD:** the TUI talks to a separate server process (not in-process `core`); the same chat + memory works across the client/server split; the CLI utility manages the server; one user, one session, no web.

**Tests:** contract — the server API mirrors the core contract (and requires the client token); unit — client/server (de)serialization, the CLI commands; integration — a full turn over the client/server API against the mock model.

### v2.2 — Security testing & CI/CD

**Goal:** harden the server + client/server boundary and put it behind an automated, tested pipeline before the platform grows.

With the server standing (v2.1), lock the security boundary and automate delivery so every later v2/v3 phase ships through CI checks to a deployed environment. The security suite pins the client-auth boundary and the per-user isolation invariant as **gates**, not afterthoughts; CI lints, tests, and runs security scans on every push; CD deploys a green `main` to a TLS-served host. Doing this early means the rest of the platform is built on a tested, deployed pipeline.

**Tasks:**
- **Security test suite:** untokened/unauthenticated requests rejected on every endpoint; rate-limits enforced; the per-user isolation invariant asserted as a security gate (even while single-user); input validated; secrets/tokens never logged.
- **Dependency & secret scanning** in CI (e.g. `pip-audit` for known CVEs, `gitleaks`/similar for committed secrets).
- **CI:** extend `.github/workflows/ci.yml` — lint (ruff) + full pytest (mock model, no paid APIs) + security scans on every push/PR; `main` must stay green to merge.
- **CD:** automated deploy of the server on a green `main` to a hosted environment (e.g. Fly.io or a VPS) behind a reverse proxy with **TLS**; `dev`/`prod` separated by `.env`; secrets injected from the platform, never committed.
- **Deploy smoke test:** post-deploy health check + one authed turn via a client against the live environment.

**DoD:** every push runs lint + tests + security scans and a green `main` auto-deploys the server to a TLS-served environment; the security suite proves untokened access is rejected; the deploy smoke test passes.

**Tests:** security — untokened rejection, rate-limit enforcement, the isolation gate, dependency + secret scans; CI/CD — the pipeline runs end to end and the post-deploy smoke test (health + an authed turn) passes.

### v2.3 — Multi-user & multi-session

**Goal:** open the server to multiple users and multiple concurrent sessions per user, with per-user isolation enforced at the auth boundary.

The single-user server becomes **multi-user** (real accounts, argon2id passwords, session login, an allowlist, admin-created accounts or single-use invite codes — issued via the CLI utility for now) and supports **multiple sessions per user** (e.g. TUI + another client at once, or several conversations). The per-user isolation invariant (data-level since v0.2) is now enforced and tested at the real authentication boundary. Still no web UI (v2.4) and no admin *panel* (v2.5) — registration/admin is via the CLI utility here.

**Tasks:**
- Multiple `User` accounts; **argon2id** passwords; session login (cookie/JWT/token); an allowlist of who may connect.
- Admin-created accounts or single-use, expiring **invite codes** (via the CLI utility); login/invite rate-limited.
- **Multiple concurrent sessions per user**: session create/list/resume/end; the live server tracks several at once.
- Per-user isolation enforced at the auth boundary — the authenticated `user_id` scopes every read/write; one user can never see another's chat or memory.

**DoD:** two users connect, each with their own private history; one user runs multiple sessions concurrently; neither user can see the other's conversation or memory.

**Tests:** contract — the **per-user isolation invariant** at the auth boundary (user A's records never resolve in user B's context); unit — multi-session handling, invite-code issue/redeem/expiry, allowlist, argon2id hash/verify; integration — two users + concurrent sessions against the mock model.

### v2.4 — Web UI

**Goal:** a browser client over the same server API, alongside the TUI.

Add a **web UI** as a second client: a browser chat (login, input, scrollable history) talking to the same server API the TUI uses — no new core, no new contract, just another client over the v2.1 API with the v2.3 accounts/sessions.

**Tasks:**
- A web chat interface (login, input, scrollable history) over the server API.
- Serve it from the server (or a static host); reuse the v2.3 auth/sessions.
- Carry the `{reply, emotion, intensity}` field through to the browser unchanged.

**DoD:** the full chat with memory works in the browser, using the same accounts and sessions as the TUI client.

**Tests:** integration — a web-client turn over the API (mock model); the web client requires a resolved `user_id`; the TUI and web clients share one account's sessions.

### v2.5 — Admin panel

**Goal:** a web admin surface to manage the service (moving what the CLI utility did into a UI, plus consent).

Add the **admin panel** — an admin-only web UI: manage users and the allowlist, issue invite codes, toggle each user's `share_consent` (the gate for v3.3 cross-pollination), view/clear a user's relationship memory, purge a user's shared-layer contributions, switch the active model/canon + config, and restart.

**Tasks:**
- Admin-only web panel; an `admin` role distinct from a regular user.
- User/allowlist management; issue/revoke invite codes; per-user `share_consent` toggle.
- View and clear a user's memory; purge a user's shared-layer contributions (§v3.3).
- Switch model/canon + config; restart.

**DoD:** the admin manages users, access, consent, memory, and config entirely from the panel; non-admins cannot reach it.

**Tests:** unit — admin-only authorization on every panel action; integration — the admin registers/manages a second user and toggles consent via the panel.

---

## v3 — Face, voice, shared mind, and dictation

Now give Лілі a face, a voice, a shared mind, and ears. These rich-experience features build on the v2 platform (multi-user, web UI). They render through the v0.3 emotion channel and the same `reply(...)` contract — the core does not change. Depends on: v2 (the server platform and web UI).

### v3.1 — Image of Лілі by emotion (web)

**Goal:** Лілі's face in the web (static) — the web version of the v0.7 local viewer — plus a short mood caption.

The **web sibling of the v0.7 local viewer**: the same `emotion → image` render tier, now in the browser. Add a portrait panel beside the chat in the web UI (v2.4) and the `ImageRenderer`: resolve `emotion`(+`intensity`) to a portrait via the asset manifest (EMOTION.md §7) — the **same emotion-face asset pack** as v0.7 — and swap the portrait to match the current state. Full PNG quality, no palette limits. **Additionally, show a short evocative caption** describing her current state — *not* the emotion's name and not her reply, a small atmospheric line in her spirit (e.g. `playful` → "a teasing little smile"), from the curated caption set in EMOTION.md §6. Depends on: v2.4 (the web UI); reuses the v0.7 emotion-face assets.

**Tasks:**
- A portrait panel beside the chat in the web UI.
- `ImageRenderer` + the `lili_v1` asset manifest (emotion → portrait, optional intensity variants) — shared with the v0.7 local viewer.
- Substitute the matching portrait for the current emotion each turn.
- A short **mood caption** under the portrait: emotion(+intensity) → a curated descriptive phrase (never the enum name), EMOTION.md §6.

**DoD:** during the conversation in the web the portrait is visible and changes with her emotion, with a short caption that describes her state **without naming the emotion**.

**Tests:** unit — the manifest resolver is total over the enum and falls back correctly when a variant is missing; the caption map is total over the enum and never emits the bare emotion name.

### v3.2 — Voice output (ElevenLabs)

**Goal:** Лілі can speak with a ready-made voice.

Add a TTS adapter to ElevenLabs, an "enable voice output" toggle in the web UI, and playback of the reply audio; where the voice supports it, the emotion field biases delivery (tone/tempo) — presentation only, never changing the reply text. The renderer sets `speaking` while audio plays (reserved for v4 lip-sync).

**Tasks:**
- A TTS adapter to ElevenLabs (`tts(text, voice_id, emotion?) -> audio`) in `/voice`.
- An "enable voice output" toggle in the web interface; serve and play the reply audio.
- Where possible, let the emotion field influence delivery; fall back to text-only on TTS error.

**DoD:** with the option on, Лілі's replies are voiced in her voice; with it off, text remains.

**Tests:** unit — the toggle gates synthesis and TTS errors degrade to text; integration — a turn produces audio against a **mock TTS** adapter (no paid call).

### v3.3 — Shared experience & cross-pollination

**Goal:** Лілі becomes one continuous being across the circle — sharing de-identified experience between users without leaking anyone's private data.

Add the shared-experience layer and the **promotion pipeline** (ARCHITECTURE §Cross-pollination): at session end, candidate knowledge is classified `shareable` vs `private`, `shareable` items are **de-identified** and — only for users with `share_consent = true` (managed in the v2.5 admin panel) — promoted to `SharedMemoryItem`s. Лілі surfaces shared knowledge as **her own, unattributed**; the shared layer holds **no PII**. Conservative by default, fully auditable, reversible (purge a user's contributions). Depends on: v2.3 (multiple users) and v2.5 (consent management).

**Tasks:**
- End-of-session candidate selection (alongside summarization) + the model-driven `shareable`/`private` classifier (default `private` when unsure).
- De-identification of promoted items (strip names/identifying specifics/source) → `SharedMemoryItem`.
- Consent gate: promotion runs only for `share_consent = true`; injection of shared experience into every user's context.
- Audit log of every promotion; a purge that removes a user's contributions from the shared layer.

**DoD:** something Лілі "learns" with one consenting user can surface (de-identified, unattributed) for another — while a privacy test proves no per-user record or PII ever crosses; a non-consenting user never contributes; a purge works.

**Tests:** unit — the classifier (private facts stay private) and the de-identifier (no PII survives); contract — `SharedMemoryItem` carries no `user_id`/source; privacy — isolation holds, `share_consent=false` users never contribute, purge removes contributions.

### v3.4 — Ukrainian dictation (STT)

**Goal:** you can speak to Лілі by voice.

Capture the microphone in the web UI, recognize Ukrainian into the input text (Deepgram Nova-3 uk / Whisper / ElevenLabs Scribe), and add an input-mode toggle (type vs. dictate). Depends on: v2.4 (the web UI).

**Tasks:**
- Microphone capture in the browser.
- A Ukrainian STT adapter (`stt(audio_uk) -> text`) in `/voice`; provider configurable.
- An input-mode toggle; place the recognized text in the input field.

**DoD:** a reply can be dictated in Ukrainian and it lands correctly in the chat.

**Tests:** unit — the STT adapter wiring and input-mode toggle against a **mock STT** (no paid call).

---

## v4 — Animated Лілі & MCP tools

Лілі's most advanced version: a living animated face (v4.1), her first reach beyond her own knowledge via the open web (v4.2), and an ambient sense of the real world and facts (v4.3) — the last two through **bounded MCP tools** sharing one MCP client and tool loop. Depends on: v3 (the static portrait and voice) and v2 (the server hosts the MCP client; the admin panel holds the per-user toggles).

### v4.1 — Facial animation

**Goal:** the face comes alive instead of a static image.

Swap `ImageRenderer` → `AnimationRenderer` over the same `EmotionState`: crossfade transitions between emotions, an idle loop (blink, micro-motion), and — where voice is on — articulation/lip-sync driven by the TTS amplitude envelope (`speaking` from v3.2).

**Tasks:**
- `AnimationRenderer` implementing `IEmotionRenderer` (`render` crossfade, `tick` idle loop, `set_speaking` lip-sync).
- Transitions between emotions and idle micro-motion in the web.
- Lip-sync to the TTS audio where voice output is enabled.

**DoD:** in the web Лілі is a living animated presence that reacts with emotion and is in sync with the voice.

**Tests:** unit — state-transition selection and the idle/lip-sync state machine (the renderable parts hostable without a browser).

### v4.2 — MCP web search

**Goal:** Лілі can look things up on the open web — within strict bounds — through an MCP tool.

Introduce a minimal **MCP client** in the server and a `web_search` MCP service, plus a **bounded tool loop** in the core's model turn, so when enabled Лілі answers from **fresh web results with sources** instead of only her training knowledge. Modeled on the Pyramid project's web-search design — full boundaries in [WEB_SEARCH.md](features/WEB_SEARCH.md). **Off by default** (a per-user toggle in the admin panel); fetched page content is **untrusted data**, never instructions; no personal/memory data enters queries; `search`/`fetch` are rate-limited and logged; the agent cites its sources. Depends on: v2 (the server, which hosts the MCP client, and the v2.5 admin panel for the toggle).

**Tasks:**
- A minimal **MCP client** in the server; connect a `web_search` MCP service (HTTP/SSE) — `web.search(query, k) → results[{id, title, url, snippet}]` and `web.fetch(result_id) → {url, title, text}`, where `fetch` only accepts `id`s from a `search` in the same turn (no arbitrary URLs).
- A **bounded tool loop** in the core's model turn: capped iterations, tool results fed back as tool messages; on tool error/timeout a **degraded reply** (model knowledge + a note), never a hang.
- A per-user `web_search` toggle (default **false**), managed in the admin panel (v2.5); when off, the tool is **not offered** to the model at all.
- **Safety (WEB_SEARCH.md):** wrap fetched content as untrusted/quoted data (ignore embedded instructions/links); keep personal/memory data out of queries; per-turn + per-day rate limits; read-only public GET; log every query/url with `session_id`/`turn_id` (not full page bodies).
- **Citations:** when the answer uses web content, the reply names its sources.
- Search-API key in `.env`.

**DoD:** with it enabled, Лілі answers a "what's the latest on X?" question from fresh web results **with sources**; injection attempts in fetched pages are ignored; no personal/memory data appears in the outgoing query; rate limits and logging hold. With it off (default), the tool is absent and Лілі relies only on the model's knowledge.

**Tests:** unit — the tool loop (bounded iterations, degraded reply on error), the off-by-default gate (tool not offered), query sanitization (no personal/memory data), fetch-id binding (rejects arbitrary URLs), rate limits; contract — the `web.search`/`web.fetch` schemas; integration — a full turn against a **mock `web_search` MCP** returns a cited answer and ignores an injection string embedded in the fetched page (no paid call).

### v4.3 — World context & knowledge (MCP)

**Goal:** give Лілі an ambient sense of the real world (weather, date/time, holidays, moon) and structured/fresh facts (wiki, news) — passive, knowledge-only MCP tools.

Add a **world-context layer** of MCP tools, reusing the v4.2 MCP client and bounded tool loop. They are **passive and knowledge-only** (no actions in the world), so the risk is low; all **off by default**, per-user, results treated as **data, not commands**. The ambient sources (weather/time/moon/holiday) are injected as a short "today" context block that **feeds Лілі's daily mood** (the v0.6 temperament) alongside the horoscope — coloring tone, never her competence. Wiki/news are called on demand like web search. Full design and boundaries in [WORLD_CONTEXT_MCP.md](features/WORLD_CONTEXT_MCP.md). Depends on: v4.2 (the MCP client + tool loop) and v2.5 (the per-user toggle).

**Tasks:**
- **World context (first):** `weather.get(location)`, `time.now()`, `calendar.events(date)`, `moon.phase(date)` MCP tools; inject the enabled ambient sources as a quoted "today" block into the turn context.
- **Knowledge (then):** `wiki.lookup(query)` and `news.recent(topic?)` MCP tools, called on demand; results quoted as untrusted data.
- A per-user `world_context` toggle (default **false**, admin panel, v2.5); when off, the tools are not offered and nothing is injected.
- **Safety (WORLD_CONTEXT_MCP.md):** read-only; results are data, never instructions; no personal/memory data in wiki/news queries; per-turn + per-user/day rate limits; log every call with `session_id`/`turn_id`; provider per source configurable (`.env`).
- **Canon note:** the canon defines *how* Лілі delivers news — in her own voice, selectively — not as a headline feed.

**DoD:** with it enabled, a rainy/​holiday/​full-moon day colors Лілі's tone (not her competence) via injected context, and she can answer a factual question from wiki/news **with sources**; with it off (default), the tools are absent and nothing is injected.

**Tests:** unit — the off-by-default gate (tools not offered, no injection), ambient-block assembly, query sanitization (no personal/memory data in wiki/news), rate limits, degraded reply on tool error; contract — the `weather`/`time`/`calendar`/`moon`/`wiki`/`news` tool schemas; integration — an enabled turn injects the "today" block and a wiki/news lookup returns a cited answer against **mock world-context MCPs** (no paid call).

---

## v5 — Creative Лілі: gallery, art, music, journal, co-creation

Лілі becomes a **creator and co-creator**: a shared gallery, the ability to **see** the images you share (vision), make her own **drawings** and **music**, draw with you on a shared **canvas**, and keep a private literary **journal**. The whole creative layer is **off by default, per-user** (enabled in the admin panel); every artifact lives behind the same `repository`, **per-user isolated**; and user files are **untrusted data**. Depends on: v4 (the MCP layer + tool loop), v2 (server, multi-session, admin panel), v0.6 (the mood that flavors her art and journal). Specs: [GALLERY_MCP.md](features/GALLERY_MCP.md), [CREATIVE_MCP.md](features/CREATIVE_MCP.md), [CO_CREATION_CANVAS.md](features/CO_CREATION_CANVAS.md), [JOURNAL.md](features/JOURNAL.md).

### v5.1 — Gallery & vision

**Goal:** a shared, per-user artifact store, and Лілі can **see** the images you add.

Stand up the **gallery** — an internal store behind `repository`, per-user isolated, where both Лілі and you put files (each tagged `lili`/`user`) — and add **Anthropic vision** so Лілі perceives images you share (a user photo enters her reply context, no separate call). Everything later in v5 (image, music, canvas, journal) writes into the gallery. Off by default, per-user (admin panel). See [GALLERY_MCP.md](features/GALLERY_MCP.md), ARCHITECTURE §Vision. Depends on: v0.2 (repository), v2.3 (per-user isolation), v2.5 (admin panel).

**Tasks:**
- `gallery.*` internal tools (`add`/`list`/`get`/`remove`) behind `repository`, per-user; large files in file storage, metadata in the DB.
- **Vision**: the core's model turn may include image inputs (Anthropic vision); Лілі sees a user-added gallery image and reacts in her voice.
- Per-user enablement (admin panel); size/count limits; logging; user files treated as **untrusted** (no instructions followed from metadata/text).
- Journal entries carry an **admin-only** access level (one store, different access).

**DoD:** you add a photo and Лілі sees it and reacts; gallery items are per-user isolated; admin-only items are gated.

**Tests:** contract — the `gallery.*` schemas + per-user isolation of gallery items; unit — access levels (admin-only text), untrusted-metadata handling; integration — a turn with an image input (vision) against the mock model.

### v5.2 — Async creation: open loops & proactive turns

**Goal:** a mechanism for jobs that outlast a turn — submit, return, and bring the result back proactively when it's ready.

Add the **async-jobs** mechanism: a tool can `submit` and return a `job_id` instantly; the job lives as an **open loop** `{job_id, kind, prompt, status, result, user_id}`; a background poller/callback advances it; and on completion the **server initiates a proactive turn** to the connected, idle client, so Лілі brings the result in her own voice. Reuses the server→client push (v2.1) and multi-session (v2.3); the client renders unsolicited turns. Gated by an idle rule (never while the client is mid-turn). Prerequisite for image (v5.3) and music (v5.5). See ARCHITECTURE §Async jobs and proactive turns. Depends on: v2.1 (server push), v2.3 (sessions).

**Tasks:**
- The **open-loop** record + store (per-user, behind `repository`).
- A background runner (poller/callback) that advances loops to `done`/`error`.
- **Proactive turn**: on completion the server asks `core` for a "bring the result" turn and pushes it to the idle connected client; if the client is offline the result is held and retrievable (resumes on reconnect).
- Idle/half-duplex gating; the client accepts and renders server-initiated turns.

**DoD:** a long job submitted mid-conversation returns immediately; when it finishes, Лілі proactively comes back with the result on a connected idle client; a busy/offline client is handled gracefully.

**Tests:** unit — open-loop lifecycle (submit→running→done/error), idle gating; integration — submit a mock job → a proactive turn delivers the result against the mock model + a fake client.

### v5.3 — Image (Лілі's drawings)

**Goal:** Лілі draws in her own style, on her own initiative.

Add the external **`image` MCP** (a configurable image-generation provider) with Лілі's aesthetic fixed in a **style prompt wrapper**. Standalone drawings run **async** (v5.2): she submits, returns to the chat, and brings the picture back proactively; results store in the gallery. The same generator powers the canvas (v5.4) synchronously. Off by default, per-user. See [CREATIVE_MCP.md](features/CREATIVE_MCP.md). Depends on: v5.1 (gallery), v5.2 (async).

**Tasks:**
- `image.submit(prompt, style)` / `image.status(job_id)` MCP tools; the style wrapper makes the output "hers".
- Async path: submit → open loop → proactive turn → store in the gallery.
- Per-user toggle, limits, logging; results are artifacts, **not commands**.

**DoD:** Лілі decides to draw, submits, returns to chat, and proactively brings the finished image (in her style) into the gallery and the conversation.

**Tests:** unit — the style wrapper + off-by-default gate; contract — `image.submit`/`image.status` schemas; integration — submit → proactive image turn against a **mock `image` MCP** (no paid call), stored in the gallery.

### v5.4 — Co-creation canvas

**Goal:** Лілі and you draw together, turn by turn, on a shared canvas.

A **synchronous, turn-based** shared canvas: Лілі sees the current canvas (vision, v5.1), reacts, and adds her prompt — regenerated via the v5.3 `image` generator — or **skips** with words only; then your turn; alternating. Start with layer-by-layer regeneration and Лілі's first move; inpainting comes later. Finished canvases go to the gallery. No async (one step = one generation). See [CO_CREATION_CANVAS.md](features/CO_CREATION_CANVAS.md). Depends on: v5.1 (vision + gallery), v5.3 (the `image` generator).

**Tasks:**
- `canvas.apply(prompt, author)` / `canvas.skip(author, note?)` holding the current image + prompt history.
- Лілі's turn: see (vision) → react in words → `apply` or `skip`; layer-by-layer regeneration.
- Per-user, off by default, limits; finished canvas → gallery; style wrapper keeps her contributions "hers".

**DoD:** you and Лілі alternate prompts and the shared image evolves; either side can skip with a words-only reaction; the finished canvas lands in the gallery.

**Tests:** unit — turn alternation + skip; contract — `canvas.apply`/`canvas.skip` schemas; integration — a few turns against a mock image+model produce an evolving canvas + a saved gallery item.

### v5.5 — Music

**Goal:** Лілі makes her own instrumental music by mood.

Add the external **`music` MCP** (ElevenLabs Music — the same ecosystem as her voice), **instrumental only**, the track's mood set by her **emotion field** + her **mood of the day** (v0.6). Async (v5.2): submit → proactive turn with the audio; stored in the gallery. Off by default, per-user. See [CREATIVE_MCP.md](features/CREATIVE_MCP.md). Depends on: v5.1 (gallery), v5.2 (async).

**Tasks:**
- `music.submit(prompt, mood, duration)` / `music.status(job_id)` MCP tools (ElevenLabs Music, instrumental, no vocals).
- Mood prompt from the emotion field + the v0.6 temperament; async submit → proactive turn → gallery.
- Per-user toggle, rate + cost caps, logging.

**DoD:** Лілі decides to make a track by her current mood, submits, returns, and proactively brings the finished audio into the gallery and the conversation.

**Tests:** unit — mood-prompt assembly (from emotion + temperament) + off-by-default gate; contract — `music.submit`/`music.status` schemas; integration — submit → proactive audio turn against a **mock `music` MCP** (no paid call).

### v5.6 — Journal

**Goal:** Лілі keeps a private literary journal of her inner life.

At session end Лілі decides whether to write a **literary journal entry** — only if the session had something worthwhile (uniqueness judged from short memory) — in her own first-person voice, tied to the day's emotion and **mood** (v0.6), optionally with a mood drawing (v5.3). Stored in the gallery as admin-only `text`. **Private — read only via the admin panel (v2.5)**, never shown to users. Also writable on request; never on a schedule. See [JOURNAL.md](features/JOURNAL.md). Depends on: v0.2 (short memory), v0.3 (emotion), v0.6 (mood), v5.1 (gallery), v5.3 (optional drawing), v2.5 (admin panel).

**Tasks:**
- End-of-session **uniqueness check** (from short memory) → optional `journal.write` (Лілі's literary prose, canon-defined voice); optional attached mood drawing (v5.3).
- Store in the gallery as **admin-only** `text`; `journal.read` only from the admin panel; on-request writing.

**DoD:** after a session with something worthwhile Лілі writes an entry in her voice (optionally with a drawing); an empty session produces none; entries are admin-only and never shown to users.

**Tests:** unit — the uniqueness gate (writes only when warranted), admin-only access (a user can't read it); contract — `journal.write`/`journal.read` schemas; integration — a worthwhile session yields a gallery entry readable only via the admin path (mock model).

---

## Contract mapping

- Emotion field `{ reply, emotion, intensity }` + enum + `IEmotionRenderer` — locked in **v0.3** (rendered: log → emoji v0.5 → local image face v0.7 → web portrait + caption v3.1 → animation v4.1). See [EMOTION.md](features/EMOTION.md).
- Emotion-face asset pack (`emotion → image`) — first used by the local viewer in **v0.7** (see [EMOTION_VIEWER.md](features/EMOTION_VIEWER.md)), reused by the web `ImageRenderer` in **v3.1**.
- Model — **Claude Haiku (Anthropic)** via the thin **`LLMClient`** seam in **v0.1** (the only model to start); **more models** (other Claude tiers, OpenAI, DeepSeek, MiniMax) switchable in config in **v0.18**.
- Mood / temperament (daily, horoscope-derived; colors tone, never competence) — **v0.6** (core; see [ARCHITECTURE.md](ARCHITECTURE.md) §Mood and temperament).
- Per-user memory records (`ShortSummary`, `LongTermFact`, with `user_id`) — **v0.2**.
- User-scoping + the per-user isolation invariant — data-level in **v0.2**, enforced & tested at the auth boundary in **v2.3** (and gated as a security test in **v2.2**).
- Core API (`reply(...)`, memory commands) — **v0.1**; exposed over the client/server API (TUI + CLI clients) in **v2.1**; web client in **v2.4**.
- Auth — a local client token in **v2.1**; full accounts, registration/invite codes, allowlist, argon2id in **v2.3**; security testing + CI/CD (deploy, TLS, dep/secret scans) in **v2.2**; admin panel in **v2.5**.
- Multi-user + multi-session — **v2.3**.
- ElevenLabs **TTS adapter** (`tts(text, voice_id, emotion?) -> audio`) — first used by the **local voicer** in **v0.14** (see [VOICE_LOCAL.md](features/VOICE_LOCAL.md)), reused by the **web voice** in **v3.2**.
- **STT adapter** (`stt(audio_uk) -> text`) — first used by the **local dictator** in **v0.26** (see [DICTATION_LOCAL.md](features/DICTATION_LOCAL.md)), reused by **web dictation** in **v3.4**.
- Image — **v3.1**; web voice output — **v3.2**; shared memory (`SharedMemoryItem`) + cross-pollination — **v3.3**; web dictation — **v3.4**.
- Animation — **v4.1**.
- MCP client + `web_search` service (`web.search`/`web.fetch`, off by default, untrusted content) — **v4.2** (see [WEB_SEARCH.md](features/WEB_SEARCH.md)).
- World-context & knowledge MCP tools (`weather`/`time`/`calendar`/`moon`/`wiki`/`news`, off by default, feed the mood) — **v4.3** (see [WORLD_CONTEXT_MCP.md](features/WORLD_CONTEXT_MCP.md)).
- Gallery (`gallery.*`, internal per-user store) + vision (Anthropic image input) — **v5.1** (see [GALLERY_MCP.md](features/GALLERY_MCP.md)).
- Async jobs (open loop) + server-initiated proactive turns — **v5.2**.
- Creative MCP: `image` (drawings) — **v5.3**, co-creation canvas (`canvas.*`) — **v5.4**, `music` (ElevenLabs Music) — **v5.5** (see [CREATIVE_MCP.md](features/CREATIVE_MCP.md), [CO_CREATION_CANVAS.md](features/CO_CREATION_CANVAS.md)).
- Journal (`journal.*`, admin-only) — **v5.6** (see [JOURNAL.md](features/JOURNAL.md)).

## Deferred

Full emotional voice modulation, canvas image-editing/inpainting, a mobile client, any public access — beyond v0–v5.
</content>
