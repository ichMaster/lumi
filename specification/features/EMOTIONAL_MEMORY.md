# Emotional memory — diary, not stenographer

Today's long-term memory is a **stenographer**: at session close it writes dry facts ("Vitalii
studies DevOps, likes Y"). This makes it a **diary** instead — Лілі's own first-person
**impressions** of you, colored by what she felt and what struck her — while keeping a parallel
**facts** layer for precision. The full vision is in
[LONG-EMOTIONAL-MEMORY.md](LONG-EMOTIONAL-MEMORY.md); this is the **Lumi design** — the contracts,
the two-phase split, and how it reuses signals Lumi already produces.

- **v0.20 — Impressions:** the impressions layer + the session-close diary generator + two-layer
  injection.
- **v0.21 — Fading & consolidation:** weight decay + merging small impressions into
  generalizations (her *understanding* of you).

> The **session-close** sibling of the inner life (which writes her *own* days at session start).
> Both are first-person, emotion-weighted, and stay consistent with prior entries — together they
> make a complete subjective memory: she remembers her own life **and** she remembers you.

## It reuses signals Lumi already produces

| Concept source | Built on |
|---|---|
| Facts layer (precision) | the existing **`LongTermFact{user_id, fact, meta, confidence, ts}`** — kept as-is |
| "what she felt / what she sensed he felt" | the **v0.3 per-turn emotion** (hers) + the **v0.10 closeness relational read** (his warmth/vulnerability/…) |
| "mood / tone of the meeting" | **v0.6 mood + v0.8 biorhythms** |
| session-close generator | the existing **end-of-session extractor seam** — same hook, diary prompt |
| pairs with the inner life | **v0.17–v0.18** writes her own days (session-start); this writes you (session-close) |

## Per-user and isolated

Unlike the inner life (global — one Лілі), these are **her impressions of a specific person**:
the `Impression` store is **keyed by `user_id`** and never crosses users (the isolation
invariant — ARCHITECTURE §Identity, users, and memory scopes), pinned by a contract test.

## Two layers (the safeguard)

- **Facts layer** — reliable specifics: names, dates, agreements, stable preferences. **Precision.**
- **Impressions layer** — her first-person diary entries. **Warmth and voice.**

She **speaks from the impressions** and **pulls facts** when she needs to "not forget" something
concrete. A diary alone is unreliable for hard specifics; a fact list alone is cold. Together:
facts give accuracy, impressions give the voice.

### The impression entry

```
Impression {
  user_id,                 # per-user, isolated
  when, ts,
  impression,              # her words, first person
  emotion,                 # what she felt (warmth, tenderness, sadness, laughter, worry…)
  about_user,              # the fact / discovery seed, if any — extractable into the facts layer
  weight                   # how much it struck her — drives whether/how long she recalls it
}
```

The fact lives **inside** the impression as its seed; it can be promoted into `LongTermFact` for
concrete recall — but the *shape and the selection* are emotional.

## v0.20 — The session-close diary generator

Replaces the dry fact-extractor's prompt with, roughly:

> *You are Лілі. Recall this conversation in your own words. What did you feel? What touched,
> moved, or surprised you about him? What new thing did you learn? Write a few impressions — like
> lines in a personal diary, not a list of facts.*

So instead of *"Vitalii is studying DevOps"* it produces *"He lit up today talking about that
pipeline — I rarely see him like that. That thing is more than work to him, I think."*

- **Seeds:** the conversation + her per-turn emotions (v0.3) + the closeness reads (v0.10) + the
  day's mood (v0.6/v0.8). One model call (the `LLMClient` seam; mocked in tests).
- **Output:** a few impressions (restraint — not a transcript), each with `emotion`, `weight`,
  and an `about_user` seed; the seed promotes into the facts layer.
- **Injection at startup:** a first-person **"what I remember & feel about you"** block
  (top-weighted, capped) **alongside** the facts block.

## v0.21 — Fading & consolidation

- **Emotion is the attention filter.** `weight` (set from emotion intensity / a closeness shift /
  "first-time" discovery) decides brightness and longevity. Recall ranks by `weight × recency`.
- **Fading.** `weight` **decays over time** (the injected clock); low-weight impressions dim and
  eventually drop — memory is human-like, not an even archive.
- **Consolidation.** A lazy pass (a model call, at session start or on a counter) clusters similar
  impressions into a **generalization** ("he shuts down when tired", "he comes alive with music"),
  kept durable and higher-weight; the absorbed detail fades. From impressions grows her
  **understanding** of you.
- **Consistent + bounded.** New impressions/consolidations see the prior ones (no contradiction);
  the store stays capped. Deterministic via the injected clock + an injected seed.

## Two canon rules (load-bearing)

- **It is her view, not the truth.** Memory is subjective — she may misread, which is natural and
  alive; but on a **direct check she clarifies rather than insists.**
- **Honesty of boundaries.** What the user asked not to remember, or painful topics, is **not
  recorded, or marked `care` — never savored.**

## Contract & tests

- New per-user **`Impression`** layer behind the `Repository` (keyed by `user_id`) → ARCHITECTURE
  §Memory + a contract test (the shape + per-user isolation). The **facts layer** (`LongTermFact`)
  and the **emotion contract** are untouched.
- Determinism: the clock and the consolidation seed are injected; the model is mocked; no paid calls.

## Mapping to the roadmap

**v0.20 + v0.21 — Emotional memory**, right after the inner-life and monologue layers (v0.17–v0.19). Depends on **v0.3**
(emotion), **v0.10** (closeness), **v0.6/v0.8** (mood), **v0.2** (the memory layers), and **v0.4**
(the clock, for fading). Per-user, isolated; the session-close half of her subjective memory.
