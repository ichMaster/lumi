# Closeness — Лілі's relationship level

Лілі is **one being with a private relationship per user** — and a relationship is not flat. She
grows **closer to (or cooler with) each person over time**, read from *how* you talk to her and
*how often*. A per-user **closeness level (1–5)** modulates how **open** she is — warmth, teasing,
initiative, vulnerability — **never her competence**. It lands as **v0.10**.

> Like the v0.6 mood, closeness shapes **tone and openness, never capability or willingness to
> help.** It is **per-user and isolated** (the same invariant as summaries/facts) — your closeness
> is yours and never leaks into anyone else's relationship.

## Essence

- A **closeness value** per user, bucketed into **5 levels**, persisted in per-user memory.
- Each turn, the model **reads your message** on a few relational dimensions (folded into the
  reply — one call); the core nudges the value, decays it over time, re-buckets to a level.
- The active level injects an authored **behavior block** into the system prompt — reserved at
  L1, intimate at L5.

## 1. The relational read (per message)

Folded into the reply turn (no extra call), the model scores **your** message — each ~0–1:

| dimension       | reads as                          | effect |
|-----------------|-----------------------------------|--------|
| **warmth**      | kindness, affection, care         | raises |
| **vulnerability** | opening up, sharing something real | raises (trust) |
| **playful**     | teasing, humor, lightness         | raises |
| **harm**        | cruelty, hostility, contempt      | lowers |
| **manipulation**| pressuring, using, deceiving      | lowers |

Extensible; weights are authored. **Internal only** — never shown as raw numbers (a bare
"manipulation 0.3" would feel accusatory).

## 2. The value and the 5 levels

- A continuous value (e.g. 0–100) → **5 buckets**, with thresholds and a **dead-zone / inertia**
  so a single sharp message doesn't drop a level — levels move on *sustained* signal.
- Each turn: `delta = w·(warmth, vulnerability, playful) − w·(harm, manipulation)`; clamp; re-bucket.
- A small **per-turn drift toward the baseline** (`LUMI_CLOSENESS_DRIFT`, ~0.1) runs **every turn**,
  so an active warm streak settles at a high *plateau* instead of **pinning at the top** — holding
  the top takes *sustained* warmth (the relational delta still pushes up beneath the drift).
- The levels (names + behavior are **authored**, editable like styles — illustrative):
  1. **Ввічлива** (reserved) — warm but boundaried; doesn't over-share.
  2. **Приязна** (friendly) — relaxed; her personality shows.
  3. **Своя** (familiar) — teasing; more of her inner world.
  4. **Близька** (close) — tender, open; initiates; lets herself be vulnerable.
  5. **Найрідніша** (trusted) — fully herself; deep warmth, private jokes, protective.

## 3. Time — it cools and warms with contact

- Closeness **decays toward a baseline over days of silence** (via the v0.4 injected clock +
  `last_ts`); regular contact sustains and deepens it, frequent contact faster.
- So a long gap eases her back toward friendly-but-less-intimate; talking often builds depth.
  Decay rate / baseline / frequency response are configurable. Deterministic + testable (the
  clock is injected — no real sleeps).
- **Two pulls toward the baseline:** the **silence-decay** here (between sessions, by *days*) and
  the gentle **per-turn drift** (§2, within a *live* session) — together, the top is never a stable
  resting point, only a place sustained warmth holds her.

## 4. Injection — behavior, never competence

- The active level's authored block is added to the system prompt (like the mood/style blocks);
  it shapes **warmth, openness, initiative, teasing, vulnerability**.
- **Hard rule (identical to the mood):** it **never** changes her competence or willingness to
  help. L1 = more reserved/formal — **never cold, withholding, or less useful.** A low
  `harm`/`manipulation` score lowers closeness but **never triggers a refusal**; she stays kind
  and capable. Only how *close* she is changes, not how *useful*.

### Effective level = base + today's mood-shift (ephemeral)

The level injected each turn isn't the raw stored one — at prompt-assembly an **ephemeral
mood-shift** colors it: `effective = base_value + shift`, bucketed **for this turn only** (no
inertia, **never persisted**). The `shift` (±one level band, ≈ ±20 pts) is drawn from the two
**deterministic body rhythms**:

```
shift = clamp( 14 · emotional_biorhythm  +  cycle_offset(phase),  −20, +20 )
        # emotional biorhythm ∈ −1…+1 (v0.8) → ±14
        # cycle phase (v0.8.x): овуляція +6 · фолікулярна +3 · лютеїнова −2 · менструація −4 · ПМС −6
```

So a good-cycle / high-emotional-biorhythm day reads a **notch warmer**, a PMS / low day a notch
**more reserved** — *for today only*, without moving the real relationship (`base` is untouched;
`/closeness` shows the honest base level). The **intellectual and physical** biorhythms are
**excluded on purpose** — the same hard rule: this biases warmth/openness, **never competence**.

### Expressiveness budget (canon)

A constant line in the **canon** rations her warmest register: **tenderness is rare — genuine
moments, not her constant mode**; her default tone is witty, with a light edge. The closeness level
sets how *open* she is; the budget sets how *often* tenderness actually surfaces — so even at L5 or
on a warm-shift day she stays recognizably herself (warmth more often as wit and play than as
softness).

## 5. Persistence & isolation

`Closeness{user_id, value, level, last_ts}` per user, behind the `Repository`, keyed by
`user_id`. It **never crosses users** (the isolation invariant — ARCHITECTURE §Identity, users,
and memory scopes), pinned by a contract test. Clearing memory (`/forget`) resets it.

## 6. Visible — warmly

A `/closeness` command shows the current level **by name** ("близька"), optionally a small
status-line glyph. The raw dimension scores and the numeric value stay internal.

## 7. The contract

- A new per-user record **`Closeness`** + a new structured **relational-read** field on the reply
  (additive — the locked `{reply, emotion, intensity}` emotion contract from v0.3 is untouched).
- The core **validates/clamps** the read (like the emotion gate) and **never trusts raw output**.
- Updates **ARCHITECTURE §Closeness** + a contract test (the `Closeness` shape, the isolation
  invariant, and competence-unaffected).
- The later **base-drift + mood-shift + expressiveness-budget** refinement is **additive** — the
  `Closeness` shape and the emotion contract are unchanged; `mood_shift`/`shifted_level` are pure,
  prompt-time only, and nothing new is persisted.

## Mapping to the roadmap

**v0.10 — Closeness (relationship level)**, right after the v0.9 short-memory enhancement.
**Depends on** v0.3 (emotion / structured output — the read rides the same call), v0.2 (per-user
memory + isolation), and v0.4 (the injected clock for time decay). Per-user, persisted, never
crosses users.

**Refinement (shipped as a fix on v0.10, no new version):** the **per-turn drift to baseline**, the
ephemeral **mood-shift** (`effective = base + shift` from the v0.8 emotional biorhythm + cycle
phase, prompt-time only), and the canon **expressiveness budget**. Additive — no contract change.
