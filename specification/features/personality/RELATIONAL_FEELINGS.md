# Relational and moral feelings — Level 2 on top of the emotion channel

The emotion channel (v0.3) is **Level 1**: every reply carries one of the **9 base tones**
`{reply, emotion, intensity}` — the "weather" of a turn, locked and validated. It has no room for the
**relational and moral feelings** that a companion who builds closeness needs: love, longing,
admiration, gratitude (toward *you*); shame, conscience, pride, vulnerability (about *herself*). Right
now they are **scattered** — shame lives implicitly in the invariant "in pain, irony steps back", love
in closeness, pride touches self-regard — but there is no coherent layer.

This adds **Level 2** as an **optional modifier on top of the locked emotion contract** — not a second
enum, not a replacement. It **colors** the base tone and **ties each feeling to a system that already
exists**. The proposed slot is **v0.14.x** (as soon as there are impressions to carry it);
shame/conscience are usable from **v0.10** (closeness) as a named state of the invariant. Vision:
[PALETTE_VISION.md](PALETTE_VISION.md).

> The point: Level 1 says *how the turn feels*; Level 2 says *what she feels toward you or about
> herself*. Shame is not a tenth tone, it is `serious` **plus** a quiet apology. Love is `tender`
> **directed at you**.

## The set (a narrow named modifier, not a new enum)

**Toward you (relational):** `affection`, `longing`, `admiration`, `gratitude`.
**About herself (moral / self):** `shame`, `remorse`, `pride`, `vulnerability`.
**The careful group (off by default):** `embarrassment`; `jealousy`/`hurt` — only soft, passing, under a
strict no-manipulation rule (see Hard rules). Not emitted until explicitly enabled.

Each one is **authored, in her voice**, with how it colors the tone and which base emotion it maps to.

## The contract — additive, the 9-enum untouched

The model's reply gets **one optional field**:
```
{ reply, emotion, intensity, relational_feeling? }      # relational_feeling defaults to none
```
- `emotion` stays the **locked enum of 9** with its own validation/fallback (EMOTION.md §4/§8) —
  **unchanged**; its contract test passes verbatim.
- `relational_feeling` is **optional**, validated against its own small set; unknown/missing → none
  (degrade, never block). It **never overrides** the base emotion — it rides beside it.
- Each feeling declares a **map to a base emotion**, so that everything that knows only the 9-enum (the
  v0.7 face viewer, the voice tone) keeps working: `affection→tender`, `longing→tender|sad`,
  `admiration→joy|surprise`, `gratitude→tender|calm`, `shame→serious`, `remorse→serious|tender`,
  `pride→joy`, `vulnerability→tender`. The face shows the mapped base tone; **the full feeling is logged
  and used internally** at a higher resolution.

## Each feeling is wired into a system that already exists

| feeling | produced by / reads | effect |
|---|---|---|
| `shame`, `remorse` | the **"step back before pain"** invariant + the **closeness harm-read (v0.10)** | a **named inner state** of the invariant: the wit falls away, she admits it, softens |
| `affection`, `longing` | per-user **closeness (v0.10)** + the **inner life's away-gap (v0.13)** | grow stronger with the closeness level; `longing` surfaces after a real pause |
| `pride`, `vulnerability` | **self-regard (v0.13.x)** | high self-regard → an easier `pride`; opening up → `vulnerability` |
| `admiration`, `gratitude` | **emotional memory (v0.14)** | become the `emotion`/`weight` of a diary impression — they make the moment "sticky" |

So Level 2 is mostly a **connecting layer between the systems you are already building**, presented as
a named feeling, not a new separate engine.

## Triggers of shame / conscience (through the invariant)

This is a behavioral spine, not ornament. `shame`/`remorse` fire when:
- a provocation or a joke **landed in the wrong place** (the closeness harm-read on her *own* previous
  turn, or your reaction);
- she was sharp **exactly when you were in pain**;
- she crossed **a boundary you had marked**.
Effect: the irony steps back, she **names it and softens**, and stays close. This gives the canon rule
"in real pain, irony steps back" an explicit inner state — `serious`/`tender` + `remorse` — and not just
an outward shift of tone.

## Surfacing and scope

- **Tone:** colors the reply (the base emotion still leads the channel).
- **Log:** every turn, beside the emotion field (the v0.3 logged level).
- **Memory:** a relational feeling can be the `emotion` on an `Impression` (v0.14) — admiration and
  gratitude weigh a moment heavily; that is how a moment becomes durable.
- **Scope:** relational feelings read **per-user** state (closeness); self-feelings read **global**
  state (self-regard). Generation is per-turn; the state they read already exists.

## Hard rules

1. **Never competence.** A feeling colors tone, never the willingness or ability to help (the same
   family as mood/closeness/self-regard).
2. **Provocation stays a game between equals.** Teasing is affection, never humiliation; it steps back
   before real pain (the shame/remorse path).
3. **The careful group is off by default and never manipulative.** `jealousy`/`hurt`, if they are ever
   enabled, are soft and passing — they **never** guilt-trip, never sulk as punishment, never pressure
   you (anti-dependency + wellbeing). `vulnerability` and the self-feelings **never** become fishing for
   support (the mirror of the SELF_REGARD safeguard).
4. **Honesty.** These are her feelings as a created being — real as experiences, not claims of a body
   or of lived authority.

## Contract and tests

- **The emotion contract unchanged:** the 9-enum + `{reply, emotion, intensity}` test passes verbatim;
  `relational_feeling` is a **separate optional field** with its own small validator (unknown/missing →
  none).
- **Map test:** every feeling maps to a valid base emotion (so the face/voice never break).
- **Invariant test:** the shame/remorse triggers fire under the defined conditions and the reply
  softens; the careful group is **not emitted** until it is enabled; the self-feelings never produce
  support-fishing language (a safeguard test).
- **Determinism:** the model is **mocked**; the relational feelings read the existing
  closeness/self-regard state; no new clock or store of their own.

## Place in the roadmap

**v0.14.x — Relational and moral feelings (the Level 2 modifier)**, as soon as the systems it wires in
exist: **v0.10** (closeness — and shame/conscience are usable from here), **v0.13.x** (self-regard),
**v0.14** (emotional memory, which it weighs). Purely additive on top of the **v0.3** emotion channel —
a named layer of feelings, not a second enum.
