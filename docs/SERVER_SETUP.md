# Server & client — running Лілі as a server (v2.2–v2.5)

From v2.2 Лілі can run as a **server** (her brain: the core, her memory, the model calls) with the
**TUI as a client** talking to it over a local HTTP API, plus a small **CLI** to manage it. The in-process
TUI (`LUMI_SERVER=off`, the default) still works exactly as before — this is opt-in. The design is in
[ROADMAP §v2.2](../specification/ROADMAP.md) and [ARCHITECTURE §Contracts](../specification/ARCHITECTURE.md)
(the server API v1 + the command layer).

## The 30-second model

```
 TUI client  ──┐   Authorization: Bearer <LUMI_SERVER_TOKEN>
 CLI        ───┼──► http://127.0.0.1:<LUMI_SERVER_PORT>/v1/…  ──►  python -m server  ──►  core + data root
 (pywebview — v2.12)                                                (one core · one session · one request at a time)
```

- **The server owns everything stateful**: her memory (the data root), the model calls, the session. A
  client never opens the data root.
- **One instance = one server = one port + one token.** prod and dev each run their own.
- **Streamed turns (v2.3).** With `model.stream: true` in the instance's `settings.yaml` (env `LUMI_STREAM`), the reply grows token by token in
  the client and the think-box fills live, as in the in-process TUI.
- **The server speaks first (v2.4).** Every client also listens on a push channel: the status line follows
  the server wherever a change came from, and a thought she has out loud reaches every client unasked.
- **Her time lives in the server (v2.5).** The scheduler, the idle think / nudge and the Telegram bridge run
  inside `./lumi-server` — with no client attached. The TUI is a window: open it and it shows what happened
  meanwhile.

## Setup (once per instance)

1. Install the extra: `uv sync --all-extras` (or `--extra server`).
2. In the instance's settings (v2.9 — dev: `~/development/lumi/settings.yaml` + `secrets.yaml`; prod:
   `~/lumi/prod/…`; the examples are `settings.example.yaml` / `secrets.example.yaml`):

   ```yaml
   # settings.yaml
   server:
     port: 8765                 # prod 8765 · dev 8766 — never the same port for both
     # host: 127.0.0.1          # localhost (the default); on the Linux box its Tailscale/LAN IP —
                                # docs/LINUX_DEPLOY.md (never 0.0.0.0 or a public address: refused)
   # secrets.yaml (chmod 600)
   server:
     token: <a long random string>   # python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Or `./lumi-cli config set server.port 8765` and `./lumi-cli config set server.token <…>` — validated, written
   to the right file, never echoed. The token is a secret: never commit it, never paste it into logs or chats. The
   server refuses to start without one.

   > **`.env` is deprecated in v2.9 and removed in v2.10.** A tree that still has one keeps working (it is read
   > below the YAML files, with a warning at start-up); migrate it once with
   > `uv run python scripts/env_to_settings.py .env` (a dry run) → `--apply` (writes both files, keeps
   > `.env.migrated`).

## Run (three processes)

```bash
./lumi-server                     # the server (Ctrl+C stops it, closing the session) — = python -m cli serve
./lumi-client                     # the TUI as a client of that server (= LUMI_SERVER=on ./lumi)
./lumi-cli status                 # what it's running: env, version, model, session, turns, mood, theme
```

The launchers `cd` to their own checkout, so the right settings are read wherever you call them from
(prod: `~/lumi/prod/app/lumi-server`, dev: `~/development/lumi/lumi-server`). **One brain per memory:**
`./lumi` (in-process) and `./lumi-server` can't run on the same instance at once — the second one refuses
("another brain already runs on this data root…"); talk to a running server with `./lumi-client`.

## The CLI

One-shot commands (not a live monitor): each asks the server once, prints, exits. `./lumi-cli` is
`uv run python -m cli` from its own checkout.

| Command | Does |
|---|---|
| `./lumi-cli serve` | run the server |
| `./lumi-cli status` | health + state (env, version, model/profile, session, turns, mood, theme) |
| `./lumi-cli memory show` | what she remembers about you (`/memory`) |
| `./lumi-cli memory clear` | asks `[y/N]`, then clears (`/forget`) |
| `./lumi-cli model [name]` | show / switch the engine (`/model`) |
| `./lumi-cli model-set [profile]` | show / switch the model profile (`/model-set`) |
| `./lumi-cli config [show]` | the instance's effective config, every key/token masked |
| `./lumi-cli config get <key>` | one setting (`memory.window` or its env name `LUMI_MEMORY_WINDOW`): its value and where it comes from (default / `.env` / `settings.yaml` / `secrets.yaml` / env); a secret shows `•••set•••` |
| `./lumi-cli config set <key> <value>` | validated against the schema (a wrong value is refused with the allowed ones), written into `settings.yaml` — or `secrets.yaml` (`0600`) for a secret — changing only that line; applies after a restart |
| `./lumi-cli config validate` | checks both files completely: one line per problem, exit 1 |

`config` works on the files directly (no server needed); `--dir <folder>` points it at another instance's settings
folder (the box: [LINUX_DEPLOY §2b](LINUX_DEPLOY.md#2b-settings-as-files-v29)).
| `./lumi-cli think [topic]` | she thinks out loud (`%think! [topic]`) — printed here **and** shown in every connected client |

Errors are one readable line and exit code 1 (server down, token rejected, busy).

## Streaming and what happens when it breaks (v2.3)

The client streams whenever the **server** streams (`model.stream` in the server's settings); otherwise every
turn is blocking, as in v2.2. Each turn carries its own id. If the stream breaks mid-answer — the server
restarts, the connection drops, a frame arrives garbled — the client asks for **that same turn** the
blocking way and shows its final text: Лілі never answers twice, and nothing you sent is sent again. If the
server is gone entirely you get one `⚠ server unreachable …` line; start it again and the next turn streams.

## What the client hears without asking (v2.4)

`./lumi-client` keeps one long-lived connection to the server's push channel (`/v1/events`) besides its
requests. Over it arrive:

- **state on change** — model/profile, style, mood, theme, closeness, the session, the token stats. Change
  something from another terminal (`./lumi-cli model …`, `./lumi-cli model-set …`) and the TUI's status line
  updates by itself;
- **thoughts out loud** — a `%think!` (or any open `%directive`) fired from anywhere shows in every client
  as `💭 …`; the client that fired it shows it once, from its own answer;
- **her face state after every turn** — `{emotion, intensity, theme}` — for the face clients to come (v2.12).
  Only a theme *name* crosses the API, never a face file.

**Typed `%directives` work in client mode again** — they run on the server (`%think!`, `%wonder`,
`%note`, …, as in-process), one at a time like a turn.

If the server stops or the connection drops, the client says it once — `⚠ lost the server's pushes —
reconnecting…` — and keeps retrying (1 s, then doubling up to 30 s; any turn or command that gets through
retries at once). When the server is back: `✓ the server's pushes are back`, and the status line resyncs.
Stopping the server with Ctrl+C no longer waits for connected clients to leave.

## Her time on the server (v2.5)

`./lumi-server` hosts what the in-process TUI used to run on its timers, from the same settings:

- **the scheduler** (`scheduler.enabled`, `core/schedule.yaml`): her scheduled acts fire on time (with the
  startup catch-up, the day caps and quiet hours as before); a shown thought is pushed as `💭`, a thought that
  turns into words is a message from her;
- **the idle think / nudge** when the scheduler is off (`LUMI_THOUGHTS`, `LUMI_IDLE_NUDGE`);
- **the Telegram bridge** (`LUMI_BRIDGE`): run the two daemons beside the server — same data root, no TUI:

```bash
./lumi-server                 # the brain
./lumi-telegram-inbound       # Telegram → inbox
./lumi-telegram-outbound      # outbox → Telegram
./lumi-client                 # optional — a window onto her
```

**When you open the client** it shows what happened while it was closed — the recent conversation (Telegram
turns, what she said first, her shown thoughts), introduced by `· while you were away ·`, then everything
live. A dropped connection picks up where it left off (nothing shows twice); a server restart starts fresh
(a new session). Your own lines from another window appear too.

**One at a time, still:** while she runs one of her own acts (a long news read, say), your message waits for
it instead of bouncing as busy.

**A quick check without waiting an hour** — point the server at a test schedule:

```bash
cat > /tmp/lumi-quick.yaml <<'YAML'
schedule:
  - directive: think
    every: "2m"
    show: true
YAML
LUMI_SCHEDULE_PATH=/tmp/lumi-quick.yaml LUMI_SCHED_SHOW_TO_OUTBOX=on ./lumi-server
```

Within two minutes a thought arrives in Telegram (and in the client, if one is open). Restart without the
variables to go back to her real schedule.

## The desktop window (v2.12)

`./lumi-desktop` opens Лілі in one native window (pywebview — the first run installs the `desktop` extra). It is a
**client** like `./lumi-client`: it reads the same files of this tree — `server.host` / `server.port` in
`settings.yaml`, `server.token` in `secrets.yaml` — and talks to that server; it holds no core. Both clients can run
at once and show the same conversation (a line typed in one appears in the other, tagged "Terminal").

**Why a local proxy.** The page in the window can't call the server directly: the server sends no CORS headers. So
the window's Python shell starts a small server on `127.0.0.1` (a free port, `desktop/proxy.py`) that serves the page
(`desktop/ui/`) and forwards the `/v1` routes it uses (eleven since v4.1, thirteen since v4.2, sixteen since v4.3, eighteen since v4.4, twenty-three since v4.5), adding the token itself — the token never enters the
page. Every `/v1` request must carry this launch's random key (templated into the page), and the `Host` must be the
proxy's own, so another app or a web page on the Mac can't borrow the token. No server change.

**v2.12.1 (the chat):** streamed replies, her unasked lines ("wrote first"), lines from Telegram and the terminal,
slash commands, `%directives`, `/new`, `/web`, a queue while she answers, the status line (DEV when not prod; the
details panel; text size ⌘− / ⌘+), and a clean reconnect: the server down → a banner with a countdown, back → the
missed lines under "While offline". The interface is English; her words stay Ukrainian.

**v2.12.2 (the face & state panel):** her portrait beside the chat changes with each reply's emotion — the file chosen
by intensity from **this client's own face pack** (`viewer.faces_dir`, default `viewer/faces`; the window's proxy
serves it on `127.0.0.1`, no face crosses the network) — with the caption, the theme of the day, the closeness meter
and the mood of the day. While she thinks, her inner voice streams into a block above her reply (Details → Inner
voice: show / collapse / hide). Her quiet thoughts gather in a strip under the mood, never in the chat. Below 760 px
the panel becomes a strip at the top. Voice, dictation and `/image` from a file stay in the TUI (her pictures reach
the window since v4.2).

**v4.1 (the window's views: Files and the reader).** A row of views sits over the chat: **Chat · Files** (⌘1 / ⌘2;
later versions add Gallery, Journal, Thoughts, State and Schedule). A view replaces the chat column, and the chat keeps
receiving underneath: a reply that arrives meanwhile marks the Chat tab.
- **Files** browses her sandbox read-only: folders first, sizes, dates, back / forward (⌘[ ⌘]). A file of a kind the
  window doesn't show is listed but can't be opened.
- **The reader** shows a markdown file as a formatted page, with an outline for long pages and a Formatted / Raw switch.
  Her relative links open her other files in the reader, and her relative pictures show in the text. An external link
  is shown with its address and never opened in the window.
- **Any view but Chat can be pinned** to a right panel or a bottom-right panel beside the chat. The column is
  resizable and splits between two panels; ⌘\ folds it to a rail. The layout is kept per window, in its own storage.

**v4.1 (her files — the server's side).** Three read-only routes serve her sandbox (`files/<user_id>/` in the data
root) to the owner's token, never the room's:
- `GET /v1/files?path=` lists a folder;
- `GET /v1/files/text?path=` returns a text file (at most 256 KB);
- `GET /v1/files/raw?path=` returns a picture (png/jpg/gif/webp, at most 10 MB; never SVG or HTML).

Nothing in the sandbox changes, no model is called, and a read never waits for her turn.

**v4.2 (the window: Gallery and her pictures in the chat).** **Gallery** (⌘3) shows every picture of her sandbox as
thumbnails, newest first, by month, with a folder filter; it loads more as you scroll. A click opens the **full picture
view**: the picture as large as fits, its path, date, size and dimensions, "Open in Files", ← → for the next and
previous, Esc to close. A picture in Files or in the reader opens the same view. When she makes a picture
(`generate_image`) or sends one (`send_image`) in a turn, it shows **under her line in the chat**, whichever client or
Telegram started the turn; a click opens it full size. A picture she sends still reaches Telegram as before.

**v4.2 (her pictures — the server's side).** Two more read-only routes, the owner's token only:
- `GET /v1/images?before=&limit=&folder=` pages through every picture of the sandbox, newest first (100 a page), with
  the total and the count per folder;
- `GET /v1/images/thumb?path=` returns a 320 px JPEG thumbnail.

The thumbnails are made on the first request and kept in `cache/thumbs/<user_id>/` in the data root — outside her
sandbox, so her file tools never see them. The cache is small (about 20 KB a picture), is rebuilt on its own when a
picture changes, and may be deleted at any time; it is inside the data archive the deploy agent takes before a prod
deploy. A turn's outcome and her `message` push carry `pictures` (the sandbox paths) when she made or sent one in that
turn; `GET /v1/state.features.gallery` says the Gallery is served.

**v4.3 (the window: Journal and Thoughts).** **Journal** (⌘4) lists her journal's days by month, newest first, beside
the day: the stamp of the day (mood, biorhythms, forecast) as its own block, then each `HH:MM` entry in her words. A
day written before she kept a stamp says so. ← → step through the days, and the narrow layout (a pinned panel, a
phone) shows the list as its own screen. **Thoughts** (⌘5) is her whole thought stream, newest first, a hundred at a
time as you scroll, back to her first thought, with a header ("5,589 · about 34 a day · 6% spoken") and three filters:
the kinds (several at once), spoken or silent, and a date to jump to. Each day's divider carries the day's count. The
strip of quiet thoughts beside the portrait stays as it was, and its header gains "Thoughts →".

**v4.3 (her journal and thoughts — the server's side).** Three more read-only routes:
- `GET /v1/journal` lists the days of her journal (`journal/<user_id>/` in the data root) and `GET /v1/journal/{date}`
  returns one day, parsed. The journal is **the owner's only**: the owner's token and a role check (today the owner's
  id; the accounts of v6.2 replace it), so a server for another user answers 403;
- `GET /v1/thoughts?before=&limit=&kind=&spoken=&date=` pages through her thoughts through the surfacing filter, so a
  thought sparked with another user never shows.

Nothing changes in the journal or the store, no model is called, and a read never waits for her turn.
`GET /v1/state.features` says `journal` (whenever a journal folder is configured, whether or not the journal tool is
on) and `thoughts` (unless `thoughts.show` is `off`).

**v4.4 (the window: State, Schedule and a reply's tools).** **State** (⌘6) shows her day: the mood of the day in full
(the theme, the resolution, the reading), the three biorhythms with their cycles drawn ±14 days around today, the
closeness level, and the place for the day's plan (later). The mood shows once today's is made; until then the view
says so. **Schedule** (⌘7) shows what wakes her thoughts: the quiet hours, the day cap and today's firings, and every
directive with its rule in English ("weekdays at 08:00", "after 15 min of silence"), when it last fired and when it is
next due — the next to fire marked, a disabled one dimmed. With `files.trace` on, the **details panel** lists the tools
she called in the window's last reply (name, a short input, a short result).

**v4.4 (her day and timetable — the server's side).** Two more read-only routes, the owner's token only:
- `GET /v1/today` — today's mood (only once it is made; the route never makes it), the biorhythms with their trend
  and place in the cycle, the closeness level;
- `GET /v1/schedule` — the running scheduler's view: every entry of `schedule.yaml` (with the settings' overrides),
  its last firing from `schedule.state`, its next due time by the scheduler's own rules (quiet hours veto everything
  but an `at`; a reached day cap waits for midnight). 404 while the scheduler is off.

While `files.trace` is on, a turn's outcome carries `tool_calls` (at most 20, each input and result one short line); with
it off the outcome is unchanged. `GET /v1/state.features` says `today` and `schedule`.

**v4.5 (the window: actions on her files).** Files gets **Upload** (a file picker, or drop files on the folder) and a
**⋯** menu on each row: **Download** (saved into `~/Downloads`, with «Show in Finder»), **Rename…** and **Move to
trash…**; inside `.trash/` a row offers **Restore…**. Rename, trash and restore ask first. The reader gets Download, and
its external links «Open in browser». The full picture view (from the Gallery, Files or the chat) gets the same
actions and **Show to Lili**: type a line and the picture goes to her with it as your turn; she answers about it, and
your line shows the picture.

**v4.5 (her files — the server's side).** Five owner-only routes over her sandbox, all through the sandbox guard:
- `POST /v1/files/upload?path=&name=` writes a **new** file: a name already taken gets `(2)`, nothing is overwritten.
  Pictures (by their bytes) up to 10 MB, text up to 1 MB, anything else refused.
- `POST /v1/files/rename`, `POST /v1/files/trash` (into `.trash/<date>/` inside her sandbox) and
  `POST /v1/files/restore` (back to where it came from). **Nothing is ever deleted.**
- `GET /v1/files/download?path=` gives any file up to 50 MB.

Every action is one line in `actions.jsonl` in the data root (what happened to which path — never the content). A turn
may carry `sandbox_images` (pictures of her sandbox by path, read by the server; refused while `image.enabled` is off).
The Gallery leaves out what is in the trash. `GET /v1/state.features` says `actions`. The window's proxy saves a
download into `~/Downloads` and opens an http(s) link in the default browser itself, behind its key.

**v4.6 (her voice — the server's side).** While the switch is on, the server voices each of her lines **once**,
after the line is shown: a reply in the window, a Telegram turn, a nudge, a spoken or shown thought. It uses the box's
ElevenLabs key and voice (`voice.elevenlabs_api_key`, `voice.voice_id`, `voice.model`), her voice tags and the
emotion's voice settings.
- The file goes to `<data root>/voice/<user_id>/<id>.mp3` and is removed after 30 days (checked at start and daily).
- Her outbox record names the file (`voice`), and the Telegram daemon sends that file instead of calling ElevenLabs
  again.
- `GET /v1/voice/<id>` gives the audio and `POST /v1/voice {enabled}` turns the switch. Both are owner-only; the room's
  token gets 403.
- The switch lives in `<data root>/voice.json` and survives a restart. Until it is first turned, it follows
  `telegram.voice`.
- Without a key or a voice id the switch is unavailable (`GET /v1/state.voice.available` false), and `POST /v1/voice`
  answers 409.
- With the switch off, no audio is made and Telegram gets text. A failed generation leaves the text everywhere.
- The server log has one line per generation: `voice: made <id> (<n> chars, <ms> ms)` or `voice: failed <id>`, never
  the text.

**As a Mac app (LUMI-270).** `uv run python scripts/make_mac_app.py` builds `~/Applications/Lili.app`, with her
portrait as the icon, which opens the window of the Mac prod client (`~/lumi/prod/app`; `--app-dir` for another
checkout, `--portrait` for another picture). It opens from Launchpad, Spotlight or the Dock, as one "Lili" tile. The
bundle holds no client code: it runs `uv run --extra desktop` in that checkout, so `prod_update.py` moving the client to
a new tag updates what the app runs. Rebuild it only to change the icon or the target. Its output goes to
`~/Library/Logs/Lili.log`.

| The window says | Why | Fix |
|---|---|---|
| "No server is set yet" | `server.host` or `server.token` missing in this tree | set them (`./lumi-cli config set server.host 192.168.1.197`, the port, and the token in `secrets.yaml`), then "Check" |
| "The server refused the token" | the instance's `server.token` changed (e.g. rotated in the admin panel) | put the new value into this tree's `secrets.yaml`, then "Check again" |
| "The server isn't answering" + countdown | the server is down or unreachable | start it (the admin panel, or the box); the window reconnects by itself |
| "the desktop window needs pywebview" | `python -m desktop` without the extra | use `./lumi-desktop` |

## Agora: the room as a space of her own (v3.1)

matrix-agora's bridge agent brings Лілі into Agora, the owner's Matrix room of agents. The room talks to this server
through its own channel, the **agora space API v2** ([`docs/agora-space-api.json`](agora-space-api.json)), and nothing
of it reaches the owner's side. The design is in
[AGORA_SPACE.md](../specification/features/AGORA_SPACE.md); the phases are in ROADMAP §v3.

**Turning it on (one instance at a time — dev first).**
1. Make the room's token and keep it out of argv and logs:
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` into the instance's `secrets.yaml` as
   `agora.token` (`0600`). It must differ from `server.token`.
2. In the instance's `settings.yaml`: `agora:` → `enabled: true`. The room's model is `agora.model`
   (`gemini-2.5-flash`), so `providers.gemini_api_key` must be set too.
3. Restart the server. `GET /v1/health` now shows `"agora": 2` (v3.4; `1` before). Without a room token, with the client token reused,
   or without a Gemini key, the server refuses to start (exit 78) and says which.

**What the room reaches.** With its token: `POST /v1/observe` (the room's lines she hears — stored, embedded, no
model call) and `POST /v1/turn` with `channel: "agora"` (she answers on her own Gemini client, one room turn at a time,
`agora.timeout_s` 20 s). Everything else answers `403`: `/v1/state`, `/v1/events`, `/v1/command`, `/v1/directive`,
`/v1/session/new`, `/v1/turn/stream`, and a turn of any other channel. The owner's token keeps its full reach.

**What the room never touches.** Her answers in the room never reach his Telegram, the voicer, his desktop window,
the TUI's event stream, `/v1/state`, his totals or the idle clock; a room turn never waits for his turn, nor his for
the room's. The room's lines live in `<data root>/spaces.sqlite`, a file of its own (deleting it forgets the room
whole); his memory files never hold a room line. The room's model calls show in `cache-log.jsonl` as `kind: "agora"`.

**What she remembers of the room (v3.2).**
- **In the room** she speaks from:
  - her canon;
  - the room rule (`core/agora_room.md`: the owner is «Ich» there, nothing of their private talks);
  - her mood of the day;
  - the room's own past: its day gists, today's running digest and recall over the room alone.

  None of the owner's memory enters the room's prompt. His real names (`agora.private_names` — set them) read «Ich»
  there, and a reply that still names him is a silent `pass`.
- **With the owner** she knows the room:
  - «Агора сьогодні» at the end of her prompt, on a day the room spoke;
  - the room's lines in her recall, labelled «в Агорі, Ада: …»;
  - each day's gist inside her day and week digests.

  The digest and the gists are written by the room's own client every minute (`kind: "agora"` in the cache log).

| Answer | Meaning in the room |
|---|---|
| `200` `outcome: "reply"` | her reply — the bridge posts it as is |
| `200` `outcome: "pass"` | silence: an empty reply, Gemini's blocked «…», or a reply that named the owner; nothing stored |
| `POST /v1/turn/{turn_id}/retract` → `200 {retracted}` | the room took back her reply (its rules dropped it, its send failed, it timed out): gone from her memory of the room; always a 200 |
| `409` | a room turn is already running — silence |
| `422` | a malformed call: a speaker outside 1–64 characters or with `:`, a line without `id`, a `ts` without its offset, over 200 lines, a `register` other than `voice` |
| `429` | the room's hourly cap (`agora.max_turns_per_hour`, 20) — silence |
| `502` / `504` | the room's model failed / ran past `agora.timeout_s` — silence |

**Her initiative (v3.4).** She may open a line in the room herself. Lumi only offers; the bridge decides whether to
post, under its room rules (a quiet stretch, its hours, its day's cap, never twice in a row).
- **Turning it on:** `agora.initiative: true` in the instance's `settings.yaml`, then restart. Off (the default),
  every poll answers `none` and nothing is composed; health still says `"agora": 2`.
- **The poll:** `GET /v1/initiative` (the room's token). It never calls the model:
  - a fresh offer → `{outcome: "say", initiative_id, text, stats}`;
  - otherwise `{outcome: "none"}`, and when Lumi's own guards allow, one compose starts in the background on the
    room's client, for a later poll.
- **Lumi's guards** (code constants, the contract's `limits`):
  - one compose at a time, at most one attempt in 10 minutes (an empty one too; 30 minutes before v3.4.1);
  - never when her line is the room's last;
  - every compose counts in `agora.max_turns_per_hour`;
  - an answer that names the owner or carries «Ich» is no offer.
- **An offer** is fresh for 30 minutes, and stale at once when a newer room line arrives. It is kept a day for a
  late ack.
- **The ack:** `POST /v1/initiative/{initiative_id}/posted {event_id}` (the room's token). Only now is the text her
  line in the room's memory. `{stored: true}`, or `{stored: false}` for an unknown or already stored one; always a
  200. An offer never acked is never remembered.
- The owner's token gets `403` on both routes. Each compose shows in `cache-log.jsonl` as `kind: "agora"`, and the
  log says «initiative composed» or «initiative held: <reason>».

## What client mode doesn't do yet

The **voice mode** (`/mode-set voice`), the **voicer** and **dictation** need the local mic and speakers next
to the core — they stay in the in-process TUI (revisited after v2.6); in client mode they're switched off and
the TUI says "not yet in server mode".

`/image` and `/web` follow the **server's** settings (`image.enabled`, `web.enabled`) from v2.6 — the server may be
another machine, so the client asks it instead of reading its own.

`LUMI_SERVER=off ./lumi` always gives you the full in-process Лілі.

**On another machine (v2.6).** The server can run on the always-on Linux box as systemd services, the Mac TUI a
client over LAN/Tailscale: [LINUX_DEPLOY.md](LINUX_DEPLOY.md).

## When something's wrong

| You see | Meaning | Fix |
|---|---|---|
| `server unreachable at http://127.0.0.1:8765` | no server on that port | start it: `./lumi-server` (or check `LUMI_SERVER_PORT`) |
| `the server rejected the token` | client and server tokens differ | use the same `server.token` (`secrets.yaml`) |
| `Лілі is busy with another request` | one request at a time (e.g. the CLI ran during a turn) | try again |
| `LUMI_SERVER_TOKEN is not set` | no `server.token` in `secrets.yaml` | add one (Setup step 2) |
| `Lumi settings — refusing to start: … is not valid — allowed: …` | a wrong value in a settings file | fix that key — `./lumi-cli config validate` lists every problem |
| `Port 8766 … is already in use by another server` (server start, exit 78) | another process holds the port (the line names the finder: `lsof -nP -iTCP:<port> -sTCP:LISTEN` on the Mac, `ss -ltnp 'sport = :<port>'` on Linux) | stop it, or give this instance its own `LUMI_SERVER_PORT` (and its clients the same) |
| `LUMI_SERVER_HOST=… listens on every interface` / `is a public address` / `is a hostname` | the server binds LAN/Tailscale only (v2.6) | use `127.0.0.1`, a LAN IP or the Tailscale IP |
| `another brain already runs on this data root` | `./lumi` or `./lumi-server` already runs on this instance | stop it — or, for a running server, use `./lumi-client` |

Logs: the server writes `<data root>/lumi.log` (as the in-process TUI did); a client writes
`~/.cache/lumi/client-<env>.log` (never into the data root).
