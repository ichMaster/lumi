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
 (pywebview — v2.8)                                                 (one core · one session · one request at a time)
```

- **The server owns everything stateful**: her memory (the data root), the model calls, the session. A
  client never opens the data root.
- **One instance = one server = one port + one token.** prod and dev each run their own.
- **Streamed turns (v2.3).** With `LUMI_STREAM=on` in the instance's `.env`, the reply grows token by token in
  the client and the think-box fills live, as in the in-process TUI.
- **The server speaks first (v2.4).** Every client also listens on a push channel: the status line follows
  the server wherever a change came from, and a thought she has out loud reaches every client unasked.
- **Her time lives in the server (v2.5).** The scheduler, the idle think / nudge and the Telegram bridge run
  inside `./lumi-server` — with no client attached. The TUI is a window: open it and it shows what happened
  meanwhile.

## Setup (once per instance)

1. Install the extra: `uv sync --all-extras` (or `--extra server`).
2. In the instance's `.env` (dev: `~/development/lumi/.env`; prod: `~/lumi/prod/.env`):

   ```bash
   LUMI_SERVER_TOKEN=<a long random string>   # python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   LUMI_SERVER_PORT=8765                      # prod 8765 · dev 8766 — never the same port for both
   # LUMI_SERVER_HOST=127.0.0.1               # localhost (the default); on the Linux box its Tailscale/LAN IP —
                                              # docs/LINUX_DEPLOY.md (never 0.0.0.0 or a public address: refused)
   ```

   The token is a secret: never commit it, never paste it into logs or chats. The server refuses to start
   without one.

## Run (three processes)

```bash
./lumi-server                     # the server (Ctrl+C stops it, closing the session) — = python -m cli serve
./lumi-client                     # the TUI as a client of that server (= LUMI_SERVER=on ./lumi)
./lumi-cli status                 # what it's running: env, version, model, session, turns, mood, theme
```

The launchers `cd` to their own checkout, so the right `.env` is read wherever you call them from
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
| `./lumi-cli config` | the instance's effective config, every key/token masked |
| `./lumi-cli think [topic]` | she thinks out loud (`%think! [topic]`) — printed here **and** shown in every connected client |

Errors are one readable line and exit code 1 (server down, token rejected, busy).

## Streaming and what happens when it breaks (v2.3)

The client streams whenever the **server** streams (`LUMI_STREAM` in the server's `.env`); otherwise every
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
- **her face state after every turn** — `{emotion, intensity, theme}` — for the face clients to come (v2.8).
  Only a theme *name* crosses the API, never a face file.

**Typed `%directives` work in client mode again** — they run on the server (`%think!`, `%wonder`,
`%note`, …, as in-process), one at a time like a turn.

If the server stops or the connection drops, the client says it once — `⚠ lost the server's pushes —
reconnecting…` — and keeps retrying (1 s, then doubling up to 30 s; any turn or command that gets through
retries at once). When the server is back: `✓ the server's pushes are back`, and the status line resyncs.
Stopping the server with Ctrl+C no longer waits for connected clients to leave.

## Her time on the server (v2.5)

`./lumi-server` hosts what the in-process TUI used to run on its timers, from the same `.env`:

- **the scheduler** (`LUMI_SCHEDULER`, `core/schedule.toml`): her scheduled acts fire on time (with the
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
cat > /tmp/lumi-quick.toml <<'TOML'
[[schedule]]
directive = "think"
every = "2m"
show = true
TOML
LUMI_SCHEDULE_PATH=/tmp/lumi-quick.toml LUMI_SCHED_SHOW_TO_OUTBOX=on ./lumi-server
```

Within two minutes a thought arrives in Telegram (and in the client, if one is open). Restart without the
variables to go back to her real schedule.

## What client mode doesn't do yet

The **voice mode** (`/mode-set voice`), the **voicer** and **dictation** need the local mic and speakers next
to the core — they stay in the in-process TUI (revisited after v2.6); in client mode they're switched off and
the TUI says "not yet in server mode".

`/image` and `/web` follow the **server's** `.env` (`LUMI_IMAGE`, `LUMI_WEB_LOOKUP`) from v2.6 — the server may be
another machine, so the client asks it instead of reading its own.

`LUMI_SERVER=off ./lumi` always gives you the full in-process Лілі.

**On another machine (v2.6).** The server can run on the always-on Linux box as systemd services, the Mac TUI a
client over LAN/Tailscale: [LINUX_DEPLOY.md](LINUX_DEPLOY.md).

## When something's wrong

| You see | Meaning | Fix |
|---|---|---|
| `server unreachable at http://127.0.0.1:8765` | no server on that port | start it: `./lumi-server` (or check `LUMI_SERVER_PORT`) |
| `the server rejected the token` | client and server `.env` differ | use the same `LUMI_SERVER_TOKEN` |
| `Лілі is busy with another request` | one request at a time (e.g. the CLI ran during a turn) | try again |
| `LUMI_SERVER_TOKEN is not set` | no token in `.env` | add one (Setup step 2) |
| `Port 8766 … is already in use by another server` (server start, exit 78) | another process holds the port (the line names the finder: `lsof -nP -iTCP:<port> -sTCP:LISTEN` on the Mac, `ss -ltnp 'sport = :<port>'` on Linux) | stop it, or give this instance its own `LUMI_SERVER_PORT` (and its clients the same) |
| `LUMI_SERVER_HOST=… listens on every interface` / `is a public address` / `is a hostname` | the server binds LAN/Tailscale only (v2.6) | use `127.0.0.1`, a LAN IP or the Tailscale IP |
| `another brain already runs on this data root` | `./lumi` or `./lumi-server` already runs on this instance | stop it — or, for a running server, use `./lumi-client` |

Logs: the server writes `<data root>/lumi.log` (as the in-process TUI did); a client writes
`~/.cache/lumi/client-<env>.log` (never into the data root).
