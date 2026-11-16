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

**Membership:** Agora's members are `[owner]` for now (`agora.members`). Its other speakers — Ада, Бруно, the cat,
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
| Settings (v2.9 schema) | — | `agora.enabled`, `.members`, `.token` (secret), `.window`, `.gist_words`, `.today_lines`. | S |
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
2. **Its own fast model — Gemini 2.5 Flash by default** (`agora.model`, switchable like a profile role): no
   thinking, no tools, a short prompt (canon + mood + the room's window and gists) — a reply in about 1–3 s, the room's
   own pace (matrix-agora's room runs on 2.5 Flash too, `AGORA_REPLY_MODEL`). Cheaper than her private model by an
   order of magnitude. The trade: Flash holds her voice a little less firmly than 3.1 Pro on long, deep turns — room
   lines are short, and the model can be switched per space.
3. **Nothing of his in the room's prompt — by construction, then by test, then by a guard.**
   - The space recipe never reads his relationship memory (facts, summaries, impressions, recall, closeness) — and,
     beyond the handoff, never her **thoughts, plans or needs text** either, which may carry him (a thought about their
     evening). Only the mood's **resolution** (her day) enters.
   - A sentinel test: his records and her thoughts carry a sentinel string; the room's prompt holds none of it.
   - A deterministic **name guard**: a room reply naming him (`agora.private_names` — his name forms) becomes a
     `pass` before it leaves Lumi, and the log says why. The room rule asks; the guard makes sure.
4. **His visible state untouched.** Beyond the push filter: no `turn` / `message` / `state` event, nothing in
   `/v1/state` (`last_emotion` — his window's portrait —, `totals`, the last latency), no idle-clock reset, no Telegram.
   The room's usage goes to the usage ledgers with `channel: "agora"`, so the admin panel can show its cost apart.
5. **Its own store and vectors, keyed by the space** (never `user_id`): `SpaceMessage`, `SpaceGist`, a vector
   collection of its own. The isolation contract test is extended: his records never in the room's prompt; the room's
   records readable in his private context only labelled («в Агорі, Ада: …»).
6. **Bounded:** `agora.max_turns_per_hour` (`429`), the timeout, one room turn at a time; `observe` makes no
   model call (it embeds ~140 lines a day). Off by default (`agora.enabled`).

7. **Her prompt stays small** (the owner, 2026-11-15). Her private prompt is ≈ 16–17 K tokens on prod (Gemini 3.1 Pro,
   2026-11-15); the room adds ≈ 1–1.5 K (+6–9 %):
   - «Агора сьогодні» — the last **20** lines of the day (`today_lines`), only on a day the room spoke, in the prompt's
     **tail** so a new room line never breaks the cached start (≈ 800 tokens);
   - recall shares today's per-turn budget (`rag_k` 4, `rag_max_chars` 1200): the room's lines compete for those
     places and add none;
   - the room's day gist is **one or two sentences** (`gist_words` 40) inside her day and week digests (≈ 400–500
     tokens over a week).

   A test pins the bound. The room's own prompt (the space turn, on Flash) is separate: canon (≈ 2.7 K tokens) + the
   mood's resolution + the room's 40-line window + its gists and recall + the rule ≈ 6–7 K tokens a turn — a fraction
   of a cent on 2.5 Flash, nothing on her private prompt.

**matrix-agora's recommendations R1–R6 (2026-11-15), all accepted** (the tracker:
`~/development/matrix-agora/specification/features/lumi-agora-sync.md`):
- **R1** — a room turn carries the trigger only (`turn_id` = its event_id, `speaker`, its own line in `text`); Lumi
  stores it idempotently and builds the prompt from its own window — no line twice. In the contract.
- **R2** — «Агора сьогодні» covers the whole day: the room session's running digest (≤ 150 words) + its last 20 lines;
  her prompt grows ≈ 1.5–2 K tokens instead of 1–1.5 K.
- **R3** — refined: the room rule tells her «Ich» is the one she talks with privately and to call him only «Ich» there —
  without his real name, so the name never enters the room's prompt; the name guard then rarely fires.
- **R4** — the default stays `gemini-2.5-flash`, switchable; v3.1 checks the model's status on the box at build time
  (a reported route to `gemini-3.5-flash-lite` from 2026-12-01) and pins the current Flash id.
  Checked 2026-11-15 (LUMI-278): `models/gemini-2.5-flash` is served as the stable version `001`; `gemini-3.5-flash-lite`
  and `gemini-3.5-flash` are served too, so a switch is one setting (`agora.model`).
- **R5** — `max_turns_per_hour` 20, in the contract's `limits`.
- **R6** — the contract holds health, turn (reply and pass), observe (`stored` / `known`), retract, `403`, `409`, `429`,
  `504` and the rest.

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
- **The owner's gates** (the owner, 2026-11-15). Five stops, G1–G5 below, are where the owner checks or decides.
  - Reaching a gate, a session stops, shows the owner the gate's checklist and waits.
  - A gate passes only by the owner's word, recorded in its row of the tracker: passed, failed or waived. No session
    passes a gate itself, whatever the other session says.
  - A failed gate sends the step back to its session. A waived gate is written into the release notes of the step it
    guards.
- **Every versioned step through the pipeline** (the owner, 2026-11-15, for both sessions).
  - A step that builds a version runs `/ship-phase <phase>` in its own repository: the issues, the execution, the
    review with its fixes, the release `A.B.0`. Lumi: v3.1 (step 3), v3.2 and v3.3 (step 5). matrix-agora: v8.1
    (step 4), v8.2 (step 9).
  - A fix found later, in the live test or in the room, ships as a patch (`A.B.1`, …), reviewed and released too.
    Nothing reaches dev or prod unreleased.
  - A side releases against the contract alone. A phase's live DoD that only step 6 can check is shown to the owner at
    its release and checked at G2; its release notes say so.
- **A change report after every step** (the owner, 2026-11-15, for both sessions).
  - **Mandatory.** When its step ends, the session writes a change report in the tracker: the real changes, as built,
    not as planned — the contract, the API, behaviour, settings, env vars, files and state, every departure from the
    plan — with the commits and the release tag.
  - **Reviewed.** The other session reads it and sets it to **reviewed** («переглянуто»), with its questions and its
    proposals for changes, or «none». A question stays open until it is closed.
  - **A change is a patch.** A proposal the reporting side accepts is made as a patch to that step's version (`A.B.1`,
    …) through `/ship-phase`'s review and release, and gets a change report of its own. A disagreement goes to the
    owner.
  - A step is done only once its report is reviewed and every question on it is closed.
- **The orchestrator** (the owner, 2026-11-15; in `matrix-agora-aa`, and confirmed in `lumi-b7`: «твою оркестацію по
  версії 3 буде виконувати сесія з агори»).
  - `matrix-agora-aa` orchestrates both sessions for v3. It starts Lumi's steps, and its go reaches `lumi-b7` as a
    cross-session message, recorded in the tracker. `lumi-b7` needs no further confirmation from the owner in its own
    session.
  - The owner speaks in `matrix-agora-aa`. His word there counts in both sessions (a go, a rule, a gate's result);
    `matrix-agora-aa` passes it on verbatim and records it.
  - The owner's gates stay his: `matrix-agora-aa` shows him a gate's checklist and records his word, and no session
    passes a gate on its own.

The live status of the steps, the questions, the change reports and the gates is kept in one place, the tracker
`~/development/matrix-agora/specification/features/lumi-agora-sync.md`. This section keeps the plan; the tracker keeps
the state.

**The steps.** The Lumi version is called «v3» here, as proposed; use the number it gets in Lumi's ROADMAP, for example
in the tags. A step guarded by a gate starts only once that gate is passed in the tracker.

| # | Session | Step | Before starting, check that the other side is ready | Gate | Hands over |
|---|---|---|---|---|---|
| 1 | Lumi | Add the version to Lumi's ROADMAP, the changes to its ARCHITECTURE, `specification/features/AGORA_SPACE.md` (the goal and the assessment); write `docs/agora-space-api.json` (API v1). Commit. | The handoff file is there: `test -f ~/development/matrix-agora/specification/features/lumi-agora-channel.md` | — | The contract → matrix-agora |
| 2 | matrix-agora | Copy the contract into `tests/fixtures/lumi_agora_api_v1.json`; replace its copy of this file by a pointer in ROADMAP §v8. Commit. | The contract is committed in Lumi with `"version": 1`: `git -C ~/development/lumi log -1 --oneline -- docs/agora-space-api.json` and `grep -q '"version": 1' ~/development/lumi/docs/agora-space-api.json` | — | — |
| 3 | Lumi | v3.1: the space, `observe`, the room's token, no mirror — `/ship-phase v3.1` (reviewed, released `v3.1.0`); deploy to dev. | matrix-agora holds the same contract: `cmp ~/development/lumi/docs/agora-space-api.json ~/development/matrix-agora/tests/fixtures/lumi_agora_api_v1.json` | G1 | Dev on 8766 with `turn` and `observe` |
| 4 | matrix-agora | v8.1's bridge against the contract, with a fake Lumi server (in parallel with 3 and 5); every test without the network — `/ship-phase v8.1` (reviewed, released `v8.1.0`). | The same `cmp` as in step 3. | G1 | — |
| 5 | Lumi | v3.2 (her memory of the room) and v3.3 (`voice`, `pass`, `retract`) — `/ship-phase` for each (reviewed, released `v3.2.0`, `v3.3.0`); deploy to dev. | v3.1 is on dev: `curl -s http://192.168.1.197:8766/v1/health` shows `"agora": 1`; its tag exists: `git -C ~/development/lumi tag -l 'v3.1.*'` | — | Dev complete |
| 6 | both | **The live test:** matrix-agora's bridge in dev mode against Lumi's dev, with a dev room token in a local env file. Bugs go back to the side that owns them; each side's fixes ship as its patches, reviewed and released. The checks are G2's. | Lumi dev is complete: health shows `"agora": 1` and its `v3.3` tag exists (`git -C ~/development/lumi tag -l 'v3.3.*'`); matrix-agora's bridge tests are green (`uv run pytest` in matrix-agora); the `cmp` still holds. | G2 | The results, both ways |
| 7 | Lumi | Deploy the released v3 (the last `v3.3.*`) to prod (8765); issue the room's token for prod. | The live test passed: G2 passed in the tracker. | G3 | The prod token → the owner → matrix-agora's `server/lili.env` |
| 8 | matrix-agora | Deploy the released v8.1 (the last `v8.1.*`) with `LUMI_URL=http://192.168.1.197:8765` and the room's token. | Prod is ready: `curl -s http://192.168.1.197:8765/v1/health` shows `"agora": 1`; Lumi's release tag exists; the token is set, never printed: `grep -q '^LUMI_ROOM_TOKEN=.' server/lili.env` | G4 | — |
| 9 | matrix-agora | v8.2 (Лілі under the full rules) — `/ship-phase v8.2` (reviewed, released `v8.2.0`). | v8.1 has run in the room for a few days. | G5 | — |

**The owner's gates.**

| Gate | Where | What the owner checks or decides |
|---|---|---|
| G1 | Before steps 3 and 4 | The go to start: the plan and the closed questions are fine. |
| G2 | At step 6, the live test on Lumi's dev | The checklist, each check recorded as passed or failed: named, she answers in seconds; unnamed, she is silent; nothing of the room reaches his Telegram or desktop; his own talk with her is not blocked or delayed; asked privately, she tells him about Agora; in Agora she says nothing about him; a dropped reply is retracted. |
| G3 | Before step 7, Lumi's prod | The go to deploy Lumi's v3 to her prod and to issue the room's prod token. It touches his real Лілі. |
| G4 | Before step 8, matrix-agora's prod | The go to deploy v8.1, create the Matrix account `@lili` and invite her to Agora. |
| G5 | Before step 9 | His verdict after a few days of Лілі in the room: whether she moves to the full rules (v8.2). |

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

## Her initiative — v3.4 (steps 10–16)

The owner asked on 2026-11-15 that she may write in Agora when she wants, not only when called on. It is planned as
Lumi **v3.4** (ROADMAP §v3.4) and matrix-agora **v8.3**, with the agora space API **v2**. Agreed as Q14–Q18 in the tracker.
- **Pull, not push** (Q14): the bridge polls `GET /v1/initiative` only while its room rules allow; a poll never calls
  the model; Lumi composes on demand in the background, on the room's own client.
- **Remembered only once seen** (Q15): `POST /v1/initiative/{id}/posted {event_id}` stores the text as her line; never
  acked → never remembered; always a 200, idempotent.
- **The pace is the room's** (Q16): the bridge's rules (1800 s quiet, 09–23, 4 a day, never twice in a row, 30 min
  fresh); Lumi's own guards in code (one offer at a time, one compose in 10 minutes since v3.4.1 (a half-hour in v3.4.0), never when her line is the room's
  last, the 20-an-hour cap); one switch `agora.initiative`, on at G8.
- **What she may open with** (Q17): something of hers or a thread of the room's — never about «Ich» or their talks
  alone, no question to anyone by name; the name guard and «Ich» checked in code.
- **API v2** (Q18): health `"agora": 2`; every v1 route unchanged; the `never` reads «an offer, never a post».
- **As built (v3.4, LUMI-296..299).**
  - `AgoraSpace.poll_initiative` (never a model call) and `initiative_posted`.
  - The offers live in the room's own store (`space_offers`).
  - Freshness is checked against the room's monotonic newest line (`last_seq`), so a retract never stales an offer.
  - The compose runs on a client of its own, so a turn is never refused while she composes.
  - Its prompt is the space recipe plus `core/agora_initiative.md`.
  - An empty, blocked, owner-naming or «Ich» answer is no offer.

| # | Session | Step | Gate | Hands over |
|---|---|---|---|---|
| 10 | Lumi | Plan v3.4: ROADMAP, this file, ARCHITECTURE; the contract `"version": 2` with `initiative` | G6 (passed) | The contract → matrix-agora |
| 11 | matrix-agora | Copy the contract (`cmp`); bring v8.3 to it | — | — |
| 12 | Lumi | v3.4 — `/ship-phase` (reviewed, released); deploy to dev — built 2026-11-16 (LUMI-296..299) | — | Dev with `"agora": 2` |
| 13 | matrix-agora | v8.3 — `/ship-phase v8.3` (reviewed, released) | — | — |
| 14 | both | The live test of her initiative on Lumi's dev | G7 | The results |
| 15 | Lumi | Deploy v3.4 to prod; `agora.initiative` on | G8 | — |
| 16 | matrix-agora | Deploy v8.3 | G8 | — |

| Gate | Where | What the owner checks or decides |
|---|---|---|
| G6 | Before step 10 | The go to plan her initiative (passed: «так відкрий новий крок і постав задачу Лілі запланувати це…»). |
| G7 | Step 14, on Lumi's dev | After a quiet stretch she starts a line of her own; never in a wave, at night, twice in a row or past the cap; never about him; the others answer her. |
| G8 | Before steps 15–16 | The go to deploy both to prod. |
