# Conversation moves (v1.1) — operator notes

The anti-mirror engine: every reply performs a deliberate **conversation move** chosen
before generation inside the think-phase. Design: the concept doc
([EN](../specification/features/lili-conversation-system-concept-en.md) /
[UA, reality-anchored](../specification/features/ukrainian/lili-conversation-system-concept.md))
and ROADMAP §v1.1.

## Switch it on

```env
LUMI_MOVES=on                   # off by default; off → byte-identical to v0.42 behavior
# LUMI_INNER_VOICE_MOVES_FILE=core/inner_voice_moves.md   # the v2 think instruction (default)
```

One flag. With it on:

- the system prompt asks for the **`move` field** on `set_state` (`MOVE_INSTRUCTION`) — one of
  `deepen / position / object / develop / associate / example / return`;
- the think-phase runs the **v2 instruction** (`core/inner_voice_moves.md`): `[ретроспектива]`
  (declared-vs-done validation) → `[голоси]` (typed proposals) → `[арбітр]` (the rule table +
  the code-substituted `{move_rules}` dynamics) → `[репліка]`;
- each of Лілі's messages in `store.json` carries `"move": "<type>"` (`null` on your lines);
- her recent replies replay to the model **with** their declared type (a `<move>…</move>`
  marker in the replayed content only — never in stored text, never rendered anywhere).

## What to look at

- **The think box / log** — with `LUMI_THINK_SHOW=debug` the four blocks are visible per turn;
  the chosen move is the `[арбітр] вибір:` line.
- **`store.json → messages`** — the per-reply `move` values; a healthy conversation alternates
  types (the arbiter bans a type declared twice in a row).
- **The dynamic rules** — when data warrants it, the prompt carries: the declared-type history,
  a same-type ban, or the topic-died hint (two terse reactions → `associate`/`return`).

## Guarantees

- The `{reply, emotion, intensity}` contract is untouched — `move` is additive (the v0.10
  `relation` pattern); a garbled value is dropped silently, never blocks a turn.
- Old stores load without migration (`move` defaults to `null`); with the flag off, typed
  records replay with **no** marker and nothing new is stored — byte-identical off, always.
- Never competence: the move shapes how she leads, never whether she helps.
