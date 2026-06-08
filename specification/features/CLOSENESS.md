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

## 4. Injection — behavior, never competence

- The active level's authored block is added to the system prompt (like the mood/style blocks);
  it shapes **warmth, openness, initiative, teasing, vulnerability**.
- **Hard rule (identical to the mood):** it **never** changes her competence or willingness to
  help. L1 = more reserved/formal — **never cold, withholding, or less useful.** A low
  `harm`/`manipulation` score lowers closeness but **never triggers a refusal**; she stays kind
  and capable. Only how *close* she is changes, not how *useful*.

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

## Mapping to the roadmap

**v0.10 — Closeness (relationship level)**, right after the v0.9 short-memory enhancement.
**Depends on** v0.3 (emotion / structured output — the read rides the same call), v0.2 (per-user
memory + isolation), and v0.4 (the injected clock for time decay). Per-user, persisted, never
crosses users.
