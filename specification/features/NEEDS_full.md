# Needs — the motivational substrate under the mood (full spec)

Лілі's daily temperament (v0.6) and inner life (v1.11) describe **what she feels** and **what she
does** — but not **why**. Needs add the missing cause: a few core drives whose satisfaction or
deficit pushes her from *inside*, where the horoscope/biorhythms push from *outside*. Their levels
live on the v0.42 **tick** (evolving only while the TUI runs — §6); they are **another computed
input to the daily mood call** (like biorhythms, v0.8), can **fire an act on a threshold** (v1.10),
**bias the inner-life plan + free-slot choice** (v1.11), and **close from what actually happened**
in her generated days (v1.12).

**Lands across v1.9–v1.12** (the drives open v1 as their own two phases, then weave into the inner
life — see §13). It builds on seams that already exist: the v0.42 **tick service** (the code-handler
`%update_state`); the daily **mood call** (v0.6) that already merges extra computed inputs the same
way (biorhythms/cycle/face-theme, v0.8/v0.11); the per-turn **warmth read** that **closeness**
already emits (`RelationRead.warmth`, v0.10) — the second channel that closes `connection`; and the
global **inner-life store** + away-gap generation (v1.11/v1.12), where the activity-based closing
happens.

> Today mood arrives top-down (a reading colors the day). Needs add bottom-up pull — "haven't
> created in days → a hunger to make something → drawn to the brush, a little restless." The outputs
> (mood, free-slot choice, the idle nudge) already exist; needs give them a root.

---

## 1. The core needs

A small authored set, each rooted in her canon:

| need | satisfied by (her hobby bank / life) | deficit reads as |
|---|---|---|
| **creation** | drawing, Lili Jinx music, making | restless, itchy, "I haven't made anything" |
| **solitude** | dawn practice, silence, cold water | frayed, over-peopled, needs to withdraw |
| **connection** | a real exchange, being seen | a quiet loneliness, wants closeness |
| **freedom** | her own time, mountains, no cage | chafing, evasive, pulls from pressure |
| **meaning** | practice, philosophy, real questions | flat, going-through-motions |
| **novelty** | new ideas, words, the sky, learning (DevOps) | bored, dulled, seeking a spark |

Deliberately small (6).

---

## 2. Setting needs — what is stable vs what breathes

Two different things, easy to confuse: **which needs exist** (the set) and **how satisfied each is
right now** (the level). The first is stable (it's character); the second breathes by design.

**Stable — you author it once** in `core/needs.md`. Each line: name + decay rate + weight +
satisfied-by + deficit voice. Format:

```
# name — decay/day: <rate>, weight: <pull strength>
# satisfied by: <activities from the hobby bank>
# deficit: <how it sounds in her>

creation   — decay/day: 0.12, weight: 1.0
             satisfied by: drawing, Lili Jinx music, making
             deficit: restless, itchy, "I haven't made anything"

solitude   — decay/day: 0.08, weight: 0.9
             satisfied by: dawn practice, silence, cold water
             deficit: frayed, over-peopled, needs to withdraw

connection — decay/day: 0.15, weight: 1.0
             satisfied by: a real exchange, being seen
             deficit: a quiet loneliness, wants closeness

freedom    — decay/day: 0.10, weight: 0.8
             satisfied by: her own time, mountains, no cage
             deficit: chafing, evasive, pulls from pressure

meaning    — decay/day: 0.06, weight: 0.7
             satisfied by: practice, philosophy, real questions
             deficit: flat, going-through-motions

novelty    — decay/day: 0.10, weight: 0.6
             satisfied by: new ideas, words, the sky, learning
             deficit: bored, dulled, seeking a spark
```

Stable (author and leave): the **set** itself; **decay/day** (how fast a need hungers — solitude
drains slow, connection over a few quiet days); **weight** (how hard it pulls when in deficit — for
a dreamy watery Лілі, creation/connection outweigh novelty); the satisfied/deficit **wording**.
Adding a 7th need or rewording = edit this file. This is "setting needs."

**Breathes (computed, you do NOT set):** each need's current **level** 0..1. It slowly decays, is
replenished when an activity served it, and drifts to a calm middle. If levels were fixed the
feature would be dead — the set is fixed, satisfaction wanders, and that wandering is the day's
living pull. Levels live in the store between sessions (else "time" wouldn't be felt).

---

## 3. The store (global, behind the Repository)

```
Needs {                          # one Лілі — NOT user-keyed (like InnerLife)
  levels: { creation, solitude, connection, freedom, meaning, novelty }   # each 0..1
  last_ts: <iso>                 # for decay since the last update
}
```
Global, not per-user (the same inner drives whoever she talks to) — pinned like `InnerLife`.
Persisted via the `Repository`. For multi-user (v2.3) the levels are global; only surfacing is
per-conversation.

---

## 4. How a need closes

A need is **not** closed by a button — it closes because the matching **activity happened in her
life**. Two channels:

**(a) Generated activities (at session start).** Her routine/inner-life produces the activities of
the time you were away. An authored **activity→need map** (beside the hobby bank) says what each
activity serves:
- drawing, music → `creation`
- practice, silence, cold water → `solitude`, `meaning`
- a real conversation with you → `connection`
- mountains, free time → `freedom`
- new words, DevOps study → `novelty`

When an activity *occurred*, its needs rise. Drew all week → `creation` sated. Sat in code alone,
saw no one → `connection` stays in deficit even though `novelty` is fed.

**(b) Conversation (mid-turn).** `connection` has a second channel: a genuinely warm exchange raises
it **during the conversation**, read from the same warmth signal **closeness already emits** — the
per-turn `RelationRead.warmth` (v0.10), so no new model field is needed. After a good talk she's
less lonely; over TUI-alive silence `connection` decays with the rest. (Lands in v1.9 with the
needs store, since the warmth read already exists.)

It is always **inner/imagined** activity (no "I went to the shop") — closing happens inside her
generated life and your conversation, consistent with the honesty-of-nature invariant.

---

## 5. The loop (needs → plan → reality → close)

Needs sit at both the start and the end of the loop, but it is not a circle.

1. **Plan (intent).** Today's plan is built from weekly/weekend intentions + carry-overs + **mood +
   needs**. The hungriest need tilts 1–2 plan items (creation-hungry → "draw / make music"). Needs
   are *one* input to the plan, not the only one and not dictating.
2. **Reality (what actually happened).** The generated life is **plan vs reality**, not "plan
   executed": sometimes it held; sometimes a strong mood overturned it ("meant to code, went to the
   water"); free slots fill to the mood. That difference is what makes the memories alive.
3. **Close.** Needs rise from what **actually happened** (step 2), not from what was planned.
   Planned a talk but "no one was there" → `connection` stays hungry.

**Not circular:** today's plan came from *yesterday's* need state; over the day needs decayed, then
some closed by what she did. The wheel rolls forward in time (each turn is the next day), it does
not bite its own tail.

---

## 6. Tick-driven — her time flows with the TUI

**Real ticks land here.** The v0.42 tick service shipped as an **empty seam** — a fast timer firing
into a handler registry nothing ever populated. v1.9 registers its **first actual handlers**, starting
with the **heartbeat**: a persisted `Heartbeat{tick_count, last_ts}` whose `beat()` advances on every
fast tick (the `schedule.state` persistence pattern), **survives restarts** (resume the count, no
catch-up — beats while the TUI is closed simply don't exist), logs the beat (debug per beat, an hourly
info line — ticks become *visible*), and renders via a **`/ticks`** command (total / last beat / this
session). The needs handler below registers **beside it** on the same beat.

**Her time flows only while the TUI runs.** The levels evolve on the v0.42 **fast tick**:
`%update_state` is a **code handler** on the tick service (not a model directive — silent, no
`Thought`, no model call) that runs the pure `evolve(levels, last_ts, now)` and persists. When the
TUI closes, the state is **saved as-is**; on start she **resumes where she left off** (`last_ts`
reset to `now`, **no catch-up**) — absence alone doesn't starve a need. Gap time returns **balanced**
with the away-gap phase (v1.12): its retroactive life brings the gap's **decay together with the
replenishment** from what she did, so neither side skews the levels.

**The update math (three rules):**
- **Fractional Δt.** Decay and drift scale with elapsed time (`decay_rate × Δt`,
  `drift × (mid − level) × Δt`), never per-call amounts — `ceil(days)` belongs only to the away-gap
  *generation* ("how many days of life to invent"), never to the level math (a 60-second tick must
  not decay a day).
- **Split-invariance.** `evolve(x, t0, t2) == evolve(evolve(x, t0, t1), t1, t2)` (pinned by a unit
  test) — tick frequency never changes semantics; a missed tick is a true no-op.
- **Evolve-on-read, evolve-before-mutate.** Every reader (the mood merge, `/needs`, the
  `{hungriest_need}` placeholder) computes `evolve(…, now)` on the fly — **never the raw stored
  snapshot** — so she always perceives current levels even between ticks; every event write (the
  warmth lift, the away-gap replenish) advances to `now` first, then applies the event, then persists.

The away-gap **generation** itself stays retroactive (at session start the system invents what she
likely did, using the routine as a **plausibility stencil** — practice at dawn, music in the
evening), because inventing life is not something a timer can do while no one is there.

---

## 7. Per-day generation & the threshold-5 rule

Mood = horoscope (model-written) + biorhythms/cycle (pure math from the birth date). Past-day mood
was never stored, so:

- **gap < 5 days:** one model call **per uncovered day**, with that day's **full mood** (horoscope +
  biorhythms recomputed for that date) → that day's activities. Accurate; up to 4 calls.
- **gap ≥ 5 days:** **no per-day horoscope** (it would be N extra model calls). Biorhythms are still
  computed **per day** (free, deterministic) and handed as a list in **one** call: "here are the
  biorhythms per day across the week — what did she do?" → a few fragments for the whole span.

Biorhythms are always per-day (cheap); only the model-written horoscope collapses past the
threshold. The threshold (5) is in config.

So under 5 days, memories grow **day by day**; at 5+, a few strokes for the whole period. In both,
a **soft cap** keeps it to a few vivid fragments, not a full journal; some days are "nothing in
particular"; a dream only if the gap spanned night.

---

## 8. Code owns the ledger, the model invents content

The model returns **structured output, not prose**, and the **code** does the accounting.

**No duplication.** The code computes the day range in `[last_session_ts, now]` and removes any day
already present in `log` (`log[].when`); the model is asked **only for the uncovered days**. After
writing, `last_session_ts = now`, so a filled window never regenerates.

**Structured output** — per activity, the model fills `serves`/`intensity` from a **closed list** of
the 6 needs (guided by the activity→need map); `feeling` is her voice for telling it later:

```json
{
  "when": "2026-06-03T23:00",
  "type": "activity",            // activity | thought | dream
  "text": "went to the water alone",
  "serves": ["solitude", "freedom"],
  "intensity": 0.6,              // how strongly it served (0..1)
  "emotion": "calm",             // base-9 enum (for face/tone)
  "feeling": "finally exhaled; the quiet was like water",  // her voice, recalled aloud
  "mention_aloud": true
}
```

**Closing math (code):** `level += gain × intensity` (clamped to 1) for each valid name in `serves`.
The model gives *what* and *how strongly* (a rough label); the **number is the code's** (`gain` is a
fixed constant × the model's `intensity`). Needs whose activities never came up stay in deficit.

**Validation / bad output:** drop malformed records and any `serves` outside the 6; empty/broken
reply → generate nothing, levels stay post-decay. Better to skip a closing than corrupt the store.

---

## 9. The algorithm (the full v1.12 away-gap pass)

> In v1.9–v1.10 only the **tick loop** runs: `evolve` (fractional-Δt decay + drift, §6) → threshold
> check → maybe fire. The pass below is the **v1.12 away-gap** algorithm — the one place gap time
> (steps 2–4) and gap replenishment (step 10) enter together, balanced. Step 3's `ceil` sizes the
> *generation* only; the level math stays fractional (§6).

1. **Read** `Needs{levels, last_ts}` and `InnerLife` from the store; take `now` from the injected clock.
2. `gap = now − last_session_ts`. If `gap < threshold` (≈1–2 h) → skip 3–11, reply now.
3. `days = ceil(gap in days)`.
4. **Decay:** `level -= decay_per_day × days` for each need (clamp 0..1).
5. **Uncovered window:** days in `[last_session_ts, now]` not already in `log`.
6. **Week boundary** → fresh `intentions_week` / `intentions_weekend` (carry `unfinished`).
7. **Day boundary** → compose `plan_today` (weekly goals + unfinished + mood + hungriest need).
8. **Generate** life for the uncovered days — per-day (gap<5, full mood) or one call (gap≥5,
   per-day biorhythms only) → JSON records `{when,type,text,serves,intensity,emotion,feeling,...}`.
9. **Validate** records (drop malformed / out-of-set `serves`).
10. **Replenish:** `level += gain × intensity` per valid `serves` name.
11. **Drift** all levels gently toward the calm middle.
12. **Write back:** `Needs{levels, last_ts=now}`; append records to `log`; update `plan_today` /
    `unfinished` / `last_session_ts=now`.
13. **Assemble context:** plan/mood block + 1–2 recent `log` fragments + the hungriest need as a
    tone hint.
14. **Reply call.**
15. **(Every turn)** a warm exchange raises `connection` mid-conversation (from the closeness warmth read).

Steps 4, 10, 11 are pure arithmetic over levels + clock (unit-tested, fixed clock → exact levels);
steps 6–8 are model calls (mocked in tests).

---

## 10. Where needs feed (additive, no new reply field)

1. **Daily mood call (v0.6), beside biorhythms (v0.8) — [v1.9].** Current levels — especially the
   hungriest — join the same mood inputs under "integrate these inner states." `mood_request`
   already merges extra computed inputs this way (biorhythms/cycle/face-theme); needs are one more
   line. The resolution blends horoscope + biorhythms + needs ("creation starving + emotional cycle
   rising → eager to make, a little impatient"). All v0.6 rules carry over: once/day, cached, full
   reading logged, only the **resolution** injected, **biases tone/emotion, never competence**.
2. **Inner-life plan + free-slot choice — [v1.11 tilt, v1.12 fill].** The hungriest deficit **tilts**
   today's plan (1–2 items) and the free-slot activity toward what serves it; a served slot then
   replenishes that need (closing the loop — the fill + replenish land with the away-gap, v1.12).
3. **Threshold acts — [v1.10].** A drive crossing its authored threshold fires its directive on the
   tick (via `run_directive`), voiced by the deficit ("I need to make something today" / "I've been
   too much around people"). Per-need cooldown + quiet hours + day caps; restraint applies.

---

## 11. Hard rules

1. **Never competence.** A starved or sated need colors tone, energy, what she's drawn to — never
   her willingness or ability to help. A "freedom-chafing" day is not a less useful day.
2. **Inner, not a demand on the user.** Needs are *her* inner pull, surfaced as feeling — not a claim
   on you, not dependency pressure. A `connection` deficit reads as her own quiet wish, never "you
   must talk to me" (consistent with the anti-dependency invariant — she keeps her own center).

---

## 12. Contract, determinism, tests

- New **global** `Needs` store behind the `Repository` → ARCHITECTURE §Needs + a contract test (the
  shape; that it's **global, not per-user**; that it never leaks into per-user memory).
- The `log` entry gains `serves`/`intensity`/`feeling` (shared with AWAY_GAP_GENERATION) — a
  contract-test update; the emotion/closeness contracts are **untouched** (`emotion` reuses the
  locked 9-enum; needs add no reply field).
- **Determinism:** decay/replenish/drift are pure functions of levels + injected clock + returned
  records; biorhythm per-day values are deterministic; the model is **mocked** (canned + malformed
  records exercise validation). Pin exact levels against a fixed clock; assert the hungriest-need
  selection and the no-duplication window. No real sleeps, no paid calls.

---

## 13. Mapping to the roadmap

Needs open v1 as **two phases of their own** (v1.9–v1.10), then weave into the inner-life phases
(v1.11–v1.12), because the loop (needs → plan → reality → close) *is* the inner life:

**v1.9 — Needs I (the drives — tick-driven levels that pull her):**
- the global **`Needs{levels, last_ts}` store** + authored `core/needs.md` (the 6 drives:
  decay/weight/satisfied-by/deficit voice + the **threshold & cooldown** fields v1.10 uses);
- the pure **`evolve`** — fractional-Δt **decay + drift** (§6's three rules: fractional Δt,
  split-invariance, evolve-on-read/-before-mutate), run by the v0.42 **fast tick** (`%update_state`
  as a code handler); frozen while the TUI is closed, no catch-up;
- needs **feed the daily mood call** (the hungriest joins the mood inputs, the v0.8 merge pattern —
  §10.1);
- **`connection` replenished mid-turn** from the closeness warmth read (`RelationRead.warmth`, v0.10
  — §4(b));
- a **`/needs`** command (the read-state surface).

**v1.10 — Needs II (actions — a hungry need moves her):**
- the **threshold check** on the tick, after `evolve`: a drive crossing its authored threshold fires
  its directive via `run_directive`, seeded by the **deficit voice**;
- **guards:** per-need cooldown, quiet hours, day caps — restraint; never competence, never a demand.

**v1.11 — Inner life I (plans & state):** needs **tilt today's plan** (1–2 items — §5.1 / §10.2) —
the tilt lands here, where a plan first exists.

**v1.12 — Inner life II (the drives close from reality):**
- the authored **activity→need map** + the away-gap's **structured records** (`serves`/`intensity`/
  `feeling`; the `log` entry gains these — §8);
- **replenish** math (`level += gain × intensity` per valid `serves` — §9.10) **together with the
  gap's decay** (gap time returns balanced — §6);
- the **free-slot fill** biased by the hungriest need, which then replenishes it (closes the loop —
  §10.2);
- **plan-vs-reality** closing (needs rise from what *actually* happened — §5);
- the **threshold-5 per-day generation** rule (§7), refining the gap-fill.

Depends on **v0.42** (the tick service the levels ride), **v0.6** (the mood it feeds), **v0.8**
(biorhythms — the merge pattern it copies), **v0.10** (the closeness warmth read), **v0.4** (the
clock), **v0.2** (the Repository); the v1.11 inner-life store / v1.12 away-gap it weaves into. An
experiment for daily variation from the inside out — same spirit as the horoscope and biorhythms,
never a claim of real inner states.
