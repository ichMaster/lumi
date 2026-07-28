# Voice mode (v1.6 redefined) — mode text / mode voice, realtime brains, the model classifier

The v1.6 phase is **redefined**: instead of text-register routing ([MODEL_ROLES.md](MODEL_ROLES.md) —
superseded, kept as the classifier's design ancestor), Lumi gains **two interaction modes**:

- **`mode: text`** — today's TUI exactly as it is: the Gemini profile answers, streaming, tools,
  the file-bus voicer/dictator — **byte-identical, untouched**.
- **`mode: voice`** — a live spoken conversation: microphone → **OpenAI Realtime** → speaker, with
  natural turn-taking and barge-in. In this mode the **realtime model IS Лілі's voice brain** — it
  answers directly in audio, wearing the same persona, memory and state the text mind wears.

Two realtime models power voice mode, picked per moment by a **classifier**:

| Register | Model | When |
|---|---|---|
| everyday | **`gpt-realtime-2.1-mini`** (default) | casual talk, most turns — reasoning + tool use at mini price |
| deep | **`gpt-realtime-2.1`** (flagship) | loaded/emotional moments, hard analytical asks |

Foundation: [REALTIME_VOICE_MODE_SET.md](REALTIME_VOICE_MODE_SET.md) — the `/mode-set` command, the
`RealtimeVoiceSession` adapter, session config, WebSocket transport, failure handling and privacy
rules all come from there (its Phase 1–3 slices are this feature's v1.6.2). What this document adds:
the **realtime-as-brain** step, the **two-model set**, and the **classifier routing** between them.

---

## 1. The two modes

`/mode-set text | voice` switches the interaction surface at runtime (session-local, like `/model`).
The modes share **one mind**: the same `Repository` (messages, facts, closeness, vectors — per-user,
isolated), the same daily mood, the same canon. A conversation started by voice continues by text
seamlessly, because both modes persist the same `Message` records and read the same memory.

**Text mode is the default and the fallback.** Voice mode failing (no key, connection drop, model
error) always degrades to text mode with a readable status line — never a dead TUI.

## 2. Realtime-as-brain (what changes vs the transport MVP)

The transport MVP (REALTIME_VOICE_MODE_SET.md) keeps `Core.reply()` as the brain and uses Realtime
only for mic/VAD/speech (`create_response=false` — the realtime model never improvises an answer
before Core replies). That is the safe first slice — but it is **not the end state**, for one hard
reason: the shell **inherits the text-pipeline latency**. A committed transcript still runs the full
`Core.reply()` on the reply tier (~6–8 s measured) before a word is spoken — and a spoken
conversation with 8-second pauses is not a live conversation. Realtime-as-brain answers in audio
sub-second; that is why v1.6.3 is the destination, not a side experiment. The full v1.6 flips
voice mode to **realtime-as-brain**:

- The realtime session carries **Лілі's assembled system prompt** as its `instructions` — built by
  the same `core` prompt builder (canon + memory blocks + facts + mood + a **voice-trimmed inner
  voice**), refreshed via `session.update` between turns when the prompt changes (mood flip, memory
  update). The core stays the *author* of who she is; the realtime model is the *mouth and mind of
  the moment*.
- The model **answers directly in audio** — no `Core.reply()` text round-trip. First audio lands in
  well under a second on 2.1-family models (their p95 is 25% lower than the 2.x line).
- **The emotion contract survives via the tool path**: the realtime models support function calling
  (structured outputs — not), so the session exposes the **`set_state` tool** — the same Anthropic
  pattern as v0.3 — and the instructions require calling it once per spoken turn with
  `{emotion, intensity, intent}`. The core validates through the same gate (unknown → `calm`,
  clamp); the face/status update as always. A turn where the model skips the tool degrades to
  `calm` — never an error.
- **Memory stays single-path**: the committed user transcript and Лілі's spoken-reply transcript
  persist as ordinary `Message` records through the same `Repository` (the v1.5 async queue applies).
  Recall/RAG indexes them like typed turns. The turn's transcripts are the durable truth; audio is
  ephemeral.
- **Tools in voice**: phase-gated. v1.6.3 ships `set_state` only; v1.6.5 may expose `recall` (and
  later news/files) as realtime tools — the same executors, the same untrusted-content framing.
- **The inner voice, voice-trimmed**: the full v4 think-skeleton is a text-mode instrument (visible
  box, verbatim negotiation). Voice mode carries a **compressed instruction** — the accent rule
  («деталь, додана не просто так»), the intent choice from the same 9-intent enum (declared via
  `set_state.intent`), the anti-mirroring traps, the hard limits — but no verbatim `<think>` block
  (there is no box to show it in, and it would cost first-audio latency).

## 2b. Where the live loop runs — no separate application

A live conversation does **not** need a new app. The loop lives **in-process in the TUI**
(`voice/live.py`, started by `/mode-set voice`): the WebSocket to OpenAI is plain asyncio (fits
Textual's event loop), microphone capture and speaker playback are `sounddevice` streams
(PortAudio callback threads — the same library the dictator already uses, just in-process instead
of a separate daemon). Audio frames must NOT ride the file bus — the JSONL FIFO is for asynchronous
mirrors (Telegram, the voicer), not for 24 kHz PCM; the bus pair keeps doing its job unchanged.

**The one honest wrinkle is echo**: full-duplex means the mic is open while the speaker plays, and
PortAudio has no built-in echo cancellation — without it the model hears itself. **The decision:
all local phases (v1.6.1–v1.6.4) run on HEADPHONES** — zero code, zero risk; no software AEC is
built. Echo cancellation arrives only with the **web client** (v1.6.5), where WebRTC gives it for
free — and that is the point at which a "separate application" (the browser page + an
ephemeral-token endpoint) genuinely earns its existence, not before.

## 3. The classifier (which model answers this moment)

The two-stage design survives from MODEL_ROLES, adapted to speech and to **two** targets:

**Stage 1 — lexical, code, ~0 ms** (on the committed transcript): distress/emotional markers and
long/hard-analytical markers from an authored `core/roles.md`; short everyday utterances always land
on **mini**. Clear cases never call any classifier model.

**Stage 2 — the tiny classifier** (only on *unsure*): one structured call — the transcript + the
last few labels, **no memory, no personal context** (pinned by a contract test) — returning
`{label: everyday|deep, confidence}`. Below `LUMI_VOICE_CONFIDENCE` → mini. Any failure → mini
(never blocks a turn). The classifier model is the profile's `classifier` role (a cheap text tier —
flash-lite / gpt-5.5-nano class); its cost per call is ~$0.0001.

**Escalation = a session handoff (an API-imposed fact, not a choice).** OpenAI's Realtime reference
documents that `session.update` **cannot change `model`** — a session is bound to its model for its
lifetime — and that the **voice cannot change after the session's first audio output**
([API reference](https://platform.openai.com/docs/api-reference/realtime-client-events)). So
switching means: close the mini session → open a flagship session (same voice set *before* first
audio) → replay `instructions` + a compact recent-context block → continue speaking. That costs
~1–2 s once — acceptable at a loaded moment (she may bridge it naturally: «зажди, це важливе…»).
**Stickiness matters more than in text**: the hold (`LUMI_VOICE_COOLDOWN`, ~5 spoken turns) prevents
handoff thrash; short interjections bypass the hold's *classification* but the countdown keeps
running; when it expires, the next natural pause hands back to mini. Routing reads **his words,
never her mood — never competence**.

*Rejected for now — hot standby:* keeping both sessions (mini + flagship) open and handing off
instantly would erase the 1–2 s gap, but double sessions are too costly/complex for local Lumi.
Revisit only if the handoff gap measurably hurts.

## 4. Costs (measured against real usage, July 2026)

From 11 days of real cache-log data (~83 turns/day, his msg ≈ 96 chars ≈ 7 s speech, hers ≈ 270
chars ≈ 19 s): the current text stack costs ≈ $0.027/turn (Gemini pipeline). Voice projections at
the same volume:

| Stack | Per turn | Per month |
|---|--:|--:|
| Current chained voice (Gemini + Deepgram + ElevenLabs) | ~$0.055 | ~$137 |
| **Voice mode, all-mini** | **~$0.016** | **~$40** |
| Voice mode, all-flagship | ~$0.081 | ~$202 |
| **Voice mode, routed (~85% mini / 15% flagship)** | **~$0.026** | **~$65** |

The classifier's job is exactly to keep the flagship share small. Prompt caching is the biggest
lever either way (the ~16.5 K-token instructions ride every turn; cached text on the 2.1 family is
~90% off — without it the flagship jumps past $0.10/turn).

## 5. Config

```ini
LUMI_MODE_SET=text                      # text (default) | voice — the startup mode
LUMI_REALTIME_MODEL=gpt-realtime-2.1-mini      # the everyday voice brain
LUMI_REALTIME_MODEL_DEEP=gpt-realtime-2.1      # the escalation target ("" → routing off, mini only)
LUMI_VOICE_CONFIDENCE=0.6               # classifier floor; below → mini
LUMI_VOICE_COOLDOWN=5                   # spoken turns the deep register holds
LUMI_REALTIME_VOICE=marin               # the OpenAI voice
LUMI_REALTIME_TRANSPORT=websocket       # local TUI: server-side WS (the key never leaves the process)
OPENAI_API_KEY=sk-...
```

`[modes.voice]` in `core/models.toml` mirrors these (env wins), plus the `classifier` role reuse
from the active profile. Off (`LUMI_MODE_SET=text`, default) → **byte-identical** to today.

## 6. Invariants (unchanged, pinned)

- **The emotion contract is untouched** — `{reply, emotion, intensity}`: in voice mode `reply` is
  the spoken transcript, `emotion`/`intensity` arrive via the `set_state` tool, the v0.3 gate
  validates. Contract test.
- **One mind, one memory** — per-user isolation and the single `Repository` path hold across modes;
  a voice turn is a `Message` like any other. Contract test.
- **Never competence** — routing reads his words, never her mood; the deep register is about
  *depth*, not willingness.
- **The classifier prompt carries no personal data** (transcript + labels only). Pinned.
- **Text mode byte-identical** — voice mode off → nothing changes anywhere. Pinned.
- **No paid CI** — every realtime interaction runs behind a fake transport in tests (scripted
  events: transcript-committed, tool-call, audio-done); the classifier is mocked.

## 7. Phasing (the v1.6.x slices)

- **v1.6.1 — the live prototype (first, before any framework).** A standalone dev probe —
  `scripts/realtime_probe.py`, the `gemini_probe` pattern: mic ↔ WebSocket ↔ speaker on
  **`gpt-realtime-2.1-mini`**, **no tools, no classifier, no persistence — pure context only**: the
  session `instructions` are a one-shot snapshot of Лілі's assembled system prompt (canon + memory
  + mood, dumped via the core prompt builder). Headphones (no AEC yet). **Manual + paid, never
  CI.** The goal: hear Лілі live *now* — feel the first-audio latency, check the persona survives
  in speech, validate the event shapes (transcript/audio/VAD) that everything later builds on.

  > **Probe result (v1.6.1 — RAN 2026-07-28, three sessions on `gpt-realtime-2.1` flagship,
  > ~10 turns each):**
  > - **Latency — GO ✅:** speech-stop → first audio **median 1.37 s / 1.25 s** across sessions
  >   (min 0.41 / max 2.8) — and that covers the WHOLE cycle (his speech understood → reply
  >   generated → audio out). The inherited ≤2.5 s DoD beaten on the median; the live feel is real.
  > - **Voice — NO-GO ❌ («голос жахливий»):** a strong American accent in Ukrainian, **unchanged**
  >   by the OpenAI-recommended steering (positive identity anchor, leading block, stable-accent
  >   phrasing) even on the flagship, which documentedly follows accent instructions harder than
  >   mini, and across voices (marin, shimmer). Ukrainian pronunciation is a real limitation of the
  >   built-in realtime voices. → the
  >   **ElevenLabs hybrid** (realtime brain + `output_modalities:["text"]` + her existing ElevenLabs
  >   voice via streaming TTS, ~+0.5–1 s) is promoted from fallback to the **primary voice-identity
  >   candidate**; OpenAI custom voices exist but are gated to eligible customers (consent + sample
  >   recordings — check account eligibility).
  > - **Persona — PARTIAL ⚠:** session 1 read as a **generic assistant**; session 2 (longer,
  >   varied topics) showed the snapshot DOES carry through — she recalled the owner's name, her
  >   creative canon (малює ескізи, музика як Lili Jinx), his projects (Пічка/hill-303), held one
  >   mood consistently across turns, and stayed honest about no live clock/weather access. What
  >   broke instead: the snapshot's TEXT-protocol instructions leaked into **speech** — she spoke
  >   `<emotion>calm 0.7</emotion>` / `<intent>position</intent>` aloud (English tokens
  >   mid-repliка, feeding the accent problem). Fix shipped in the probe: a leading no-tags rule
  >   in the speech directive + a transcript tag-parser. The v1.6.3 lesson: the snapshot needs a
  >   **voice-mode variant** (text-protocol sections stripped or overridden) — and v1.6.2's
  >   Core-as-brain shell, where the persona provenly lives in the text mind, stays the safe path.
  > - **Event shapes discovered:** `conversation.item.input_audio_transcription.delta` (the user's
  >   ASR streams as deltas — the v1.6.2 adapter should assemble them or rely on `.completed`) and
  >   `response.output_audio.done` (the audio completion marker), on top of the handled set.
  > - **Implementation notes:** streamed-playback distortion traced to a chunk-truncating speaker
  >   callback — fixed with a lossless byte buffer; startup beep / `--devices` / mic watchdog /
  >   per-run voice pick (`./scripts/voice.sh cedar`) added along the way.

  > **A/B/C/D follow-up (2026-07-28/29 — four stacks, same snapshot, same metric
  > speech-stop → first audio):**
  >
  > | probe | stack | median | voice | brain/quality |
  > |---|---|---|---|---|
  > | realtime (`voice.sh`) | all-OpenAI `gpt-realtime-2.1` | **1.31 s** | ❌ American accent | flagship; persona partial |
  > | chain (`voice_chain.sh`) | Deepgram REST → `gemini-2.5-flash-lite` → ElevenLabs | 2.78 s (stt 1.41 · llm 0.93 · tts 0.26) | ✅ HER voice | ❌ flash-lite broke: name misses, confabulated memory, an obscene one-word reply, an untagged English CoT spoken aloud |
  > | hybrid (`voice_hybrid.sh`) | `gpt-realtime-2.1` text-out → ElevenLabs | 2.12 s (llm 1.18 · sentence-wait ~0.65 · tts 0.28) | ✅ HER voice | flagship |
  > | chain-WS (`voice_chain_ws.sh`) | **Deepgram WS** → `flash-lite` (structured) → ElevenLabs | **1.40 s** (llm 1.13 · tts 0.26 · endpoint ~0.8 felt) | ✅ HER voice | held THIS run (structured output); history says fragile |
  > | **chain-WS (winner)** | **Deepgram WS → `gemini-2.5-flash` (structured) → ElevenLabs** | **1.60 s**, then **1.23 s over 32 turns** with the phrase-hold (llm 0.83 · tts 0.26 · endpoint ~1.1 felt — the hold's price) | ✅ HER voice | ✅ the owner's verdict: «ідеально» → «прекрасно» — persona, memory (Зетрос recall), natural repartee, zero leaks |
  >
  > **Read (the v1.6.3 design inputs):** the streaming-STT chain WINS — 1.6 s with her real voice
  > and a brain that held the persona, beating the hybrid (2.12 s) and nearly matching all-OpenAI
  > (1.31 s, unusable voice). Three hard-won lessons are design requirements for the voice
  > register: **(1) structured output** (`responseMimeType: application/json` + a `{reply}` schema,
  > streamed via `decode_json_string_value`) — the ONLY thing that stopped plain-text CoT preambles
  > (instructions failed against three shapes: bare `thought`, `<thought>` — now routed by the core
  > StreamTagFilter — and `(_thought_`); **(2) the core's permissive `safetySettings`** — without
  > them Gemini's default filter cut replies mid-sentence (`finishReason` now surfaced live);
  > **(3) the talking register** — the snapshot built with `LUMI_REASONING=off` (no think directive:
  > prose streams immediately; the full inner voice stays a text-mode luxury), plus the `speakable`
  > Latin-vs-Cyrillic gate before TTS. Known rough edge: silence-based endpointing splits a turn
  > on a natural mid-sentence pause (observed at 300 ms; she bridged it gracefully) — the one thing
  > realtime's semantic VAD does better. Mitigated: the window is 500 ms by default
  > (`--endpoint-ms`) and an **un-punctuated `speech_final` is HELD** — the tracker waits for the
  > continuation (segments join into one turn) or the ~1 s `UtteranceEnd` backstop, so a breathing
  > pause no longer fires a reply. Probes: `scripts/chain_probe.py`, `scripts/hybrid_probe.py`,
  > `scripts/chain_ws_probe.py`.
  >
  > **Next feature (owner-requested 2026-07-29): barge-in for the chain-WS stack** — she stops
  > speaking when interrupted. The realtime stack gets this from `interrupt_response`; the chain
  > must do it itself: Deepgram already streams interims DURING her playback (headphones — the mic
  > hears only him), so *his speech while she is playing* = interrupt → **clear the speaker buffer,
  > drop the queued TTS sentences, abandon the un-spoken tail of the reply — but never the
  > committed turn** (the v1.6.2 rule verbatim: barge-in cancels audio playback only). The spoken
  > prefix stays in history as what she actually said. Lands with v1.6.2.

- **v1.6.2 — voice mode in the TUI: the chain-WS stack productized (REDEFINED by the probes).**
  The A/B/C/D winner becomes `mode: voice`: `/mode-set text|voice` + `LUMI_MODE_SET` (default
  `text` → byte-identical); the in-TUI live loop (asyncio **Deepgram WS** + `sounddevice`, the
  probe's `UtteranceTracker` phrase-hold promoted into `/voice` with tests; headphones); every
  committed utterance runs **exactly one `Core.reply()`** on the **talking register** (reasoning
  off for the turn, the profile's `voice` tier — default `gemini-2.5-flash`, streamed structured
  reply) so the emotion contract, closeness, RAG, memory writes and the v1.5 async queue apply
  unchanged; the reply streams sentence-by-sentence into **ElevenLabs** (her voice, the lossless
  speaker buffer, `StreamTagFilter` + `speakable` before TTS); **barge-in** — a Deepgram interim
  during playback clears the speaker buffer + the queued sentences, never the committed turn.
  Full phase definition: ROADMAP §v1.6.2; issues: `v1.6.2-issues.md`. (The OpenAI-Realtime
  transport MVP — REALTIME_VOICE_MODE_SET.md Phases 1–3 — is superseded as the transport but
  remains the design ancestor for the mode framework and failure rules.)
- **v1.6.3 — realtime-as-brain — DEFERRED** (the chain-WS victory removes its driver; revisit only
  if the chain's turn-taking proves insufficient) — instructions from the core prompt builder
  (live, refreshed via `session.update`); `set_state` as a realtime tool; transcripts persisted as
  Messages; the voice-trimmed inner-voice instruction; mini only.
- **v1.6.4 — the classifier + the two-model set** — the lexical stage + tiny-classifier routing,
  the session handoff, stickiness, `/roles`-style surfacing (`status: mode:voice ✦ deep`), cost
  logging into the usage/cache reports.
- **v1.6.5 (later — may slip beyond v1.6)** — realtime tools beyond `set_state` (recall/news), the
  web client (WebRTC + ephemeral tokens + AEC for free), full-duplex GPT-Live evaluation.

## 8. Open questions

1. **The voice itself — resolved: a config choice.** `LUMI_REALTIME_VOICE` (+ `voice` in
   `[modes.voice]`) picks an OpenAI built-in (marin/cedar/…); it must be set **before the session's
   first audio** (immutable after — API rule), so changing it means restarting voice mode. Whether
   to match it to her ElevenLabs voicer identity stays a taste call for later.
2. **Думки/scheduler in voice mode**: do proactive thoughts speak (they'd interrupt) or queue for
   the next pause?
3. **The trimmed inner voice** — how much of the v4 skeleton survives without hurting first-audio?
   (My lean: РОЗУМІННЯ's accent rule + intent + traps; drop the verbatim negotiation.)
4. **De-escalation UX** — hand back to mini silently at a pause, or never mid-topic?

## Relationship to existing plans

- **MODEL_ROLES.md (old v1.6)** — superseded as a phase; its classifier/stickiness design lives on
  here (§3). Text-register routing may return later as a cost lever, unscheduled.
- **LATENCY.md S6 / LAT-4 (live voice, on hold)** — absorbed: this IS the live-voice mode, on
  realtime models instead of the STT→core→TTS chain; the ≤2.5 s first-audio DoD is inherited and
  beaten (sub-second on 2.1).
- **REALTIME_VOICE_MODE_SET.md** — the foundation (transport, adapter, config, privacy); this doc
  extends it with the brain flip and routing. Its "naming note" is outdated: `gpt-realtime-2.1` and
  `gpt-realtime-2.1-mini` shipped in the API on 2026-07-06/07 (GA `v1/realtime`; model page:
  https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini).
- **V1_6_REALTIME_VOICE_MODE_SET_ROADMAP.md** — the Codex roadmap draft, **merged into this doc**:
  its Phase-A rules, the sourced API constraints (`session.update` can't change `model`; voice
  immutable after first audio), and the hot-standby rejection live in §2/§3/§7. Where it deferred
  the two-model routing and realtime-as-brain entirely, this doc sequences them instead (v1.6.2 → v1.6.3 → v1.6.4)
  per the owner's decision.
- **v0.14 voicer / v0.26 dictator** — remain as the *asynchronous* pair (Telegram voice-overs,
  hands-free dictation into text mode); voice mode does not replace the file bus.
