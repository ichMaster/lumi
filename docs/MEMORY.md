# Memory — how Лілі remembers (implementation reference)

How the **v0.2 three‑layer memory** is actually built, collected, and injected into the
prompt — grounded in the code as shipped, not the design intent. For the design/spec
(including the future shared‑experience layer), see
[../specification/ARCHITECTURE.md](../specification/ARCHITECTURE.md) §Memory and §Identity,
users, and memory scopes. This document describes **what the code does today**.

> TL;DR — Three layers, all in one JSON file keyed by `user_id`. **Session history** rides
> the model's `messages` array (trimmed to a window). **Short summaries** and **long‑term
> facts** ride the **system prompt** (rebuilt every turn). Summaries + facts are *written*
> at session end (summarized + extracted by the model) and *read* on every turn.

---

## 1. The layers at a glance

| Layer | What it is | Where stored (JSON key) | Goes into the model as | Written when | Read when |
|---|---|---|---|---|---|
| **Session history** | the live conversation's messages | `messages` (by `session_id`) | the `messages` array (the verbatim live tail, 40–60) | every turn (append) | every turn (windowed) |
| **Session digest** | running summary of *this* session's earlier part | `digests` (by `session_id`) | the **system prompt** (in-session compaction) | when the window overflows (batches) | every turn (if present) |
| **Short memory** | a gist of each finished session (length scales with size, 1–8 sentences) | `summaries` (by `user_id`) | the **system prompt** (last 5) | at session end | every turn |
| **Long‑term memory** | durable facts about the user | `facts` (by `user_id`) | the **system prompt** (all) | at session end | every turn |

Everything is **user‑scoped**: every record carries a `user_id` (the single default
`owner` until real accounts arrive in v1.3), and a record written under user A is never
read in user B's context — the **isolation invariant** ([../core/repository.py](../core/repository.py),
pinned by [../tests/contract/test_isolation.py](../tests/contract/test_isolation.py)).

---

## 2. Where it lives — the store

All three layers persist to **one JSON file**: `.lumi/store.json` by default
(`DEFAULT_STORE_PATH` in [../core/config.py](../core/config.py); overridable via the
`LUMI_STORE_PATH` env var). The concrete store is `JsonRepository` in
[../state/local_store.py](../state/local_store.py), behind the `Repository` interface — so the
core never touches a concrete store, and a SQLite/server DB is a drop‑in swap later.

File structure:

```json
{
  "sessions":  { "<session_id>": { "id", "user_id", "started_at", "ended_at" }, ... },
  "messages":  { "<session_id>": [ { "session_id", "user_id", "role", "text", "ts" }, ... ] },
  "summaries": { "<user_id>":    [ { "user_id", "session_id", "summary", "ts" }, ... ] },
  "facts":     { "<user_id>":    [ { "user_id", "fact", "meta", "confidence", "ts" }, ... ] },
  "digests":   { "<session_id>": { "session_id", "summary", "compacted_count", "ts" } }   // in-session compaction
}
```

The record shapes are defined in [../core/repository.py](../core/repository.py):

- `Session{ id, user_id, started_at, ended_at? }`
- `Message{ session_id, user_id, role, text, ts }` — `role` is `"user"` or `"lili"`
- `ShortSummary{ user_id, session_id, summary, ts }`
- `LongTermFact{ user_id, fact, meta, confidence, ts }`

Persistence is **atomic** — `JsonRepository._persist` writes a temp file then `os.replace`s
it, on every mutation. On load, a **migration shim** defaults any pre‑v0.2 record missing a
`user_id` to `owner`, so older stores keep working.

> The **canon** (Лілі's character) is **not** in the store — it lives in
> [../core/canon/lili.md](../core/canon/lili.md) and is loaded fresh from disk each turn.

---

## 3. How memory is collected (written)

### 3.1 Session history — appended every turn

`Core.reply` ([../core/agent.py](../core/agent.py)) persists **both** the user line and Лілі's
reply after each turn:

```python
self._repo.append_message(make_message(session.id, self._user_id, "user", user_text))
self._repo.append_message(make_message(session.id, self._user_id, "lili", reply_text))
```

This is the *full* history. It is never trimmed in storage — only the in‑context view is
windowed (§4.1).

### 3.2 Short memory + long‑term facts — written at session end

Both are produced by **`Core.end_session(session)`** ([../core/agent.py](../core/agent.py)),
which runs when a session ends:

1. Mark the session `ended` (`ended_at`).
2. **If the session has no messages → stop** (an empty session writes nothing).
3. Temporarily **disable extended thinking** for the housekeeping calls (they're internal
   extraction, not user‑facing reasoning — this keeps quitting fast).
4. `_write_summary(...)` — one model call to summarize → store a `ShortSummary`.
5. `_accumulate_facts(...)` — one model call to extract facts → store new `LongTermFact`s.
6. Restore the thinking setting.

Everything is **best‑effort**: a model failure on either step degrades to *nothing written*
and **never raises** (so it can't block quitting).

**When does `end_session` fire?** Only on a **clean end**, from the TUI:

| Trigger | Path | Behavior |
|---|---|---|
| **Quit** (Ctrl+Q / Ctrl+C) | `action_quit` → `_process_current_session` → `end_session` | shows a *"Зберігаю сесію перед виходом…"* system line, runs off the UI thread, **exits only when done** |
| **`/new`** | `_new_session` → `_process_current_session` → `end_session` | summarizes the old session, then starts a fresh one and clears the screen |
| crash / kill | `on_unmount` fallback | best‑effort; may not run |

> If the process is killed (not a clean quit/`/new`), that session's raw messages remain in
> the store but **no** summary/facts are written for it.

#### The summarization prompt (`SUMMARY_SYSTEM`, [../core/memory.py](../core/memory.py))

```
Ти стискаєш діалог у підсумок для памʼяті Лілі — від третьої особи, по суті:
про що говорили й важливе про співрозмовника. Без вступів і звертань — лише підсумок.
```

`summary_request(messages)` turns the session transcript into one `user` message and
appends a **target length scaled to the session size** — `Орієнтовний обсяг: N речень.`
where `N = summary_sentences(len(messages))`. The model's reply **is** the summary text;
`_write_summary` stores it as `ShortSummary(user_id, session_id, summary, ts)` (skipped if
empty).

**Length scaling** (`summary_sentences`, [../core/memory.py](../core/memory.py)) — roughly
**one sentence per 3 messages, clamped to `[1, 8]`** (`max(1, min(8, (n+2)//3))`), so a short
exchange gets a one‑liner and a long conversation a fuller paragraph:

| messages | 1–2 | 4 | 8 | 12 | 20 | 30+ |
|---|---|---|---|---|---|---|
| **sentences** | 1 | 2 | 3 | 4 | 7 | 8 (cap) |

`SUMMARY_SYSTEM` is the stable base instruction; the length directive is appended
per‑session. (The model decides the actual prose — the target only guides it.)

#### The fact‑extraction prompt (`FACTS_SYSTEM`, [../core/memory.py](../core/memory.py))

```
Виокрем стійкі, довготривалі факти про співрозмовника з діалогу —
по одному факту на рядок, стисло (імʼя, уподобання, важливі обставини).
Лише те, що варто памʼятати надовго. Якщо нічого вартого — поверни порожньо.
```

The **selection policy is entirely this prompt** — there is no scoring/ranking on our side.
The flow:

1. `facts_request(history)` → `(FACTS_SYSTEM, transcript)` → one model call.
2. `parse_facts(text)` ([../core/memory.py](../core/memory.py)) splits the reply **one fact per
   line**, strips leading bullets/numbering (`_BULLET_CHARS`), and drops blank lines.
3. **Dedup against stored facts (exact‑string match)**, then store each *new* one as
   `LongTermFact(user_id, fact, meta="", confidence=0.5, ts)`.

> `meta` (`""`) and `confidence` (`0.5`) are **fixed placeholders** today — present in the
> shape but not computed and not used to select or filter. The dedup is **exact‑match only**,
> so paraphrases accumulate (e.g. `Звати Віталік` and `Імʼя: Віталій` are stored as two
> different facts). This is the main reason the fact list grows over time. See §8.

---

## 4. How memory is used in prompts (read)

Every turn assembles an Anthropic call with **two channels**:

```
system   = build_system_prompt(canon, summaries, facts)   ← canon + Short + Long-term memory
messages = [ ...windowed session history..., {new user line} ]   ← Session history
```

So **session history → the `messages` array**, while **short + long‑term memory → the
`system` prompt**. The assembly happens **fresh on every turn** (so new memory takes effect
immediately, and a restart rehydrates).

### 4.1 Session history → the `messages` array (windowed)

In `Core.reply` ([../core/agent.py](../core/agent.py)), the **live tail** (messages not yet
folded into the session digest) is sent verbatim:

```python
digest = self._maybe_compact(session, history)              # fold older msgs into the digest
compacted = digest.compacted_count if digest else 0
live = trim_history(history[compacted:], self._memory_window + self._compaction_batch)
messages = [{"role": _ROLE_TO_LLM[m.role], "content": m.text} for m in live]
messages.append({"role": "user", "content": user_text})
```

- The verbatim window is a **floating window** (default `memory_window` = **40**, batch = **20**):
  the live tail floats between **40 and 60** messages; once it would exceed 60, the oldest 20 are
  compacted into the digest (§4.5) and the tail drops back to 40. The `trim_history(..., 40+20)`
  is a safety cap.
- Roles are mapped to the model's chat roles: `lili` → `assistant`, `user` → `user`.
- **Only the *current* session's messages** ride this array. Prior sessions are **not**
  re‑sent as raw messages — they are represented by their **summaries** (§4.2). Older messages of
  *this* session are represented by the **digest** (§4.5).

### 4.2 Short + long‑term memory → the system prompt (rehydration)

`Core._system_prompt()` ([../core/agent.py](../core/agent.py)) rebuilds the system prompt
each turn from the active user's own records:

```python
summaries = [s.summary for s in self._repo.recent_summaries(user_id, RECENT_SUMMARIES)]  # last 5
facts     = [f.fact    for f in self._repo.facts(user_id)]                                # ALL
return build_system_prompt(self._canon, summaries=summaries, facts=facts)
```

- **Summaries** are capped at the **last 5** (`RECENT_SUMMARIES` in
  [../core/memory.py](../core/memory.py)).
- **Facts** are **uncapped** — *all* of them are read every turn.
- Isolation holds at read time too — only this `user_id`'s records are loaded.

### 4.3 The assembled system prompt

`build_system_prompt(canon, summaries, facts, digest, style)`
([../core/prompt.py](../core/prompt.py)) composes the blocks **around** the canon, in a fixed
order — **canon → summaries → facts → digest → style**:

```
<canon — core/canon/lili.md, verbatim>

Памʼять про попередні розмови з цією людиною:
- <summary 1>
- <summary 2>
...

Що ти памʼятаєш про цю людину:
- <fact 1>
- <fact 2>
...

Раніше в цій розмові (стисло):
<session digest — §4.5, only if compacted>

<STYLE_HEADER — only if a /style is active; see docs/STYLES.md>
<active style overlay text>
```

With no summaries/facts/digest/style (a brand‑new user), the result is the **canon
verbatim** — exactly the v0.1 behavior. The **digest** is the in‑session compaction block
(§4.5); the **style** block is the answer‑style overlay (not memory — it's documented
separately in [STYLES.md](STYLES.md)), appended last as a prioritized directive.

### 4.4 What is added every turn (and the caps)

| Piece | Cap | Notes |
|---|---|---|
| Canon | none — **full** file | fixed overhead every turn |
| Summaries | **last 5** | compresses older sessions so they aren't resent |
| Facts | **none — ALL** | grows unbounded as facts accumulate (§8) |
| Session digest | 1 (current session) | the earlier part of *this* conversation, if compacted (§4.5) |
| Session history | **floating 40–60 messages** of the *current* session | + the new user line |

`Core.last_prompt = {"system", "messages"}` captures the exact prompt sent on the last turn
— surfaced in the TUI by `/prompt` (§6).

### 4.5 In‑session compaction (the running session digest)

Within a single long session, messages that fall outside the verbatim window aren't dropped —
they're folded into a per‑session **`SessionDigest{session_id, summary, compacted_count}`** and
injected into the system prompt under *"Раніше в цій розмові (стисло):"*. This is **in‑session**
compaction (the current conversation), distinct from the cross‑session `ShortSummary` (whole past
sessions).

The **floating window** (in [`compaction_plan`](../core/memory.py)): keep the verbatim tail between
`memory_window` (40) and `memory_window + compaction_batch` (60). When a turn would push it past 60,
fold the oldest 20 into the digest (`Core._maybe_compact` → `digest_request` → store via
`set_digest`) and the tail drops back to 40. So:

- ≤ 40 messages → no digest, all verbatim (today's behavior unchanged).
- The digest summarization fires **~once per 20 messages** past the window — not every turn.
- Nothing is ever lost: a message is always either **verbatim** or **in the digest**.
- `Core.last_compaction` reports how many messages were folded this turn; the TUI shows
  *"Compacted N earlier messages into a running summary."*
- Best‑effort: runs with extended thinking off; a model failure keeps the prior digest and never
  breaks the turn. **Auto‑triggered** (no command). Tunable via `LUMI_MEMORY_WINDOW` /
  `LUMI_COMPACTION_BATCH`.

---

## 5. Lifecycle

```
TURN  (Core.reply)
  maybe_compact(session)  → fold older-than-40 msgs into the digest [In-session compaction]
  messages ← live tail (40–60) ;  system += session digest          [Session history + digest, read]
  system   ← canon
           + recent_summaries(user, 5)   → injected                 [Short memory, read]
           + facts(user)                 → injected                 [Long-term memory, read]
  → model → reply
  → append user + lili messages                                     [Session history, write]
  → record last_prompt / last_stats / totals

SESSION END  (Core.end_session — on quit or /new, thinking forced off)
  if session has messages:
    full history → summarize  → add_summary      [Short memory, write]
    full history → extract    → parse → dedup → add_fact   [Long-term memory, write]

RESTART / NEXT SESSION
  the next turn's _system_prompt() loads the just-written summaries + facts
  → Лілі recalls past sessions + durable facts (the "rehydration")
```

The restart path is exactly what [../tests/integration/test_rehydration.py](../tests/integration/test_rehydration.py)
proves: chat → end (writes summary + fact) → new `Core` over the same store → the next
turn's system prompt carries the prior summary and fact.

---

## 6. Viewing, clearing, and inspecting (TUI)

| Command / key | What it does | Touches memory? |
|---|---|---|
| **`/memory`** | renders `Core.view_memory()` — your summaries + facts | read‑only |
| **`/forget`** | `Core.clear_memory()` after a confirm — **deletes** your summaries + facts | wipes short + long‑term (keeps messages, canon, other users) |
| **`/new`** | summarizes the current session, starts a fresh one, clears the screen | writes summary + facts for the old session |
| **`/prompt`** | shows `Core.last_prompt` — the exact `[SYSTEM]` + `[MESSAGES]` sent last turn | read‑only |
| **Ctrl+L** | clears the **screen** only | none — memory and the live session are untouched |

`Core.view_memory(user_id?)` returns a `MemoryView{summaries, facts}`; `Core.clear_memory`
calls `Repository.clear_memory(user_id)`, which pops the user's `summaries` and `facts`
(message history is **not** removed).

---

## 7. Configuration & code map

**Config knobs** ([../core/config.py](../core/config.py)):

| Setting | Default | Env override | Effect |
|---|---|---|---|
| `memory_window` | `40` | `LUMI_MEMORY_WINDOW` | verbatim message window (older messages are compacted) |
| `compaction_batch` | `20` | `LUMI_COMPACTION_BATCH` | fold older messages into the digest in batches of this size |
| `store_path` | `.lumi/store.json` | `LUMI_STORE_PATH` | where the store file lives |

Plus the in‑code constant `RECENT_SUMMARIES = 5` ([../core/memory.py](../core/memory.py)) — how
many summaries are recalled into the prompt.

**Code map:**

| File | Responsibility |
|---|---|
| [../core/repository.py](../core/repository.py) | record shapes (`Session`/`Message`/`ShortSummary`/`LongTermFact`/`SessionDigest`) + the `Repository` interface + `make_message`/`now_iso` |
| [../state/local_store.py](../state/local_store.py) | `JsonRepository` — the concrete JSON store (sessions/messages/summaries/facts/digests), atomic write, migration shim |
| [../core/memory.py](../core/memory.py) | `trim_history`, `summary_request`/`SUMMARY_SYSTEM`/`summary_sentences`, `facts_request`/`FACTS_SYSTEM`, `parse_facts`, `compaction_plan`/`digest_request`/`COMPACTION_DIGEST_SYSTEM`, `RECENT_SUMMARIES` |
| [../core/prompt.py](../core/prompt.py) | `load_canon`, `build_system_prompt(canon, summaries, facts, digest)` — the prompt assembler |
| [../core/agent.py](../core/agent.py) | `Core` — `reply`, `_system_prompt`, `_maybe_compact`/`_housekeeping_reply`, `end_session`/`_write_summary`/`_accumulate_facts`, `view_memory`/`clear_memory`, `last_prompt`/`last_stats`/`last_compaction`/`totals` |
| [../core/config.py](../core/config.py) | `memory_window`, `compaction_batch`, `store_path` |
| [../core/user.py](../core/user.py) | `User` + `DEFAULT_USER_ID = "owner"` |
| [../tui/app.py](../tui/app.py) | the `/memory` `/forget` `/new` `/prompt` commands, Ctrl+L, quit‑summarize |

**Tests that pin the behavior:**

- [../tests/contract/test_isolation.py](../tests/contract/test_isolation.py) — the per‑user isolation invariant
- [../tests/contract/test_memory_records.py](../tests/contract/test_memory_records.py) — the `ShortSummary`/`LongTermFact` shapes
- [../tests/unit/test_memory.py](../tests/unit/test_memory.py) — `trim_history` windowing, summary scaling, `compaction_plan`
- [../tests/integration/test_compaction.py](../tests/integration/test_compaction.py) — in‑session compaction (digest persists, floating window, digest injected, `last_compaction`)
- [../tests/unit/test_prompt.py](../tests/unit/test_prompt.py) — `build_system_prompt` assembly + canon‑verbatim‑without‑memory
- [../tests/integration/test_summary.py](../tests/integration/test_summary.py) — end‑of‑session summarization
- [../tests/integration/test_facts.py](../tests/integration/test_facts.py) — fact extraction, accumulation + dedup, isolation
- [../tests/integration/test_rehydration.py](../tests/integration/test_rehydration.py) — restart recalls a prior summary + fact
- [../tests/integration/test_memory_commands.py](../tests/integration/test_memory_commands.py) — `/memory` and `/forget`

All of the above run against `MockLLMClient` — **no paid API calls**.

---

## 8. Known limitations (current v0.2 scope)

These are deliberate simplifications, not bugs — flagged so the behavior isn't surprising:

1. **Facts are uncapped at read time.** *All* facts are injected every turn, so the system
   prompt grows as facts accumulate. (Summaries are capped at 5; history at 60 messages.)
2. **Fact dedup is exact‑string match only.** Paraphrases of the same fact are stored as
   separate entries, so near‑duplicates pile up over many sessions.
3. **`confidence` / `meta` are placeholders.** They exist in the record but aren't computed
   or used for selection/ranking.
4. **Extraction is line‑parsing, not structured output.** Whatever lines the model returns
   become facts; there's no schema/validation yet (that hardens with the v0.3 emotion‑field
   validation gate).
5. **Rolling window is by message count (40 verbatim, floating to 60), not a token budget.**
   In‑session compaction (§4.5) folds older messages into a digest rather than dropping them.
6. **Single user.** The `user_id` scoping is in place but runs with one default `owner`;
   real accounts/auth arrive in v1.3.
7. **No shared‑experience layer.** Cross‑user, de‑identified memory (the v2.3
   cross‑pollination pipeline) is **not** built — every layer here is just the owner's.
8. **Summarization only on a clean end.** A crash/kill skips it for that session.

Natural follow‑ups (not yet implemented): semantic dedup/merge of facts at extraction time,
a fact cap/relevance filter at read time, real `confidence` scoring, and prompt caching on
the stable system prefix.

---

*This document reflects the implementation at v0.2.0. When the memory seams change, update
this file alongside the code and the contract tests.*
