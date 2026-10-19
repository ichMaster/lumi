# Linux deploy — Лілі always on (v2.6)

Run Лілі's server on the always-on home **Linux box** as systemd services, and talk to her from the Mac over
**LAN/Tailscale**. This is the operator's guide: install, run, check, monitor, fix. The design is in
[ROADMAP §v2.6](../specification/ROADMAP.md) and [ARCHITECTURE §Deployment and CI/CD](../specification/ARCHITECTURE.md);
the server itself in [SERVER_SETUP.md](SERVER_SETUP.md), the bridge in [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md).

## The 30-second model

```
Mac (a client only)                          Linux box (always on) — 192.168.1.197 (ssh: slate-server)
./lumi-client ─┐                             ~/lumi/dev/{app,.env,data}
./lumi-cli    ─┴── LAN (ufw: 192.168.1.0/24) ► lumi-server@dev  + lumi-telegram-inbound@dev / -outbound@dev
                                             ~/lumi/prod/{app,.env,data}
~/lumi/prod (v2.1.1, her real memory)        lumi-server@prod — installed, STOPPED, data root EMPTY
  keeps running on the Mac until v2.7
```

- Each instance on the box is **three user services** from the repo's templates (`deploy/systemd/`): the server
  and the two Telegram daemons, `%i` = `prod` | `dev`. Each instance has its own checkout, `.env`, data root,
  port, token and Telegram bot — the v2.1 split, carried to the box.
- **In v2.6 only the dev instance runs on the box.** Her real memory stays on the Mac prod until the one-time
  migration in **v2.7**; the box's prod unit is installed and left stopped with an empty data root.
- **LAN/Tailscale only.** The server refuses to listen on `0.0.0.0`, `::`, a public address or a hostname — only
  loopback, a LAN (RFC 1918) or a Tailscale address (100.64.0.0/10). No TLS yet (v4.1): never port-forward it.
- **The same box runs matrix-agora** (Matrix on :8008, its panel on :8090) and Lumi follows its pattern: bind the
  box's **LAN address** and let **ufw** admit the home subnet only. On the LAN the Mac needs no Tailscale, and the
  agora containers on the box can reach her too (they can't reach 127.0.0.1). Tailscale is optional, for away
  from home.
- The Mac never touches the box's disk: the client reads everything through the API.

---

## Setup

### 0. Prerequisites (once)

The box's **LAN address** is what the server binds and the Mac dials — here `192.168.1.197` (keep it fixed: a
DHCP reservation on the router). Tailscale is optional: with it on **both** machines you may bind the box's
Tailscale address instead (`tailscale ip -4`, 100.x.y.z) — one address per instance, the client dials the same one.

On the **box**:

```bash
sudo apt install git curl ffmpeg                        # ffmpeg: her Telegram voice replies (LUMI_TELEGRAM_VOICE)
curl -LsSf https://astral.sh/uv/install.sh | sh         # uv → ~/.local/bin (the units' PATH has it)
sudo loginctl enable-linger "$USER"                     # user services run without a login, start at boot
```

uv fetches the Python the project needs (≥ 3.11) on the first sync.

### 1. Pick two free ports (and keep them)

The defaults are **prod 8765 · dev 8766** — one port per instance, never the same for both. Before installing,
check that nothing on the box already listens there:

```bash
ss -ltnp | grep -E ':(8765|8766)\b'      # prints nothing → both are free
```

If anything shows up, pick another free pair (e.g. 8775 / 8776), check it the same way, and use it everywhere
below — in the instance's `.env` **and** in the Mac client's `.env`.

The server checks again at every start: a port another service holds is refused with the culprit's hint
(`ss -ltnp 'sport = :<port>'`), exit **78** — the unit stays `failed` instead of restarting every few seconds.

**Open the ports to the home subnet** (ufw is active on the box — without this rule the Mac's connection just
times out, while the agora ports answer):

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8765:8766 proto tcp
sudo ufw status | grep -E '8765|8008'       # 8765:8766/tcp ALLOW 192.168.1.0/24 — beside agora's rules
```

### 2. Install the dev instance

```bash
mkdir -p ~/lumi/dev && cd ~/lumi/dev
git clone https://github.com/ichMaster/lumi.git app
git -C app checkout v2-dev                         # the dev line (v2.7 will deploy it by pipeline)
(cd app && uv sync --frozen --all-extras)          # app/.venv, exactly the lock
cp app/.env.example .env && chmod 600 .env         # then edit — below
```

In `~/lumi/dev/.env` — copy your keys and settings over from the Mac dev `.env`, then set:

```bash
LUMI_HOME=~/lumi/dev/data
LUMI_ENV=dev
LUMI_SERVER_HOST=192.168.1.197        # the box's LAN IP (or its Tailscale IP) — never 0.0.0.0, never public
LUMI_SERVER_PORT=8766                 # from step 1
LUMI_SERVER_TOKEN=<a new random one>  # python3 -c "import secrets; print(secrets.token_urlsafe(32))"
LUMI_BRIDGE=on                        # the Telegram bridge beside the server
LUMI_TELEGRAM_TOKEN=<the DEV bot's token>
LUMI_TELEGRAM_ALLOWLIST=<your Telegram id>
```

**Delete every per-path override** you copied (`LUMI_STORE_PATH`, `LUMI_FILES_DIR`, `LUMI_JOURNAL_DIR`,
`LUMI_INBOX_PATH`, `LUMI_OUTBOX_PATH`, `LUMI_LISTEN_FLAG`) — `LUMI_HOME` places them all. Check:
`grep -nE '^LUMI_[A-Z_]*(PATH|DIR|FLAG)=' ~/lumi/dev/.env` prints nothing.

For the "scheduled thought" check later, turn the scheduler on as in
[SERVER_SETUP §Her time on the server](SERVER_SETUP.md) (its quick-check schedule recipe).

### 3. Install the prod instance — but don't start it

```bash
mkdir -p ~/lumi/prod && cd ~/lumi/prod
git clone https://github.com/ichMaster/lumi.git app
git -C app checkout "$(git -C app tag --sort=-v:refname | head -1)"   # the latest release tag — prod only runs a tag
(cd app && uv sync --frozen --all-extras)
cp app/.env.example .env && chmod 600 .env
mkdir -p ~/lumi/prod/data                          # stays EMPTY until the v2.7 migration
```

In `~/lumi/prod/.env`: `LUMI_HOME=~/lumi/prod/data`, `LUMI_ENV=prod`, the same `LUMI_SERVER_HOST`, its own
`LUMI_SERVER_PORT=8765` and its own `LUMI_SERVER_TOKEN`. The prod bot and her real keys come with the migration.

**Do not start `lumi-server@prod` now.** The first start marks the data root `prod` and would begin a blank
Лілі there; the v2.7 migration copies her memory in first.

### 4. Install the units

The templates are the same for both instances; copy them from either checkout:

```bash
mkdir -p ~/.config/systemd/user
cp ~/lumi/dev/app/deploy/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lumi-server@dev
systemctl --user enable --now lumi-telegram-inbound@dev lumi-telegram-outbound@dev
systemctl --user status lumi-server@dev            # active (running)
```

The Telegram daemons are `PartOf` their server: they start with it and stop with it. Nothing is enabled for
`prod` — `systemctl --user is-active lumi-server@prod` says `inactive`, and that's the v2.6 state.

When `deploy/systemd/` changes, copy the files again and `systemctl --user daemon-reload`.

### 5. One Telegram consumer per bot

A bot token can be polled by **one** process. Before the box's dev daemons start, **stop the Mac's dev Telegram
daemons** (the dev bot) — two pollers fight over the updates. The Mac prod keeps its prod bot untouched.

### 6. The Mac client

In the `.env` the Mac client reads (the dev tree's `~/development/lumi/.env`):

```bash
LUMI_SERVER_HOST=192.168.1.197        # the box — the address its server binds (LAN, or Tailscale if you bound that)
LUMI_SERVER_PORT=8766                 # the box's dev port
LUMI_SERVER_TOKEN=<the box's dev token>
```

```bash
./lumi-client                         # the TUI, talking to the box
./lumi-cli status                     # env, version, model, session, mood, theme — from the box
```

After the first turn, `/latency` ends with `NETWORK client↔server: last … ms · median … ms` — the time the network
adds, separate from her PRE / MODEL / POST. `/image` and `/web` follow what the **box** has on.

On the Mac the same `.env` also configures a local `./lumi-server`: with `LUMI_SERVER_HOST` naming the box, a
local server refuses to start ("not an address of this machine") — set it back to `127.0.0.1` for local work.

---

## Check it (the v2.6 DoD)

On the box's **dev** instance:

1. **A full conversation** from the Mac TUI to the box (a few turns, a `/mood`, a `/style`).
2. **A scheduled thought reaches Telegram with no client connected anywhere** — close every client, wait for a
   scheduled row (the quick-check recipe), see it in Telegram.
3. **A reconnect resumes cleanly** — close and reopen `./lumi-client`: `· while you were away ·` shows what
   happened, nothing twice.
4. **Killing the client changes nothing server-side** — `kill` the client mid-session; the server keeps running
   and keeps firing its acts (`journalctl` / `lumi.log`).
5. **`systemctl --user stop lumi-server@dev` closes the session** (a summary lands, exactly like Ctrl+C), and
   `systemctl --user start lumi-server@dev` brings her back.
6. **Prod is installed and idle** — `~/.config/systemd/user/lumi-server@.service` present,
   `lumi-server@prod` inactive, `~/lumi/prod/data` empty.

---

## Monitoring

```bash
systemctl --user status 'lumi-*@dev'                     # the three services at a glance
journalctl --user -u lumi-server@dev -f                  # its start line, refusals, crashes
tail -f ~/lumi/dev/data/lumi.log                         # her log (turns, thoughts, mood, the scheduler)
tail -f ~/lumi/dev/data/telegram-inbound.log ~/lumi/dev/data/telegram-outbound.log
```

From the Mac:

```bash
curl -s http://192.168.1.197:8766/v1/health             # {"ok": true, "env": "dev", "version": "…"} — open, no token
./lumi-cli status                                        # the full picture, with the token
```

## Stop, restart, update (by hand until v2.7)

```bash
systemctl --user stop lumi-server@dev          # closes her session (summary + facts); the daemons stop too
systemctl --user restart lumi-server@dev
git -C ~/lumi/dev/app pull && (cd ~/lumi/dev/app && uv sync --frozen --all-extras) \
  && systemctl --user restart lumi-server@dev  # a dev update — code only, data untouched
```

A stop waits up to 300 s (`TimeoutStopSec`) for the session close — it makes model calls.

## Troubleshooting

| You see | Meaning | Fix |
|---|---|---|
| unit `failed`, journal: `LUMI_SERVER_HOST=0.0.0.0 listens on every interface` | a wildcard bind | set the box's Tailscale or LAN IP |
| …`is a public address` / `is a hostname` | not LAN/Tailscale, or a name | use the IP from `tailscale ip -4` (or the LAN IP) |
| unit `failed`, journal: `Port 8766 … is already in use … ss -ltnp 'sport = :8766'` | another service holds the port (exit 78, no restart) | stop it, or pick another free port — in the instance's `.env` **and** the Mac's |
| journal repeats `… is not an address of this machine (yet)` | the box's LAN IP changed (no DHCP reservation) or Tailscale is down | fix the reservation / `tailscale up`; it recovers by itself once the address exists |
| unit `failed`: `LUMI_SERVER_TOKEN is not set` | no token in the instance's `.env` | add one (step 2) |
| Mac: `server unreachable at http://192.168.1.197:8766` (a timeout) | ufw has no rule for the port, the service is down, or the port differs | the ufw rule (step 1); `systemctl --user status lumi-server@dev`; compare ports |
| Mac: `the server at … rejected the token` | the Mac's token isn't this instance's | copy the box instance's `LUMI_SERVER_TOKEN` |
| TUI: `… rejected the token — no pushes until a restart` | the server restarted with another token | fix the token, restart the client |
| no replies in Telegram, `telegram-outbound.log` repeats `No such file or directory: 'ffmpeg'` | voice replies are on (`LUMI_TELEGRAM_VOICE`) and the box has no ffmpeg — the same message is retried for ever | `sudo apt install ffmpeg` (the daemon sends the backlog by itself), or turn the voice replies off |
| no Telegram messages arrive, `telegram-inbound.log` shows a *Conflict* (another getUpdates) | another machine polls the same bot | stop the other daemons (step 5) |
| the env guard refuses (`prod runs from a dirty or untagged checkout`, a marker mismatch) | prod checkout not on a tag / the wrong data root | see [PROD_SETUP §When the guard refuses](PROD_SETUP.md) |

## Security & scope

- **LAN/Tailscale only, enforced by the server.** Never forward the port on the router. On the home LAN the token
  crosses the wire in the clear (no TLS until v4.1) — the same trust as agora's panel; bind the Tailscale address
  instead when you want the link encrypted or reachable away from home.
- **One token per instance**, secrets only in `~/lumi/<instance>/.env` (`chmod 600`), never in a unit file or a log.
- **ufw admits the home subnet only**: `sudo ufw allow from 192.168.1.0/24 to any port 8765:8766 proto tcp`
  (step 1), beside agora's rules for :8008 / :8090.
- Not here yet: her real memory on the box and the deploy pipelines (**v2.7**); TLS, public exposure and the
  security scans (**v4.1**); voice mode, the voicer and dictation beside the server (revisited after v2.6).
