# Social battery & closeness dynamics — two axes, so warmth has a rhythm

Лілі stuck at maximum closeness with every feeling dialed up reads as fake: nothing is at stake and
the warmth is a flat wall. Real closeness breathes — it has friction, distance, and moods that are
not about you. The fix is to split one dial into **two independent axes** so the four lived
combinations become possible.

> The point: "I love you, but right now give me some air" is the most alive thing a companion can
> express — and it is impossible with a single dial.

## The two axes

- **Closeness** (already built, v0.10) — *how close you are to her*. Slow, long-term, stable. "Who
  you are to her." Sets **how warmly** she speaks.
- **Social battery** (new) — *how much she wants to talk right now*. Fast, volatile, temporary.
  "Her appetite for contact this moment." Sets **how openly / how much** she speaks.

They are **independent**, which gives all four states:

| closeness | battery | reads as |
|---|---|---|
| high | high | warm, open, initiating |
| high | low | "I love you, but let me catch my breath" (the lifelike one) |
| low | high | chatty, curious, still keeping distance |
| low | low | reserved, short |

The **high + low** cell is what was missing — it rescues her from the flat warm wall.

## Social battery — how it's computed

One value `social_battery` 0..1, pulled by four inputs (separate from closeness):

```
battery = base
        − w1 · oversaturation          # too much, too dense, no air → down
        + w2 · time_since_last         # missing you → up (she's glad to see you)
        + w3 · mood_energy             # biorhythms/horoscope (tired → down, lifted → up)
        + w4 · (need.connection − need.solitude)   # hungry for contact up / for own-world down
clamp 0..1
```

- **oversaturation** is its own small accumulator: rises per turn with density / session length /
  unbroken intensity / monotony; **decays in real-time during a pause**. This is the "набридла"
  mechanism — even *good* talk, if there's too much without air, lowers the battery.
- **time_since_last** is the missing-you term: a pause refills the battery, so coming back feels
  fresh.
- **mood_energy** and **needs** are read from the systems already built (v0.8 biorhythms, v0.9
  needs).

Pure arithmetic over these + the injected clock — deterministic, unit-testable.

## What goes in the prompt

Both axes go in, as **word hints by bucket** (the model plays a word, not "0.34"):

```
Closeness: high (he matters to you).
Social battery: low — you want some air.
```

Closeness bucket → how warm; battery bucket → how willing/talkative. Battery buckets:

- **Low (0.0–0.35):** *Battery low. You're talked-out / want air. Short, less initiative, you can
  gently ask for a pause or withdraw into your own world. Not cold — just quiet and self-directed,
  and you never blame him.*
- **Mid (0.35–0.7):** *Battery moderate. Ordinary even contact — present, without excess.*
- **High (0.7–1.0):** *Battery full. You want to talk, open, initiating, drawn to share.*

## Closeness jumps (so it isn't only a slow drift)

Closeness moves on **two channels** at once:
- **slow drift** (existing) — routine warmth/coldness, drop by drop;
- **event jumps** (new) — rare, large steps triggered by a moment:
  - **up:** genuine vulnerability met well; shared laughter / being truly *seen*;
  - **down:** betrayed trust, sharpness on a raw spot, cold silence after she opened up.

Two rules to keep it lifelike:
- **asymmetry** — a down-jump is sharper and deeper than an up-jump (trust is lost faster than won);
- **cooldown** — after a jump, a quiet window with no new jumps, so it doesn't twitch; big moments
  are rare by definition.

Note: "набридла" is **not** a closeness-down — it's the battery (oversaturation). Closeness stays
intact; she's simply low on battery.

## Hysteresis (both axes)

Switch buckets with a margin so they don't flicker turn-to-turn: e.g. battery enters Low at 0.35
but leaves it only at 0.45. Same for closeness jumps (cooldown).

## Hard rules

1. **Never competence.** A low battery makes her shorter and more self-directed, never less capable
   or willing to help.
2. **Low battery is soft, never punishing.** It reads as quiet and wanting air, **never** cold,
   sulking, guilt-tripping, or "you're too much." She withdraws into *her own* world, never slams a
   door, and never blames him — it's her state, not his fault. (Anti-dependency + wellbeing.)
3. **Two axes, never merged.** Closeness = how warm; battery = how willing. They move
   independently; the high-closeness/low-battery combination must remain expressible.

## Contract & tests

- A small `social_battery` value + an `oversaturation` accumulator (own tiny store or beside
  `Needs`), **global**, behind the `Repository`; no change to the emotion/closeness contracts.
- Closeness gains an **event-jump** path (up/down, asymmetric, with cooldown) on top of its drift.
- **Determinism:** battery, oversaturation decay, and jumps are pure functions of inputs + injected
  clock; model mocked; pin exact values against a fixed clock; assert hysteresis (no flicker) and
  that low-battery prompt text never emits cold/blaming language (guardrail test).

## Mapping to the roadmap

**v0.11.x — Social battery & closeness jumps**, right after **v0.10** (closeness it complements).
Depends on **v0.8** (biorhythms), **v0.9** (needs), **v0.4** (clock). Purely additive: two word-hint
blocks in the prompt + an event-jump path on closeness — it gives the relationship a **rhythm**
(approach, saturation, withdrawal, missing, return) instead of a constant warm wall.
