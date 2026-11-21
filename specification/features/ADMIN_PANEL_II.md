# Admin panel II — the server and the agent (v5)

**Status:** planned (the owner, 2026-11-20). Placed as **v5**; the old v5–v9 moved to v6–v10. Ten phases,
operations first (the owner's choice), then Лілі in the panel:

| Phase | Area | What | Release |
|-------|------|------|---------|
| v5.1 | both | Usage analytics: every paid call, its origin, her tools; the panel's new shell | 5.1.0 |
| v5.2 | server | The health monitor: history, incidents, the containers | 5.2.0 |
| v5.3 | server | Logs and alerts: the log explorer and the alert rules | 5.3.0 |
| v5.4 | server | The audit: every change to an instance, wherever it came from | 5.4.0 |
| v5.5 | agent | The schedule and the models | 5.5.0 |
| v5.6 | agent | Her texts and the prompt | 5.6.0 |
| v5.7 | agent | Лілі in the panel I: her state and the conversation history | 5.7.0 |
| v5.8 | agent | Her memory: the summaries and the facts about you | 5.8.0 |
| v5.9 | agent | Лілі in the panel II: thoughts, gallery, journal and files | 5.9.0 |
| v5.10 | agent | Edits of her files from the panel (the v4.5 actions) | 5.10.0 |

The design was made in Claude Design from [design/v5/BRIEF.md](../../design/v5/BRIEF.md) (English only). Its handoff
is in [design/v5/handoff/](../../design/v5/handoff/README.md) (2026-11-21); the brief's last section lists what the
design changed and added, what was corrected after the export, and what each phase still has to handle.

The phases, their tasks, DoD and tests are in [ROADMAP.md](../ROADMAP.md) §v5. This document holds the design they
share: what exists, the rules, the two areas, the new records, the page map, the decisions and the open questions.

## Why

The v2.10–v2.11 panel ([ADMIN_PANEL.md](ADMIN_PANEL.md)) watches and configures both instances: eight health
checks, the containers' start / stop / restart, a 7-day usage table, a log tail, the settings editor with history
and rollback, the schedule and model sections as YAML text, promotion from dev to prod, an audit of the panel's own
actions and Telegram alerts on any red prod check. What it doesn't do:

- **It counts only the model calls, and only for 10 days.** `cache-log.jsonl` records each model call, and
  `core/logtrim.py` keeps 10 days of it. Voice (ElevenLabs), dictation and Telegram voice notes (Deepgram), her
  pictures (Gemini image) and the web lookup are paid but not counted. No record says what started a call.
- **Her tools are invisible.** A turn's tool trace (`Core.last_tool_calls`) lives in memory for one turn and rides the
  window's `message` push. Nothing counts which tools she uses, how often they fail or how long they take.
- **Health is only "now".** `health.jsonl` is rotated at a size cap; there is no uptime figure, no list of incidents
  and nothing about the containers beyond running / stopped.
- **The logs are a tail of one file.** One source at a time, no time range, no live view.
- **Alerts have one rule:** any red check on a watched instance.
- **The audit sees only the panel.** A `lumi-cli config set`, a hand edit, a deploy or a window action never reaches it.
- **The schedule and the models are YAML text.** The owner can't see what fired last, what fires next, or which model
  each role runs right now.
- **Her authored texts change only through a release,** and the panel can't show the prompt she was given (the
  `/prompt` command exists in the TUI and the window only).
- **Лілі herself is not in the panel.** Her state, her conversation, her memory (the summaries, the facts), her
  thoughts, pictures, journal and files are only in the window, and only for the instance the window points at.

## The two areas: the server and the agent

The panel is split into two areas, and the split runs through the page, the API and the registry. The reason (the
owner, 2026-11-20): **the next version adds a second agent like Лілі**, and the panel must take it without a
redesign.

| Area | What belongs to it |
|------|--------------------|
| **The server** | the box (load, memory, disk); the containers; the health checks of the services; incidents; the logs; the alert rules and their delivery; the audit; each instance's settings, secrets, config history and promotion; the deploy agent, the archives, the image tags |
| **The agent** (Лілі today) | her usage and tools; her schedule and models; her authored texts and the prompt; her state; the conversation history; her memory (the summaries, the facts); her thoughts, gallery, journal and files, and their edits |

- **The registry names the agents.** `~/lumi/admin/admin.yaml` gains an `agents` map: an agent's id, its display name
  and the instances that run it (today `lili: {name: Лілі, instances: [prod, dev]}`). An instance runs one agent.
- **The agent's API is keyed by the agent:** `/api/agents/<agent>/<instance>/…`. The server's API stays
  `/api/<what>/<instance>`. A request for an instance that doesn't run that agent is a 404.
- **No agent code path assumes her name.** The views, the allowlist of her authored texts, the usage breakdowns and the
  alert rules that concern an agent all take the agent's id; "Лілі" appears only as the display name from the
  registry.
- **Usage belongs to the agent** (her calls), with the server's view showing the sum over the agents.
- **A health check is the server's** unless it reads the agent's state (the mood, the scheduler, the room): those are
  shown in both areas, under the agent they concern.

## What stays

- **One panel on the box, both instances**, LAN only, its own token (`lumi-admin`, port 8767). Prod is red.
- **The panel never writes a data root itself.** It reads ledgers and logs through read-only mounts and talks to each
  instance's server over the API. It writes only the instance folder's settings (v2.11) and authored copies (v5.6),
  outside `data/`. Every change to her memory or her files is the **server's** own route, so the server's guards apply.
- **No paid call** unless the owner presses a button that says it costs one (the model probe; see the open questions
  for a schedule row's "run now").
- **No view takes the turn lock or makes a model call.** A fact edit (v5.8) takes the lock briefly; a running turn
  answers 409.
- **No secret** in any API response, log line, audit line or alert. The page never holds a token.
- **Dev is free, prod is guarded.** Settings, services, the schedule, the models and her texts on prod need the typed
  `prod`. Edits of her files use the window's confirmations (the owner, 2026-11-20).
- **Tests:** fakes for Docker, the servers and the senders; fixture data roots; no network, no paid call in CI.

## The rules for the agent's views

1. **The panel talks to her through her server, as the window does.** Every agent view calls the instance's owner
   routes with **that instance's client token**, read from its `secrets.yaml` inside the panel's process
   (`admin/readers.py` `server_token` already does this for `/v1/state`). The page calls the panel's
   `/api/agents/<agent>/<instance>/…`; the panel adds the token. The room's token is never used.
2. **What the panel keeps, it keeps in its own folder**, `~/lumi/admin/` (the instances' `data/` stay read-only): the
   usage rollups, the health history and incidents, the alert rules and their history, the audit. Each store is
   bounded.
3. **Every new record is written by the process that does the work.** The ledgers of v5.1 are written by the server
   and the Telegram daemons, never by the panel. A recorder that fails logs one warning and never breaks a turn (the
   `cache-log` rule).
4. **A turn's channel comes from the client id it already sends.** Since v2.4 every client names itself in
   `X-Lumi-Client` (the events it causes carry that id): the window sends `desktop-<hex>`, the TUI client a bare hex
   id. v5.1 gives the TUI client and the CLI the prefixes `tui-` and `cli-`, and the server records the prefix as the
   turn's channel (`desktop-` → `window`); a missing or unprefixed id is `api`. Telegram turns are `telegram`, the
   room's are `agora`. No new header, and no existing route changes. The channel is written on the ledgers and on the
   message itself (`Message.channel`, additive; older messages have none), so the history can show and filter it.

## v5.1 — the records and the shell

### Every paid call

`cache-log.jsonl` keeps its role (one row per model call, the cache report reads it) and gains `origin` and
`channel`. A new ledger, `media-ledger.jsonl` in the data root, records the paid calls that are not model calls:

```
{ts, provider, service, model, units: {chars | audio_s | images | requests, input, output}, latency_ms, outcome,
 origin, channel}
```

- `service`: `tts` (ElevenLabs, a reply voiced once in v4.6), `stt` (Deepgram or ElevenLabs Scribe: the dictation of
  v4.7, a Telegram voice note recognized by the inbound daemon), `image` (Gemini image generation, v0.23), `search`
  (the v0.27 web lookup: one grounded Gemini call, recorded nowhere today; its request plus the tokens Gemini reports
  in `usageMetadata`).
- `outcome`: `ok`, `error:<ExceptionType>` or `refused` (a cap or a guard).
- `origin` is the same in both ledgers:

| origin | When |
|--------|------|
| `turn` | the owner's turn (with `channel`: `window`, `tui`, `cli`, `api`, `telegram`) |
| `directive:<name>` | a `%directive` the owner typed |
| `schedule:<row>` | a scheduled row (its name, or its key) |
| `room` | the Agora space (`kind: agora` today) |
| `initiative` | her Agora initiative (v3.4) |
| `housekeeping:<what>` | the mood, the session close, the compaction, the topic close, the voice of a pushed line |

No message text, no prompt and no audio enter either ledger.

### Every tool call

`tools.jsonl` in the data root, one line per tool call:

```
{ts, tool, outcome, ms, result_chars, turn_id, session_id, origin, channel, input}
```

- `outcome`: `ok`, `error:<ExceptionType>`, `refused:<guard>` (the sandbox, a cap, an off flag) or `not_found`.
- `input` is the **one bounded line** the window's trace already shows (`tool_calls_view`: `key=value` pairs, cut at
  `TOOL_INPUT_CHARS`). The result text is never written; only its size.
- `turn_id` joins a tool call to her reply in the history (v5.7).

### The panel's rollups

The ledgers are trimmed to 10 days by `core/logtrim.py`. The panel rolls each instance's ledgers up into its own
folder after every health run, so the history outlives the trim:

- `~/lumi/admin/usage/<instance>.jsonl`: one row per **closed** local day × provider × service × model × kind ×
  origin × channel, with the summed units, the estimated cost and the call count. Today's figures are computed live
  from the ledger. A day is closed once, at the first run after its end; a re-read changes nothing. Kept 400 days.
- `~/lumi/admin/tools/<instance>.jsonl`: one row per closed day × tool × outcome, with the count and the duration
  median / p95. Kept 400 days.

### Prices

`core/pricing.yaml` gains the non-token units: ElevenLabs per 1 000 characters for the configured model, Deepgram per
audio minute, Gemini image per picture, the grounded search per request. A unit without a price is shown as
"not priced" and counted, never guessed.

### The shell

The design's page frame ([design/v5/handoff/](../../design/v5/handoff/README.md)) lands in v5.1, and every
v2.10–v2.11 view moves into it, so nothing is lost on the way:

- **The page:** `admin/static/` rebuilt on the handoff's tokens (`nocturne.css`, `tokens.css` with the light theme)
  and strings (`strings.json`); no framework, no bundler; the icons inlined as SVG; Inter served from
  `admin/static/` (the `@import` from Google Fonts removed); nothing loaded from the network; every data string set
  with `textContent`.
- **The frame:** the attention strip, the rail (the two areas, their groups and badges, the agent picker with the
  reserved slot for a second agent), the header (the crumb, the updated time, the instance switcher with each
  instance's version, the theme toggle) and prod's red edge on every view that follows the switcher.
- **The shared pieces:** the sign-in; the loading, empty and "can't reach {instance}'s server" states (with the time of
  the last answer and the next retry); the confirmation dialog (a diff; the typed `prod`); the toast; the keyboard
  (P / D the instance, J / K the view, Esc); the view, instance and theme in `localStorage`; deep links in the URL
  hash.
- **The views carried over:** Settings, Secrets, Config history and Promote restyled as the design draws them; Health,
  Containers, Logs, the audit and the schedule and models YAML keep today's content in the new frame until their
  phases.
- **A first Overview** from what exists: the instance cards (today's checks and containers), the month's cost, the
  agent's card with its cost per instance. v5.2 adds the box and the incidents, v5.3 the alerts, v5.2–v5.4 the last
  24 hours, v5.7 her mood and last reply. The attention strip shows the red and amber checks first, then the open
  incidents (v5.2) and the firing alerts (v5.3).
- The registry's `agents` map with the agent-keyed API. Every later phase fills a view in the frame.

## v5.2 — the health history and the containers

- **The history:** the minute rows stay 48 hours (`health.jsonl`); an hourly rollup per check (the minutes in each
  state) is kept 90 days (`health-hourly.jsonl`). Uptime per check and per instance is the share of green and amber
  minutes over 24 h / 7 d / 30 d.
- **Incidents:** an incident opens at the first red check of an instance and closes when none is red. It records
  when, how long, which checks, the first red detail, and the container events inside it (a restart, an OOM kill).
  `incidents.jsonl`, kept 400 days. The alerts of v5.3 point at it.
- **The box:** the kernel, the cores, the uptime, the load, the memory, the disk and the running containers, read from
  the host (`/proc`, `statvfs`, the Docker API with GET), kept as the containers' figures are.
- **The containers:** the six instance containers (prod and dev: `server`, `telegram-inbound`, `telegram-outbound`)
  and `lumi-admin` itself. Each minute the runner reads, with GET requests to the Docker API only: the state, the
  health status, the start time, the restart count, the last exit code and `OOMKilled` (inspect), the image tag, and
  one stats sample (`/containers/<id>/stats?stream=false`: CPU % from the deltas, memory used and the limit, network
  and block I/O). Kept as minute samples for 48 hours and hourly figures for 90 days. **The container's `Env` and
  `Mounts` are never returned to the page.**
- **New checks:** the deploy agent (its last run, the result, a lock held longer than a deploy can take); the newest
  prod archive's age; the image drift (prod's tag against the newest release tag; dev's against `main`); the Agora
  space when it is on (the room's last `observe`); the outbound Telegram's last send failure; the voice and
  dictation providers configured (a key present, never shown).
- **The model probe** (planned since v2.10): a button per instance that says it costs one call; the result is
  recorded as a `probe` row in the history.

## v5.3 — logs and alerts

- **The sources** in one explorer: each instance's `lumi.log`, `mood.log` and the Telegram daemons' logs (10 days, as
  trimmed), each container's log (the Docker API), the panel's own log and the deploy agent's log. A line keeps its
  traceback folded under it. Every secret value is redacted (the v2.10 redactor).
- **The live tail** is an SSE stream from the panel, filtered on the panel's side, paused while the owner scrolls up.
- **The alert rules** live in `~/lumi/admin/alerts.yaml`, edited in the panel (a diff, the audit). The rule kinds:

| Kind | Fires when |
|------|-----------|
| `check` | a check is red, or amber longer than N minutes |
| `log` | lines matching a level / logger / text appear N times in M minutes |
| `usage` | an agent's cost today passes X, or the month's forecast passes Y, or a provider's failures pass N per hour |
| `container` | a restart, an OOM kill, or N restarts in M minutes |
| `schedule` | an agent's row was due and did not fire within its grace |

  Each rule: the instances (and the agent, for `usage` and `schedule`), a severity (`critical` ignores quiet hours),
  quiet hours, a rate limit, on / off. The defaults reproduce v2.11 exactly (any red check on prod), so nothing
  changes until the owner edits a rule.
- **Delivery** stays the dedicated admin bot, plus a banner in the panel. A failing message names the rule and links
  the incident; the recovery message stays.
- **Acknowledge** stops the reminders of one firing; **snooze** holds a rule for 1 hour or until the morning;
  **mute** turns a rule off. Each is in the audit.
- **The alert history:** every message sent or held (rate limit, quiet hours, snooze), its rule, its incident, its
  acknowledgement. 400 days.
- **From the design:** a rule's scope may be the box; the rule editor shows a **back-test** (how many times the rule
  would have fired in the last 7 days, from the stored history; nothing is sent); the messages held in a rule's quiet
  hours go out **as one message when they end**; the alerts of one incident are **grouped** into one message; a log
  line has a deep link.

## v5.4 — the audit

One stream, `~/lumi/admin/audit.jsonl`, with a `source` on every entry, and the agent on an entry that concerns one:

| source | What it records | How it is seen |
|--------|-----------------|----------------|
| `panel` | every panel action, refusals included (as today), plus the client's address and browser | written by the panel |
| `agent` | the deploy agent's deploy, confirm, rollback: the tag, the archive, the smoke's result | its log / state file, read-only |
| `outside` | a settings file or an authored copy changed outside the panel (`lumi-cli config set`, a hand edit) | the files' hashes against the last ones the panel knew, each minute; the diff, a secret named only |
| `window` | the owner's actions in the window (v4.5) | each instance's `actions.jsonl`, read-only |
| `docker` | a container started, stopped or restarted outside the panel | the Docker events (GET, read-only) |

- **Tamper evidence:** each line carries the SHA-256 of the line before it. A "verify" action walks the chain and
  names the first line that doesn't match. The rotation keeps the chain across generations (the first line of a
  generation carries the hash of the previous generation's last line).
- **Retention:** 12 generations of 2 MB (today: one).
- **The page:** filters (instance, agent, source, action, result, time range, text), the diff of an entry, export as
  CSV or JSONL.

## v5.5 — the schedule and the models

- **The schedule.** The rows are the merged schedule (`core.schedule.merged_rows`: the authored `core/schedule.yaml`
  with the instance's `schedule:` overrides), each marked `authored`, `changed here` or `added here`, with its trigger
  in words, the directive, the topic, shown or silent, on / off, the last firing (`schedule.state`) and its outcome
  (the log line of that firing), and the next firings (`core.schedule.next_due` with the quiet hours). The form: the
  directive from her registry, the trigger kind (`every`, `idle`, `at` + `days`, `between` + `every`, `cron`) with
  that kind's fields, the topic (the placeholders listed), `show`, `enabled`, validated by the core's own parser, the
  next five firings previewed live. The form offers `every` (with its optional `between` window), `idle` and `at` (with
  the days); `cron` is edited in "Advanced". Each row shows its **missed** firings: the due times since its last
  firing, outside the quiet hours, that did not fire. The scheduler's settings beside it, the ones that exist (on /
  off, `thoughts.quiet_hours`, `scheduler.day_cap`, `thoughts.spoken_ratio`), and the next firings across all rows. A save is a v2.11 plan. Reset a row to the
  authored one; copy a row from dev to prod. The YAML editor stays as "Advanced".
- **The models.** Each role and the provider and model it runs **now** (`GET /v1/models`, owner only, read-only),
  beside what the settings say; a runtime `/model-set` or `/model` switch shows as a difference until the restart. The
  roles: reply, think, mood, housekeeping, the voice register, the tool step, the room (`agora.model`), the web lookup,
  the pictures, the embeddings, the voice (ElevenLabs model), the recognition (STT provider and model). The profiles
  and aliases (`core/models.yaml` + the instance's `models:` section), each profile a card; a provider without a key
  can't be chosen; a role's options show each model's price from `core/pricing.yaml`.
  **Save** writes the setting as a plan (persisted, prod's confirmation, the restart); **now, until the restart**
  uses the existing commands through `POST /v1/command` for the roles they cover. Every switch is audited.

## v5.6 — her texts and the prompt

- **The overlay.** An instance can carry its own copy of an authored file at `~/lumi/<instance>/authored/<path>`
  (outside `data/`, mounted read-only into the containers). The instance reads the copy when it exists, else the
  image's file; an explicit path setting (`persona.canon_path`, `persona.inner_voice_file`, …) still wins.
- **The allowlist** (per agent; Лілі's today): `core/canon/lili.md`, `core/inner_voice.md`,
  `core/inner_voice_trace.md`, `core/styles.md`, `core/emoji.md`, `core/natal.md`, `core/closeness.md`,
  `core/nudges.md`, `core/think_seeds.md`, `core/think_seeds2.md`, `core/voice_tags.md`, `core/agora_room.md`,
  `core/agora_initiative.md`. Never code, never the `_bak` copies, never a path outside the list. (`core/schedule.yaml`
  and `core/models.yaml` are edited through v5.5.)
- **The editor:** each file marked `release` or `changed here`; the current text (the copy, or the running image's
  file read through the Docker API with GET); the size and an estimate of the tokens it adds to the prompt, with the
  change; the check of that file's own loader (the closeness file still gives five levels, the emoji map the nine
  emotions, the natal file its anchor); a save is a v2.11 plan; reset to the release's file; copy dev's copy to
  prod. A changed canon or inner voice moves the cached prompt prefix: the next turn writes the cache once.
- **Drift:** a copy remembers the hash of the release file it was made from. When a new release changes that file, the
  panel marks the copy and shows both differences (the release's change, the copy's change); "download as a patch"
  lets the change go into the repo. The owner resolves it: **take the release** (the copy reset), **keep mine** (the
  copy now remembers the new release's file) or **merge by hand** beside both differences; each is audited.
- **The check line** names the loader's check, the sections, any unknown placeholder (the placeholder resolver's
  names) and the encoding.
- **The prompt:** `GET /v1/prompt` returns what the `/prompt` command shows (owner only, read-only, never the turn
  lock, no model call, bounded): the last turn's system prompt with the cache split marked, its sections with their
  sizes, the messages, and that turn's token counts, time, channel and model (the panel prices the counts). The panel shows it with **copy** buttons (the whole prompt; the
  system part only). The panel is plain http on the LAN, so the page has no secure context and no clipboard API: it
  copies through a selected text area.

## v5.7–v5.10 — Лілі in the panel

- **State (v5.7)** shows what the window's State view (v4.4) shows: the mood of the day, its resolution, reading and
  made-at time, the biorhythms and the hormonal cycle, the theme of the day (its English name and scene), the
  closeness level (its English name), and the last reply's emotion and intensity (her emoji and the caption) with the
  owner's line it answered and its channel. The needs and the plans appear once v6.5–v6.8 build them; until then each
  shows the window's "Arrives in a later version". The portrait stays in the window: the face pack lives beside the client, and no face
  asset crosses the network (the v2.12 rule).
- **History (v5.7):** a new server route, `GET /v1/history`, owner only (the room's token 403), read-only,
  user-scoped, bounded (a page of at most 100 messages, a cursor, a day, a channel filter, a case-insensitive text
  search over the last 30 days or, on request, all time), never the turn lock. Each message: who, when, the text, the emotion and intensity, the conversation move (v1.1), the
  channel (`Message.channel`, v5.1; older messages have none), her pictures (v4.2), and for her replies the turn's
  tools from `tools.jsonl` with their outcome, input line and duration. Her hidden reasoning is not there (it is never
  persisted, v2.6). The room's lines are not there (they are the space's, v3).
- **Memory (v5.8):**
  - **The summaries**, `GET /v1/summaries`: the in-session compaction digests (`SessionDigest`), each session's
    summary and gist (`ShortSummary`), the day digests (`DaySummary`), the week digests (`WeekSummary`) and the facts
    digest (`FactsDigest`), by tier and time; the room's digests (v3.2) as a separate, labelled filter. Each entry says
    what it covers and when it was written; a `ShortSummary` keeps neither, so a session's first and last message
    time and their count come from its messages. Read-only.
  - **The facts about the owner**, `GET /v1/facts`: the text, the confidence, the source line, when, the
    identity-core mark, obsolete or not. **Edits** through new owner routes: add, correct (the text, the confidence),
    mark obsolete, restore, pin or unpin in the identity core. **Non-destructive:** a correction marks the old row
    obsolete and adds the new one; nothing is deleted (the `review-facts` skill's rule). The old row's new
    `replaced_by` (additive) names the new text, shown as "corrected <date> → «…»". A fact is addressed by its text,
    as the store does today; its source line is its stored `meta`. The facts digest is marked
    stale and the fact's recall vector replaced. The edit takes the turn lock briefly; a running turn answers 409.
    Logged in the server's `actions.jsonl` (`origin: admin`) and in the panel's audit with the old and the new text.
- **Thoughts, Gallery, Journal, Files (v5.9)** are the window's v4.1–v4.3 views over the same routes, drawn as the
  design draws them. A thought's origin line is its `seeds` (the schedule row is not recorded). A picture's model and
  where it was shown are joined from the v5.1 ledgers (an `image` row, a `send_image` row) for the pictures made
  since; the gallery's Trash lists the pictures in `.trash/` that the actions log knows. The markdown
  reader shares the window's safe renderer (`desktop/ui/`), never `innerHTML`; the gallery's thumbnails come from
  `/v1/images/thumb` through the panel.
- **Edits of her files (v5.10)** are the v4.5 actions: upload (create-only, capped), download, rename, move to
  `.trash/` and restore. The window's confirmations (the owner, 2026-11-20). The server records each in its
  `actions.jsonl` with `origin: admin`; the panel records it in its audit. "Show this to her" is not included: it
  starts a turn.

## The page map

| Area | Group | Views |
|------|-------|-------|
| — | **Overview** | the box, each instance, each agent at a glance |
| **Server** | Operations | Health · Incidents · Containers · Logs · Alerts |
| **Server** | Configuration | Settings · Secrets · Config history · Promote |
| **Server** | Audit | the audit stream |
| **Agent: Лілі** | Her | State · History · Memory (Summaries, Facts) · Thoughts · Gallery · Journal · Files |
| **Agent: Лілі** | How she runs | Usage · Tools · Schedule · Models · Texts · Prompt |

The instance switcher stays at the top; prod is red everywhere it appears. The agent picker shows one agent today.

## Decisions

- **v5 is the admin panel**, the old v5–v9 moved to v6–v10 (the owner, 2026-11-20).
- **Operations first**, then Лілі in the panel (the owner, 2026-11-20).
- **"Her history" is the conversation history**, read-only (the owner, 2026-11-20).
- **A Claude Design pass for the whole panel** before v5.1 (the owner, 2026-11-20).
- **Edits of her files on prod use the window's confirmations**, not the typed `prod` (the owner, 2026-11-20).
- **The containers are monitored** (the owner, 2026-11-20): the six instance containers and `lumi-admin`.
- **Her authored texts are edited from the panel** per instance (the owner, 2026-11-20); this replaces the v2.11
  rule "editing her canon in the panel — not planned".
- **The prompt is shown with a copy button** (the owner, 2026-11-20).
- **The summaries are viewed by tier and time, the facts viewed and edited** (the owner, 2026-11-20).
- **The models are shown per role and switched from the panel** (the owner, 2026-11-20).
- **The server and the agent are separate areas** of the panel, its API and its registry, because the next version
  adds a second agent like Лілі (the owner, 2026-11-20).
- **The look and the font are the mockup's** (the owner, 2026-11-21): the Nocturne system as the design made it
  denser (radii of 4–8 px, fading rules, outlined primary buttons) and Inter. Nothing loads from the network, so
  Inter's font file (the variable woff2, with its OFL license) ships in `admin/static/`.

## Open questions (decide before the phase's `/generate-issues`)

1. **A monthly budget per agent** (v5.3): a `usage` rule with a monthly sum, a warning at 80 % and a message at 100 %.
   Proposed; it needs one new value per agent in `alerts.yaml`.
2. **A daily digest** (v5.3): one morning message with yesterday's cost, incidents and errors. Proposed as a rule
   kind; off by default.
3. **"Run now" for a schedule row** (v5.5): fires the row's directive once through `POST /v1/directive`. A paid call;
   the button would say so, and prod would need the typed confirmation.
4. **"Show this to her" from the panel** (v5.10): not included, because it starts a turn. Add it only if the owner
   asks.
5. **The tool ledger's `input` line** (v5.1): the proposal keeps the window's bounded line so the history can show
   what she did. The alternative keeps only the tool's name and outcome.
6. **The allowlist of her texts** (v5.6): the list above; the owner confirms it, adds or removes files.
7. **Pinning a fact in the identity core from the panel** (v5.8): proposed. The `review-facts` skill re-ranks the core
   too, so a later run of it may unpin a fact the owner pinned, unless pins made in the panel are kept by it.
8. **Retention:** usage and tool rollups 400 days, the health hourly rollup and container figures 90 days,
   incidents and alert history 400 days, the audit 12 × 2 MB. Proposed.

## Not here

- **A second agent** like Лілі: the next version (the owner, 2026-11-20). v5 only keeps the agent separate from the
  server so that it can be added.
- Users, accounts, roles, consent and other users' memory: v7.4 (it extends this same panel).
- TLS and public exposure: v7.1.
- A metrics stack (Prometheus, Grafana): not planned; the panel reads files and the Docker API.
