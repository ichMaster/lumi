# Lili: Conversation Development System

Concept of changes. Goal: eliminate mirroring (retelling the user's words, especially metaphorically) and give Lili a mechanism for actively driving the conversation — her own moves, topic memory, returning to open questions.

Root cause: when the model has nothing to add, it picks the safest pattern — reflective listening. The solution has two parts: (a) every reply must execute a concrete conversation move, (b) Lili has her own material for moves — a topic base with the development history of each topic.

---

## Phase 1. Prompt changes

Goal: remove the sources of mirroring that can be fixed without code.

System prompt changes:

1. Positive replacement rule. Instead of the prohibition "do not use metaphors" — a requirement: "Never retell or paraphrase what the user said. Every reply must add something new: a question, your own position, disagreement, a fact, or a memory."
2. Character sheet audit. Remove or rephrase traits like "poetic", "figurative", "deep" — the model signals them in the cheapest way possible: with metaphors. Replace with behavioral traits: "asks concrete questions", "has her own preferences and defends them".
3. Few-shot contrast. Two or three example pairs "bad reply (mirror) / good reply (move)" directly in the prompt. For small models this is the most effective lever.

Definition of done: in a 20-exchange test session, the share of retelling replies drops noticeably (eyeball evaluation).

---

## Phase 2. Seven move types, three voices, validation

Goal: every reply is a deliberate conversation move, chosen before generation, with execution checked on the next exchange.

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

The first four work inside the current topic, "association" and "return" bring material from outside, "example" grounds the discussion.

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

### 2.3. Tagging the history

Every Lili reply is stored in the chat history with a type tag: `[move: deepen] Do they run into anything in the book...`. The tag is hidden from the user but included in the prompt of the next call. User messages carry no tags. No separate ledger is needed: intent, text, and reaction are all visible in the history itself (last 20 messages).

### 2.4. Retrospective and arbiter rules

The retrospective is the first step of the monologue, before the voices. It looks at Lili's last 2 replies: tag (intent) versus text (execution), plus the user's reaction. The questions are dry and binary — no "rate the quality", otherwise the model starts apologizing and that tone leaks into the reply.

Selection rules for the arbiter (fixed lines in the prompt, partially driven by Python code via substitution):

| State over the last 2 exchanges | Rule |
|---|---|
| move executed, reaction elaborate | do not repeat the same type twice in a row |
| 1 execution failure | the same type may be repeated (there was no honest attempt yet) |
| 2 failures of the same type in a row | the type is banned for this reply |
| 2 failures in a row of any types | emergency mode: only "association" or "return" + strict format: the first sentence must be a question or a fact from external material; retelling the user's words is forbidden |
| 2-3 short reactions in a row to executed moves | the topic is dead: only "association" or "return" |

Emergency mode logic: external material blocks mirroring mechanically — you cannot retell what is not in the user's words.

Definition of done: the logs show the chosen move on every exchange; runs of 3+ mirrors in a row disappear; move types alternate.

---

## Phase 3. Topic base (JSON)

Goal: give Lili her own material — memory of topics, their development, and open questions.

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

Output is JSON; the program merges it into the base.

Interest decay: effective interest = score × recency multiplier (a 5 from half a year ago is weaker than a 4 from last week). Python computes this at selection time, not the model.

### 3.3. Prepared topics at session start

Before a session, Python selects the top 3 topics by effective interest (priority: presence of open loops, then interest × recency) and puts them into the system prompt: title + one line of arc + open loop. About 150 tokens.

Non-interference rule: prepared topics feed the "association" and "return" moves — typically in emergency mode or when the conversation sags. While the user is driving the topic and replying at length, the prepared topics stay untouched.

### 3.4. Initial population from the archive (1.5 GB of history)

1. Split by sessions; long ones into chunks of 50-100k tokens.
2. Extraction: each chunk → a Haiku call with the same prompt as in 3.2. Cost — a few dollars for the whole archive.
3. Duplicate merging: embeddings of titles+arcs, clustering, each cluster → a "merge these records into one" call.
4. Pilot: first 10 sessions, eyeball the topic quality, tune the extraction prompt — only then run the full pass.

Definition of done: the base exists, updates after every session, the top 3 topics appear in the prompt, Lili returns to open loops.

---

## Phase 4. RAG over topics

Goal: associations relevant to the current conversation, not random ones from the prepared set.

Mechanics:
1. Every topic is indexed with an embedding (title + arc).
2. On every exchange (or every N exchanges to save cost) Python takes the last 1-2 messages and runs a semantic search over the base.
3. The top 2 relevant open topics (status=open, with open loops) are placed into the thinking call's prompt as dynamic material for the "association" move.

Separation from Phase 3: the static prepared topics (top 3 by interest) remain for "return" — relevance to the current topic doesn't matter there, priority does. RAG serves "association" specifically.

While the base has fewer than ~50 topics, embeddings are overkill: putting an index (title + one line) into the prompt is enough, the model will find what's relevant itself. Vector search turns on as the base grows.

Definition of done: Lili's associations relate to the current topic; the debug log shows which topics the search pulled in.

---

## Phase 5. New topics from the news

Goal: Lili brings in fresh material from outside, tied to the user's real interests.

Mechanics (at session start, before the first reply):
1. Python selects 3-5 topics with the highest effective interest.
2. A call with web search: "find 1-2 fresh news items or materials related to these topics" (from the artificial-life topic → an article about Lenia, an ALife conference).
3. The result is written into the base as a new topic: status=new, source=news, arc = one line about the find.
4. One such topic is added to the session's system prompt alongside the top 3 (4 prepared topics total).

Constraints:
- no more than 2 generated topics per session, otherwise the base fills up with topics the user never cared about;
- a new topic means changing the subject, so it is allowed only when the conversation sags, never in the middle of a live topic;
- topics with status=new that fail to catch on after 2-3 sessions (interest 1-2) get archived.

The same mechanism without web search ("propose 2 adjacent topics not yet in the base") is a cheap fallback that works offline.

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

**Roadmap placement (official):** this concept opens v1 as roadmap phases **v1.1–v2.1** (before the needs, which moved to v2.2–v2.3): **v1.1 — Conversation moves** (Phases 1+2; Phase 1 is its opening authoring task), **v1.7 — Topic base** (Phase 3), **v1.8 — Topic RAG** (Phase 4), **v2.1 — News-seeded topics** (Phase 5). The reality-anchored version of this concept (which shipped mechanisms each phase reuses — the v0.38 think-phase, the v0.40/41 housekeeping tier, the v0.16/v0.36 vector seams, the v0.25/v0.27/v0.33/v0.42 news stack) is the UA original: [ukrainian/lili-conversation-system-concept.md](ukrainian/lili-conversation-system-concept.md); the full roadmap entries (Goal/Tasks/DoD/Tests) live in [ROADMAP.md](../ROADMAP.md) §v1.1–v2.1.
