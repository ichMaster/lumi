# Linux deploy — Лілі always on (v2.6–v2.7)

> **Both instances run on the box**: dev since 2026-10-19, prod since **2026-10-20** (her memory moved from the Mac at
> v2.6.0 — the Mac prod is its client, its `data/` the fallback). **From v2.7 code reaches the box only through the
> deploy agent** ([§Deploys](#deploys-v27)): a green `main` → dev by itself, a release tag → prod after your
> `confirm`. From **v2.8** both instances run as Docker containers and this guide is rewritten for them.

Run Лілі's server on the always-on home **Linux box** as systemd services, and talk to her from the Mac over
**LAN/Tailscale**. This is the operator's guide: install, run, check, monitor, fix. The design is in
[ROADMAP §v2.6](../specification/ROADMAP.md) and [ARCHITECTURE §Deployment and CI/CD](../specification/ARCHITECTURE.md);
the server itself in [SERVER_SETUP.md](SERVER_SETUP.md), the bridge in [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md).

## The 30-second model

```
Mac (clients only)                           Linux box (always on) — 192.168.1.197 (ssh: slate-server)
./lumi-client ─┐                             ~/lumi/dev/{app,.env,data}     app follows `main`
./lumi-cli    ─┼── LAN (ufw: 192.168.1.0/24) ► lumi-server@dev :8766 + lumi-telegram-inbound@dev / -outbound@dev
~/lumi/prod/  ─┘                             ~/lumi/prod/{app,.env,data}    app at a release tag, her real memory
  app/lumi (a client, LUMI_SERVER=on)        ► lumi-server@prod :8765 + lumi-telegram-inbound@prod / -outbound@prod
  data/ = the pre-move fallback              ~/lumi/deploy/                 the deploy agent + its timer (v2.7)
```

- Each instance on the box is **three user services** from the repo's templates (`deploy/systemd/`): the server
  and the two Telegram daemons, `%i` = `prod` | `dev`. Each instance has its own checkout, `.env`, data root,
  port, token and Telegram bot — the v2.1 split, carried to the box.
- **Both instances run on the box.** Dev since v2.6; prod since the one-time migration of 2026-10-20 (§3). The Mac
  runs no server and no Telegram daemon — only clients.
- **Code arrives by pipeline (v2.7).** CI runs ruff + pytest on every push; the box's deploy agent (a systemd timer)
  deploys a green `main` to dev and a confirmed release tag to prod, smoke-checks it and rolls back on failure
  ([§Deploys](#deploys-v27)). Nothing is copied by hand any more.
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
git -C app checkout --detach origin/main           # dev follows main — the deploy agent moves it from here
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

### 3. Install the prod instance — with her memory, never empty

```bash
mkdir -p ~/lumi/prod && cd ~/lumi/prod
git clone https://github.com/ichMaster/lumi.git app
git -C app checkout "$(git -C app tag --sort=-v:refname | head -1)"   # the latest release tag — prod only runs a tag
(cd app && uv sync --frozen --all-extras)
```

**Never start `lumi-server@prod` on an empty data root** — the first start marks it `prod` and begins a blank
Лілі. Her memory comes in first. This was done once, on **2026-10-20** (v2.1.1 on the Mac → v2.6.0 on the box):

1. **Stop everything on the Mac prod** — the TUI and both Telegram daemons (nothing may write `data/` meanwhile).
2. **Archive** on the Mac: `tar -czf ~/lumi/prod/backups/data-<time>-<tag>-premove.tar.gz -C ~/lumi/prod data`
   (2026-10-20: `data-20261020-0116-v2.1.1-premove.tar.gz`, ~290 MB — the whole data root: the store, vectors,
   journal, her files, the bus files and pointers, the logs, `env-marker`).
3. **Copy** it to the box (`scp` → `~/lumi/backups/`) and unpack: `tar -xzf ~/lumi/backups/data-…tar.gz -C ~/lumi/prod`.
4. **Settings:** copy the Mac prod `.env` to `~/lumi/prod/.env` (`chmod 600`) and set `LUMI_HOME=~/lumi/prod/data`,
   `LUMI_ENV=prod`, the box's `LUMI_SERVER_HOST`, `LUMI_SERVER_PORT=8765` and a **new** `LUMI_SERVER_TOKEN`; delete
   every per-path override (as in step 2).
5. **Start** the prod units (step 4) and **verify** before trusting it: the counts match the Mac (2026-10-20: 18 844
   messages, 27 913 vectors, 2 951 facts, 372 summaries, 4 543 thoughts), `/mood` answers, an old memory is
   recalled, a Telegram reply arrives.
6. **The Mac prod becomes a client** (step 6); its `data/` stays untouched as the fallback.

**Back to the Mac** (if the box ever fails her): stop the box's prod services, restore the Mac's
`~/lumi/prod/.env.bak-premove-20261020-0118`, copy the box's current `data/` back if it's newer, start the Mac prod
and its Telegram daemons — **never both machines' pollers at once**. Details in [PROD_SETUP.md](PROD_SETUP.md).

### 4. Install the units

The templates are the same for both instances; copy them from either checkout:

```bash
mkdir -p ~/.config/systemd/user
cp ~/lumi/dev/app/deploy/systemd/lumi-*@.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lumi-server@dev lumi-telegram-inbound@dev lumi-telegram-outbound@dev
systemctl --user enable --now lumi-server@prod lumi-telegram-inbound@prod lumi-telegram-outbound@prod   # after §3
systemctl --user status 'lumi-*'                    # six services, active (running)
```

The Telegram daemons are `PartOf` their server: they start with it, stop with it, and restart with it on a deploy.
The deploy agent's own two units (`lumi-deploy.service` + `.timer`) are installed by the agent itself (step 7).

When `deploy/systemd/` changes, copy the files again and `systemctl --user daemon-reload`.

### 5. One Telegram consumer per bot

A bot token can be polled by **one** process. The box polls **both** bots now (dev and prod), so the Mac never
starts a Telegram daemon — two pollers fight over the updates.

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

**Her own (prod) client** is the Mac prod checkout: `~/lumi/prod/.env` has `LUMI_SERVER=on` + the box's prod host,
port `8765` and the prod token, so `~/lumi/prod/app/lumi` talks to the box. It must stay at a release tag (the env
guard); update it with `scripts/prod_update.py --tag …` when you like — it only moves the client.

### 7. Install the deploy agent (v2.7)

```bash
python3 ~/lumi/dev/app/scripts/deploy_agent.py install
```

It copies itself to `~/lumi/deploy/deploy_agent.py` (so it never runs from a checkout it is updating), installs
`lumi-deploy.service` + `lumi-deploy.timer` into `~/.config/systemd/user/`, enables the timer and records what each
instance runs today (no deploy). The system `python3` runs it (stdlib only, Python ≥ 3.10). Check it:

```bash
systemctl --user list-timers lumi-deploy.timer      # the next run, every 5 minutes
python3 ~/lumi/deploy/deploy_agent.py status        # dev: <sha> · prod: v2.6.0 · what waits
```

**When a release changes `scripts/deploy_agent.py` or the deploy units, run `install` again** from the dev checkout
— the agent never updates itself.

---

## Check it

**The v2.6 DoD**, on the box's **dev** instance:

1. **A full conversation** from the Mac TUI to the box (a few turns, a `/mood`, a `/style`).
2. **A scheduled thought reaches Telegram with no client connected anywhere** — close every client, wait for a
   scheduled row (the quick-check recipe), see it in Telegram.
3. **A reconnect resumes cleanly** — close and reopen `./lumi-client`: `· while you were away ·` shows what
   happened, nothing twice.
4. **Killing the client changes nothing server-side** — `kill` the client mid-session; the server keeps running
   and keeps firing its acts (`journalctl` / `lumi.log`).
5. **`systemctl --user stop lumi-server@dev` closes the session** (a summary lands, exactly like Ctrl+C), and
   `systemctl --user start lumi-server@dev` brings her back.
6. ~~Prod is installed and idle~~ — superseded: prod runs on the box since the 2026-10-20 migration (§3).

**The v2.7 DoD:**

1. **CI is green on every push** (GitHub → Actions → `CI`), and its output shows no secret.
2. **A merge into `main` reaches dev by itself** within minutes — `deploy_agent.py status` names the new commit,
   `deploy.log` ends with `dev: <sha> is live — the smoke is green`.
3. **A release reaches prod only after `confirm`** — after the tag, `status` says `waiting: vA.B.C`; after
   `confirm vA.B.C` prod runs it (`curl …:8765/v1/health` shows the version).
4. **Rollback is one command** and was rehearsed once on each instance (`rollback dev`, `rollback prod`).
5. **The migrated prod** — today's `/mood`, something old recalled, a Telegram reply, a scheduled thought with the
   Mac off; the Mac prod a client, its `data/` untouched.

---

## Deploys (v2.7)

```
v2-dev ── merge (fast-forward) ──► main ── CI green ──► the agent ──► ~/lumi/dev/app   (by itself, ≤ 5 min)
                                    └── /release-version ──► tag vA.B.C ── confirm ──► ~/lumi/prod/app
```

- **Branches.** Work lands on `v2-dev`; it reaches the box's dev when it is **merged into `main`**
  (`git checkout main && git merge --ff-only v2-dev && git push`, then back to `v2-dev`). A release is a tag cut by
  `/release-version`; prod runs only tags.
- **The agent.** `lumi-deploy.timer` runs `~/lumi/deploy/deploy_agent.py tick` every 5 minutes. It reads GitHub
  itself (the repo is public: no runner in GitHub, no deploy secret anywhere, nothing reaches into the box):
  - **dev** — the newest `main` commit whose CI checks are **all green** and which **contains** what dev runs (so an
    older `main` is deployed: the server is **stopped first** (her session closes over the code it runs), then the
    commit is checked out (`git checkout --detach`), synced (`uv sync --frozen --all-extras`), the server started
    (`systemctl --user start lumi-server@dev`, its Telegram daemons with it) and smoke-checked. Red or unfinished
    CI → it waits.
  - **prod** — the newest `vA.B.C` tag **waits for your confirmation**; then the server is **stopped** (her session
    closes) and her data **archived** — `~/lumi/prod/backups/data-<YYYYmmdd-HHMM>-<the tag that ran>.tar.gz`, the
    `prod_update.py` naming (~290 MB, about a minute) — and then the same checkout → sync → start → smoke. A failed
    archive (a full disk) deploys nothing: prod is started again on its tag and the release waits for a fresh
    `confirm`. It never moves prod to an older tag.
- **The smoke** (no model is called): `/v1/health` answers as this instance (`env`) and the expected version (prod:
  the tag; dev: the commit's `VERSION`); `/v1/state` answers with the instance's token (read from its `.env`, never
  logged); the instance's enabled services stay up the **whole** minute (still `active` and not restarted meanwhile — systemd would bring a crashed one back within seconds) and `/v1/health` still answers at its end; no new `ERROR` line in
  `data/lumi.log` since the restart.
- **A failed smoke rolls back** to the previous commit / tag at once and marks the failed one **bad** — it is never
  retried by itself (a newer commit is; a bad tag deploys again only after a fresh `confirm`).
- **A deploy moves code only.** The agent never writes under a data root (it only reads prod's, into the archive)
  and never touches an `.env`; its own files are `~/lumi/deploy/{deploy_agent.py,state.json,deploy.log,agent.lock}`
  and the archives in `~/lumi/prod/backups/` (never pruned — delete old ones by hand when the disk wants it). Settings changes are still yours (by hand
  until v2.9). A deploy **restarts** the instance — her session is closed (summary + facts) exactly like a stop.

The commands (on the box, or from the Mac as `ssh slate-server 'python3 ~/lumi/deploy/deploy_agent.py …'`):

```bash
python3 ~/lumi/deploy/deploy_agent.py status           # what runs where, what waits, what failed, the last deploy
python3 ~/lumi/deploy/deploy_agent.py confirm v2.7.0   # approve a release for prod — deploys it now (a few minutes)
python3 ~/lumi/deploy/deploy_agent.py rollback prod    # back to the previous tag, one command (dev: the previous commit)
python3 ~/lumi/deploy/deploy_agent.py tick             # one step now, without waiting for the timer
tail -f ~/lumi/deploy/deploy.log                       # every decision and deploy, with the reason
```

A rollback **sticks**: the rolled-back commit or tag is marked bad, so the next tick doesn't redeploy it. To return
prod to the newer tag, `confirm` it again. A rollback moves **code** only — her data stays as it is, which is right
unless the newer release changed stored data badly. Then restore the archive taken when it was deployed (it is named
after the tag you rolled back to):

```bash
systemctl --user stop lumi-deploy.timer lumi-server@prod
cd ~/lumi/prod && mv data data.broken
tar -xzf backups/data-<time>-v2.6.0.tar.gz -C ~/lumi/prod     # the newest archive labelled with the running tag
systemctl --user start lumi-server@prod lumi-deploy.timer
```

**Pause the pipeline** (e.g. to work on the box by hand): `systemctl --user stop lumi-deploy.timer` — `start` it
again afterwards.

## Monitoring

```bash
systemctl --user status 'lumi-*@dev'                     # the three services at a glance
journalctl --user -u lumi-server@dev -f                  # its start line, refusals, crashes
tail -f ~/lumi/dev/data/lumi.log                         # her log (turns, thoughts, mood, the scheduler)
tail -f ~/lumi/dev/data/telegram-inbound.log ~/lumi/dev/data/telegram-outbound.log
python3 ~/lumi/deploy/deploy_agent.py status             # the pipeline: what runs, what waits, what failed
journalctl --user -u lumi-deploy -n 50                   # the agent's last steps
```

The same with `@prod` / `~/lumi/prod/…` for her real instance.

From the Mac:

```bash
curl -s http://192.168.1.197:8766/v1/health             # {"ok": true, "env": "dev", "version": "…"} — open, no token
./lumi-cli status                                        # the full picture, with the token
```

## Stop, restart, update

```bash
systemctl --user stop lumi-server@dev          # closes her session (summary + facts); the daemons stop too
systemctl --user restart lumi-server@dev
```

A stop waits up to 300 s (`TimeoutStopSec`) for the session close — it makes model calls.

**Updates go through the agent** ([§Deploys](#deploys-v27)) — merge into `main` for dev, tag + `confirm` for prod.
Never `git pull` in an instance checkout while the timer runs: the agent's state would no longer say what runs.

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
| `status`: `waiting: main <sha> (CI pending)` / `(CI red)` / `(CI none)` | CI hasn't finished, failed, or hasn't started for that commit | wait / fix it on `v2-dev` and merge again |
| `status`: `waiting: main <sha> (behind what dev runs)` | dev runs a commit `main` doesn't contain (an unmerged `v2-dev`) | merge `v2-dev` into `main` (fast-forward) |
| `status`: `prod … waiting: vA.B.C` | a release awaits your approval | `deploy_agent.py confirm vA.B.C` |
| `status`: `bad: …`, `last deploy …: rolled back: <reasons>` | a deploy failed its smoke and was rolled back | read the reasons + `deploy.log`; fix forward with a new commit / patch tag |
| `another deploy step is running — try again in a minute` | the timer's step holds the lock (a deploy can take minutes) | wait, then repeat the command |

## Security & scope

- **LAN/Tailscale only, enforced by the server.** Never forward the port on the router. On the home LAN the token
  crosses the wire in the clear (no TLS until v4.1) — the same trust as agora's panel; bind the Tailscale address
  instead when you want the link encrypted or reachable away from home.
- **One token per instance**, secrets only in `~/lumi/<instance>/.env` (`chmod 600`), never in a unit file or a log.
- **ufw admits the home subnet only**: `sudo ufw allow from 192.168.1.0/24 to any port 8765:8766 proto tcp`
  (step 1), beside agora's rules for :8008 / :8090.
- **The pipeline holds no secret:** CI needs none (every paid service is mocked), the agent reads GitHub without a
  token, and the instance token it sends to `/v1/state` stays on the box, unlogged.
- Not here yet: containers (**v2.8**); TLS, public exposure and the security scans (**v4.1**); voice mode, the voicer
  and dictation beside the server (revisited after v2.6).
