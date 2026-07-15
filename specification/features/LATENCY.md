# Latency — fast replies & the road to live voice

Make Лілі **fast in casual conversation** (feel: an answer in ~2–3 s, eventually a **live audio
conversation**) while keeping her ability to **think longer when the moment deserves it** — deep
reasoning stays, but becomes a *chosen register*, not the tax on every "привіт".

> **Status (2026-07-14): proposed.** All numbers below are **measured** on the running system
> (Gemini 3.1 Pro Preview, prompt cache on, RAG on, inner voice + intent on) — from
> `.lumi/outbox.jsonl` turn pairs, `.lumi/cache-log.jsonl`, and `.lumi/lumi.log` of 2026-07-13/14.
> The prompt-size side continues [docs/PROMPT_OPTIMIZATION.md](../../docs/PROMPT_OPTIMIZATION.md) /
> [II](../../docs/PROMPT_OPTIMIZATION_II.md); the model-side composes with the proposed **v1.6
> [MODEL_ROLES](MODEL_ROLES.md)** registers. This doc is the **latency umbrella**: it reviews where
> the seconds go and evaluates the levers.

---

## 1. The measurement — where the seconds go

### 1.1 User-visible turn latency (the thing to fix)

Paired `kind="user"` → `kind="lili"` outbox records (the user line is mirrored **at submit**,
[tui/app.py:578](../../tui/app.py) — so the pair spans the whole turn), last 40 turns of 2026-07-13/14:

| metric | value |
|---|--:|
| **median** | **14.5 s** |
| mean | 18.1 s |
| p90 | 23 s |
| a **1-character** reply | still 12–14 s |

The last row is the diagnosis: **latency is dominated by fixed per-turn costs, not by reply
length**. Shortening her answers won't make her fast.

### 1.2 Prompt size today (the input side)

From the reply-channel `cache-log.jsonl` records of 2026-07-14 (Gemini implicit caching active):

| | tokens |
|---|--:|
| input per reply call | **18,650–19,511** |
| of which cache-read (warm) | 8,064–12,095 |
| visible output (reply text) | 72–123 |
| hidden reasoning (the think phase; not in `output` — Gemini bills thoughts separately) | ~**400–600** est. (think blocks in the log run 1.5–2.5 KB) |

The system prompt dump is down to ~44 KB (2026-06-24) from ~111 KB (2026-06-12) — the
[PROMPT_OPTIMIZATION](../../docs/PROMPT_OPTIMIZATION.md) work already cut it ~2.5×, and
[II](../../docs/PROMPT_OPTIMIZATION_II.md) has a plan for another ~−50%. **But note the asymmetry:**
prompt size drives **cost** hard and TTFT only mildly (prefill is fast, and 8–12 K of it is served
from cache). The remaining latency lives elsewhere — in the *output* tokens and in *local* work.

### 1.3 Anatomy of one turn ([core/agent.py `reply()`](../../core/agent.py))

```
submit ──► PRE (local)          ──► MODEL CALL (blocking, non-streamed) ──► POST (local)          ──► shown
           ~0.5–2 s                 ~6–10 s                                  ~4.5–6.5 s MEASURED
           RAG query embed          hidden think phase 400–600 tok           2 × full store.json
           (e5-large, CPU) +        (inner voice: retrospective →            rewrite (12.9 MB each,
           cosine search;           voices → arbiter) BEFORE the             synchronous!) +
           every ~20 msgs: a        reply; then the reply; TTFB =            2 × local embeds +
           blocking compaction      the FULL completion (no                  vector append; closeness/
           model call               streaming anywhere)                      face are negligible
```

The **POST** figure is measured directly: the model call completes (cache-log ts / the
`lumi.think` log line) at 03:07:16 / 03:07:55 / 03:08:29 → the emotion renders at 03:07:21 /
03:08:00 / 03:08:35 — **5.3 s / 4.5 s / 6.5 s** during which the reply already exists but Віталік
is still watching a spinner. The culprits:

- **`Repository.append_message` × 2 → `_persist()`** ([state/local_store.py](../../state/local_store.py))
  serializes and rewrites the **entire 12.9 MB `store.json`** — twice per turn, on the critical
  path, and the cost **grows with the relationship** (the store only gets bigger).
- **`_index_messages` × 2** — `intfloat/multilingual-e5-large` (~560 M params) embedding on CPU,
  plus an append to the 450 MB `store.vectors.jsonl`.

And one structural fact: **nothing streams.** The TUI shows the reply only when the full
completion (think phase included) has arrived; the v0.14 voicer speaks it only after it lands in
the outbox. Every generated token — including the ~500 hidden ones — is serial wait time.

### 1.4 Targets

| mode | target | today |
|---|---|--:|
| casual text turn (feel) | **≤ 3 s** to visible text starting | 14.5 s |
| casual text turn (complete) | ≤ 5 s | 14.5 s |
| **live voice**: user stops speaking → **first audio** | **≤ 2–2.5 s** | ≈ 16–20 s |
| "think longer" turns (explicit register) | 10–30 s is **fine** — but *chosen*, announced, never the default | every turn |

---

## 2. The solutions

Each lever with its measured/estimated saving, effort, and risk. They are **independent and
compose**; §4 sequences them.

### S0 — Instrument first: per-stage turn timing (measure, don't guess)
Add `pre_ms / llm_ms / post_ms` (+ `think_chars`) to the existing per-call cache-log record and a
`/latency` read-out. Everything below then gets a before/after number instead of an estimate.
**Saving:** none directly — it protects every other phase. **Effort: hours. Risk: none.**

### S1 — Take the POST off the critical path · **−4.5–6.5 s, the biggest single win**
`reply()` currently returns only after persist + embed. Move everything after `validate()` (the
two `append_message`s, `_index_messages`, closeness write, face signal is cheap either way) onto a
**single background worker queue**: the turn returns the `EmotionState` the moment it's validated;
the queue preserves order; session close / app exit **drains the queue** (flush-on-exit).
**Saving:** the whole measured 4.5–6.5 s. **Effort:** ~1 day. **Risk: LOW-MEDIUM** — a hard crash
inside the window can lose the last turn (mitigate: drain on every prompt-build, i.e. the next
turn *waits* for the queue — ordering already forces this — plus flush-on-exit; S2 shrinks the
window to milliseconds). One seam note: the **next** turn's prompt needs the previous messages
persisted — the queue must be drained (or read-through) at prompt build; with S2 the drain is
instant, without it the wait just moves *between* turns where nobody is watching.

### S2 — Incremental store: stop rewriting 12.9 MB per message · kills the POST at the root
Swap the all-in-one `store.json` dump for an **append-only path behind the same `Repository`
seam** — either a JSONL journal (messages append; the rest of the store persists on close/interval)
or **SQLite** (the ARCHITECTURE already names it as the intended next backend; `sqlite-vec` is
likewise the named next step for the 450 MB vector file). The core does not change — this is
exactly the "swap the backend must not touch the core" contract.
**Saving:** turns S1's queue from "hides seconds" into "there are no seconds to hide"; also fixes
the store-growth time bomb (persist cost is O(history) today). **Effort:** 1–2 days. **Risk: LOW**
(the seam exists; migration script + keep `store.json` export for back-compat).

### S3 — Stream the reply · **perceived TTFT ~10 s → ~2–3 s**, prerequisite for live voice
Stream the completion into the TUI bubble as it generates. The contract survives untouched:
- the **think phase streams first** → suppress it live (or stream it into the think box — nicer),
  show reply text only after `</think>` / the thought-part boundary (Gemini/Anthropic deliver
  reasoning as separate parts — easy; the inline-tag fallback needs a streaming tag filter);
- the trailing `<emotion>/<intent>/<style>` tags and `set_state` arrive **at the end** → the
  `EmotionState` is validated exactly as today and the renderer/face updates on completion. **The
  locked v0.3 contract is honored: emotion is still the model's own, still validated — never
  inferred mid-stream.**
Core seam: an optional `on_delta` callback / iterator on `LLMClient` (per-provider streaming;
Anthropic + Gemini + OpenAI all support it; **mock streams in tests**). TUI: v1.2's non-blocking
input composes naturally.
**Saving:** no wall-clock change, but the *felt* latency becomes time-to-first-visible-word: with
S4 that's ~1–2 s. For voice it *is* wall-clock (sentence-chunked TTS, S6). **Effort:** 2–3 days
(provider-by-provider; Anthropic first). **Risk: MEDIUM** — the tool-loop turns (file/wiki/news)
stream only their final round; tag filtering must never leak a half-tag; malformed-stream fallback
= today's non-streamed path.

### S4 — Register-routed thinking: fast by default, deep when it matters · **−3–7 s on casual turns**
This is the "sometimes she can think longer" ask, and it is **already designed** as
**[v1.6 MODEL_ROLES](MODEL_ROLES.md)** (talking / thinking / emotional). What this doc adds is
the *latency* framing and the interim knobs:

- **The hidden think phase is a latency tax on every turn.** The v1.1 inner-voice
  (retrospective → three voices → arbiter) generates **400–600 tokens before the first reply
  character** — at ~50–100 tok/s that alone is **4–8 s**. On the **talking** register, replace it
  with a **one-line arbiter** (declare the move + emotion, skip the essay — an authored variant of
  [core/inner_voice.md](../../core/inner_voice.md), zero engine change); the **emotional /
  thinking** registers keep the full monologue.
- **Thinking budget by register.** The v0.39 `effort → thinkingBudget` mapping already ships:
  talking = `effort=low` (or budget 0), thinking = high/dynamic. Today `LUMI_EFFORT` is one global
  knob — the register router makes it per-turn.
- **Model by register.** talking = the profile's fast tier (haiku 4.5 / gemini-flash /
  gpt-5.5-mini — [core/models.toml](../../core/models.toml) already names them), emotional/thinking
  = the frontier reply model. Fast tiers also cut TTFT and 5–25× the cost.
- **Interim, zero-code, today:** `LUMI_EFFORT=low` + a trimmed `inner_voice.md` retrospective —
  banks a chunk of the saving while v1.6 is built.

**Saving:** on casual turns the model call drops ~6–10 s → **~1.5–3 s** (short think + fast tier +
short reply). **Effort:** rides v1.6 (~2–3 days) + an authoring pass. **Risk: MEDIUM** — register
misroute on a loaded message (v1.6's stickiness + "unsure → escalate" design addresses exactly
this; the lexical stage keeps short messages cheap). **Invariant: routing reads the message, never
her mood — and never competence.**

### S5 — Keep shrinking the prompt (cost lever, mild latency lever)
Execute [PROMPT_OPTIMIZATION_II](../../docs/PROMPT_OPTIMIZATION_II.md) P1–P5 (19 K → ~10 K input).
Worth doing for cost and cache stability regardless; expect only ~0.5–1 s of latency from it
(prefill + cache-read are not where the time goes — §1.2). Listed here so nobody expects prompt
work alone to make her fast. **Risk:** per that doc's own table.

### S6 — Live voice mode (the target architecture)
A **local duplex loop** — a "live mode" the TUI (or a sibling console app) enters, distinct from
the ambient v0.14 voicer / v0.26 dictator (which stay: they're the *asynchronous* pair; the
**file-bus FIFO is the wrong shape for a <2 s round-trip** — polling files adds seconds by design,
so live mode is a direct in-process path):

```
mic ─ streaming STT ─ endpoint ─► turn (talking register, S4) ─ stream (S3) ─ sentence chunker ─ streaming TTS ─ speaker
      (Deepgram nova streaming /        LLM TTFT ~1–1.5 s              first sentence            (ElevenLabs flash/realtime,
       ElevenLabs realtime — the        (cached prefix, fast tier)      ~0.5–1 s                   ws, ~0.1–0.3 s first byte)
       /voice STT seam already
       names Deepgram)                                    barge-in: VAD while speaking → stop playback, cancel generation
```

**Latency budget (the arithmetic that makes ≤2.5 s honest):** STT finalize ~0.3 s + LLM TTFT
~1–1.5 s + first sentence ~0.5–1 s + TTS first byte ~0.2 s ≈ **first audio ~2–2.5 s** — *only if
S3 + S4 exist*; on today's pipeline the same wiring would speak after ~15 s.
Details that matter: sentence-chunk TTS (never wait for the full reply); the face/emotion updates
at completion (never inferred early — the v0.3 contract); **"think longer" in voice = an authored
spoken acknowledgment** ("хм, дай подумаю…") TTS'd immediately when a turn routes to the thinking
register, while the deep call runs — honest, in-character, and it buys the register its 10–30 s
without dead air. **Effort:** 1–2 weeks. **Risk: MEDIUM-HIGH** — barge-in/VAD tuning, echo
(headphones first), paid streaming APIs (**mock STT/TTS/LLM streams in tests**, no paid CI).
Off by default (`LUMI_LIVE_VOICE` + keys). Web sibling later (v3.2/v3.4 reuse the same adapters).

### S7 — Small fry (do opportunistically, after S0 proves them)
- **RAG pre-work:** if S0 shows the e5-large query embed + cosine over the 450 MB JSONL costs
  >0.3 s, load vectors as one warm numpy matrix / move to `sqlite-vec` (rides S2).
- **Compaction off the turn:** the every-~20-messages compaction is a *blocking housekeeping model
  call inside a user turn* — run it in the S1 background queue instead (it only feeds the *next*
  prompt).
- **Retry/timeout budget:** the 2026-07-14 log shows turns lost to 30–60 s provider errors+retries;
  in live mode cap the reply path to one fast retry, then the honest "не можу відповісти" line.

---

## 3. Evaluation summary

| # | Lever | Saving (casual turn) | Effort | Risk | Depends on |
|---|---|---|---|---|---|
| S0 | stage timing | 0 (protects the rest) | hours | none | — |
| S1 | async POST | **−4.5–6.5 s** | ~1 day | low-med (crash window) | — |
| S2 | incremental store | makes S1 structural; O(1) persist | 1–2 days | low | Repository seam (exists) |
| S3 | streaming | perceived −7–8 s; wall for voice | 2–3 days | medium | LLMClient seam |
| S4 | registers (v1.6) | **−3–7 s** model-side | 2–3 days + authoring | medium | v0.41 profiles ✅, v1.6 |
| S5 | prompt P1–P5 | ~−0.5–1 s (big on cost) | per PO-II | per PO-II | — |
| S6 | live voice | first audio ~2–2.5 s | 1–2 wks | med-high | **S3 + S4**, /voice adapters ✅ |
| S7 | RAG/compaction/retries | −0.3–1.5 s | small each | low | S0 numbers |

**Projected trajectory (median casual turn):**

| after | full reply | first visible / audible |
|---|--:|--:|
| today | 14.5 s | 14.5 s |
| + S1 + S2 (+S0) | ~8–10 s | ~8–10 s |
| + S4 interim (effort=low + short inner voice) | ~5–7 s | ~5–7 s |
| + S3 streaming | ~5–7 s | **~2–3 s** |
| + S4 full (v1.6) | **~2.5–4 s** | ~1.5–2 s |
| + S6 | — | **first audio ~2–2.5 s** |

---

## 4. Phasing (roadmap slots)

- **S0 · instrument now (not a version).** The per-stage `pre_ms`/`llm_ms`/`post_ms` timing + a
  `/latency` read-out ships as an **immediate fix**, ahead of everything, so each lever gets a
  before/after number. (A few hours, no risk.)
- **v1.4 · the durable POST fix (S1 + S2).** The async post-turn queue **and** the incremental store
  behind `Repository` (append-only / SQLite, O(1) persist), shipped **together**: S1 alone is a
  band-aid (crash-loss window + the store-growth time bomb — persist is O(history) today); with S2 the
  POST fix is **complete and durable**. No contract change, **14.5 s → ~6–8 s felt.**
- **v1.5 · streaming (S3).** Streaming behind `LLMClient` (Anthropic first, then Gemini/OpenAI).
  **Felt latency ~2–3 s.**
- **v1.6 · registers = the [MODEL_ROLES](MODEL_ROLES.md) phase (S4, incl. S4-interim) = LAT-3.**
  Register-routed thinking with latency as the driving DoD (casual turn ≤ 4 s full). **S4-interim**
  folds in here: `LUMI_EFFORT=low` for the talking register + the trimmed talking-tier `inner_voice.md`
  (the short inner voice the fast register uses).
- **S7 · optional for now** — the RAG/compaction/retry fixes are small, low-risk levers taken
  opportunistically (e.g. compaction off the turn rides naturally with the v1.4 queue); not a phase
  of their own.
- **LAT-4 · live voice mode (S6) — on hold.** A later phase beside the voice family; hard-gated on
  v1.5 + v1.6. DoD (when taken up): median stop-speaking → first-audio ≤ 2.5 s over 20 live turns;
  barge-in works; the thinking register produces a spoken acknowledgment, not silence.

---

## 5. Invariants (unchanged, pinned)

- **The emotion contract is untouched** — `{reply, emotion, intensity}`, model-emitted, validated,
  rendered on completion; streaming never infers or pre-renders an emotion (v0.3, EMOTION.md).
- **Core stays interface-independent** — streaming is an `LLMClient` seam + a callback the TUI
  supplies; the store swap hides behind `Repository`; live mode is a client of `core.reply`, not
  logic inside it.
- **Speed never trades competence or persona.** The registers route *depth of deliberation*, not
  who she is; the full canon + boundaries ride every prompt on every tier (the
  PROMPT_OPTIMIZATION-II hard line). A fast turn is still **her**.
- **She may always take the slow path** — a loaded message escalates (v1.6), and in voice she
  *says* she's thinking. Fast is the default, not a cage.
- **Off by default, mocked in tests** — `LUMI_LIVE_VOICE`, streaming behind a flag until proven;
  no paid APIs in CI (mock streams for LLM/STT/TTS); each phase A/B-able and reversible.
- **Per-user isolation** as everywhere (the queue/store changes carry `user_id` through unchanged).
