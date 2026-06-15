# Checkers — Лілі plays a game against you over a file bus

Лілі can be **a player in an external game** (Russian checkers / шашки) that runs in its own process. The
game and Лілі never call each other directly: the game drops a **move request** on a file bus, Лілі picks
a move and drops a **move reply** back, and the game plays it. She **keeps the whole game in memory** (every
move, both sides), so she understands the position as a story, not a one-off snapshot — and her answer on
this channel is shaped to a **single number**: an index into the legal moves the game already computed.

> The point: you sit at the checkers board and play *against Лілі*. The game owns the rules; Лілі owns the
> choice. She remembers how the game has gone so far, and replies with one move — as Claude or OpenAI,
> whichever she's configured to be.

This reuses three seams that already exist — it adds **no new model integration**:

- **Provider** — the move call goes through the same **`LLMClient`** seam (v0.1 / v0.18). "Лілі plays as
  Claude or OpenAI" is just `LUMI_PROVIDER` / `LUMI_MODEL`; nothing here is provider-specific.
- **Transport** — a **dedicated** file bus, a sibling of the v0.13 bridge (`inbox.jsonl` / `outbox.jsonl` +
  `state/fifo` pointers), but a **separate pair of files** (see *Why a separate channel* below).
- **Memory** — the running game lives behind the `Repository`, like every other record.

## Non-goals (scope guards)

- **Лілі is not a checkers engine.** She **chooses among the legal moves the game gives her** — she never
  invents a move and never needs to know the full ruleset. Playing strength is the model's, and will be
  modest; that's fine — the point is playing *with her*, not a hard opponent.
- **Rules and legality live in the external game**, not in Лілі. The bus carries an already-validated
  `legal_moves` list; the only thing Лілі returns is *which one*.
- **No new provider code.** If a move comes back malformed, code repairs it (below) — same philosophy as the
  v0.3 emotion gate.

## Why a separate channel (not her conversational inbox/outbox)

Her chat bus carries **persona turns** — natural-language messages that come back as `{reply, emotion,
intensity}`, and she also writes to the outbox **unprompted** (idle nudges v0.4, proactive thoughts v0.12).
A checkers move needs a constrained, correlated request→reply, and a clean "answers only" stream. So the game
gets its **own** file pair and its **own** call type (a directive, *not* a persona reply, like the mood and
summary calls). This keeps a board out of her relationship memory as if you'd *said* it, and keeps her
spontaneous chatter out of the channel the game polls.

---

## Data contract

Everything below is the stable seam. Three shapes: the two **bus records** (request / reply) and the
persisted **game record**. All bus records are **one JSON object per line** (append-only JSONL), carry
`game_id` + `move_number`, and are idempotent on that pair.

### Channel files (the bus)

| File | Writer | Reader | Purpose |
|---|---|---|---|
| `game-inbox.jsonl`  | external game | Лілі's daemon | move **requests** (the game asks Лілі to move) |
| `game-outbox.jsonl` | Лілі's daemon | external game | move **replies** (Лілі's chosen move) |
| `game-inbox.pos`    | Лілі's daemon | — | last consumed request offset (resume after restart) |
| `game-outbox.sent`  | external game | — | last consumed reply offset (the game's own pointer) |

Append-only + pointers mirror the v0.13 bridge / `state/fifo`, so both sides are **resumable** and a crash
never loses or double-plays a move. Paths are config (`LUMI_GAME_INBOX` / `LUMI_GAME_OUTBOX`), default under
`.lumi/`.

### 1. `move_request` — game → Лілі

```jsonc
{
  "type": "move_request",
  "game_id": "ck_20260615_a1b2",   // stable id for ONE game; groups the move history
  "move_number": 7,                // ply counter, strictly increasing within a game — the consistency key
  "to_move": "black",              // the side Лілі plays this turn ("white" | "black")
  "board": [[ ... ], ...],         // 8x8 snapshot, opaque to Лілі (context only) — see Board encoding
  "legal_moves": [                 // the ONLY choices; `index` is Лілі's entire answer space
    { "index": 0, "from": [5,2], "to": [4,3], "captures": [] },
    { "index": 1, "from": [5,4], "to": [4,3], "captures": [[5,3]] }
  ],
  "deadline_ms": 8000,             // optional: how long the game will wait before it self-plays a fallback
  "opponent": "owner",             // optional: the user_id Лілі is playing against (memory tag)
  "ts": "2026-06-15T09:30:00Z"
}
```

- **`legal_moves` is authoritative and complete.** The game guarantees every entry is legal and that the set
  is exhaustive. Лілі must return one of these `index` values — nothing else is a valid answer.
- `move_number` is **the** correlation/consistency key. The game emits exactly one open request per
  `(game_id, move_number)` and will not advance until it sees the matching reply (or the deadline lapses).

### 2. `move_reply` — Лілі → game

```jsonc
{
  "type": "move_reply",
  "game_id": "ck_20260615_a1b2",   // echoes the request
  "move_number": 7,                // echoes the request — together with game_id, the correlation key
  "move_index": 1,                 // THE ANSWER: a single integer, an index into the request's legal_moves
  "by": "lili",
  "model": "claude-opus-4-8",      // provenance: the active LLMClient backend (v0.18) — informational
  "say": "ця клітинка була надто спокійна",  // OPTIONAL in-character one-liner; NEVER parsed as the move
  "fallback": false,               // true → the move was chosen by code, not the model (see Output contract)
  "ts": "2026-06-15T09:30:03Z"
}
```

- **`move_index` is the whole contract** — one integer in `[0, len(legal_moves))`. `say` is decorative
  (trash-talk / commentary) and is on a different field on purpose, so a chatty model can never corrupt the
  move. A consumer that wants a silent opponent ignores `say`.
- **Validity rule (both sides enforce):** a reply is accepted only if `game_id` + `move_number` match the
  open request **and** `0 <= move_index < len(legal_moves)`. Anything else is dropped as stale/invalid.
- **Idempotent:** Лілі's daemon writes at most one reply per `(game_id, move_number)` and advances
  `game-inbox.pos`; a duplicate request for an already-answered ply re-emits the same reply, never a new call.

### 3. `GameRecord` — persisted memory (behind `Repository`)

This is "Лілі keeps all moves in memory." One record per game; the full move list is what the move call is
prompted with, so she reasons over the **whole game**, not just the current board.

```jsonc
GameRecord {
  game_id,                         // matches the bus records
  variant: "checkers_ru",          // ruleset label (room for others later)
  opponent_user_id,                // who she played — per-user tag (reuses v0.2 user-scoping)
  lili_side: "white" | "black",
  moves: [                         // the full ordered history — BOTH sides; this is the "memory"
    { move_number, side, move_index, from:[r,c], to:[r,c], captures:[[r,c]...], board_after, ts }
  ],
  result: null | "win" | "loss" | "draw",
  result_reason: null | "no_pieces" | "no_moves" | "resignation" | "deadline",
  started_at, ended_at, ts
}
```

- **Global to Лілі** (one being), but **tagged** with `opponent_user_id` so "games with you" stay yours
  (isolation invariant — a game played with A is never recalled as B's; pinned by a contract test).
- Updated every turn: append Лілі's chosen move and the opponent's last move as the requests arrive.
- **On game end**, the *experience* (not the move log) may distil into one `Impression` via the existing
  emotional-memory path (v1.4) — "a long, sharp game with you; she liked the endgame" — so the game enters
  her real memory as a memory, not a table. (Optional; gated like other impressions.)

### Output contract — "one number"

The move is produced by a **directive call** through `LLMClient` (the active provider — Claude or OpenAI),
**separate from the persona/emotion channel**:

- **Input:** the board, the **numbered** legal moves, and **the running move history for this `game_id`**
  (from the `GameRecord`, so she understands the whole game), ending in a hard instruction:
  *"Reply with ONLY the index number of your move — a single integer, nothing else."*
- **Output:** a single integer in `[0, len(legal_moves))`. Code extracts the first integer from the text and
  validates the range — the model's bit is **validated/repaired by code, never trusted raw** (same rule as
  the v0.3 emotion gate).
- **Repair ladder:** invalid / missing / out-of-range → **one bounded retry** → **code fallback** (a legal
  move chosen deterministically — prefer a capture, else first legal by index) with `fallback: true`. The
  turn never hangs and never plays an illegal move.

> Prompt-shaping for the single number is deliberate (the user's call): it keeps the channel
> provider-neutral (works the same on Claude tool-less and OpenAI JSON paths). A provider-native constrained
> form (an Anthropic `set_move` tool / OpenAI structured output with an integer-enum over the legal indices)
> is a **stricter optional upgrade** behind the same contract — `move_index` is unchanged either way.

### Board encoding

The `board` is **context only** — Лілі answers from `legal_moves`, so the encoding just has to be stable and
legible to the model. Recommended: an 8×8 array using the same scheme the external game already serialises
(`null` empty; `{color, isKing}` per piece). Лілі never parses it for legality; the game owns that.

---

## Contract & tests

- **Bus:** append-only JSONL + offset pointers (resumable, crash-safe); dedupe by `(game_id, move_number)`;
  a separate file pair from the chat bus (no persona turns, no spontaneous writes on this channel).
- **Determinism:** the model is **mocked** in tests; the move call is a pure function of `(GameRecord,
  legal_moves)`; the fallback picker is deterministic given an injected seed; no real network, no paid call.
- **Assertions:**
  1. a `move_request` with N legal moves → a `move_reply` whose `move_index ∈ [0, N)`;
  2. a garbage / out-of-range model answer → one retry → a **legal** `move_index` with `fallback: true`;
  3. a duplicate or stale `move_number` is **ignored** (no second model call, the prior reply re-emitted);
  4. the `GameRecord.moves` list accumulates the full ordered history across turns;
  5. **restart** resumes from `game-inbox.pos` (no missed or replayed move);
  6. **isolation** — a game tagged `opponent_user_id = A` never surfaces in B's memory/context.
- **Provider-agnostic:** the same contract passes on the Anthropic and OpenAI-compatible backends (the
  directive call uses `LLMClient.reply(...)`; only the `model` provenance field differs).

## Mapping to the roadmap

**Current release: `0.18.0`** (the root `VERSION`). Every seam this feature stands on is **already shipped**,
so it is buildable today with no new infrastructure:

- **v0.18** — provider switching: "Лілі plays as Claude or OpenAI" is the existing `LUMI_PROVIDER` /
  `LUMI_MODEL` config, used unchanged.
- **v0.13** — the file-bus + `state/fifo` pattern this clones into a **separate** channel.
- **v0.2** — the user-scoped `Repository` the `GameRecord` lives behind.

It introduces **no new seam**: a directive call on the existing `LLMClient`, a new persisted record behind the
existing `Repository`, and a second instance of the existing bus — a natural sibling of the v0.13 Telegram
bridge daemons (a separate process coupled to the core only by files), **off by default**.

**Suggested slot.** The planned `v0.19`–`v0.24` phases are already spoken for (file tool, dictation,
Wikipedia, semantic recall III/IV), so this lands as a **new later v0.x phase (≈ `v0.25`)** — or wherever the
maintainer prefers; it has no ordering dependency on those. Optionally composes with **v1.4** (emotional
memory — a finished game becomes one `Impression`).
