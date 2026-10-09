# Server & client — running Лілі as a server (v2.2)

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
- **Blocking turns.** Streaming over the socket arrives in v2.3; pushes and proactive turns in v2.4.

## Setup (once per instance)

1. Install the extra: `uv sync --all-extras` (or `--extra server`).
2. In the instance's `.env` (dev: `~/development/lumi/.env`; prod: `~/lumi/prod/.env`):

   ```bash
   LUMI_SERVER_TOKEN=<a long random string>   # python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   LUMI_SERVER_PORT=8765                      # prod 8765 · dev 8766 — never the same port for both
   # LUMI_SERVER_HOST=127.0.0.1               # localhost only (the default) — no exposure until v2.6
   ```

   The token is a secret: never commit it, never paste it into logs or chats. The server refuses to start
   without one.

## Run (three processes)

```bash
./lumi-server                     # the server (Ctrl+C stops it, closing the session) — = python -m cli serve
./lumi-client                     # the TUI as a client of that server (= LUMI_SERVER=on ./lumi)
uv run python -m cli status       # what it's running: env, version, model, session, turns, mood, theme
```

The launchers `cd` to their own checkout, so the right `.env` is read wherever you call them from
(prod: `~/lumi/prod/app/lumi-server`, dev: `~/development/lumi/lumi-server`). **Never run `./lumi`
(in-process) and `./lumi-server` on the same instance at once** — two brains on one memory.

## The CLI

| Command | Does |
|---|---|
| `python -m cli serve` | run the server |
| `python -m cli status` | health + state (env, version, model/profile, session, turns, mood, theme) |
| `python -m cli memory show` | what she remembers about you (`/memory`) |
| `python -m cli memory clear` | asks `[y/N]`, then clears (`/forget`) |
| `python -m cli model [name]` | show / switch the engine (`/model`) |
| `python -m cli model-set [profile]` | show / switch the model profile (`/model-set`) |
| `python -m cli config` | the instance's effective config, every key/token masked |

Errors are one readable line and exit code 1 (server down, token rejected, busy).

## What client mode doesn't do yet

The server in v2.2 is a skeleton: it answers turns and commands. These stay in the **in-process** TUI
until the brain moves into the server (**v2.5**) — in client mode they're switched off and the TUI says
"not yet in server mode":

- the **thought-stream** — idle thoughts, the scheduler, typed `%directives`;
- the **Telegram bridge** — keep running Telegram against the in-process instance meanwhile;
- the **voice mode** (`/mode-set voice`), the **voicer** and **dictation** (they need the local mic and
  speakers next to the core — revisited after v2.6).

`LUMI_SERVER=off ./lumi` always gives you the full in-process Лілі.

## When something's wrong

| You see | Meaning | Fix |
|---|---|---|
| `server unreachable at http://127.0.0.1:8765` | no server on that port | start it: `./lumi-server` (or check `LUMI_SERVER_PORT`) |
| `the server rejected the token` | client and server `.env` differ | use the same `LUMI_SERVER_TOKEN` |
| `Лілі is busy with another request` | one request at a time (e.g. the CLI ran during a turn) | try again |
| `LUMI_SERVER_TOKEN is not set` | no token in `.env` | add one (Setup step 2) |
| `address already in use` (server start) | another server holds the port | stop it, or give this instance its own port |

Logs: the server writes `<data root>/lumi.log` (as the in-process TUI did); a client writes
`~/.cache/lumi/client-<env>.log` (never into the data root).
