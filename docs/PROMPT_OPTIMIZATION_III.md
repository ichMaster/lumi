# Prompt Optimization III — the new whales (thoughts & the inner voice)

The successor to **[PROMPT_OPTIMIZATION.md](PROMPT_OPTIMIZATION.md)** (Phases 0–4) and
**[PROMPT_OPTIMIZATION_II.md](PROMPT_OPTIMIZATION_II.md)** (tool-pull memory). Those plans worked:
the memory tiers that were **73% of the prompt** in June are **~9% today**. This doc re-measures the
prompt after that victory and finds the *new* top targets — the **thought stream** (one-fifth of the
prompt, blown up by two essay-length thoughts) and the **inner-voice think instruction** (15%, and
also the driver of 400–600 hidden reasoning tokens per turn — the one lever that cuts size *and*
latency at once). The latency side lives in [specification/features/LATENCY.md](../specification/features/LATENCY.md);
this doc is the size side.

> **Status (2026-07-14): measured; proposals open.** Method: the system prompt was **rebuilt
> offline** through the same `core._system_prompt` path `reply()` uses (read-only on the live
> store — the `/prompt` dump needs a turn in the current session, so a fresh session dumps empty).
> Token counts are `chars/3` estimates (the Cyrillic lower bound of II) — **percentages are exact**.
> Real Gemini-counted input per reply call (cache-log, same day): **18,650–19,511 tok**, of which
> 8–12 K cache-read — i.e. Ukrainian runs ~1.7–2 chars/tok, so absolute savings below are
> *understated* by ~1.5–1.8×. The rebuild lacks the mood block, the auto-RAG block, and the
> in-session digest (≈ +2–3 KB in the real prompt); `[MESSAGES]` + tool schemas ride on top.

---

## 0. TL;DR

- **The II plan shipped and worked**: system prompt **~111 KB (06-11) → 62.6 KB (06-20) → 44 KB
  (06-24) → 29.1 KB today** (~4× down). Sessions=gist, day/week index, facts core-only, style trim,
  thoughts capped at 10 lines — all live in `.env`.
- The old whales (conversations + days + weeks) are now **2.6 KB combined**. The new top-3:
  **thoughts 19.7%** · **inner-voice instruction 15%** · **facts core 10.8%**.
- The thoughts block is capped by **count** (10) but not by **length per thought** — two scheduled
  long-form thoughts (1.8 + 2.0 KB) are **⅔ of the block**. A per-thought snippet cap is the
  single biggest remaining win (**T1**, small code).
- The inner-voice instruction is the **double lever**: 4.4 KB of prompt *and* the cause of the
  400–600-token hidden think phase before every reply (**T2**, authoring only).
- Ceiling: after T1–T4 the system prompt is **~21 KB, ~65% canon + instructions** — near its floor
  without touching who she is. Further wins live in [LATENCY](../specification/features/LATENCY.md)
  S4 (registers), not in more memory surgery.

---

## 1. Measured current state — 2026-07-14

System (offline rebuild) = **29,118 chars ≈ 9.7 K est-tok** (≈ 15–17 K real tok incl. mood/RAG).
Active knobs: `LUMI_SESSION_DAYS=1`, `LUMI_SESSION_FORMAT=gist`, `LUMI_MEMORY_INDEX=on`,
`LUMI_THOUGHTS_MAX_LINES=10`, `LUMI_FACTS_CORE_ONLY=on`.

| Section | chars | % | Nature | vs 06-20 (II §1) |
|---|--:|--:|---|---|
| **# Що в мене на думці (thoughts, 24h)** | **5,744** | **19.7** | volatile tail | 5,806 → *uncapped per-thought* — **the un-shrunk one** |
| **# Внутрішній голос (think-phase, v1.1)** | **4,358** | **15.0** | static, cached | *new since II* (inner voice + retrospective + arbiter) |
| **## Факти (core-only)** | **3,157** | **10.8** | session-stable | 8,603 → core-flag shipped (P3 ✅) |
| ## Як тече розмова (canon) | 2,104 | 7.2 | static | canon growth (v1.1 moves) |
| # Як відповідати | 1,690 | 5.8 | static | instructions |
| ## Останні розмови (gist) | 1,636 | 5.6 | session-stable | 16,268 → **P1 ✅ (−90%)** |
| ## ЗАБОРОНИ | 1,392 | 4.8 | static, **untouchable** | — |
| ## Голос | 1,150 | 3.9 | static | canon |
| canon rest (Хто ти … Творче «я», натальні) | ~5,700 | ~19.6 | static | — |
| ## Останні дні + тижні (index) | 943 | 3.2 | daily-stable | 15,126 → **P2 ✅ (−94%)** |
| # Стиль відповіді | 758 | 2.6 | static | 3,542 → **P5 ✅ (−79%)** |
| # Близькість | 412 | 1.4 | per-turn tail | — |
| *(not in rebuild: mood ~1 KB · auto-RAG ≤1.2 KB · in-session digest)* | ~2–3 K | — | tail | — |

**Thoughts detail** (the last-10 injected, from the store): lengths
`223, 1792, 305, 1957, 243, 205, 220, 216, 227, 205` chars — the two long ones are scheduled
`%think`/`%brief` musings; **3,749 of 5,593 chars (67%) sit in 2 of 10 thoughts**.

**What II shipped, for the record:** P1 (gist sessions) ✅ · P2 (day/week index) ✅ · P3 (facts
core + recall scope) ✅ v0.36 · P4 (thoughts *count* cap) ✅ (150 → 10 lines) · P5 (style trim) ✅.
The decision rule of II §2 (*index in the prompt, body behind a tool*) now applies to the one tier
it was never pointed at: **her own thoughts**.

---

## 2. Proposals (ranked by value)

### T1 — Per-thought snippet cap · −~12% of system (the biggest remaining win)
**Now:** `LUMI_THOUGHTS_MAX_LINES=10` caps *how many*; nothing caps *how long* — one `%brief` essay
injects 2 KB.
**Change:** clamp each injected thought to a snippet (~250 chars + `…`), new knob
`LUMI_THOUGHTS_SNIPPET_CHARS` (mirrors `rag_snippet_chars`). The **full body stays in the store**
— `/thoughts` shows it, and **v1.14** (*Lean memory IV: thoughts cap + thought recall* —
`recall(scope=thoughts)`, already on the roadmap) is the pull path; this proposal is v1.14's
prompt half, worth pulling forward.
**Saving ≈ 3.4 KB (5.7 → ~2.3 KB).** **Effort:** small code (one clamp in the injection).
**Risk: LOW** — her own ephemera; the feedback loop needs the gist, not the essay; the tail keeps
its dated index shape (II's guardrail: never to zero, drop to the index).

### T2 — Trim the inner-voice instruction · −~8% AND seconds of latency (the double lever)
**Now:** [core/inner_voice.md](../core/inner_voice.md) = 4.4 KB re-sent every turn (cached), and
its retrospective → three voices → arbiter essay makes her generate **400–600 hidden tokens before
the first reply character** (measured think blocks 1.5–2.5 KB in the log) — at ~50–100 tok/s that
is **4–8 s of every turn** ([LATENCY](../specification/features/LATENCY.md) §S4).
**Change:** an authoring pass to ~2 KB — keep the skeleton (retrospective, the three voices as
one-liners, the arbiter's closed enum + declared move), cut the worked examples and repeated
phrasing; explicitly instruct a **short** monologue (3–5 sentences), not an essay. The full v0.43
register split later gives the talking tier a one-line arbiter variant.
**Saving ≈ 2.4 KB prompt + measurable latency.** **Effort:** authoring only, zero code.
**Risk: MEDIUM** — this instruction *is* the anti-mirror engine; over-trim and the moves get
lazy. **Mitigation:** the v1.1 declared-vs-done retrospective validation still runs (it reads the
history tags, not the instruction length); A/B a day of real use + diff the think blocks.

### T3 — Re-rank the facts core down · −~4%
**Now:** core-only injection = 3.2 KB.
**Change:** lower `LUMI_FACTS_CORE_MAX` a notch and let the session-start re-flag re-rank; the
tail is already behind `recall(scope=facts)` (P3 ✅). Boundaries/agreements stay pinned past the cap.
**Saving ≈ 1 KB (3.2 → ~2 KB).** **Effort:** config + one re-flag pass. **Risk: LOW–MEDIUM** — the
"she forgot me" failure mode of II §6; the pinned class and the auto-RAG backstop hold the floor.

### T4 — Canon-adjacent authoring compression (optional, careful) · −~5%
«Як тече розмова» (2.1 KB — grew with the v1.1 moves), «Як відповідати» (1.7 KB), «Голос» (1.2 KB)
have redundant phrasing an authoring pass can tighten by ~⅓ **without touching a single boundary**:
**ЗАБОРОНИ, цінності/межі, the emotion palette and the honesty clauses are out of scope** (the II
§6 hard line stands verbatim).
**Saving ≈ 1.5 KB.** **Effort:** authoring + a careful A/B. **Risk: MEDIUM** — voice drift; this is
last for a reason.

### Explicit non-targets
The canon core and every boundary/safety block stay **always-injected, never trimmed for size**
(II §6). The `[MESSAGES]` window and tool schemas are small today (short sessions, tools mostly
off) — not worth touching. The mood/natal blocks (~1.5 KB) are the temperament engine — keep.

---

## 3. Combined effect & the floor

| Stage | system (chars) | Δ |
|---|--:|--:|
| today (measured) | 29,118 | — |
| + T1 thoughts snippet | ~25,700 | −12% |
| + T2 inner-voice trim | ~23,300 | −8% |
| + T3 facts re-rank | ~22,100 | −4% |
| + T4 canon-adjacent (optional) | **~20,600** | −5% |

Real input per turn ≈ **19.5 K → ~14 K tok** (Gemini count), most of it cache-read. After T1–T4
the prompt is **~⅔ canon + instructions** — the irreducible "who she is". That is the floor of
*size* work; the next wins are *shape* work: [LATENCY](../specification/features/LATENCY.md) S4
registers (a talking-tier think phase), not more memory surgery.

## 4. Sequencing & guardrails

1. **T1** (small code + the `LUMI_THOUGHTS_SNIPPET_CHARS` knob; pulls v1.14's prompt half forward).
2. **T2** (authoring; A/B against a day of real think blocks — the move quality watch below).
3. **T3** (config); **T4** only if the squeeze is still wanted.

Guardrails, inherited from II §8 plus one new:
- **Reconstruction test (T1):** plant a reference to a clipped thought's detail in the next
  message → assert she reaches it (`/thoughts` today, `recall(scope=thoughts)` when v1.14 lands).
- **Move-quality watch (T2):** the declared-vs-done intent validation rate must not drop after the
  trim; diff a day of think blocks before/after.
- **"She forgot X" watch (T3):** unchanged from II — a regression reverts the flag.
- **Cache-write watch:** the thoughts block lives in the volatile tail; T1 shrinks the tail churn
  — confirm in `.lumi/cache-report.md`, not just raw size.
- Every step behind config, reversible, A/B'd by diffing a fresh `/prompt` dump (run it **after at
  least one turn** — a fresh session dumps empty).

---

## v1.3 — Explicit Gemini prompt cache: measured before/after (PENDING operator run)

The v1.3 phase (LUMI-184..187) makes the Gemini prompt cache **explicit** so a reply after a pause
stays warm. The before/after is measured from `.lumi/cache-report.md` once the flag is exercised —
`LUMI-185` added `latency_ms` to every cache-log record and `LUMI-187` a **Continuity** table
(post-gap reply turns with their `cache read` and latency).

**Method (operator, paid — a few turns):**
1. `LUMI_GEMINI_EXPLICIT_CACHE=off`, `LUMI_PROMPT_CACHE_TTL=1h` — chat, pause > 20 min, reply again.
2. `LUMI_GEMINI_EXPLICIT_CACHE=on` — repeat.
3. Compare the **Continuity** rows: OFF a post-gap turn shows low `cache read` (cold re-read) and a
   higher `latency`; ON it should show `cache read` ≈ the cached prefix and lower TTFT.

| Scenario | Post-gap `cache read` | Post-gap latency |
|---|--:|--:|
| OFF (implicit) | _pending_ | _pending_ |
| ON (explicit)  | _pending_ | _pending_ |

Run `GEMINI_API_KEY=… uv run python scripts/gemini_probe.py --cache gemini-3.1-pro-preview` first to
confirm `cachedContents` support + the `cache+systemInstruction` constraint on the active model, then
fill the table from two `.lumi/cache-report.md` snapshots.
