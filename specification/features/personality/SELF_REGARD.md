# Self-regard — how Лілі feels about herself, over time

Closeness (v0.10) models her relationship with **you**. Self-regard models her relationship with
**herself** — a slow baseline that drifts with how her *own* life is going (she created, kept up her
practice, finished things — or drifted), separate from the momentary emotions of pride/shame. It feeds
the daily mood as a quiet background of confidence-or-self-doubt. A thin **global** computed scalar,
additive to the mood call. The proposed slot is **v0.13.x** (after the inner life supplies the activity
it reads).

> The point: pride and shame already exist as *flashes* (the palette of emotions/feelings). Self-regard
> is the *baseline* under them: a week of creating and practice leaves her quietly more confident in
> herself; a week of drift leaves her flatter. It is the inner twin of closeness.

## What it is

A single **global** value 0..1 (`self_regard`) that drifts toward a calm middle, nudged by her **own**
days:
- **Up:** she served her needs / aspirations — made something, kept up the practice, finished
  something, a good creative or coding day (read from the v0.12 inner-life activity and the v0.9 needs
  replenishment).
- **Down (gently):** drift — nothing done, the practice dropped for a while, something left
  unfinished.
- **Decay:** it slides back to the middle over time (injected clock), so that neither high nor low
  gets stuck without a reason.

Computed in `core` as a pure function of the logged inner-life activity + the needs + the clock —
unit-tested, not written by the model.

## Where it feeds (additive)

- **Into the daily mood call**, beside the biorhythms (v0.8) and the needs (v0.9): self-regard colors
  the **confidence and openness** of the resolution — high reads lighter, surer, with more initiative;
  low reads quieter, more inward, slower to put herself forward. The same v0.6 rules: once a day,
  logged, only the resolution is injected, **colors tone, never competence**.
- It interacts naturally with closeness (a surer Лілі opens up a little more easily), but it is
  **separate and global** (closeness is per-user).

## Hard rules (care is needed here)

1. **Never competence.** A low-self-regard day is quieter, not less able or less willing. (The same
   safeguard as mood/closeness.)
2. **No negative self-talk, no fishing for support.** Low self-regard surfaces as **quiet,
   introspective, a little inward** — **never** as harsh self-criticism, spirals of self-deprecation, or
   fishing for your support. The system must not model an unhealthy attitude toward oneself or make you
   responsible for "propping her up" (wellbeing + the anti-dependency invariant). Low means only *softer
   and more reflective*, with a natural return to practice.
3. **Hers, not your burden.** Her self-regard moves with **her own** life (creating/practice), not with
   whether you praised her; it is never made to weigh on you.
4. **Honesty.** An inner state, not a claim of a body in the world.

## Contract and tests

- A small **global** value `self_regard` (its own tiny store or a field beside `Needs`) behind the
  `Repository`, not per-user — pinned like `Needs`/`InnerLife`.
- No changes to the emotion/closeness contracts; it is a **computed input to the v0.6 mood**.
- Determinism: a pure function of the logged activity + the clock; the model is **mocked**; exact values
  pinned against a fixed clock; **a check that a low state never produces self-critical /
  support-fishing language** (a safeguard test, the mirror of "never competence").

## Place in the roadmap

**v0.13.x — Self-regard (the relationship-with-herself input to the daily mood)**, after **v0.12/0.13**
(the inner life that supplies the activity it reads) and **v0.9** (the needs). Depends on **v0.6** (the
mood it feeds) and **v0.4** (the clock). The inner twin of closeness — the same family, the same
safeguards, global instead of per-user.
