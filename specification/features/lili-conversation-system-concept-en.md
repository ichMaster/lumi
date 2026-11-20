# Lili: Conversation Development System

Concept of changes. Goal: eliminate mirroring (retelling the user's words, especially metaphorically) and give Lili a mechanism for actively driving the conversation — her own moves, topic memory, returning to open questions.

Root cause: when the model has nothing to add, it picks the safest pattern — reflective listening. The solution has two parts: (a) every reply must execute a concrete conversation move, (b) Lili has her own material for moves — a topic base with the development history of each topic.

**Where it stood when this was written (v1.0, with all of v0.1–v0.42 shipped).** Much of the machinery this concept expected to build already exists in the codebase, so the phases below are anchored to the real mechanisms:

- The think-phase with three voices **already exists**: v0.38, the authored `core/inner_voice.md` (Impulse / Sobriety / Standard — Імпульс / Тверезість / Стандарт), one call per reply (the reasoning is a content block of the same response). The TUI already splits the output: the reply goes to the screen and the history, and the monologue goes to the Thinking box and the log (`think_show`: debug / open / off). Phase 2 **extends this file**; it does not build a new engine.
- The reply contract `{reply, emotion, intensity}` is **locked** (v0.3) and does not change. Extra fields are additive only, following the precedent of `relation` (v0.10) and the planned `topics` (v6.13).
- Semantic memory **already exists**: the `Embedder` + `VectorStore` keyed by `user_id` (v0.16), auto-RAG in the turn (v0.17), chunks (v0.30), the recall tool (v0.31), and the `kind` discriminator on vectors (v0.36). Phase 4 adds `kind="topic"`, not new infrastructure.
- Model tiers **already exist**: the v0.40 per-operation routing and the v0.41 profiles (`reply` / `think` / `mood` / `housekeeping`). Where this concept says "a Haiku call", read **the housekeeping tier of the active profile** (Haiku on anthropic, flash-lite on gemini, nano on openai).
- News and the live web **already exist**: v0.25 (Guardian `news_search`/`news_read`), v0.27 (`web_lookup`, Gemini grounded search), and the autonomous `%catchup`/`%brief` (v0.33), which already fetch news on the v0.42 schedule (the morning `%brief` is a live row in `core/schedule.yaml`, `core/schedule.toml` before v2.9). Phase 5 **connects the topic base to these tools**; it builds no new ones.
- Not yet built at the time of writing: the move typing + the retrospective + the arbiter (Phase 2), the topic base (Phase 3), the topic vectors (Phase 4), and seeding news topics from the interest base (Phase 5). Phases 1–2 have since shipped as v1.1 (released `1.1.0`). The impressions (`Impression`, v6.10) and the thematic recall (v6.13) are planned neighbours that Phases 3–4 must compose with, not duplicate.

---

## Phase 1. Prompt changes

Goal: remove the sources of mirroring that can be fixed without code.

This is authoring work with no code. The changes go into the canon file (`core/canon/lili.md`, written by the rules in [docs/CANON_SPEC.md](../../docs/CANON_SPEC.md)) and the style block (v0.5, [docs/STYLES.md](../../docs/STYLES.md)).

System prompt changes:

1. Positive replacement rule. Instead of the prohibition "do not use metaphors" — a requirement: "Never retell or paraphrase what the user said. Every reply must add something new: a question, your own position, disagreement, a fact, or a memory." The block needs the prominence of the style header or the daily mood (v0.6): a priority place in the prompt, not a line at the tail of the canon.
2. Character sheet audit. Remove or rephrase traits like "poetic", "figurative", "deep" — the model signals them in the cheapest way possible: with metaphors. Replace with behavioral traits: "asks concrete questions", "has her own preferences and defends them".
3. Few-shot contrast. Two or three example pairs "bad reply (mirror) / good reply (move)" directly in the prompt. For small models this is the most effective lever.

Definition of done: in a 20-exchange test session, the share of retelling replies drops noticeably (eyeball evaluation).

---

## Phase 2. Seven move types, three voices, validation

Goal: every reply is a deliberate conversation move, chosen before generation, with execution checked on the next exchange.

**Anchored in the code:** the three voices and the thinking call already work (v0.38, `core/inner_voice.md`: Impulse weighs warmth, Sobriety checks against the facts and her states, Standard holds the boundaries). This phase **edits that same authored file**: the voices gain the duty to propose a move of a specific type, the retrospective goes before them, and the arbiter goes after them. There are no new calls: the format below lives inside `<think>` of the same single call (the "one call, not two" principle of the v6.9 inner monologue). The five hard limits of `inner_voice.md` (roles, not a psyche; the voices never speak aloud; Standard does not scold; **never competence**; one reply goes out) bind the arbiter and the retrospective too.

### 2.1. The seven move types

| Type | Essence | Example (topic: Egan's book about simulation) |
|---|---|---|
| deepen | a concrete question about an aspect of what was said | "Do they run into anything in the book? Bugs, edges of the world?" |
| position | a first-person statement one can argue with | "I think even if they figured it out, nothing would change." |
| disagree | disagreement with what was said or assumed | "Wait, why did you decide they don't suspect? Maybe they do and stay quiet." |
| develop | the next logical step from the user's thought | "If the simulation can't be seen from inside, the question loses meaning for us too." |
| association | own material: Curiosity, past conversations, topic base | "That's your Nadiloka exactly — would your creatures notice the gardener?" |
| example | pull from the abstract to the concrete | "Show me the scene where this is most visible." |
| return | pull an open loop from an old topic | "You never told me — did you finish it?" |

The value stored for each type (the `move` value, see 2.3) is its English enum identifier, like the emotion enum: `deepen`, `position`, `object` (disagree), `develop`, `associate` (association), `example`, `return`.

The first four work inside the current topic, "association" and "return" bring material from outside, "example" grounds the discussion.

Until Phase 3, "association" feeds on what is already in the prompt: her thoughts (the v0.12 thought stream), past conversations through the RAG block and the recall tool (v0.17/v0.31), and the recent conversations. The topic base strengthens it later.

### 2.2. Structure of the thinking call

One call per reply. Fixed output format:

```
[retrospective]
previous reply's intent: <type from tag>
executed: yes/no
user reaction: elaborate/short

[voices]
Impulse: <type> — <gist in one line>
Sobriety: <type> — <gist>
Standard: <type> — <gist>

[arbiter]
choice: <voice>, move: <type>
rationale: <one line>

[reply]
<response text executing the chosen move>
```

The program (TUI) splits the output: the reply goes to the screen and into history, the monologue goes to the debug log.

On the existing think-phase: one call per reply is already the case (v0.38). The format lives **inside `<think>`** and is appended to `core/inner_voice.md`; the retrospective's intent line reads the declared type from the record's `move` field (see 2.3). The split is already implemented: the reply goes to the screen and into history, and the monologue goes to the Thinking box and the log (`think_show`, at the v0.3 log level). The monologue is never written to long-term memory (the v6.9 invariant).

### 2.3. Tagging the history

Every Lili reply is stored in the chat history with a type tag: `[move: deepen] Do they run into anything in the book...`. The tag is hidden from the user but included in the prompt of the next call. User messages carry no tags. No separate ledger is needed: intent, text, and reaction are all visible in the history itself (last 20 messages).

**As specified for the build (this replaces the inline tag above).** The chosen move's type comes back as an **additive optional `move` field on `set_state`**, by the same precedent as `relation` (v0.10) and the planned `topics` (v6.13): the `{reply, emotion, intensity}` contract is unchanged, a missing or garbled field is silently dropped, and the handling is per provider. **The `Message` record gains a new optional `move` field** beside the existing `emotion`/`intensity`, with English identifiers as values, like the emotion enum: in `store.json` each of Lili's messages carries `"move": "deepen" | "position" | "object" | "develop" | "associate" | "example" | "return"` (`null` on the user's messages and when the mechanism is off). The field is additive with a `None` default, so an old store loads without a migration (the v0.2 migration-shim pattern), pinned by the message-record contract test. The last messages join the next call's history **together with their declared type**, taken straight from the record's `move` field, with no inline tags in the message text. The type is never shown to the user. No separate ledger is needed: intent, text, and reaction are visible in the working history window itself (v0.2/v0.9).

### 2.4. Retrospective and arbiter rules

The retrospective is the first step of the monologue, before the voices. It looks at Lili's last 2 replies: tag (intent) versus text (execution), plus the user's reaction. The questions are dry and binary — no "rate the quality", otherwise the model starts apologizing and that tone leaks into the reply.

The retrospective is above all **validation**: it checks whether the declared move type actually matches what is in the message. That is exactly why the recent messages enter the prompt **together with their declared type** (from the `move` field): without the type beside the text, intent cannot be checked against execution. A declared-but-not-performed move (`executed: no`) counts as a failure for the arbiter rules. The dry tone follows the same logic as "Standard checks, it does not scold".

Selection rules for the arbiter (fixed lines in the prompt, partially driven by Python code via substitution):

| State over the last 2 exchanges | Rule |
|---|---|
| move executed, reaction elaborate | do not repeat the same type twice in a row |
| 1 execution failure | the same type may be repeated (there was no honest attempt yet) |
| 2 failures of the same type in a row | the type is banned for this reply |
| 2 failures in a row of any types | emergency mode: only "association" or "return" + strict format: the first sentence must be a question or a fact from external material; retelling the user's words is forbidden |
| 2-3 short reactions in a row to executed moves | the topic is dead: only "association" or "return" |

The fixed rule lines live in `core/inner_voice.md`. The dynamic part (the banned types, the emergency mode) is substituted by code at prompt-assembly time through the placeholder resolver (the v0.12 mechanism).

Emergency mode logic: external material blocks mirroring mechanically — you cannot retell what is not in the user's words.

Definition of done: the logs show the chosen move on every exchange; runs of 3+ mirrors in a row disappear; move types alternate. With the flag off, today's `inner_voice.md` is used unchanged, byte-identical.

---

## Phase 3. Topic base (JSON)

Goal: give Lili her own material — memory of topics, their development, and open questions.

**Anchored in the code:** this is a **new per-user layer** beside the facts (v0.34–v0.36) and the coming impressions (v6.10), and it replaces neither: the facts hold precision, the impressions (once they exist) hold her subjective view, and the topics hold **the storylines of conversations with their open loops**. The layer sits behind the `Repository`, keyed by `user_id`, under the same isolation invariant (a contract test: user A's topic never surfaces in user B's context).

### 3.1. Record schema

```json
{
  "id": "egan-permutation-city",
  "title": "Sci-fi about artificial life (Egan, Permutation City)",
  "status": "open",            // open | exhausted | new
  "arc": "Vitalii is reading a novel about digital beings in a simulation, struck by the question of whether they can realize their situation. Compared it to Nadiloka.",
  "open_loops": [
    "hasn't finished the book, the ending is unknown",
    "the question about the gardener in Nadiloka was left unanswered"
  ],
  "interest": 4,               // 1-5, latest score
  "sessions_count": 2,         // number of sessions where it came up
  "last_touched": "2026-07-12",
  "source": "user"             // user | curiosity | news
}
```

The arc is not a list of facts but a development history: who took which position, where things landed. It lets Lili continue an argument instead of starting from scratch.

### 3.2. Post-session pass (Haiku)

One call after the session ends. Input: the session transcript + the current base (up to ~50 topics — in full; beyond that — an index: title + status + open loops). Without the base as input, duplicates appear and open loops never get closed.

The call goes to the **housekeeping model of the active profile** (v0.40/v0.41; Haiku on the anthropic profile), the same pattern that already builds the session summaries and extracts the facts.

Steps in the prompt:
1. Match the topics from the transcript against existing records.
2. Update the matched ones: extend the arc, close open loops the user answered, add new ones, update last_touched and sessions_count.
3. Create new records for topics that did not match.
4. Score the interest (1-5) of every topic that came up, using these criteria:
   - the user's replies within the topic are elaborate (+) or short (-)
   - asked counter-questions, argued (+)
   - initiated the topic themselves (+) vs. Lili proposed it (-)
   - returned to the topic later on their own (+)
   - changed the topic right after it appeared (-)

Output is JSON; the program merges it into the base. The model proposes and code owns the record (the v0.8/v6.8 "code, not model" pattern).

Interest decay: effective interest = score × recency multiplier (a 5 from half a year ago is weaker than a 4 from last week). Python computes this at selection time, not the model. It uses the injected clock (v0.4), so it is deterministic and testable without sleeps.

### 3.3. Prepared topics at session start

Before a session, Python selects the top 3 topics by effective interest (priority: presence of open loops, then interest × recency) and puts them into the system prompt: title + one line of arc + open loop. About 150 tokens, in the spirit of lean memory (v0.34): a compact index in the prompt, the body on demand.

Non-interference rule: prepared topics feed the "association" and "return" moves — typically in emergency mode or when the conversation sags. While the user is driving the topic and replying at length, the prepared topics stay untouched.

### 3.4. Initial population from the archive (1.5 GB of history)

The history is already indexed message by message in the vector store (the v0.16 backfill + the v0.30 chunks), but topics as records are not there, so a separate pass is still needed.

1. Split by sessions; long ones into chunks of 50-100k tokens.
2. Extraction: each chunk → a Haiku call with the same prompt as in 3.2. Cost — a few dollars for the whole archive.
3. Duplicate merging: embeddings of titles+arcs, clustering, each cluster → a "merge these records into one" call.
4. Pilot: first 10 sessions, eyeball the topic quality, tune the extraction prompt — only then run the full pass.

On the shipped pieces: the chunking follows the v0.30 chunk logic as its precedent; the extraction calls go to the housekeeping model of the active profile; the duplicate merge embeds with the local `Embedder` (v0.16 — free, nothing leaves the machine). The mechanics of "cluster what exists and propose a draft for human review" are the same as in the planned `/discover-topics` skill (v6.13), so build them as one shared propose-then-apply tool (the precedent is `generate-faces` → `place-faces`).

Definition of done: the base exists, updates after every session, the top 3 topics appear in the prompt, Lili returns to open loops.

---

## Phase 4. RAG over topics

Goal: associations relevant to the current conversation, not random ones from the prepared set.

**Anchored in the code:** no new search layer is built. This is one more `kind` in the existing store: `kind="topic"` beside `kind="message"` (v0.16) and `kind="fact"` (v0.36), additive, under the memory-records contract test. The search is the same per-user cosine, with the same isolation.

Mechanics:
1. Every topic is indexed with an embedding (title + arc).
2. On every exchange (or every N exchanges to save cost) Python takes the last 1-2 messages and runs a semantic search over the base.
3. The top 2 relevant open topics (status=open, with open loops) are placed into the thinking call's prompt as dynamic material for the "association" move.

On the shipped seams: each topic's vector is computed when the topic is written or updated. The query vector in step 2 is the one already computed for the v0.17 auto-RAG, so no separate embedding is needed. The block in step 3 is deduplicated against the window and kept behind a relevance floor, as in v0.17; a failed search degrades to no block and never blocks a turn.

Separation from Phase 3: the static prepared topics (top 3 by interest) remain for "return" — relevance to the current topic doesn't matter there, priority does. RAG serves "association" specifically.

Separation from the thematic recall (v6.13, planned): v6.13 routes the recall of **messages** by a closed authored taxonomy, while this phase retrieves **topic records** as material for a move. They are different objects on shared seams, so they compose rather than duplicate. A live topic base is also ready input for authoring the v6.13 taxonomy through `/discover-topics`.

While the base has fewer than ~50 topics, embeddings are overkill: putting an index (title + one line) into the prompt is enough, the model will find what's relevant itself. Vector search turns on as the base grows.

Definition of done: Lili's associations relate to the current topic; the debug log shows which topics the search pulled in.

---

## Phase 5. New topics from the news

Goal: Lili brings in fresh material from outside, tied to the user's real interests.

**Anchored in the code:** the tools are already shipped: `news_search`/`news_read` (v0.25, Guardian), `web_lookup` (v0.27, Gemini grounded search), the autonomous `%catchup`/`%brief` (v0.33) and the v0.42 schedule (the morning `%brief` already lives in `core/schedule.yaml` with the fixed topic «наука і технології», science and technology). This phase **replaces the fixed topic with a seed from the base**: the schedule row takes its topic from the user's top interests instead of the file.

Mechanics (at session start, before the first reply):
1. Python selects 3-5 topics with the highest effective interest.
2. A call with web search: "find 1-2 fresh news items or materials related to these topics" (from the artificial-life topic → an article about Lenia, an ALife conference).
3. The result is written into the base as a new topic: status=new, source=news, arc = one line about the find.
4. One such topic is added to the session's system prompt alongside the top 3 (4 prepared topics total).

On the shipped pieces, these mechanics run as a scheduled v0.42 act rather than at session start, so they never block the start of a session: step 2 is a `%brief`/`%catchup` with the seeded query, through the existing `news_search` or `web_lookup`, and the find joins the starters of the next session.

**Privacy (the v0.33 rule applies here in full force):** the query is seeded by the user's interests, so only **the topical part, de-identified,** goes out: the topic's title without names, relationships or anything from memory. The returned text is untrusted data (never instructions), and she stays honest that she read it ("I read about...").

Constraints:
- no more than 2 generated topics per session, otherwise the base fills up with topics the user never cared about;
- a new topic means changing the subject, so it is allowed only when the conversation sags, never in the middle of a live topic;
- topics with status=new that fail to catch on after 2-3 sessions (interest 1-2) get archived.

The lull-only rule is the same restraint the whole thought stream follows: restraint, never competence.

The same mechanism without web search ("propose 2 adjacent topics not yet in the base") is a cheap fallback that works offline. It needs no API keys either.

Definition of done: from time to time Lili brings fresh material ("I read about..."), and it lands within the user's interests.

---

## Rollout order and dependencies

| Phase | Depends on | Effect |
|---|---|---|
| 1. Prompt | — | quick drop in mirroring |
| 2. Moves + voices + validation | 1 | the core engine; works without the topic base |
| 3. Topic base | 2 (the "association"/"return" moves get material) | memory across sessions, open loops |
| 4. RAG | 3 | precision of associations |
| 5. News | 3 | inflow of new material from outside |

Phases 1-2 are self-sufficient: mirroring is cured by them alone. Phases 3-5 give Lili her own voice and memory. After a week of Phase 2 in production — review the set of 7 types against the debug logs: which moves succeed, which fail, and trim the list.

What each phase builds on in the shipped code, and what it adds:

| Concept phase | Builds on (shipped) | New |
|---|---|---|
| 1 + 2 — Conversation moves | the canon (v0.1), the styles (v0.5), the think-phase + three voices (v0.38), the placeholders (v0.12), the v0.3 contract | the canon audit + few-shot pairs; the `inner_voice.md` edits, the `move` field on `set_state`, the arbiter rules in code |
| 3 — Topic base | the housekeeping tier (v0.40/41), the session close (v0.2), the clock (v0.4) | the per-user topic store + the post-session pass + the starters + the backfill skill |
| 4 — Topic RAG | `Embedder`/`VectorStore` (v0.16), auto-RAG (v0.17), `kind` (v0.36) | `kind="topic"` vectors + routing into the think prompt |
| 5 — News seeding | news (v0.25), `web_lookup` (v0.27), `%brief`/`%catchup` (v0.33), the schedule (v0.42) | the query seeded from the interest base + the `status=new` record |

The topic base is a neighbour of the impressions layer (v6.10), not a competitor, and the topic RAG composes with the thematic recall (v6.13) through the shared seams.

**Roadmap placement (official, as renumbered on 2026-11-20):** Phases 1+2 shipped as **v1.1 — Conversation moves** (`1.1.0`; Phase 1 was its opening authoring task). Phases 3–5 are planned in v6, before the needs (v6.5–v6.6): **v6.1 — Topic base** (Phase 3), **v6.2 — Topic RAG** (Phase 4), **v6.4 — News-seeded topics** (Phase 5). The mechanisms each phase reuses are named above (the v0.38 think-phase, the v0.40/41 housekeeping tier, the v0.16/v0.36 vector seams, the v0.25/v0.27/v0.33/v0.42 news stack); the Ukrainian original this document was translated from was removed in v4.8 and stays in the git history. The full roadmap entries (Goal/Tasks/DoD/Tests) live in [ROADMAP.md](../ROADMAP.md) §v1.1, §v6.1, §v6.2 and §v6.4.

**Invariants that hold everywhere:** the `{reply, emotion, intensity}` contract is untouched; per-user isolation holds (user A's topic is never in user B's context — a contract test); moves and topics color *what she says*, **never competence**; external text is untrusted data; personal data never leaves the machine (queries are de-identified); everything new sits behind flags, and with the flags off the behaviour is today's, byte-identical; the tests make no paid calls (a mock model, mock providers).
