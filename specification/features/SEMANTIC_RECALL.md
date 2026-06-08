# Semantic recall — RAG over all messages

Lumi's memory so far is **lossy by design**: the session window holds only recent turns, short
memory compresses to summaries/gists, long-term keeps facts + impressions. None can pull **the
exact words you said months ago** when they suddenly matter. Semantic recall adds the missing
layer: **every message is embedded into a per-user vector store, and the relevant past is
retrieved by meaning** — explicitly (`/recall`) and automatically (RAG in the turn).

- **v0.16 — Index & search:** the `Embedder` + `VectorStore` seams, index/backfill, and the
  explicit `/recall <query>` search.
- **v0.17 — Automatic RAG:** the incoming message is the query; the most relevant past moments
  are injected into the reply turn.

## Where it sits among the memory layers

| Layer | Holds | Its gap |
|---|---|---|
| session window | recent turns, verbatim | only recent |
| short memory (v0.9) | compressed recent + last 5 days | lossy |
| long-term: facts + impressions (v0.14) | durable understanding | not verbatim, not exhaustive |
| **semantic recall (v0.16–v0.17)** | **every message embedded → searched by meaning** | **exact recall of anything, anytime** |

It **complements**, never replaces, the others: the summary/impression layers give the *voice and
the gist*; semantic recall gives *the exact line* on demand.

## The two seams (swap the backend, never the core)

- **`Embedder`** — `embed(texts) → vectors`, mirroring the `LLMClient` seam. The **default is a
  local multilingual model** (e.g. fastembed / sentence-transformers — Ukrainian-capable), so
  **private messages never leave the machine** and there's no per-call cost. **Swappable to a
  cloud API** (Voyage / OpenAI) via config for higher quality — at the cost of sending private
  text out (off unless configured). Always **mockable** with deterministic fake vectors — **no
  paid APIs in CI**. The core depends on the seam, never an embedding SDK.
- **`VectorStore`** — behind the `Repository`, **keyed by `user_id`**:
  `{ user_id, msg_id, vector, text, ts, role }`. Local first — brute-force cosine in numpy is
  instant at this scale (a few thousand vectors), or `sqlite-vec`; a server vector DB later.
  Swapping the backend never touches the core (same principle as the Repository).

## Indexing

Every message (yours **and** Лілі's) is embedded as it's written; existing messages are
**backfilled** once on first run; incremental thereafter. Embedding failures degrade gracefully —
the message is still stored, just not yet indexed (retried later).

## v0.16 — `/recall <query>` (search on request)

An explicit semantic search: embed the query → **top-K cosine** over this user's vectors → return
the closest past lines, **dated**. The "search on request" surface — useful on its own and the
proof that the index works before automatic RAG rides on it.

## v0.17 — automatic RAG in the turn

Each turn: embed the incoming message → **top-K** over this user's vectors → inject a compact
**"relevant past moments"** block (dated) so the model can ground the reply in actual past lines.

- **Dedup:** drop anything already in the rolling window (no double-context).
- **Bound:** cap by count / token budget; a **relevance floor** so weak matches aren't injected.
- **Graceful + non-blocking:** error/empty → no block; never blocks or delays a turn (best-effort,
  like ambient context).
- **Trusted history.** The recalled text is *your/her own* past words — **trusted**, unlike
  untrusted web content (v3.2). It grounds the reply but never overrides her voice, the emotion
  contract, or her competence.

## The hard contract — per-user isolation

The vector store is keyed by `user_id`, and **retrieval (both `/recall` and the per-turn RAG) runs
only over the requesting user's vectors**. User A's messages can **never** surface in user B's
context — the same isolation invariant as the rest of memory (ARCHITECTURE §Identity, users, and
memory scopes), pinned by a contract test. Only de-identified `SharedMemoryItem`s cross users, via
the v2.3 pipeline — never raw embedded messages.

## Privacy

With the **local** embedder, message text and vectors stay on the machine — nothing is sent
anywhere. A **cloud** embedder sends the message text to the embedding provider; it's therefore
**off unless explicitly configured**, and the choice is surfaced in config. Vectors live in the
per-user store and are cleared by `/forget` like the rest of that user's memory.

## Intended stack

A local embedding model (fastembed / sentence-transformers, multilingual), `numpy` for cosine (or
`sqlite-vec`), both behind the seams. Added to `pyproject.toml` when v0.16 is built.

## Mapping to the roadmap

**v0.16 + v0.17 — Semantic recall**, right after the emotional-memory layer (v0.14–v0.15) — the
exact-recall complement to the lossy memory. Depends on **v0.2** (messages + the Repository).
Per-user, isolated; local-by-default and private.
