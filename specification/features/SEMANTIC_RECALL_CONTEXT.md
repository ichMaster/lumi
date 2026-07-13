# Semantic recall — context expansion (retrieved lines arrive with their surroundings)

A naive top-K vector search returns the single closest **message**, ranked by cosine similarity.
But a message read alone is **torn from its thread**: you can't see what led to it, who was
answering whom, or how it resolved. `[2026-04-02] Ти: третю ніч не сплю` retrieved bare is weak —
it's a fragment without its conversation.

This document specifies how the **v0.17 automatic RAG** (and the `/recall` surface) inject retrieved
moments **with their context**, so the model grounds the reply in a coherent fragment instead of an
orphan line. It is a **refinement of [SEMANTIC_RECALL.md](SEMANTIC_RECALL.md)** — it changes neither
the `Embedder` nor `VectorStore` seam, only the **retrieve → assemble → inject** step that sits on
top of them.

## The principle: the retrieval unit ≠ the injection unit

- **Rank by message.** The vector match stays per-message — that's what gives the precise,
  by-meaning hit on the exact line.
- **Inject the moment.** What goes into the prompt is the hit **plus a small window of its
  neighbours** in the same session — a snippet of dialogue, not a single line.

Searching fine and showing coarse is the standard fix for context-orphaned RAG. The store already
carries everything needed to widen a hit into a moment.

## Mechanics over the existing `VectorStore`

The v0.16 store row is `{ user_id, msg_id, vector, text, ts, role }`. Context expansion needs one
extra lookup: **a message's neighbours in its session**. Two equivalent options:

- Add `session_id` (and an in-session ordinal/`ts`) to the stored row, or
- Resolve `msg_id → session_id + position` through the `Repository` (messages are already ordered
  per session).

Expansion is then: for each hit, take the `W` messages before and after the anchor **within the
same session** (default `W = 2`), in order.

```
hit:           [2026-04-02 03:14] Ти: третю ніч не сплю
window (W=2):  [2026-04-02 03:13] Я: ще не спиш?
               [2026-04-02 03:14] Ти: третю ніч не сплю      ← anchor (the matched line)
               [2026-04-02 03:15] Я: не вставай із телефоном, я поруч
```

The **anchor is marked** so the model knows which line actually matched; the neighbours are there
only to restore the thread.

## Assembling the injected block

Each hit becomes one **dated dialogue snippet**, grouped by moment rather than a flat list of lines:

```
# Релевантні моменти минулого
— 2026-04-02, ніч —
  Я: ще не спиш?
  Ти: третю ніч не сплю            ← (matched)
  Я: не вставай із телефоном, я поруч
```

The block lives in the **volatile tail** of the prompt (it changes per turn — `cache-the-static /
RAG-the-dynamic`, see [PROMPT_OPTIMIZATION.md](../../docs/PROMPT_OPTIMIZATION.md) §3) and is labelled
as **recall**, so the model treats it as a remembered fragment, not current conversation flow.

## Bounds (expansion costs tokens — spend them carefully)

A window multiplies each hit's token cost by `~(2W + 1)`, so the v0.17 caps tighten:

- **Merge overlapping windows.** Two hits a few turns apart share neighbours → emit **one** merged
  snippet, never the same line twice.
- **Dedup the whole snippet against the rolling window.** If any part is already in the live 20
  messages, drop that part (no double-context) — dedup applies to the expanded snippet, not just the
  anchor (extends [SEMANTIC_RECALL.md](SEMANTIC_RECALL.md) §v0.17 dedup).
- **Lower K, fund W.** With expansion on, fewer hits (`K`) at more context each often beats many
  bare lines. `K` and `W` are both config-bounded, under one token budget for the block.
- **Relevance floor unchanged.** Weak matches still aren't injected; on a turn with no strong hit,
  the block is empty (zero cost) — expansion never *adds* a turn's worth of noise.

## Coarser-grained alternative (and why message-window is the default)

Instead of expanding a message hit, one could **retrieve at session-summary or chunk granularity** —
the summary already carries context. That's cheaper to assemble but **loses the exact words**, which
is the entire reason semantic recall exists (the lossy summary layers already give the gist). So the
default is **message-ranked + window-expanded** (exact line, restored thread); summary-granular
retrieval is a fallback for very long-range hits where the surrounding turns are themselves stale.

## Division of labour (the snippet is never alone in the prompt)

Context expansion handles the **local** thread around a hit. The **global** arc is still carried by
the lossy layers that sit beside the RAG block: the week/day/session digests give "what's been going
on," the impressions layer (v1.9) gives the felt understanding. Together: RAG = the exact moment
with its immediate surroundings; digests = the arc it sits in. Neither alone; both in the same tail.

## Contract & isolation

- **Per-user, unchanged.** Both the message match and its neighbour-window read run **only over the
  requesting user's** messages — a hit never pulls neighbours from another user's session (the same
  isolation invariant, [SEMANTIC_RECALL.md](SEMANTIC_RECALL.md) §The hard contract). Pinned by the
  existing contract test, extended to assert the expanded snippet is single-user.
- **Trusted history, framed as recall.** The snippet is her/your own past words — trusted, dated,
  and labelled a memory; it grounds the reply but never overrides her voice or the emotion contract.
- **Graceful + non-blocking.** A failed neighbour lookup degrades to the bare anchor line (still
  better than nothing) and never blocks the turn.

## Mapping to the roadmap

A refinement **inside v0.17** (automatic RAG), not a new phase: v0.16 indexes every message and ships
`/recall`; v0.17 retrieves per turn — **and assembles the moment, not the line**, per this document.
`/recall` reuses the same expansion so search results are equally readable. Adds one capability to the
`VectorStore`/`Repository` (neighbours-of-a-hit); the `Embedder` seam is untouched.
