# Semantic recall — thematic recall (topic-routed RAG)

By v0.17 recall is **one undifferentiated pool**: every message is embedded into a single per-user
store, and a turn pulls the top-`K` nearest vectors regardless of subject. That is exactly right for
"find the closest line," but it has no notion of *what the conversation is about* — a query that sits
between two subjects pulls a blurred mix, and Лілі has **no say** over which part of her memory the
turn leans on.

This document specifies **thematic recall**: every message is tagged with one or more **topics** from
a fixed, authored taxonomy, and each turn retrieves **preferentially from the topics the conversation
is currently about**. The topic of the turn is chosen **locally, without an LLM call** (reusing the
embedding already computed for RAG), and **Лілі can steer it** — she names the active topics in her
reply the way she already emits emotion, and that nudges which topics the *next* turns lean on. It is a
**refinement of [SEMANTIC_RECALL.md](SEMANTIC_RECALL.md) +
[SEMANTIC_RECALL_CONTEXT.md](SEMANTIC_RECALL_CONTEXT.md)**: the `Embedder`/`VectorStore` seams and the
`{reply, emotion, intensity}` contract are untouched; it adds a **label on each record** and a **router**
in front of retrieval.

> **"Topic" here ≠ the v0.11 face *themes*.** A *topic* is subject matter (what a memory is *about*);
> a face *theme* ([FACE_THEMES.md](FACE_THEMES.md)) is a visual outfit pack. They are unrelated axes —
> only the everyday word "тема" overlaps.

## The principle: route retrieval by topic, picked locally, steerable by Лілі

- **A closed taxonomy.** Topics are a **fixed, authored set** (like the emotion enum), so a name a
  classifier or Лілі picks always maps to a topic that exists. Unknown topics are dropped, exactly as
  an unknown emotion falls back to `calm`.
- **Tag at write time.** Every message is classified into **zero or more** topics as it's indexed, by a
  **local embedding classifier** (no LLM) — so the per-topic "catalogs" are already filled when a turn
  needs them.
- **Pick the active topics locally.** Each turn, the incoming message's embedding (already computed for
  RAG) is matched against the topic centroids → the **active topic set** for this turn. No extra model
  call, no one-turn lag.
- **Retrieve topic-first.** Top-`K` is taken from an **over-fetched candidate pool**, **preferring
  records that share an active topic**, then topped up from the rest so a turn is never starved — then
  the v0.17/v0.30 expansion + injection runs unchanged.
- **Лілі steers.** Alongside her reply she emits the topics she senses (the v0.10 `RelationRead`
  pattern); the core validates them against the taxonomy and folds them into the active set **going
  forward**, with inertia. Local pick is the *floor*; her read is the *steering*.

Multiple topics are first-class: a message may carry several labels, and the active set may hold several
topics at once — retrieval draws from their **union**.

## The topic taxonomy (authored, fixed)

A small authored file, `core/topics.md` (the path is config, like `core/needs.md`), defines the closed
set. Each topic has a **name** (the validated token) and a few **seed terms / a one-line description**
used to build its vector. Authored, not learned, so the set is stable, legible, and Ukrainian-first.
A topic's **centroid** is the embedding of its seeds (optionally blended later with the mean of the
messages assigned to it — a refinement, not required for v1). Seeds embed **document-side** — the stored
records are passage vectors, and the per-turn *query-side* pick accepts the mixed-space asymmetry of an
asymmetric embedder. Centroids are global, authored data — they carry **no user content**, so they never
cross a user boundary.

## Authoring the taxonomy (the `/discover-topics` → `/refresh-taxonomy` skills)

The taxonomy is **authored, not learned** — but it shouldn't be *guessed*. Two Claude Code skills (the
`generate-faces` → `place-faces` propose-then-apply pattern) author and maintain `core/topics.md`:

- **`/discover-topics`** — clusters the **existing per-user vectors** (offline, over the store) and
  proposes a draft `core/topics.md`: candidate topic names + seed terms + a few representative exemplars
  per cluster. It only **proposes** — the closed set stays **human-curated**, so "authored, not learned"
  holds. It never mutates the live taxonomy or vectors, and it runs on the existing index, so it can
  author the **initial** set before topic routing ships.
- **`/refresh-taxonomy`** — applies a reviewed `core/topics.md`: bumps `topics_vN`, rebuilds the
  centroids, and **re-tags the stored vectors via the local classifier — no re-embedding** (labels
  recompute from existing vectors, no embedder calls). It follows the **store-free discipline** (stop the
  app + back up first) and reports per-topic coverage + the untagged share.

The split mirrors the data flow: discovery is an **authoring aid** over the corpus; refresh is the cheap
**local re-tag** that the separate `topics_vN` marker already triggers in the running app — the skill
just runs it on demand. Both are **dev-time tooling**, outside the runtime contract.

## Data model (extends the v0.16/v0.30 `VectorRecord`)

The record gains one field (additive — a contract change, pinned by the memory-records contract test):

- **`topics`** — the tuple of topic names this record (message, or chunk under v0.30) is assigned to,
  from the taxonomy; possibly empty (a message that matches no topic above the floor). Ordered by
  descending assignment score. Only **`kind="message"`** records are tagged — the v0.36 fact vectors
  (and v2.9 thought vectors) keep an empty `topics`.

Everything else (`user_id`, `msg_id`, `vector`, `text`, `ts`, `role`, v0.30's `parent_msg_id` /
`chunk_index`, and v0.36's `kind`) is unchanged. The `Repository` gains a small **`retag_vectors`**
write path (rewrite labels on existing records) — the vector API is otherwise add-only, and
`reset_vectors` wipes.

## Classification at index time (local, no LLM)

- **Assign on write / backfill:** for each message (or chunk) — `kind="message"` records only — cosine
  the record's **own vector** against every topic centroid; assign the topics scoring **≥ `topic_floor`**,
  capped at `topic_max` labels. A record matching nothing stays **untagged** (it can still be recalled by raw
  similarity — thematic routing only *re-orders* the pool, never hides it).
- **No re-embedding to re-tag.** Topic labels are derived from the **already-stored vectors**, so
  changing the taxonomy (or its seeds) re-runs a cheap local cosine over existing vectors — **no calls
  to the embedder**. The taxonomy version is a **separate `topics_vN` marker beside the model tag,
  not folded into it**: the shipped model tag (`model@embed_max_chars[@chunkN]`) keeps its semantics —
  a mismatch means `reset_vectors` + a full re-embed — while a `topics_vN` mismatch triggers only the
  **local label re-tag** via `retag_vectors` (the store survives; the vectors are untouched).
- **Graceful:** a classification failure leaves the record untagged (still stored, still recallable),
  never blocks the write.

## Active topics for the turn (local pick + Лілі's steering)

The active set used for *this* turn's retrieval is:

1. **Local pick (always, no LLM):** cosine the **query** embedding against the topic centroids → topics
   ≥ `topic_floor`, capped at `topic_max`. (The query vector is surfaced from the RAG search — today it
   is embedded inside `recall()` and discarded — or re-embedded locally; either way, no extra paid call.)
2. **∪ carried-forward set:** the topics Лілі emitted on recent turns, **decayed** by
   `topic_decay` each turn (inertia, so one off-topic line doesn't whip the routing around).

After the reply, Лілі's emitted topics (below) refresh the carried-forward set for the next turn.

## Лілі steers — the emit pattern (no contract change)

Just as she emits her own emotion, Лілі emits the topics she senses as part of the reply turn — a new
**optional `topics` field on the `set_state` tool**, the same additive pattern as v0.10's `relation`
read (a **sibling** field, not part of the `relation` object — that one is a read of the *user's*
message on five relational dims), following its per-provider handling. The core **validates** the
emitted topics against the taxonomy (unknown → dropped) and folds them into the carried-forward active
set; a missing or garbled field degrades to the local pick. Because the RAG block for a turn is assembled **before** the model replies, her emitted topics take
effect on the **next** turn — which is why the **local pick** handles the current turn with no lag and no
extra call. The locked `{reply, emotion, intensity}` contract is **untouched**; topics ride alongside it
exactly like the closeness read does.

## Retrieval → expansion → injection (extends LUMI-072)

1. **Search** — embed the query → **over-fetch** top-`K×N` over this user's **message** vectors →
   relevance floor. The over-fetch is the one change to the search step: re-ordering within the raw
   top-`K` alone would change almost nothing, since every floor-passing hit injects anyway.
2. **Topic routing (new):** partition the floor-passing pool into **on-topic** (shares an active topic)
   and **off-topic**; take on-topic first (ranked by cosine), then **top up** from off-topic up to `K`
   so the turn is **never starved**. With no active topics, or with `LUMI_RAG_TOPIC` off, this is a
   no-op → identical to v0.17/v0.30.
3. **Expansion + injection (v0.17 / v0.30):** the chosen hits expand to their dialogue thread (and, under
   v0.30, their passage) and inject into the same `# Релевантні моменти минулого` block — unchanged.

So thematic recall **re-orders the candidate pool toward the conversation's subject**; it never removes
the safety net of raw similarity. (A pure-filter mode that drops off-topic hits entirely is a possible
config variant, but the default is **prefer-then-top-up** precisely so it can never block a turn.)

## The `/topics` command

`/topics` shows the **current active topics by name** (and, optionally, the taxonomy) — the read-state
surface, like `/mood`, `/closeness`, and `/thoughts`. Raw centroid scores stay internal.

## Bounds & invariants

- **Closed set.** Only authored topics exist; unknown labels (from the classifier or Лілі) are dropped.
- **Never starves.** Prefer-then-top-up guarantees a turn gets its `K` hits even if no record is
  on-topic; routing only changes *order/preference*, never *availability*.
- **Inertia.** The carried-forward set decays (`topic_decay`), so routing is steady across a few turns,
  not whipped by a single message.
- **Never competence.** Like mood and closeness, topic routing biases **what is recalled**, never Лілі's
  competence or willingness to help; a missed topic degrades to plain v0.17 RAG, never to a refusal.
- **Per-user, unchanged isolation.** Topics are labels on **this user's** records; classification and
  retrieval run only over the requesting user's vectors. Centroids are authored, user-content-free. The
  existing isolation contract test is extended to assert topic-routed retrieval is single-user (A↔B).
- **Additive data-model change.** `VectorRecord` gains `topics`; ARCHITECTURE §Semantic recall + the
  memory-records contract test update **in the same commit**. The emotion field and the
  `Embedder`/`VectorStore` seams are untouched.
- **Graceful.** Classifier or router failure degrades to v0.17 behaviour (untagged, unrouted); never
  blocks or delays a turn.

## Composition with chunking (v0.30)

Orthogonal and composable. With chunking on, each **chunk** is classified from its **own** vector, so a
long message can carry **different topics in different passages** — and routing then prefers the chunk
whose topic matches the conversation, which is strictly finer than message-level tagging. With chunking
off, the message is the unit, as before.

## Composition with the other vector kinds (v0.36 facts, v2.9 thoughts)

The store also holds `kind="fact"` vectors (v0.36) and, once v2.9 lands, `kind="thought"` vectors.
Thematic recall is **message-layer only**: tagging and routing apply to `kind="message"` records;
fact/thought vectors stay untagged, and the v0.36 auto fact-RAG block, the `/recall` command, and the
v0.31 recall tool run unrouted (byte-identical). Extending topics to the other scopes is a possible
follow-on, not part of this phase.

## Why a local classifier and not an LLM pre-pass

A pre-pass (an extra model call to name the topic before retrieval) would give Лілі a say on the
**current** turn, but at the cost of +1 call, added latency, and pressure on the prompt budget — against
the project's "one call, not two" grain (see [INNER_MONOLOGUE.md](INNER_MONOLOGUE.md)). The embedding
classifier is **free** (the query vector already exists), **deterministic**, and **multilingual** (the
default embedder is Ukrainian-capable). Лілі keeps her agency through the **emit-and-carry-forward** read
— the current turn is handled locally, her steering shapes what follows.

## Mapping to the roadmap

**v2.10 — Semantic recall V: thematic recall (topic routing)**, a refinement of the recall line
(v0.16 index + v0.17 auto-RAG/context-expansion). Adds a topic label to each message `VectorRecord`, a
local embedding classifier (index-time tagging + per-turn active-topic pick), an over-fetching router
in front of the v0.17/v0.30 selection, and Лілі's `topics` emit on `set_state`. Depends on **v0.16**
(the index + seams), **v0.17** (auto-RAG + context expansion), and **v0.36** (the `kind` discriminator +
the memory-records contract test); **independent of v0.30** (composes with chunks). Reuses the v0.10
`RelationRead` emit pattern and the v0.6/v0.10 read-state command pattern. Per-user, isolated;
local-or-cloud per the configured embedder. **Off by default** (`LUMI_RAG_TOPIC`) → behaves exactly
like v0.17/v0.30 (one undifferentiated pool).
