# The personality layer — index

Additive specifications that extend Lumi's character **beyond emotions** (six layers), in the style of
the project's specifications. Each one is **additive** (no new infrastructure that would break a locked
contract), **global or per-user** as noted, and carries the same hard rules as `mood`/`closeness`:
**never competence**, determinism (injected clock, mocked model), honesty about her nature,
anti-dependency.

## The six layers and where they sit

| Doc | Version slot | One line | Scope | Feeds / depends on |
|---|---|---|---|---|
| **[NEEDS_full.md](../NEEDS_full.md)** | v0.9 | the motivational *cause* under the mood — 6 core drives whose deficit pulls her | global | an input to the daily mood (v0.6/0.8); tilts the inner life's free slots (v0.12); the idle nudge (v0.4) |
| **[OPINIONS.md](OPINIONS.md)** | v0.5 (+emergent after v0.15) | her own tastes and positions — a spine, not a mirror | global | an injected block; the emergent layer rides on consolidation (v0.15); colors the creative layer (v4) |
| **[ASPIRATIONS.md](ASPIRATIONS.md)** | v0.12.x | the long arc — who she is *becoming*; seeds the weekly intentions | global | seeds the inner life's weekly intentions (v0.12); colors the mood (v0.6); the journal (v4.6) |
| **[SELF_REGARD.md](SELF_REGARD.md)** | v0.13.x | her relationship with *herself* — a slow baseline of confidence | global | an input to the daily mood (v0.6); reads the inner life's activity (v0.12) + the needs (v0.9) |
| **[RELATIONAL_FEELINGS.md](RELATIONAL_FEELINGS.md)** | v0.14.x | the named Level 2 feelings (love, shame, pride…) as an optional modifier on top of the emotion channel | per-user (relational) + global (self) | wires in closeness (v0.10), self-regard (v0.13.x), emotional memory (v0.14); a modifier on top of the emotion channel (v0.3) |
| **[INNER_MONOLOGUE.md](../INNER_MONOLOGUE.md)** | v0.13.x | thinking *in the moment* in her voice — the hidden step where all the states converge into how she speaks | per-turn (reads per-user + global states) | the already existing Opus 4.8 thinking; weighs mood/closeness/needs/self-regard/relational feelings; one reply call, thinking on |

Supporting vision: **[PALETTE_VISION.md](PALETTE_VISION.md)** (the two-level palette of emotions — the
source of the idea behind RELATIONAL_FEELINGS.md).

## How they fit together (one picture)

- **Needs** are the *cause* (a pull from below) under the existing mood from above.
- **Self-regard** is the *relationship with herself*, the inner twin of closeness.
- **Opinions** give her a *spine* (held tastes and positions that she voices).
- **Aspirations** give her movement a *direction* (a long arc on top of the weekly intentions).
- **Relational feelings** are the *named layer of feelings* on top of the emotion channel, mostly a
  connecting layer between closeness, self-regard and emotional memory.
- **Inner monologue** is *thinking in the moment* in her voice — the hidden step (the existing Opus
  4.8 thinking) where all the states above are weighed into how she speaks; one reply call.

Already in the project (not re-specified here): the **emotion channel** (Level 1, 9 tones),
**mood/temperament** + **biorhythms** + the **hormonal cycle**, **closeness**, the **inner life** (plans,
the away-gap, the routine, the hobby bank), **memory** (session / short / long-term facts + impressions /
semantic recall). These specifications sit *between* and *beside* them.

## Placement

Drop the feature specifications into `specification/features/`; the index can live there too (or in
`specification/`). Update `ARCHITECTURE.md` with a short section per layer (each one states its store,
its "never competence" rule, and global-vs-per-user), mirroring how Needs/Inner-life are described.
