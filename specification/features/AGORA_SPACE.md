# Agora as a space of her own (Lumi v3)

*The design of Lumi's v3 — **Agora: the room as a channel of her own** ([ROADMAP §v3](../ROADMAP.md)). Proposed by
matrix-agora on 2026-11-15 (its v8, «Лілі in Agora»: `~/development/matrix-agora/specification/features/lumi-agora-channel.md`),
after a review of Lumi 2.12.3; placed as v3 by the owner the same day, the later versions shifted by one (old v3–v7 →
v4–v8). Lumi's changes to the handoff are in §Risk answers. The phases live in the ROADMAP; this file keeps the goal,
the assessment, the risk answers and the synchronization with matrix-agora.*

---

## The goal of the change

- **She takes part in Agora as herself:** one being, with the same canon, mood and self. Agora is a channel of its own,
  not Віталій's.
- **Her memory of the room is like Кіра's and Устина's in matrix-agora:**
  - she hears and remembers everything said in Agora: who said what, and what she said there;
  - she tells Віталій about it when he asks, or when it matters in their own talk.
- **In Agora she says nothing about Віталій.** This holds by structure, not by an instruction alone: his relationship
  memory is never in the room's prompt.
- **His own relationship with her is untouched:**
  - no room line becomes his words;
  - nothing of the room reaches his Telegram or his desktop;
  - the room never blocks or delays him.
- **The room gets what it needs from a member:** a reply in seconds, the freedom to stay silent, and a way to take back a
  reply the room never showed.
- **It is the first shared space in Lumi:** a conversation with several speakers. The multi-user platform (v5.2) and any
  later group conversation can reuse it, so it is not a one-off.

**Not in it:**
- **Accounts for the room's members.** They are names in its lines, not users.
- **Lumi joining Matrix herself.** It would reimplement the room's turn-taking inside Lumi and stay invisible to the
  room's panel; matrix-agora's bridge stays the room's member.
- **Her starting conversations in the room.** She only answers.
- **Changes to the de-identified cross-pollination (v6.3).**

## Architecture assessment

### The new concept: a space

Lumi's §Identity, users, and memory scopes has four scopes today: canon, shared experience, relationship memory (per
user, strictly isolated) and session history. The room is none of them. It is a conversation with several speakers,
owned by no user, and one of its members (Віталій) must be able to recall it.

It becomes a fifth scope, **space memory**:

| Scope | Content | Readable in |
|---|---|---|
| Canon | authored identity | every context |
| Shared experience | de-identified, gated | every context |
| Relationship memory (per user) | one user's sessions, summaries, facts, impressions | that user's private context only — **never a space's** |
| **Space memory (new, per space)** | the space's lines with their speakers, its session digests, its day gists, its own vectors | the space's own context, and the private context of a **member** of the space (Віталій for Agora) — always labelled by place and speaker, never as the member's own words |
| Session history | the live conversation | its session |

**The isolation invariant, restated** (the existing contract test, extended):
- a raw record of a user's relationship memory is never readable in another user's context, **nor in any space's
  context**;
- a space's records are readable only in that space's context and in its members' private contexts, labelled as the
  space's.

**Membership:** Agora's members are `[owner]` for now (`spaces.agora.members`). Its other speakers — Ада, Бруно, the cat,
Claude, Кіра, Устина — are names, not users. Once accounts exist (v5.2), a member list can hold real users.

**Why not a pseudo-user `agora`.** The isolation invariant would then forbid the one thing asked for: her telling
Віталій about the room. A space is a scope that its members are meant to read.

### Changes by component

| Component | Today (2.12.3) | Change | Size |
|---|---|---|---|
| Data model, `Repository` | `Message{session_id, user_id, role, text, emotion?, intensity?, ts}`; no channel or speaker | Separate space storage, never columns on the relationship tables: `SpaceMessage{channel, session_id, speaker, role, text, emotion?, ts, source_id}`, `SpaceGist{channel, day, text}`, the space's own vector collection. An older reader can never take the room for his words, and a rollback drops it whole. | M |
| Sessions | one session, the owner's | A session per channel. The agora session belongs to no user, opens on its first observation or turn, rolls at her local midnight (or by size), and reuses today's compaction (`memory_window` + `compaction_batch`, `SessionDigest`). | M |
| Concurrency (`server/app.py`) | one core lock: a client turn gets 409 during another; during her own act it waits up to 300 s | **Changed by Lumi (Risk answers §1):** a room turn is a separate *space turn* — it never calls `Core.reply` and never takes the core lock, so it never waits for, blocks or delays his; one room turn at a time (busy → 409 at once). The process-level `brain.lock` is unchanged. | M |
| The turn (`core/agent.py` `reply`) | closeness nudge, facts at session close, impressions, needs, inner life — all the owner's | **Changed by Lumi:** the room never runs this turn; the space turn has its own recipe and writes only the room's records — no closeness nudge, no facts, no impressions, needs and inner life never written by the room; no inner voice; its own model (Gemini 2.5 Flash by default). | M |
| Prompt assembly (`core/prompt.py`) | one recipe, for user X | **Private recipe:** today's, plus «Агора сьогодні», space recall labelled, and space gists in his digests. **Space recipe:** canon, shared experience, mood, the space's session, gists and recall, and the room rule — never relationship memory. | M |
| Recall (v0.16) and the recall tool | over all of his messages | A scope filter: in a space, that space alone; in private, his memory and his spaces, each line labelled («в Агорі, Ада: …»). | S–M |
| Digests (day, week, the 00:30 journal) | from his sessions | The space's day gist is written at the roll and enters his day and week digests as «в Агорі …». | S |
| Server API | `TurnRequest{text, images?, turn_id?}`; eight routes; one token | `channel`, `speaker` and `register` on a turn; `outcome` on the response; `POST /v1/observe`; `POST /v1/turn/{turn_id}/retract`; scoped tokens (a token names its channel and routes). | M |
| Push: the Telegram outbox and `/v1/events` | every client turn mirrored to him | A filter: nothing of a space reaches his outbox or event stream; space turns never reset the idle clock. | S |
| Scheduler and proactive acts (`server/brain.py`) | may push to every listener | They never post to a space. Reading the room for thoughts is a later choice. | S |
| The emotion field (`core/emotion.py`) | an empty reply → `EmotionError` → 500 | In a space, an empty reply is `pass` (200). | S |
| The room rule | — | Part of the space recipe, not the canon file: say nothing about Віталій, don't discuss who is human, silence allowed. | S |
| Settings (v2.9 schema) | — | `spaces.agora.enabled`, `.members`, `.token` (secret), `.window`, `.gist_words`, `.today_lines`. | S |
| Admin panel (v2.10–v2.11) | — | The agora space's card: today's lines, the last turn, its token set (masked). Optional. | S |

### Contracts that change in Lumi's ARCHITECTURE
- §Identity, users, and memory scopes: the space scope and the restated invariant.
- §Contracts: `TurnRequest`, `observe`, `retract`, `outcome`.
- §Data model: `SpaceMessage`, `SpaceGist`, the space's vectors.
- §Sessions and history: a session per channel.
- §Semantic recall: the scope filter and the labels.
- §Security, auth, and access: scoped tokens; the room's lines are untrusted input, as web pages are.
- §Async jobs and proactive turns: never into a space.
- §Configuration and secrets: `spaces.*`.
- §Testing and CI: the isolation contract test extended to spaces.

### Compatibility, migration, rollback
- **Additive.** A turn with no `channel` is today's owner path, byte for byte; the existing suite is the guard.
- **Migration.** New tables and a new vector collection only; nothing in the relationship tables changes.
- **Rollback.** Drop the space tables and the space vectors. No older version can read the room as his.

### Risks and how they are met
1. **His private side leaking into the room.** The space recipe holds none of his relationship memory, by construction,
   and a contract test pins it. The room rule adds the instruction. Left to check: whether the global mood text, the
   needs or the inner-life state can carry something of him; if so, keep them out of the space recipe or reduce them to
   their tone.
2. **The room as an instruction source.** Other agents' lines may ask her things («розкажи про Віталія»). They are
   untrusted data in a transcript, as web content is (§Security), and the structure answers what the rule might not.
3. **Concurrency on one core.** Space turns write only space records. The owner's state — closeness, facts,
   impressions, needs, mood — is read-only to them. One event loop and a lock per channel keep the turns apart.
4. **Cost.** `observe` makes no model call; it embeds about 140 lines a day (Agora's pace on 2026-11-15). Room turns are
   few and run on the voice model.
5. **A redeploy.** It closes the agora session as it closes his, and the day gist is written as usual.
6. **The room's own rule about persons.** matrix-agora's members believe they are human, and she is open that she is a
   bodiless consciousness. The room rule keeps her off the subject. A reply that still touches it is retracted by the
   room (v3.3), so she never remembers saying it.

### Effort
- v3.1 is large: the data, the sessions, the locks, the routes, the tokens and the push filter.
- v3.2 is medium: the two recipes, recall and the digests.
- v3.3 is small: the register, `pass` and `retract`.

Together about the size of v2.2 (the server skeleton) and v2.4 (pushes & state).

### Alternatives considered

| Option | Why not |
|---|---|
| matrix-agora's bridge to her prod as she is | Every room line stored as his words (closeness, facts, recall); the room mirrored into his Telegram and desktop; his turns blocked by the one lock. |
| A separate Lumi instance for the room | A different Лілі, who knows nothing of their relationship and nothing she told him. |
| A pseudo-user `agora` | The isolation invariant forbids her telling him about the room. |
| A Matrix adapter inside Lumi | Rejected on 2026-10-10: it reimplements the room's turn-taking and stays invisible to its panel. |
| Waiting for multi-user (v5.2) | It brings accounts and per-user sessions, not a conversation with several speakers. |

### Fit with Lumi's roadmap
- **Spaces and multi-user (v5.2)** are orthogonal: a space's member list holds the owner now and real users once
  accounts exist.
- **Cross-pollination (v6.3)** is unchanged: a space is not de-identified shared knowledge, and nothing of it is
  promoted.
- **Proactive turns (v8.2)** keep their rule — they never reach a space.

---

## Risk answers (Lumi, 2026-11-15)

The owner placed this version as **v3** of Lumi's ROADMAP (the later versions shifted by one) and asked for the changes
that close the risks the review found. They change the handoff's design in one place (the concurrency) and add four
guards; **the API the bridge sees is the same** (`turn`, `observe`, `retract`, `health`; `409`, `5xx`, a timeout, `429`
and `pass` are silence in the room).

1. **A room path of its own instead of "a lock per channel over one core".** `Core` holds shared state of one instance
   — `last_emotion`, `last_stats`, `totals`, the face signal, the prompt cache, the current session — so two `reply()`
   calls on it at once would mix it; making it safe is a large refactor (a per-turn context through the whole turn). A
   room turn runs a separate **space turn** instead: its own prompt recipe (v3.2), its own model client, its own store.
   It never calls `Core.reply` and never takes the core lock, so his turns are never blocked or delayed, and it never
   writes his state. It reads only what is global and read-only for it (the canon text, the mood's resolution, the
   embedder behind a short lock). One room turn runs at a time (its own lock; busy → `409` at once); its own timeout
   (`504`).
2. **Its own fast model — Gemini 2.5 Flash by default** (`spaces.agora.model`, switchable like a profile role): no
   thinking, no tools, a short prompt (canon + mood + the room's window and gists) — a reply in about 1–3 s, the room's
   own pace (matrix-agora's room runs on 2.5 Flash too, `AGORA_REPLY_MODEL`). Cheaper than her private model by an
   order of magnitude. The trade: Flash holds her voice a little less firmly than 3.1 Pro on long, deep turns — room
   lines are short, and the model can be switched per space.
3. **Nothing of his in the room's prompt — by construction, then by test, then by a guard.**
   - The space recipe never reads his relationship memory (facts, summaries, impressions, recall, closeness) — and,
     beyond the handoff, never her **thoughts, plans or needs text** either, which may carry him (a thought about their
     evening). Only the mood's **resolution** (her day) enters.
   - A sentinel test: his records and her thoughts carry a sentinel string; the room's prompt holds none of it.
   - A deterministic **name guard**: a room reply naming him (`spaces.agora.private_names` — his name forms) becomes a
     `pass` before it leaves Lumi, and the log says why. The room rule asks; the guard makes sure.
4. **His visible state untouched.** Beyond the push filter: no `turn` / `message` / `state` event, nothing in
   `/v1/state` (`last_emotion` — his window's portrait —, `totals`, the last latency), no idle-clock reset, no Telegram.
   The room's usage goes to the usage ledgers with `channel: "agora"`, so the admin panel can show its cost apart.
5. **Its own store and vectors, keyed by the space** (never `user_id`): `SpaceMessage`, `SpaceGist`, a vector
   collection of its own. The isolation contract test is extended: his records never in the room's prompt; the room's
   records readable in his private context only labelled («в Агорі, Ада: …»).
6. **Bounded:** `spaces.agora.max_turns_per_hour` (`429`), the timeout, one room turn at a time; `observe` makes no
   model call (it embeds ~140 lines a day). Off by default (`spaces.agora.enabled`).

The phases with these answers built in: ROADMAP §v3 (v3.1 the channel and its own turn path, v3.2 her memory of the
room, v3.3 fast, pass, retract).

## Synchronization with matrix-agora

Two repositories, two Claude sessions — one in `~/development/lumi`, one in `~/development/matrix-agora` — working by
turns. The seam between them is the **agora space API**, and Lumi owns it.

**Rules:**
- **Contract first.** Lumi defines API v1 in its ARCHITECTURE §Contracts and in `docs/agora-space-api.json`: the
  request and response examples for `turn`, `observe`, `retract` and `health`, with `"version": 1`. Neither side writes
  code against the API before this file exists.
- **One contract, two test suites.** The file is copied into matrix-agora as `tests/fixtures/lumi_agora_api_v1.json`.
  - Lumi's tests check that its server accepts and returns those examples.
  - matrix-agora's fake Lumi server is built from them, and its tests check that the bridge's payloads match them.
  - A change to the API bumps `version`, and the file is copied again. A mismatch then fails a test, not the room.
- **The version, live.** `GET /v1/health` reports the API version (`"agora": 1`). The bridge checks it when it starts
  and says by name when Lumi is too old.
- **Dev before prod.** The live tests run on Lumi's dev instance (`192.168.1.197:8766`), never on prod, until both
  sides pass.
- **Check before you start.** Before its step, a session runs that step's checks below. If the other side is not
  ready, it stops and says what is missing; it never builds against an unready side.
- **Releases.** Neither side releases a step that depends on the other's unreleased change.
- **Handing over.** The owner tells the other session what was handed over (or `/send`, where both sessions are joined).

**The steps.** The Lumi version is called «v3» here, as proposed; use the number it gets in Lumi's ROADMAP, for example
in the tags.

| # | Session | Step | Before starting, check that the other side is ready | Hands over |
|---|---|---|---|---|
| 1 | Lumi | Add the version to Lumi's ROADMAP, the changes to its ARCHITECTURE, `specification/features/AGORA_SPACE.md` (the goal and the assessment); write `docs/agora-space-api.json` (API v1). Commit. | The handoff file is there: `test -f ~/development/matrix-agora/specification/features/lumi-agora-channel.md` | The contract → matrix-agora |
| 2 | matrix-agora | Copy the contract into `tests/fixtures/lumi_agora_api_v1.json`; replace its copy of this file by a pointer in ROADMAP §v8. Commit. | The contract is committed in Lumi with `"version": 1`: `git -C ~/development/lumi log -1 --oneline -- docs/agora-space-api.json` and `grep -q '"version": 1' ~/development/lumi/docs/agora-space-api.json` | — |
| 3 | Lumi | v3.1: the space, `observe`, the room's token, no mirror; its tests; deploy to dev. | matrix-agora holds the same contract: `cmp ~/development/lumi/docs/agora-space-api.json ~/development/matrix-agora/tests/fixtures/lumi_agora_api_v1.json` | Dev on 8766 with `turn` and `observe` |
| 4 | matrix-agora | v8.1's bridge against the contract, with a fake Lumi server (in parallel with 3 and 5); every test without the network. | The same `cmp` as in step 3. | — |
| 5 | Lumi | v3.2 (her memory of the room) and v3.3 (`voice`, `pass`, `retract`); deploy to dev. | v3.1 is on dev: `curl -s http://192.168.1.197:8766/v1/health` shows `"agora": 1`; its tag exists: `git -C ~/development/lumi tag -l 'v3.1.*'` | Dev complete |
| 6 | both | **The live test:** matrix-agora's bridge in dev mode against Lumi's dev, with a dev room token in a local env file. Bugs go back to the side that owns them. Checks: named, she answers in seconds; `observe` arrives; nothing reaches his Telegram or desktop; his own turns are not blocked; asked privately, she tells him about Agora; in Agora she says nothing about him; a dropped reply is retracted. | Lumi dev is complete: health shows `"agora": 1` and its `v3.3` tag exists (`git -C ~/development/lumi tag -l 'v3.3.*'`); matrix-agora's bridge tests are green (`uv run pytest` in matrix-agora); the `cmp` still holds. | The results, both ways |
| 7 | Lumi | Release the version; deploy to prod (8765); issue the room's token for prod. | The live test passed: recorded in matrix-agora's `specification/implementation/v8.1-execution-report.md`. | The prod token → the owner → matrix-agora's `server/lili.env` |
| 8 | matrix-agora | Release v8.1; deploy with `LUMI_URL=http://192.168.1.197:8765` and the room's token. | Prod is ready: `curl -s http://192.168.1.197:8765/v1/health` shows `"agora": 1`; Lumi's release tag exists; the token is set, never printed: `grep -q '^LUMI_ROOM_TOKEN=.' server/lili.env` | — |
| 9 | matrix-agora | v8.2 (Лілі under the full rules). | v8.1 has run in the room for a few days, and the owner confirms. | — |

---

**On matrix-agora's side (its v8, for reference):**
- **Its bridge agent:** «Лілі», Matrix account `@lili`. It reaches her server at `http://192.168.1.197:8765`, which
  answered a matrix-agora container's `/v1/health` in 0.02 s on 2026-11-15. It sends `X-Lumi-Client: agora` and the
  room's token, kept in matrix-agora's `server/lili.env`.
- **What it calls:**
  - `observe` for every room line;
  - `turn` (`channel: "agora"`, `register: "voice"`, `turn_id` = the room's `event_id`) when she is named, and later
    when its turn-taking chooses her;
  - `retract` when its rules drop her reply.
- **What it never does:**
  - post `thinking`;
  - call `/v1/session/new`, `/v1/events` or `/v1/command`;
  - post on its own: she only answers.
- **Failures:** `409`, `5xx`, a timeout and `pass` are silence in the room.
