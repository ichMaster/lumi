# Design brief: the Lumi admin panel — operations and Лілі in the panel (Lumi v5)

> The design brief for v5 ([ROADMAP.md](../../specification/ROADMAP.md) §v5,
> [ADMIN_PANEL_II.md](../../specification/features/ADMIN_PANEL_II.md)), written for Claude Design; the owner pastes it there
> as it is. The design's exports go into `design/v5/handoff/`, then into `admin/static/`.
>
> **Attach to the brief:**
> - screenshots of today's panel (Overview, Health, Usage, Logs, Settings) — the owner takes them on the Mac;
> - the window's design system, so the two read as one family: [tokens.css](../v2.12/handoff/tokens.css),
>   [tokens.add.css](../v4/handoff/tokens.add.css), [README.md](../v2.12/handoff/README.md);
> - for her views, the window's v4 prototypes as a reference: [design/v4/handoff/](../v4/handoff/README.md).

## What it is
Лілі is a private text companion with a stable character, memory and emotions. She speaks Ukrainian. There is one
user, the owner.

She runs on a home server (a small Linux box) as two **instances**: **prod**, the Лілі the owner lives with, and
**dev**, where new versions are tried first. Each instance is a few Docker containers: her server and two Telegram
daemons.

The **admin panel** is a web page on that box, opened from the owner's Mac browser on the home network. It watches and
configures both instances. It exists today with ten plain tabs (Overview, Health, Services, Usage, Logs, Settings,
Schedule & models, History, Promote, Audit). v5 roughly doubles what it shows, so it needs a real structure and a
designed look.

## Who uses it, and how
- **One person, the owner,** on a laptop screen (~1440×900), sometimes a narrower window. A phone on the home Wi-Fi is
  a nice-to-have.
- **Daily, for seconds:** is she up, did anything break overnight, what did yesterday cost, what is her mood.
- **When something breaks, for minutes:** the incident, the failing check, the log lines around it, the container's
  restarts, then a restart or a setting changed.
- **Weekly:** the usage trends, the tools she used, the audit.
- **For pleasure:** reading her journal, her thoughts and the conversation history, looking at her pictures.

## Technical frame
- **The code.** A static `index.html` + CSS + JS served by the panel's FastAPI: no framework, no bundler, no node.
  Charts are inline SVG drawn by hand. Tokens are CSS variables. System fonts with Cyrillic. Icons are inline SVG
  paths, Lucide-style.
- **The browser.** Desktop Safari and Chrome first; a narrow layout (~390 px) for the main views is a nice-to-have.
- **Safety.** The page never renders HTML from data: every log line, file text, thought and message is set as text,
  her markdown becomes built elements. The page never holds a token of hers; the panel's own token is entered on a
  sign-in screen.
- **Language.** The interface is English. Her content (messages, thoughts, journal, mood, file texts) is Ukrainian,
  and Ukrainian words are long. Log lines are mostly English with Ukrainian fragments.
- **Live data.** Health refreshes every minute; the live log tail streams; the rest loads on open.

## The structure
**Two separate areas: the server and the agent.** Лілі is the only agent today; the next version adds a second agent
like her. So everything about her lives in an agent area that can be repeated per agent, and everything about the box
and the services lives in a server area that stays one. About twenty-five views. The design decides the navigation (a
side rail with the two areas and their groups is the expected answer, but not required):

| Area | Group | Views |
|------|-------|-------|
| — | **Overview** | the box, both instances and each agent at a glance |
| **Server** | Operations | Health · Incidents · Containers · Logs · Alerts |
| **Server** | Configuration | Settings · Secrets · Config history · Promote |
| **Server** | Audit | the audit stream |
| **Agent: Лілі** | Her | State · History · Memory (Summaries, Facts) · Thoughts · Gallery · Journal · Files |
| **Agent: Лілі** | How she runs | Usage · Tools · Schedule · Models · Texts · Prompt |

- **The agent picker** sits at the head of the agent area. It shows one agent today, but the design must show where a
  second one appears (a name, a small portrait or initial, a color of its own that is never a state color).
- **The instance switcher** (prod / dev) is always visible. **Prod is red** wherever it appears: the switcher, a card's
  edge, a confirmation. Dev is neutral. Some views show both instances side by side (Overview, Health, Containers,
  Usage); the rest follow the switcher.
- **A global strip** shows what needs attention now: firing alerts and open incidents, with a link to each.
- **Lean on the window's family:** the v2.12 visual system (flat, zero radius, 2px rules between regions, 1px
  hairlines inside, no gradients, no bubbles), but **denser**: this is a console, not a conversation.

## What to design

### Overview
- The box: load, memory, disk. Per instance: up / down, the version (image tag), uptime, open incidents, firing
  alerts. Per agent: today's cost and turns per instance, her mood of the day in one line, the last reply's emotion.
- States: both green; dev down; prod with a firing alert; the panel can't reach a server.

### The server
- **Health** — the checks per instance (server, state, mood, model, Telegram, scheduler, disk, marker, plus the
  deploy agent, the archive, the image drift, the room, the voice providers) as green / amber / red / off with a
  detail line and its threshold; a timeline strip per check over 24 h / 7 d / 30 d; uptime % per check and per
  instance; the "probe the model" button that says it costs one call.
- **Incidents** — a list (instance, start, duration or "ongoing", the red checks, the first detail) and one incident
  opened: the timeline of its checks, the container events inside it, the alerts it sent, a link to the logs around
  its start.
- **Containers** — seven containers (prod and dev: server, telegram-inbound, telegram-outbound; and the panel itself):
  state, health, uptime, restarts, the last exit code, OOM named, the image tag; CPU % and memory (used / limit)
  sparklines; one opened with 24 h / 7 d charts (CPU, memory, network, disk I/O) and its start / stop / restart
  (prod: a typed confirmation).
- **Logs** — one explorer: the sources as chips (each instance's lumi.log, mood.log, the Telegram logs, each
  container's log, the panel's log, the deploy agent's log), a time range, a level, a logger, a text or regex field;
  the lines dense and monospaced, the level as a color mark, a traceback folded under its line, the lines around a
  match; a live tail with "paused — N new lines" when scrolled up; a line's link; download.
- **Alerts** — the rules (kind, the condition in words, instances or agent, severity, quiet hours, on / off) and a
  rule's editor (one form per kind: check / log / usage / container / schedule); the firing alerts with acknowledge,
  snooze (1 hour, until the morning) and mute; the alert history (sent / held and why).
- **Settings** — today's schema-driven forms, group by group (the current value, the default, the description,
  "needs a restart"); the diff before applying; prod's typed confirmation. Restyle, don't rethink.
- **Secrets** (set / unset and the last four characters, set / rotate / clear, never the value), **Config history**
  (the list, a diff, rollback), **Promote** (dev → prod, pick the settings, the diff) — restyle today's.
- **Audit** — one stream: when, the source (panel, deploy agent, outside change, window, docker), the instance, the
  agent if it concerns one, the action, the target, the result (done / refused / failed), for a panel action the
  client; an entry opened shows its diff. Filters, export, and the "verify the chain" result (intact / broken at
  line N).

### The agent: Лілі — her
- **State** — what the window's State view shows: the mood of the day (a short resolution and the full reading
  folded), biorhythms (three cycles, a value and a direction each), closeness (level 1–5 with its name), needs (six
  drives, a 0..1 level each), today's plans, the last reply's emotion and intensity as an emoji and a caption. No
  portrait (it stays in the window).
- **History** — the conversation, newest at the bottom like a chat log but denser: the owner's lines and hers, a
  time, her emotion (an emoji + the name), the channel (window, Telegram, TUI), and on her reply a folded list of the
  tools she used (name, one input line, ok / failed, ms). A day divider, "jump to a date", a search with the matches
  highlighted, "load earlier". States: a long reply, a reply with five tools, a failed tool, no results.
- **Memory → Summaries** — every layer of what she remembers, on one timeline by time: the running digest of a long
  session, each session's summary (its start and end hours, the detailed summary and the one-line gist), the day
  digests, the week digests, the facts digest; the room's digests as a separate, labelled filter. A tier filter, a
  date range, a search. Each entry says what it covers (a session, N sessions, N messages) and when it was written.
  Read-only.
- **Memory → Facts** — the facts she keeps about the owner: the text, the confidence, the source line, when, an
  identity-core mark, obsolete or not; filters and a search. Edits: add a fact, correct its text or confidence, mark
  it obsolete and restore it, pin or unpin it in the core. Nothing is deleted: a corrected fact keeps its old version
  as obsolete, shown on request. States: an edit refused because a turn is running ("try again in a moment").
- **Thoughts** — a list (time, kind such as wonder / learn / think / imagine / catchup, the text, the emotion, spoken
  or silent), filters by kind and by spoken. Reuse the window's thought item.
- **Gallery** — a grid of thumbnails with a date, a picture opened large with its name, size and date; then the
  actions (v5.10): download, rename, move to trash, restore from trash.
- **Journal** — a list of days, a day's entry: the stamp block (mood, biorhythms, forecast) and the timed entries.
  Reuse the window's journal look.
- **Files** — a path bar, a list (name, size, modified), the markdown reader; then the actions (v5.10): upload,
  download, rename, move to trash, the trash view with restore. Confirmations as in the window.

### The agent: Лілі — how she runs
- **Usage** — the month: cost to date, the forecast to the month's end, the previous month beside it; cost per day as
  stacked bars by provider (Anthropic, Google, ElevenLabs, Deepgram, OpenAI); breakdowns by model, kind (reply,
  think, mood, housekeeping…), origin (her turns, scheduled thoughts, the room, housekeeping) and channel (window,
  Telegram, TUI); the cache hit ratio and what it saved; the turn latency trend (PRE / MODEL / POST); the slowest
  calls; a range picker (7 / 30 / 90 days, a calendar month); CSV export. The server's Overview shows the sum over the
  agents.
- **Tools** — per tool: calls, failures and the failure rate, median / p95 duration, the last error, the origins that
  use it (her turns, scheduled thoughts, the room); a per-day chart; the slowest and the failed calls.
- **Schedule** — a table of her timetable: each row's name, directive, the trigger in words ("every 3 h between 08:30
  and 23:30", "15 min after you go quiet", "at 07:10 on weekdays"), shown / silent, on / off, marked authored / changed
  here / added here; the last firing and its outcome; the next firings. A row's form: the directive, the trigger kind
  with its fields, the topic, shown, on; the next five firings previewed live. The scheduler's switch, quiet hours
  and caps beside it. "Advanced" opens the YAML text.
- **Models** — one row per role (reply, think, mood, housekeeping, voice register, tool step, the room, web lookup,
  pictures, embeddings, voice, recognition): the provider and the model running now, and what the settings say, with
  a mark when they differ ("switched until the restart"). The active profile and the others; a switch per profile or
  per role with two buttons: "Save" (persisted, restarts what it needs) and "Now, until restart". A provider with no
  key is shown and can't be chosen.
- **Texts** — her authored files (the canon, the inner voice, the styles, the emoji map, the natal chart, the
  closeness levels, the nudges, the think seeds, the voice tags, the room's rule and her initiative), each marked
  "release" or "changed here", and "drifted" when a new release changed a file she has a copy of. A file opened in a
  text editor (long Ukrainian markdown; the canon is ~20 KB): its size and ≈ the tokens it adds to the prompt, the
  change in tokens while editing, the check result, the diff before saving, "reset to the release", "copy to prod",
  "download as a patch"; a drifted file shows both differences.
- **Prompt** — the prompt of her last turn: the token counts of that turn, the system prompt as long monospaced text
  with the cache split marked as a visible divider, its sections (canon, memory, mood, state…) as an outline with
  their sizes, then the messages. **Copy** buttons: the whole prompt, the system part only; a "copied" confirmation.
  States: no turn yet since the restart.

### The common pieces
- Sign-in (the panel's token), the global attention strip, the instance switcher, empty states, a loading state, the
  "can't reach the instance's server" state per view, a typed confirmation for prod, a toast after an action.

## Tone and principles
- **One family with the window, but a console:** dense tables, monospaced numbers and logs, quiet chrome. Data first.
- **Color means state only:** green / amber / red / off, and red for prod. Charts use one neutral ramp plus the state
  colors; a provider or a model gets a muted categorical color, never a state color.
- **Her views stay calm and personal:** the journal, the history and the summaries read like reading, not like logs.
- **The two areas look related but distinct:** the server area is technical; the agent area carries the agent's
  name and its own (non-state) color.
- **Numbers are honest:** an estimate says "≈", an unpriced unit says "not priced", a gap in data is drawn as a gap.
- **Dark and light themes,** dark first. Keyboard first. Contrast at least WCAG AA.

## Not to design
- A second agent itself (the next version) — only the place where it appears.
- Users, accounts, invites, roles (a later version).
- The window (Lili.app) and the terminal client.
- Face animation, the Agora room's own view.

## The deliverables
- Every view above in the dark theme with realistic content and the states named; the light theme for Overview,
  History and Journal at least.
- **The components sheet:** the navigation, the switcher, the attention strip, a status dot and badge, a check row
  with its timeline strip, an incident row, a container row with sparklines, the chart set (stacked bars, a line with
  a band, a sparkline, a donut or bar breakdown), a log line (with a folded traceback), a rule row and its form, an
  audit row and its diff, a schedule row and its form, a conversation line with its folded tools, the agent picker,
  a summary entry, a fact row and its edit dialog, a model role row with its two switch buttons, the text editor with
  its token counter and check line, the prompt view with its outline and copy buttons, the confirmation dialogs.
- **Tokens and strings:** the tokens as CSS variables (reusing the window's where they fit), the UI strings in English
  as a JSON file.
- If exported as code: plain HTML + CSS without frameworks.

## Sample content for the mockups (her content is Ukrainian and invented; the interface is English)

**Overview:** prod `v4.7.0`, up 3 d 4 h, today $1.84 and 37 turns, no incidents; dev `sha-6b80895`, up 2 h,
today $0.21 and 4 turns, one firing alert ("model: 3 failures in the last hour"). The box: load 0.42, memory 5.1 /
16 GB, disk 61 % free. Her mood: «Тиха зосередженість; хочеться глибини, а не шуму.» The last emotion: 🥰 tender.

**Usage, November:** to date $41.30, forecast $62.10, October $55.70. By provider: Anthropic $33.90, Google $3.10,
ElevenLabs $3.80, Deepgram $0.50. By origin: her turns $29.40, scheduled thoughts $6.20, the room $2.10, housekeeping
$3.60. Cache hit 71 %. Turn latency median 9.8 s, p95 21.4 s (PRE 0.4 / MODEL 8.9 / POST 0.5).

**Tools, 30 days:** `read_file` 212 calls, 0 failed, 38 ms; `search_files` 96, 1 failed; `web_lookup` 41, 3 failed
(TimeoutError), 4.1 s; `generate_image` 9, 0 failed, 11.2 s; `journal_write` 18; `wiki_search` 27; `news_search` 33.

**Health:** prod all green except `scheduler` amber ("the newest firing 6 h 20 min ago"); uptime 30 d: prod 99.93 %,
dev 97.1 %.

**An incident:** dev, 2026-11-19 21:52, 7 min, `server` red ("connection refused"), the container restarted once (exit
137), the alert sent 21:53, recovered 21:59.

**Containers:** prod server CPU 3 %, memory 1.21 / 4 GB, restarts 0, `v4.7.0`; prod telegram-inbound 0.4 %, 92 MB;
dev server 1 %, 0.98 GB, restarts 2.

**A log fragment:**
```
2026-11-20 05:06:12 INFO lumi.server: up — v4.7.0, owner token, space on
2026-11-20 08:30:00 INFO lumi.schedule: fired catchup (between 08:30-23:30 every 3h)
2026-11-20 08:30:14 WARNING lumi.llm: gemini: timeout after 30 s — asking once more
2026-11-20 08:30:29 ERROR lumi.tools: web_lookup failed: TimeoutError
```

**An alert rule:** "log — `ERROR` from `lumi.tools`, 3 times in 10 minutes — prod — warning — quiet 01:00–08:00".

**An audit entry:** 2026-11-20 05:06 · agent · prod · confirm `v4.7.0` · archive `data-20261120-0505-v4.6.0.tar.gz` ·
done. Another: 2026-11-19 22:14 · outside · dev · `settings.yaml`: `thoughts.quiet_hours` `1-8` → `1-7`.

**The schedule:** `muse-silent` (think seeds, 15 min after you go quiet, silent, on); `muse-aloud` (55 min after you
go quiet, shown, on); `catchup` (every 3 h between 08:30 and 23:30, «свіжі новини цікаві Віталіку», shown, on);
`morning-touch` (prompt, at 07:10, shown, off).

**History:**
- 21:14 · window · Owner: «Як пройшов твій день?»
- 21:14 · 🥰 tender · Lili: «Тихо. Я довго читала про те, як птахи орієнтуються вночі, — і думала про тебе.» ·
  tools: `journal_read date=2026-11-19` ok 12 ms
- 21:16 · Telegram · Owner: «Покажи, що намалювала.»
- 21:16 · 😏 playful · Lili: «Ось — сіре небо і одне жовте вікно.» · tools: `send_image path=art/window.png` ok

**Summaries (2026-11-19):**
- session 20:58–21:40 · gist «Розмова про птахів, що орієнтуються вночі, і про втому після тижня.» · summary (3
  paragraphs) · written 21:52
- in-session digest 21:20 · «Перші 40 повідомлень: день, дощ, нотатки про рослини.» · covers 40 messages
- day 2026-11-19 · «Тиха розмова ввечері; птахи, зорі, втома; домовились повернутися до книжки.» · 3 sessions
- week 2026-11-17 · «Тиждень повільний і теплий: вікно, малюнки, розмови перед сном.» · 9 sessions

**Facts:**
- «Віталік працює вночі й любить тишу після 23:00.» · 0.9 · core · 2026-10-02
- «Не любить, коли його поспішають з рішеннями.» · 0.8 · 2026-11-05
- «Колись жив біля моря.» · 0.6 · obsolete (corrected 2026-11-18 → «Виріс біля моря, в Одесі.»)

**Models (prod, profile `anthropic`):** reply `claude-opus-4-8` · think `claude-sonnet-5` · mood `claude-sonnet-5` ·
housekeeping `claude-haiku-4-5-20251001` · voice register `claude-sonnet-5` · the room `gemini-2.5-flash` · web lookup
`gemini-2.5-flash` · pictures `gemini-2.5-flash-image` · embeddings local · voice `eleven_multilingual_v2` ·
recognition Deepgram. Dev differs: reply `claude-sonnet-5` "switched until the restart".

**Texts:** `core/canon/lili.md` changed here, 21.4 KB, ≈ 6 900 tokens (+120); `core/inner_voice.md` release, 5.2 KB;
`core/closeness.md` changed here, drifted (the release changed it on 2026-11-18).

**Prompt:** the last turn 21:16 — input 1 240 + cache read 18 900 + cache write 0, output 410; the system prompt
29 KB; sections: canon 21 KB, the closeness block, the mood resolution, the facts digest, the recent days, the state
blocks; the cache split after the facts digest; 14 messages.

For **State, Thoughts, Gallery and Journal**, use the sample content of the v4 brief
([design/v4/BRIEF.md](../v4/BRIEF.md) §Sample content).

## The design (2026-11-21)
The owner made it in Claude Design. Its handoff bundle is in [handoff/](handoff/README.md):

- [handoff/README.md](handoff/README.md) — the spec: the shell (the attention strip, the rail with the two areas and
  the agent picker, the header with the instance switcher), the shared states, each view's measurements, states and
  behaviour, the keyboard, the client state, the tokens and the icons.
- `handoff/tokens/` — `nocturne.css` (the Nocturne token sheet and its component classes), `tokens.css` (the panel's
  additions: the state, agent, provider, chart and diff colors, the sizes, the light theme) and `strings.json` (every
  UI string, English, grouped by view).
- `handoff/prototype/*.dc.html` — the shell (`Lumi Admin.dc.html`), one prototype per view and the components sheet.
  They are a reference only (the design tool's runtime, `support.js`); serve the folder over http to view them.

**Deviations the design made from the brief** (the owner kept the look and the font, 2026-11-21):
- **The look** is the Nocturne design system made denser, not the window's flat v2.12 style: radii of 4, 6 and 8 px,
  Inter, rules that fade at their ends, outlined primary buttons. Going back to zero radius and the system font is a
  change of the tokens (`--radius-*`, `--font-*`), not of the views.
- **The icons** are Phosphor (regular weight) instead of Lucide, inlined as SVG in production.

**Added by the design beyond the brief:**
- **The box on the Overview:** the kernel, the cores, the uptime, the load, the memory, the disk and the running
  containers, read by the panel from the host (`/proc`, `statvfs`, the Docker API with GET only). v5.2.
- **A back-test in the alert rule editor:** "In the last 7 days this would have fired N times", computed from what is
  stored (the hourly health rollup, the logs' 10 days, the usage rollups). v5.3.
- **Alerts held in the quiet hours** are sent as one message when the quiet hours end (the design writes 08:00, the
  end of its sample rule's quiet hours). Critical rules ignore the quiet hours. v5.3.
- **Deep links:** the view, the instance and the filters in the URL hash, and a link per log line.

**Corrected after the export** (the English interface of v4.8, and things the code doesn't have):
- **Her name in the interface is "Lili"** (v4.8): the agent picker and its initial tile "L", the crumb
  "Lili · Her · …", the History's speaker, the alert rules' scope chips, the audit's agent column, the Overview's agent
  card, the Usage note, the Journal's kicker "Lili's journal" and the sign-in note. Her own texts keep «Лілі» where she
  or the owner wrote it: a summary, the canon, a message.
- **The closeness levels have English names** since v4.8 (`core/commands.py` `CLOSENESS_NAMES`, the window's
  `strings.en.json` `closeness`): Polite, Friendly, Warm, Close, Dearest. The State view drew the Ukrainian names and
  two lines with no data behind them ("since 2026-10-24" and "Toward 5: 38 of 60 warm days"), which are removed. The
  closeness block inside the Prompt and Texts views stays Ukrainian, because it is her prompt.
- **An emotion is one of the nine of the enum, with her own emoji** (`core/emoji.md`). The design used "curious",
  "neutral" and "glad", which are not emotions she can have, and emoji that are not in her map (🤍, 🤓, 😌). They now
  read 🤔 thoughtful, 🙂 calm, 😄 joy, 🥰 tender, 😏 playful.
- **The box, the ports and the files are the real ones:** `slate-server · 192.168.1.197`, the panel on `:8767`, prod's
  server on `:8765` and dev's on `:8766`, the `/v1/…` routes (there is no `/admin/state`), the panel's token in
  `~/lumi/admin/secrets.yaml` (`admin.token`) and the secrets in `secrets.yaml` (not `secrets.env`).
- **The gallery's trash** said "removed for good in 28 days". Nothing is ever deleted (v4.5), and the line now says so.
- **A schedule save on prod** skipped the typed `prod` as a "reversible action". It is a plan like a settings change
  and keeps the typed confirmation (the v5 invariant); only moving to the trash and restoring use the window's simpler
  confirmations.
- **A fact correction in the audit** was drawn with the source `window`. The window doesn't edit facts; the panel does
  (v5.8), so its source is `panel`.

**Still to handle when implementing** (now in the ROADMAP's v5 phases):
- **Nothing loads from the network.** The prototype loads Inter from Google Fonts and the Phosphor icon font from
  unpkg. The panel inlines the icons as SVG and serves Inter's variable woff2 (with its OFL license) from
  `admin/static/`; the owner chose Inter on 2026-11-21.
- **State (v5.7)** shows what the window's State view shows:
  - add the theme of the day (its English name and scene from the window's `themes`) and the hormonal cycle (a fourth
    row, as in the window);
  - "resolved 07:12" is the mood's made-at time, which the window already shows;
  - Needs and Today's plans have no data until v6.5–v6.8 build them; until then each shows the window's line
    "Arrives in a later version."
- **History (v5.7):**
  - a message carries no channel today (`Message` has the role, the text, the time, the emotion, the intensity and the
    move). The channel (window, Telegram, TUI) is a new additive field, written from the client id's prefix
    (`X-Lumi-Client`) and by the Telegram inbox; older messages show none;
  - the search is a case-insensitive substring search over the last 30 days by default, and all time on "Search all
    time", one page at a time.
- **Summaries (v5.8):** a `ShortSummary` keeps no time span and no message count. "session 20:58 – 21:40 · covers 62
  messages" comes from that session's messages (the first and last time, the count).
- **Facts (v5.8):**
  - a fact's source line is its stored `meta`; no quote of the owner's words is kept;
  - "corrected 2026-11-18 → «…»" needs the correction to link the obsolete row to its replacement (a new additive
    field), or the audit's entry for it.
- **Thoughts (v5.9):** a `Thought` keeps its kind, emotion, `spoken` and `seeds`, not the schedule row that fired it.
  The origin line shows the seeds, and the row's name only once the scheduler records it.
- **Gallery (v5.9):** the route gives a picture's name, size, dimensions and time. The model comes from the v5.1 media
  ledger (an `image` row), "shown" from the `send_image` rows of `tools.jsonl`; a caption appears only if one is
  recorded.
- **Schedule (v5.5):**
  - the form's three trigger kinds cover `every` (with its optional `between` window), `idle` and `at` (with the
    days); `cron` is edited in "Advanced · YAML";
  - the scheduler's side panel shows the settings that exist: the quiet hours (`thoughts.quiet_hours`), the day cap
    (`scheduler.day_cap`) and the spoken ratio (`thoughts.spoken_ratio`). "Shown per day" and "min gap" are not
    settings.
- **The sample content** in the prototypes is illustrative: prod there runs `v4.7.0` and the Anthropic models, while
  prod today runs `v4.8.1` with the `gemini` profile.
