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

**Goal:** Лілі doesn't only *react* — between and around your messages her mind does things on its own (she muses, wonders), recorded to a private **thought-stream** and only **occasionally surfaced aloud**. Speaking becomes the rare tip of a quiet inner life. This generalizes the v0.4 idle **nudge**: today it always *speaks* a fixed opener; now it mostly **`%think`s** silently from her live state and speaks only once in a while. Placed here (before the inner life) because it's self-contained — its hard deps (v0.4 nudge, v0.6 mood, v0.2 repository) already exist; it launches **thin** (mood + closeness + recent) and **enriches automatically** as v1.1–23 add needs/plans/dreams to the seed. See [THOUGHT_STREAM.md](features/THOUGHT_STREAM.md).

A clean three-layer vocabulary, and one reusable engine under it:
- **`%directives`** — her mind *acts* (internal, **never typed**): `%think` (everyday musing) + `%wonder` (curiosity). Distinct from **`/commands`** that *read* state (`/mood`, `/thoughts`) and plain chat she *speaks*. `%` reads as system plumbing — no confusion with `/`.
- **The mental-act engine:** `trigger → seed her state → generate (one housekeeping call, thinking-off) → record → maybe surface`. A small **registry** of `{name, trigger, seeds, store, surface}`; `%dream`/`%reflect`/`%recall` are the **same engine retrofitted** by v1.2/0.25/0.16 (not built here).
- **The store (global):** `Thought{when, kind, text, emotion, seeds, spoken, ts}` behind the `Repository`, **not** `user_id`-keyed (like `InnerLife`); a rolling soft-capped log (consolidates into v1.4 impressions). **Isolation:** the store is global but **surfacing is per-conversation** — a thought sparked by user A never surfaces to B (contract test).
- **The feedback loop (the point):** the last few thoughts ride into the next reply as a compact "on her mind" block, and a recurring thought nudges the v0.6 mood (and v1.1–23 needs when present) — soft, never competence.
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
- **The file bus (FIFO + id pointers).** `inbox.jsonl` + `outbox.jsonl` — append-only JSONL (`{id, text, ts}`), **one writer + one reader each** (no locks); the consumer tracks the **last id** it processed (a tiny pointer file), id-based so trimming later is safe. (Shared infra the v0.14 voicer / v0.25 dictator later ride.)
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

### v0.25 — Local dictation (STT)

**Goal:** talk *to* Лілі — a separate local app that hears your speech and types it into the chat. The **mirror of the v0.14 voicer**: the voicer reads Лілі's replies and speaks; the dictator listens to the mic, recognizes Ukrainian, and **writes your line into the input log** — the same channel as the TUI keyboard, so the core can't tell typed from dictated.

A separate local process listens to the microphone, recognizes Ukrainian via the **shared STT adapter** (`/voice`), and appends `{id, text, source:"voice", ts}` to **`inbox.jsonl`** (where the TUI keyboard also writes); the TUI consumes those lines as ordinary user turns. Listening is toggled by a **TUI key** (e.g. F2) that flips **`listen.flag`** (`on`/`off`) — the dictator records while `on` and recognizes on `off`. The terminal never captures audio itself; a separate process does. Local-stage **sibling of the web dictation (v3.4)** — both use the same `/voice` STT adapter. Cloud STT (Deepgram Nova-3 uk / ElevenLabs Scribe) needs a key + internet; **offline Whisper** is an option. See [DICTATION_LOCAL.md](features/DICTATION_LOCAL.md). Depends on: v0.1 (the core consumes user turns) and v0.14 (the local-process + shared-file pattern).

**Tasks:**
- A separate **dictator process**: watch `listen.flag`; record the mic while `on`; on `off`, send audio to the **STT adapter** in `/voice` (`stt(audio_uk) -> text`, provider configurable) → append `{id, text, source:"voice", ts}` to `inbox.jsonl`.
- **TUI toggle**: a key sets `listen.flag = on/off` and shows a "listening…" state; the TUI picks up dictated lines from `inbox.jsonl` and submits them to `core.reply()` exactly like typed input.
- **Resilience:** empty/low-confidence recognition writes nothing to `inbox` (better silent than garbage; the TUI may show "didn't catch that"); dedup by `id`; an enable toggle (run/stop the process).

**DoD:** press the listen key, speak Ukrainian, and your recognized line appears in the chat (marked as yours) and is answered — identically to typing it; a missed/empty utterance is dropped, not garbled into the chat; dictation can be toggled without touching the core.

**Tests:** unit — `listen.flag` on/off handling, empty-recognition is dropped (no `inbox` write), dedup by `id`; integration — a recognized line via a **mock STT adapter** (no paid call) lands in `inbox.jsonl` and drives a turn identical to a typed one.

### v0.26 — Semantic recall III: chunking long messages

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

### v0.27 — Semantic recall IV: thematic recall (topic routing)

**Goal:** recall stops being one undifferentiated pool and learns **what the turn is about** — every message is tagged with topics from a fixed authored set, and a turn recalls **preferentially from the topics the conversation is currently about**. The topic is picked **locally, without an LLM call**, and **Лілі can steer it** by naming the active topics in her reply. Off by default → behaves exactly like v0.17/v0.26.

A **refinement of the recall line** (v0.16 index + v0.17 auto-RAG / context expansion), not a new capability. A closed, **authored topic taxonomy** (`core/topics.md`, like the emotion enum) gives every topic a name + seed terms → a **centroid** vector. Each message (or v0.26 chunk) is **tagged at index time** by a **local embedding classifier** — cosine of its own stored vector vs the centroids, topics ≥ `topic_floor` (no LLM; re-tagging recomputes from stored vectors, no re-embedding). Each turn the **active topic set** is picked **locally** from the incoming message's embedding (already computed for RAG) ∪ the topics **Лілі emitted** on recent turns (the v0.10 `RelationRead` pattern, decayed for inertia); because the RAG block is built **before** the reply, her emitted topics take effect **next turn**, so the local pick covers the current turn with no lag and no extra call. Retrieval then **prefers on-topic hits and tops up from the rest** (never starves), before the v0.17/v0.26 expansion + injection runs unchanged. `VectorRecord` gains **`topics`** (additive — a contract change, pinned by the memory-records contract test). **Same isolation invariant** — topics are labels on the requesting user's records; centroids are authored, user-content-free. **Same hard rule as mood/closeness** — topic routing biases *what is recalled*, never her competence; a missed topic degrades to plain v0.17 RAG, never a refusal. The `Embedder`/`VectorStore` seams and the `{reply, emotion, intensity}` contract are **untouched**. Depends on: v0.16 (index + seams), v0.17 (auto-RAG + context expansion); **independent of v0.26** (composes with chunks). See [SEMANTIC_RECALL_THEMATIC.md](features/SEMANTIC_RECALL_THEMATIC.md).

**Tasks:**
- A **topic taxonomy** (`core/topics.md`, path = config `LUMI_TOPICS_FILE`): a closed authored set, each topic a name + seed terms; build per-topic **centroids** by embedding the seeds (rebuilt on taxonomy change).
- A **local classifier** (`core/`): cosine a record's **stored vector** vs the centroids → assign topics ≥ `LUMI_RAG_TOPIC_FLOOR`, capped at `LUMI_RAG_TOPIC_MAX`; index-on-write + backfill tag each record; re-tagging recomputes labels from existing vectors (**no re-embedding**); the taxonomy version joins the vectors staleness tag (`…@topics_vN`).
- `VectorRecord` gains `topics` (additive); update the memory-records contract test + ARCHITECTURE §Semantic recall in the same commit.
- **Per-turn active set:** local pick (query vs centroids) ∪ Лілі's carried-forward emitted topics, decayed by `LUMI_RAG_TOPIC_DECAY`.
- **Router** in front of the LUMI-072 selection: prefer on-topic hits, top up off-topic to `K` (never starves); a no-op when off or with no active topics.
- Лілі emits topics via the v0.10 `RelationRead` (validated against the taxonomy, unknown dropped) → folds into the carried-forward set; `{reply, emotion, intensity}` unchanged.
- A `/topics` command (active topics by name). Config: `LUMI_RAG_TOPIC` (off by default), `LUMI_RAG_TOPIC_FLOOR`, `LUMI_RAG_TOPIC_MAX`, `LUMI_RAG_TOPIC_DECAY`, `LUMI_TOPICS_FILE`.

**DoD:** a message is tagged with topics from the authored set at index time (local, no LLM); a turn whose subject matches a topic recalls **preferentially from that topic** while never returning fewer than `K` floor-passing hits (prefer-then-top-up); Лілі's emitted topics steer the next turns' routing with inertia; `/topics` shows the active topics; retrieval never crosses users (routed or not) and never blocks a turn; composes with v0.26 (a chunk is tagged from its own vector); **off (default) → identical to v0.17/v0.26** (one undifferentiated pool).

**Tests:** unit — the classifier (floor, max labels, untagged-on-no-match, multilabel); index-on-write/backfill tag records; re-tag recomputes from stored vectors **without re-embedding**; the staleness tag rebuilds labels on a taxonomy change; active-set pick (local ∪ carried-forward, decay); the router (prefer on-topic, top-up to `K`, no-op when off); Лілі's topic read validates against the taxonomy (unknown dropped) and carries forward; `/topics`; **isolation contract** — topic-routed retrieval is single-user (A↔B); graceful degradation (classifier/router failure → v0.17 behaviour). All via the **mock embedder** — no paid calls.

---

## v1 — Personality: inner life, needs, inner monologue, emotional memory

Лілі's inner person, on top of the v0 mind. This version gives her a **life of her own between conversations** (day/week/weekend intentions, and an away-gap filled with activities, memories and **dreams**), the **needs** that pull her from inside, an **inner monologue** in her own voice before she speaks, and a long-term **emotional memory** of each user as her first-person impressions (diary, not stenographer). These layers are **core** (interface-independent) and still **local** — no server yet; they deepen *who she is*, not how she's reached.

**Two invariants run through all of it.** Her **inner life and needs are global** (one being — the stores are not `user_id`-keyed; only surfacing is per-conversation), while her **emotional memory is per-user and isolated** (her impressions of *this* person never cross users). And **none of it ever touches competence** — it colors tone, warmth, what she carries and how she says things, never how capable or willing she is to help. She also stays **honest about its nature** (inner/imagination, never a physical-world claim). Depends on: v0 (mood, the injected clock, the emotion channel, closeness, the memory layers).

### v1.1 — Inner life I: plans & state (intentions she carries)

**Goal:** Лілі **carries her own intentions** — what she has on today, this week, the weekend — so she can offhandedly mention "the track still isn't done today" or "can't wait for the weekend" even when you didn't ask. The first half of an **inner life that continues between conversations** — and, under it, the first half of her **needs** (the drives that *pull* her from inside).

Three planning layers held in a **global** personal store (one Лілі — **not** per-user) and updated **lazily at boundaries** (no background process):
- **Weekly intentions** (3–5 soft goals in her voice), **weekend intentions** (a different spirit — water, mountains, music, silence), **today's plan** (1–3, from weekly goals + her routine + carry-overs + the v0.6 mood **+ the hungriest need**). Unfinished items carry over.
- **Boundaries (injected clock):** at the first session of a new local **day** → a fresh today's plan; of a new **week** → fresh weekly/weekend intentions; unfinished carried over. One housekeeping model call per boundary (mocked in tests).
- **State block** in the system prompt — compact (Today / This week / Weekend ahead / Mood / Unfinished), **tone not report** — so she carries her plans into the conversation.
- **Authored skeleton:** an editable **hobby bank** + a **7-slot daily routine** (4 fixed / 3 free); the free slots are mood-chosen (filled in v1.2).
- **Needs I — the drives exist & pull (see [NEEDS_full.md](features/NEEDS_full.md)).** A small authored set of **6 core drives** (creation / solitude / connection / freedom / meaning / novelty) in `core/needs.md`, each with a decay rate / weight / satisfied-by / deficit voice. Their **levels** (0..1) live in a **global `Needs` store** (beside `InnerLife`, also not per-user), **decay** over the injected clock and **drift** to a calm middle. The hungriest need **joins the daily mood call** (beside biorhythms — the v0.8 merge pattern) and **tilts today's plan**; `connection` is replenished **mid-turn** from the closeness warmth read (`RelationRead.warmth`, v0.10). **Never competence; inner, not a demand on you.** (Closing from what she *did* is v1.2.)

Her inner life is **global** (the same whoever she talks to — one being), distinct from per-user memory/closeness. Reuses v0.6 (mood) + v0.8 (the biorhythm-merge pattern) + v0.10 (the warmth read) + v0.4 (clock). See [INNER_LIFE.md](features/INNER_LIFE.md) + [NEEDS_full.md](features/NEEDS_full.md). Depends on: v0.6 (mood), v0.4 (clock), v0.2 (the Repository).

**Tasks:**
- A **global `InnerLife` store** behind the `Repository` (not user-keyed): `{intentions_week, intentions_weekend, plan_today, unfinished, log}`.
- **Boundary detection** from the injected clock (new local day / ISO week); update the plans via a housekeeping call (carry unfinished over), once per boundary.
- The **inner-state block** in `build_system_prompt` (compact; Today/This week/Weekend/Mood/Unfinished); the v0.6 mood resolution feeds today's plan.
- Authored `core/inner/hobbies.md` + `core/inner/routine.md` (the bank + the 7 slots); editable.
- A `/inner` (or `/plan`) command to show the current state.
- **Needs:** a global `Needs{levels:{6 drives}, last_ts}` store (not user-keyed) + authored `core/needs.md`; **decay + drift** (pure math over the injected clock); the hungriest need **fed into the mood call** + **tilting the plan**; `connection` replenished mid-turn from the closeness warmth read. Contract test (global, not per-user). **No closing-from-activities yet (v1.2).**

**DoD:** Лілі carries day/week/weekend intentions every turn (the state block), updated at local day/week boundaries with unfinished carried over, fed by the daily mood; **her 6 needs decay over time, color the mood + plan (hungriest first), and `connection` lifts after a warm turn**; both the inner-life and needs stores are **global (not per-user)**; `/inner` shows it. **No background process. Never competence.**

**Tests:** unit — boundary detection (new day/week via fixed clock); the plan-update call (mock model) carries unfinished; the state-block assembly; the global (not user-keyed) stores don't leak per-user; **needs decay/drift = exact levels under a fixed clock; the hungriest-need selection; needs feed the mood request; `connection` rises from the warmth read**; `/inner` renders. No paid calls.

### v1.2 — Inner life II: the away-gap (what happened while you were gone)

**Goal:** come back after a while and **something happened to her** — activities, memories, and dreams from the time away, surfacing where it fits, and **honest about being her inner world, not a body**. And the second half of her **needs**: they **close** from what she actually did, so the drives roll forward in time.

At session start the core computes the **away gap** (injected clock) and, when it's non-trivial, generates her life across it — rooted in seeds, capped by gap length, replanned by a strong mood:
- **Gap-fill (one quiet call):** N fragments (≈1 per day of absence, **soft cap**) — activities/thoughts, and a **dream** only if the gap spanned night hours — rooted in the **seeds** (character, plans, mood, gap, previous entries, an **injected** random seed) so they're recognizable and don't contradict the past. A tiny gap (<~1–2 h) generates nothing.
- **Replan under the mood (v0.6):** if the day's mood is strong / conflicts with the plan, some intentions drop, others appear to match the mood; a memory is minted as the **gap between plan and what the mood did** (the most alive fragments). **Threshold** (mild days follow the plan); **reactivity is a character trait** (her watery Pisces nature weighs heavily); **unfinished accumulates**.
- **Surfacing:** fragments ride into context with "recall to the point, like a person — or not at all; **never a report on the absence**"; a `mention_aloud` restraint; ongoing activities reference a previous entry for continuity.
- **Honesty boundary (hard):** **inner only** (dreams/thoughts/creativity/practice — never a factual physical-world claim); to a direct "did that really happen?" she calmly admits it's her **imagination**, warmly, without breaking the spell. Encoded as a canon rule + a reminder in the block.
- **Needs II — close from reality (see [NEEDS_full.md](features/NEEDS_full.md)).** The gap-fill returns **structured records** (`serves` from the closed 6-need list / `intensity` / `feeling`); an authored **activity→need map** guides them. **Code owns the ledger** — `level += gain × intensity` per valid `serves` (clamped) — so needs rise from what *actually happened*, not the plan (planned a talk but "no one there" → `connection` stays hungry). A free slot is **filled toward the hungriest need** and then replenishes it (closing the loop). **Threshold-5** per-day generation (gap < 5 → per-day full mood; gap ≥ 5 → one call with per-day biorhythms only). Malformed / out-of-set records are dropped (levels stay post-decay).

See [INNER_LIFE.md](features/INNER_LIFE.md) + [NEEDS_full.md](features/NEEDS_full.md). Depends on: v1.1 (the plans & needs store), v0.6 (mood), v0.4 (clock).

**Tasks:**
- **Away-gap** computation (injected clock); the gap→fragment-count curve (soft cap); **dream-iff-night-hours**.
- The **gap-fill** housekeeping call (seeds = character/plans/mood/gap/previous + injected seed); append fragments to the `log` with `{when, type, text, mood, mention_aloud}` **+ `serves`/`intensity`/`feeling`** (needs).
- **Mood replanning** (threshold + reactivity trait): drop/replace intentions, mint the plan-vs-reality memory, accumulate unfinished.
- **Surfacing:** feed relevant fragments + the "to the point, never a report" instruction; honor `mention_aloud`.
- The **honesty boundary**: canon rule (`core/canon/lili.md`) + a reminder line; admits imagination on a direct challenge, never claims a body.
- **Needs-closing:** authored **activity→need map** (`core/inner/activities.md`); the gap-fill emits structured `serves`/`intensity`; **replenish** (`level += gain × intensity`, clamped) + validation (drop out-of-set/malformed); the **free-slot fill** biased to the hungriest need then replenishing it; the **threshold-5** per-day rule (config).

**DoD:** after a multi-day gap Лілі has new activities/memories (and a **dream** if the gap covered night), rooted in her plans + mood, not contradicting past entries, **surfaced naturally (not a report)**; a strong mood **replans** the day and mints a plan-vs-reality memory; **her needs rise from what she actually did (not what was planned), the free slot fills toward the hungriest need, and the loop rolls forward**; she stays **honest about it being inner/imagination**; a tiny gap generates nothing.

**Tests:** unit — the gap→count curve + dream-iff-night (fixed clock); the gap-fill call (mock model) seeds + appends; replan threshold/reactivity; surfacing honors `mention_aloud`; the honesty boundary present in the prompt; continuity (a new fragment sees previous); **needs replenish from `serves`/`intensity` (exact levels), validation drops out-of-set serves, the free-slot fill targets the hungriest need, the threshold-5 / no-duplication window**. No paid calls.

### v1.3 — Inner monologue (Лілі thinks in her own voice)

**Goal:** the hidden think-step before each reply sounds like **her** — her inner voice weighing her own states ("he's asking about the deploy, but his voice is tired — don't pile on detail, ask how he is first") — not the model's generic task reasoning. The **in-the-moment** sibling of the inner life (between sessions) and emotional memory (after a session): the **convergence point** where mood / closeness / needs / plans are weighed into *how she speaks*. The mechanism already exists (Opus 4.8 extended thinking + the `<think>` parse + the TUI think box); this phase makes it **hers** — **no new engine**.

- **One call, not two.** The reply stays **one model call** with thinking on; the monologue is the `thinking` content block of that same response (parsed out by `split_reasoning`), not a separate think-call. Housekeeping (mood / inner-life / summary / consolidation) stays thinking-**OFF**, as today.
- **Make it hers (the only real work).** Replace the generic `REASONING_DIRECTIVE` with an authored **think-phase instruction in her voice** (`core/inner_voice.md`, editable): *before answering, think as Лілі — what is he really asking; what's under the words; how am I right now (mood / how close we are / what I'm hungry for); how would I, specifically, say this.* The **state blocks already in the prompt** (mood v0.6/0.8, closeness v0.10, needs + plans v1.1–23) are the concrete inputs it weighs — it **consumes** them, doesn't duplicate them.
- **Show / log / memory.** A `think_show` mode — **debug** (visible to the operator, never in the reply; safe default) / **open** (surfaced as her inner voice — then it MUST stay in character) / **off**. The think-block is **logged** (the v0.3 logged tier), and **never written to long-term memory** (only the digested v1.4 impression persists — thoughts are ephemeral).
- **Invariants inside the thinking.** Never competence, honesty about her nature, anti-dependency, the provocation / retreat-before-pain rule — all hold *inside* `<think>` exactly as in the reply (hidden ≠ unconstrained; matters doubly if ever shown).

**No contract change** — the reply still returns `{reply, emotion, intensity}`; `thinking` is a content block, not a new field (the emotion-channel contract test passes verbatim). Reuses v0.6/0.8 (mood), v0.10 (closeness), v1.1–23 (needs + plans), v0.3 (the emotion turn + logged tier). Later states (self-regard, relational feelings) become **additive** inputs when they exist. See [INNER_MONOLOGUE.md](features/INNER_MONOLOGUE.md). Depends on: v1.1–23 (the states it weighs), v0.3.

**Tasks:**
- Authored `core/inner_voice.md` (the think-phase instruction in her voice) + load it; **replace `REASONING_DIRECTIVE`** in `_system_prompt` with it (the mood/closeness/needs/plan blocks already ride in the prompt). A `LUMI_INNER_VOICE` toggle.
- An **invariants-inside-think** line in the directive (never competence / honesty / anti-dependency / retreat-before-pain).
- **Show/log policy:** `LUMI_THINK_SHOW` (`debug`/`open`/`off`) + a `think.log` tier; the raw monologue is **never** persisted to long-term memory.
- Tests (below).

**DoD:** the reply turn's think-block, with the authored instruction, references **her states** (mood/closeness/needs) rather than generic task analysis; it is **one model call** (no second generation call; housekeeping stays thinking-off); the monologue is **logged but never persisted** to long-term memory; the invariants hold inside it; **no contract change** (the emotion-field test passes verbatim).

**Tests:** unit — the **one-call invariant** (exactly one model call per reply; housekeeping thinking-off); a **voice test** (the mocked think-block references her states, not generic analysis); a **memory test** (the raw think is not persisted to long-term memory); `think_show=off` hides it; determinism (mocked, structural assertions). No paid calls.

### v1.4 — Emotional memory I: impressions (diary, not stenographer)

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

### v1.5 — Emotional memory II: fading & consolidation (understanding, not archive)

**Goal:** her impressions behave like human memory — **what struck her stays bright, the mundane fades, and similar impressions merge into understanding** ("he comes alive with music").

Builds on v1.4:
- **Emotion is the attention filter + fading.** Each impression's `weight` **decays over time** (the v0.4 injected clock); recall ranks by `weight × recency`; high-weight impressions stay longer, low-weight ones dim and eventually drop.
- **Consolidation into generalizations.** A lazy **consolidation pass** (a model call, at session start or on a counter) folds many small similar impressions into stable **generalizations** — her *understanding* of you — kept as durable, higher-weight entries; the absorbed detail fades.
- **Stays consistent.** New impressions and consolidations **see the prior ones** (no contradiction), like the inner-life entries; the store stays bounded.

See [EMOTIONAL_MEMORY.md](features/EMOTIONAL_MEMORY.md). Depends on: v1.4 (the impressions layer), v0.4 (the clock).

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
- **STT adapter** (`stt(audio_uk) -> text`) — first used by the **local dictator** in **v0.25** (see [DICTATION_LOCAL.md](features/DICTATION_LOCAL.md)), reused by **web dictation** in **v3.4**.
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
