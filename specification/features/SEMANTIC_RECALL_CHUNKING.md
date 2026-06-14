# Semantic recall — chunking long messages (precise recall inside a long paste)

A long message — a pasted chapter, a roadmap, a wall of reflection — is embedded by v0.16 as **one
vector for the whole thing**. That vector is an **average** of everything in the message, so a query
about *one specific part* of it matches weakly (the signal is diluted by the rest). Raising
`LUMI_EMBED_MAX_CHARS` (v0.16.x) lets the *whole* long message be embedded instead of truncated, but
it's still **one averaged vector** — precision inside the message doesn't improve.

This document specifies **chunking**: index a long message as **several chunks** (passages), each with
its own vector, so recall can find the **exact passage** that matters — and inject that passage with
its context, not the whole 8000-char message. It is a **refinement of
[SEMANTIC_RECALL.md](SEMANTIC_RECALL.md) + [SEMANTIC_RECALL_CONTEXT.md](SEMANTIC_RECALL_CONTEXT.md)**:
the `Embedder` seam is untouched; it changes the **unit indexed** (chunk, not message) and extends the
**retrieve → expand → inject** step to two granularities.

## The principle: the retrieval unit is a chunk, the injection unit is a passage-in-its-thread

- **Index by chunk.** A message longer than a threshold is split into ~`chunk_chars`-sized passages
  (on sentence/paragraph boundaries, with a small overlap so a boundary sentence isn't lost). A short
  message stays **one chunk** — unchanged from v0.16.
- **Rank by chunk.** The vector match is per-chunk — that's what gives the precise hit *inside* a long
  message.
- **Inject the passage, in its thread.** What goes into the prompt is the matched chunk **+ a few
  adjacent chunks of the same message** (the relevant *passage*, not the whole message), placed inside
  the **±W neighbour messages** (the dialogue thread, from [SEMANTIC_RECALL_CONTEXT.md](SEMANTIC_RECALL_CONTEXT.md)).

"Search fine, show coarse" — now at **two** levels: fine = the chunk; coarse = the passage within its
conversation.

## Data model (extends the v0.16 `VectorRecord`)

The v0.16 row is `{user_id, msg_id, vector, text, ts, role}`. Chunking adds two fields (additive — a
contract change, pinned by the memory-records contract test):

- **`parent_msg_id`** — the content-addressed id of the **message** this chunk came from (so a chunk
  resolves back to its message and its position in the conversation).
- **`chunk_index`** — the chunk's ordinal **within its message** (0-based), so adjacent chunks of the
  same message can be assembled into a passage, in order.

`msg_id` becomes the **chunk's** stable content-addressed id (a hash of `parent|chunk_index|text`), so
re-chunking is idempotent. A one-chunk message has `chunk_index = 0` and `parent_msg_id == the
message id` — i.e. v0.16 behaviour is the `chunk_count == 1` special case (back-compatible).

## Indexing

- **Split on write / backfill:** a message above `chunk_threshold` chars is split into passages
  (~`chunk_chars`, overlap `chunk_overlap`, on sentence/paragraph boundaries); each passage is embedded
  and stored as its own `VectorRecord`. Below the threshold → one chunk (today's behaviour). Embedding
  failures degrade gracefully (the message is stored, indexed later) — unchanged from v0.16.
- **Staleness tag:** the chunk parameters join the existing `model@embed_max_chars` vectors tag (→
  `model@embed_max_chars@chunk_chars`), so changing chunking **re-embeds the history** the same way a
  model/cap change does (`ensure_backfill` resets + re-indexes).
- **Batching:** chunking *increases* the vector count (a long message → many vectors), so the local
  brute-force cosine and the cloud request batching (the char-budget batcher) must hold at the larger
  count — already true; just more rows.

## Retrieval → expansion → injection (extends LUMI-072)

1. **Search by chunk** — embed the query → top-`K` over this user's **chunk** vectors → relevance floor
   (all unchanged from v0.17, just over chunks).
2. **Chunk-level expansion (new):** for each hit, take its chunk **± `chunk_w` adjacent chunks of the
   same `parent_msg_id`** (in `chunk_index` order) → the **passage** of that message. Merge overlapping
   chunk windows; the matched chunk is the anchor.
3. **Message-level expansion (v0.17):** resolve `parent_msg_id` to its position in its session and take
   the **± `rag_w` neighbour messages** — but the long parent message is rendered as its **passage**
   (step 2), while the short neighbour messages render **in full**.
4. **Inject** — one dated dialogue snippet: neighbour messages whole, the parent message's relevant
   passage with the matched chunk marked `← (matched)`. Same `# Релевантні моменти минулого` block,
   same volatile tail, same char budget (`rag_max_chars`) and per-line cap (`rag_snippet_chars`).

```
# Релевантні моменти минулого
— 2026-04-02, ніч —
  Я: ще не спиш?                              ← neighbour MESSAGE (short → whole)
  Ти (з довгого повідомлення):
     …chunk 4… chunk 5 ← (matched) …chunk 6…  ← the relevant PASSAGE of the long message (a few chunks)
  Я: не вставай із телефоном, я поруч         ← neighbour MESSAGE (short → whole)
```

So a long message contributes only its **relevant passage**; short messages contribute **fully** —
precise, grounded, and compact (it never re-injects the whole 8000-char paste).

## Bounds (chunking multiplies vectors and tokens — spend carefully)

- **Threshold, not always.** Only messages above `chunk_threshold` are chunked; the common short
  message stays one vector (no index bloat, no behaviour change).
- **Passage, not message.** The parent renders as `chunk_w`-windowed chunks, never the whole message.
- **Merge + dedup.** Overlapping chunk windows merge (no chunk twice); the whole snippet is deduped
  against the live window (extends LUMI-071/072); the total stays under `rag_max_chars`.
- **`K` down, context up.** With chunking, fewer, more-precise hits at richer context often beats many
  diffuse ones — `K`, `chunk_w`, `rag_w` are all config-bounded under one budget.

## Contract & isolation (unchanged invariant)

- **Per-user, unchanged.** A chunk inherits its message's `user_id`; search, the chunk window, and the
  message window all run **only over the requesting user's** data — a chunk never pulls a passage or a
  neighbour from another user (the same isolation invariant, pinned by the existing contract test,
  extended to assert the **passage and its chunk neighbours** are single-user).
- **Additive data-model change.** `VectorRecord` gains `parent_msg_id` + `chunk_index`; ARCHITECTURE
  §Semantic recall + the memory-records contract test are updated **in the same commit**. The emotion
  field `{reply, emotion, intensity}` and the `Embedder`/`VectorStore` seams are untouched.
- **Graceful.** A failed chunk-neighbour lookup degrades to the bare matched chunk; a failed
  message-neighbour lookup degrades to the passage alone; never blocks a turn.

## Config

- `LUMI_RAG_CHUNK` — on/off (off → one-vector-per-message, i.e. v0.16/0.17 unchanged).
- `LUMI_RAG_CHUNK_CHARS` — target passage size (e.g. ~800).
- `LUMI_RAG_CHUNK_OVERLAP` — overlap between adjacent chunks (e.g. ~120) so a boundary sentence is
  reachable from either side.
- `LUMI_RAG_CHUNK_THRESHOLD` — only chunk messages longer than this (e.g. ~1200); shorter stay one
  chunk.
- `LUMI_RAG_CHUNK_W` — ± adjacent chunks of the same message to inject around a hit (the passage width).

## Why chunking and not just a bigger embed cap

`LUMI_EMBED_MAX_CHARS` (v0.16.x) fixes *coverage* (the whole long message is embedded, not truncated)
but not *precision* (it's still one averaged vector). Chunking fixes precision: each passage is its own
searchable unit, so a query about one part of a long paste lands on **that part**, and the prompt gets
**that passage** — not the whole message diluted across the budget. The two compose: a generous embed
cap **per chunk**, with chunking splitting the message into those chunks.

## Mapping to the roadmap

**v0.23 — Semantic recall III: chunking long messages**, a refinement of the recall line (v0.16 index
+ v0.17 auto-RAG/context-expansion), placed after v0.22. Adds chunk-granular indexing + the chunk
window to the existing `VectorStore`/`Repository` and the LUMI-072 expansion; the `Embedder` seam is
untouched. Depends on **v0.16** (the index + seams) and **v0.17** (auto-RAG + context expansion).
Per-user, isolated; local-or-cloud per the configured embedder. Off by default → behaves exactly like
today (one vector per message).
