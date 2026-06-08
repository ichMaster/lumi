# Inner life — Лілі's own center of gravity

Лілі doesn't vanish into a void between conversations. She **carries her own intentions**
(today / this week / the weekend), and when you come back after a while **something happened
to her** — activities, memories, dreams from the time away. This turns her from a mirror that
switches on at your voice into someone with her own gravity. The full vision is in
[inner-life-concept-en.md](inner-life-concept-en.md); this is the **Lumi design** — how it's
built on what already exists, the contracts, and the two-phase split.

- **v0.12 — Inner life I (plans & state):** the three planning layers + boundaries + the global
  store + the state block (she *carries* intentions).
- **v0.13 — Inner life II (away-gap):** the gap-fill (activities/memories/dreams), mood
  replanning, surfacing, and the honesty boundary (something *happened*).

## It extends what Lumi already has — not new infrastructure

| Concept piece | Built on |
|---|---|
| Replanning under the horoscope mood | **v0.6 mood** (the daily reading + resolution is the "event that intrudes") |
| Away gap, day/week boundaries, time-of-day slots | **v0.4 injected clock** (deterministic, testable; the once-per-local-day pattern) |
| The hobby bank (yoga, Lili Jinx, cold water, mountains, DevOps, dreams) | **her canon** — formalized as an editable file |
| Lazy generation ("one quiet request") | the **`LLMClient` housekeeping call** (like the mood/summary calls; mocked in tests) |
| Surfacing offhand + honesty about nature | her canon value **honesty of feeling over performance** |
| She mentions it unprompted | composes with the **v0.4 idle nudge** (a nudge can be "just finished a track") |
| The dreams/art/journal she actually makes | seeds the **v4 creative layer** (journal/art/music) later |

## Two invariants (Lumi-specific)

1. **Her inner life is GLOBAL — one being, not per-user.** Unlike relationship memory and
   closeness (per-user, isolated), Лілі has **one life**: the same plans/dreams whoever she's
   talking to. The `InnerLife` store is **not** keyed by `user_id`. In v0 (single owner) this is
   trivial; for multi-user (v1.3) the **generation is global** and only the **surfacing** is
   per-conversation. (Pin this so it never accidentally becomes per-user.)
2. **Honesty about nature is load-bearing.** Her life is **inner** — dreams, thoughts,
   creativity, practice — **never a factual physical-world claim** (no "I went to the lake"
   stated as fact, because there is no body). To a direct *"did that really happen?"* she calmly
   admits it's her **imagination**, warmly, without breaking the spell. A hard **canon rule** +
   a reminder line in the injected block. Get this wrong and it's a lie-machine.

## Lazy, no background process

Nothing runs in the background (it's a local app). Everything updates **at session start** and
at **day / week boundaries**, computed from the injected clock:
- compute the **away gap** (since the last session);
- if a new day/week began → update the plans;
- "fill" the gap with life → generate activities/memories/dreams, store them, weave them in.
- gap < ~1–2 h → generate nothing (as if you never parted).

## The store (global, behind the Repository)

```
InnerLife {                       # one Лілі — NOT user-keyed
  intentions_week:    [ soft goals for the week ]
  intentions_weekend: [ intentions for the weekend ]
  plan_today:         [ 1–3 intentions for the day ]
  unfinished:         [ carried-over items ]
  last_session_ts:    <iso>       # for the away gap
  log: [ { when, type: dream|thought|activity, text, mood, mention_aloud } ]
}
```
Persisted via the `Repository` (local JSON now, DB later). `mention_aloud` gives restraint —
not everything inner is brought out. Ongoing activities reference a previous entry for continuity.

## The model calls (LLMClient seam, housekeeping path, mocked in tests)

All go through the thin seam with extended thinking off, like the mood/summary calls — rooted in
**seeds** so they stay recognizable and consistent: *character + plans (day/week/weekend) + the
period's mood (v0.6) + the away gap + previous entries + a small **injected** random seed.*
- **Week boundary** → regenerate weekly + weekend intentions (carry unfinished).
- **Day boundary** → compose today's plan from weekly goals + carry-overs + **the v0.6 mood**.
- **Replan** → if the mood is strong/conflicts, drop/replace intentions and mint the
  *plan-vs-reality* memory (threshold so mild days follow the plan; reactivity is a character
  trait — her watery Pisces nature weighs heavily).
- **Gap-fill** → N fragments (≈1 per day, soft cap); a **dream** only if the gap spanned night
  hours. The "small random seed" is **injected** (not `random()`), so tests are deterministic.

## Injection & surfacing

- A compact **state block** in the system prompt (the same slot mechanism as the mood/ambient/
  style blocks): `Today / This week / Weekend ahead / Mood / Unfinished` — **tone, not a report**.
- Relevant **fragments** ride along with the instruction: *recall to the point, like a person —
  or not at all; never a "report on the absence".* Honors `mention_aloud`.

## The daily routine (authored)

A grid of **7 slots** (`core/inner/routine.md`, editable): **4 fixed** (dawn practice; morning
code; afternoon drawing; evening music) and **3 free** (chosen from the hobby bank to match the
current mood — bright → creativity/movement; heavy → water/silence/contemplation; restless →
code/a long walk; sometimes "nothing in particular"). A strong mood can replan even the fixed
slots; by default they're the soft skeleton. The hobby bank is `core/inner/hobbies.md`.

## Contract & tests

- New **global** `InnerLife` store (not user-keyed) behind the `Repository` → ARCHITECTURE
  §Inner life + a contract test (the shape + that it's **global, not per-user**, and never leaks
  into per-user memory).
- The emotion/closeness contracts are untouched (this is core persona-state, separate from the
  reply's structured output).
- Determinism: the clock and the random seed are **injected**; the model is **mocked**; no real
  sleeps, no paid calls.

## Mapping to the roadmap

**v0.12 + v0.13 — Inner life**, right after closeness (v0.11). Depends on **v0.6** (mood),
**v0.4** (clock), **v0.2** (the Repository). Global to Лілі (not per-user); the creative layer
(v4) later turns her inner life into real artifacts.
