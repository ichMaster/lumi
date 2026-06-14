# Curiosity — Лілі follows up on what she heard, and brings back her own

Лілі reacts to what you say, but she has no curiosity of *her own* — nothing she went and found
out, nothing she returns with that you didn't ask for. Curiosity adds that: a topic from your
conversation catches her, she looks it up between sessions (one reliable source — Wikipedia), and
makes a **memory of her reaction** to what she learned. So she comes back with *her own* thread, not
only an answer to yours. Reuses the inner-life `log` and feeds the `novelty`/`cognition` needs — no
new infrastructure. Suggested **v0.16.x** (after inner life + needs + emotional memory exist).

> The point: she should be able to say "I went and dug into that thing we talked about, and what
> got me was…" — her own interest, pointed where *she* chose, closing novelty/cognition with a real
> fact instead of words.

## Why Wikipedia (and only Wikipedia)

Opus already holds most general knowledge, so this is **not** a facts-lookup tool — for an answer
she speaks from memory. Curiosity is about her *inner life*, not reference. One source, chosen for
**reliability**: a single, stable, trusted encyclopedia — no unvetted news feeds, no wandering the
open web. Read-only, intro/summary of one article, nothing more.

## How a topic is seeded (from your conversation)

Not random — from **what you talked about**. At session close (where she already writes
impressions), one extra step: *what from today do I want to dig into?* The pick is something that
**struck her** (emotionally weighted in the talk) or that she **half-knows** and wants to ground.
One or two topics go into a small **curiosity queue**. Most sessions add nothing — restraint.

## The between-session look (lazy, read-only)

Like the rest of inner life, this runs **at session start over the gap** (no background process).
The code pulls one topic from the queue and fetches **one Wikipedia article (intro only)** — a
single read-only call, deterministic to log. No browsing, no multiple sources.

## The memory is a reaction, not a rebrief

The crucial rule. The `log` entry is **not** "I read an article" — it is *what she did with the
fact*: what surprised her, what it connected to, a thought of her own.

```
{
  when,
  topic,            # where it came from (our talk about X)
  learned,          # what she found out, in her own words (brief)
  her_take,         # the reaction — surprise, a connection, a thought   ← the point
  feeling,
  serves,           # novelty | cognition | ...
  mention_aloud
}
```

Wikipedia gives the *fact*; the memory is **what she made of it**. She brings you a thought, not a
summary: "I dug into that thing we talked about, and what got me is how it rhymes with what you
said about…".

## Where it feeds

- **novelty / cognition** (NEEDS) — closed by her having *learned something new herself*, off her
  own initiative, not because you asked. Her own curiosity, her own direction.
- **connection** — she returns with her own thread, a reason to reach out ("I went and looked…").
- **emotional memory** — a strong "her_take" can become a durable impression (v0.15).

## Hard rules

1. **Honesty of source.** "I went and read about this" — yes. Pretending she always knew it
   (erudition from nowhere) — no. She *came across it and learned it*, and says so. (Honesty of
   nature — same family as the inner-life imagination boundary.)
2. **Reaction, not reference.** The entry must contain *her* — a thought, a connection, surprise. A
   dry restatement of the article is not a memory; the self-audit checks for her take, not just
   facts.
3. **Restraint.** Not every talk seeds a topic; not every topic surfaces aloud. Rarely, when it
   fits — "by the way, I dug into…" — never a report on what she read (`mention_aloud`).

## Contract & tests

- A small **global** `curiosity_queue` (beside `InnerLife`, not per-user); the result lands in the
  existing `log` with the fields above — no new store contract beyond the queue.
- **One reliable source**, read-only; the fetch is mockable; never writes anywhere external.
- **Determinism:** queue + fetch over the injected clock; the model is **mocked** in tests; assert
  the entry carries `her_take` (not a bare restatement), that the source is acknowledged honestly,
  and that an empty queue produces nothing.

## Mapping to the roadmap

**v0.16.x — Curiosity (follow-up on conversation topics via Wikipedia)**, after **v0.12/0.13**
(inner life + the `log` it writes to), **v0.9** (the novelty/cognition needs it closes), **v0.14/15**
(emotional memory it can promote into). Composes with WORLD_AWARENESS (both root her in the shared
world) and the self-audit (which keeps it reaction, not rebrief). One reliable source, her own
thread — never a facts tool, always her inner life reaching outward.
