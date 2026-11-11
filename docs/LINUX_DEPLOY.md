# Linux deploy — Лілі always on (v2.6–v2.10)

> **Both instances run on the box as Docker containers (v2.8)** — one image (`ghcr.io/ichmaster/lumi`), a compose
> project per instance, her memory on the host. Dev has run on the box since 2026-10-19, prod since **2026-10-20**
> (her memory moved from the Mac at v2.6.0 — the Mac prod is its client, its `data/` the fallback). Code reaches the
> box only through the **deploy agent** ([§Deploys](#deploys)): a green `main` → dev by itself, a release tag → prod
> after your `confirm`. The v2.6 systemd units are retired and stay installed as [the way back](#the-way-back-the-v26-units).
> **Since v2.9** each instance's settings are `~/lumi/<instance>/settings.yaml` + `secrets.yaml` (`0600`), no `.env`
> ([§Settings as files](#2b-settings-as-files-v29)).

Run Лілі's server on the always-on home **Linux box** and talk to her from the Mac over **LAN/Tailscale**. This is
the operator's guide: install, deploy, check, monitor, fix. The design is in [ROADMAP §v2.6–v2.8](../specification/ROADMAP.md)
and [ARCHITECTURE §Deployment and CI/CD](../specification/ARCHITECTURE.md); the server itself in
[SERVER_SETUP.md](SERVER_SETUP.md), the bridge in [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md).

## The 30-second model

```
Mac (clients only)                           Linux box (always on) — 192.168.1.197 (ssh: slate-server)
./lumi-client ─┐                             compose project lumi-dev  (image sha-<short>, follows `main`)
./lumi-cli    ─┼── LAN (ufw: 192.168.1.0/24) ►   server :8766 · telegram-inbound · telegram-outbound
~/lumi/prod/  ─┘                             compose project lumi-prod (image vA.B.C, after your `confirm`)
  app/lumi (a client, LUMI_SERVER=on)        ►   server :8765 · telegram-inbound · telegram-outbound
  data/ = the pre-move fallback              ~/lumi/<instance>/{settings.yaml, secrets.yaml, data/, app/}
                                             ~/lumi/deploy/                          the agent, its timer, compose files
```

- **One image for every process** — the server and both Telegram daemons — built and smoke-tested by CI, published
  to GHCR from `main` (`:edge` + `:sha-<short>`) and from a release tag (`:vA.B.C` + `:latest`). Nothing secret or
  lived is inside it.
- **A compose project per instance** (`deploy/compose/compose.yaml` + `prod.env` / `dev.env`): three containers that
  share the instance's **data root** (bind-mounted at `/data` — the file bus needs one filesystem) and its **settings**
  (v2.9: `~/lumi/<instance>/settings.yaml` + `secrets.yaml`, mounted read-only at `/settings`). Each instance keeps its
  own port, token, bot and data.
- **The deploy agent** (a systemd user timer, `~/lumi/deploy/`) deploys image tags: CI green → dev, `confirm` → prod,
  with a smoke and an automatic way back ([§Deploys](#deploys)).
- **LAN/Tailscale only.** Inside its container the server binds `0.0.0.0` (the image's container mode); the compose
  file publishes it **only on the box's LAN address**. Outside a container the server still refuses a wildcard, a
  public address or a hostname. No TLS yet (v4.1): never port-forward it.
- **The same box runs matrix-agora** (Matrix :8008, its panel :8090) the same way — GHCR images, compose, the LAN
  address + ufw. The Mac never touches the box's disk: the clients read everything through the API.

---

## Setup

### 0. Prerequisites (once)

The box's **LAN address** is what the containers publish on and the Mac dials — here `192.168.1.197` (keep it
fixed: a DHCP reservation on the router).

On the **box** (Docker and compose came with matrix-agora):

```bash
docker --version && docker compose version          # Docker ≥ 24 + the compose plugin
id -nG | grep -w docker                              # your user is in the docker group
sudo apt install git                                 # the agent reads refs from the instances' checkouts
sudo loginctl enable-linger "$USER"                  # the agent's timer runs without a login, starts at boot
```

The image brings its own Python, uv, ffmpeg and tzdata — nothing else to install for Lumi itself.

### 1. Two free ports and the firewall (once)

The defaults are **prod 8765 · dev 8766** — one port per instance, in its `settings.yaml` (`server.port`) **and** in
`deploy/compose/<instance>.env` (the compose parameters — not settings, nothing secret). Check nothing else listens there, then admit the home subnet:

```bash
ss -ltnp | grep -E ':(8765|8766)\b'                  # prints nothing → both are free
sudo ufw allow from 192.168.1.0/24 to any port 8765:8766 proto tcp
sudo ufw status | grep -E '8765|8008'                # 8765:8766/tcp ALLOW 192.168.1.0/24 — beside agora's rules
```

Docker publishes past ufw's input chain, so the **publish address** is what really keeps her off other networks:
`deploy/compose/<instance>.env` names the LAN address, never `0.0.0.0`.

### 2. The instance directories

Each instance is `~/lumi/<instance>/` with these things:

```
~/lumi/dev/settings.yaml   every non-secret setting (v2.9) — mounted read-only at /settings in every container
~/lumi/dev/secrets.yaml    the keys and tokens (chmod 600, your user) — mounted beside it
~/lumi/dev/.env.migrated   the pre-v2.9 .env, kept by the migration — mounted at /app/.env for the way back below v2.9
~/lumi/dev/data/           its data root — bind-mounted at /data, owned by your user (uid 1000, as the image runs)
~/lumi/dev/app/            a git checkout — the agent reads refs from it (merge-base, tags, VERSION); the v2.6 units run it
```

```bash
mkdir -p ~/lumi/dev/data && cd ~/lumi/dev
git clone https://github.com/ichMaster/lumi.git app
cp app/settings.example.yaml settings.yaml && cp app/secrets.example.yaml secrets.yaml && chmod 600 secrets.yaml
touch .env.migrated                                  # a new instance has no pre-v2.9 past; compose mounts the file
```

Then set (uncomment and fill in — every line of the examples is commented out at its default):

```yaml
# settings.yaml
instance:
  env: dev
  home: ~/lumi/dev/data             # for the v2.6 units; inside the container it's overridden to /data
server:
  host: 192.168.1.197               # what the agent's smoke dials; inside the container it's overridden to 0.0.0.0
  port: 8766                        # = deploy/compose/dev.env
bridge:
  enabled: true                     # the Telegram bridge beside the server
telegram:
  allowlist: [<your Telegram id>]
# secrets.yaml
server:
  token: <a new random one>         # python3 -c "import secrets; print(secrets.token_urlsafe(32))"
telegram:
  token: <the DEV bot's token>
```

**Set no per-path setting** (`store.path`, `files.dir`, `journal.dir`, `bridge.inbox_path`, `bridge.outbox_path`,
`dictation.listen_flag`) — the data root places them all.

### 2b. Settings as files (v2.9)

An instance that ran before v2.9 has a `~/lumi/<instance>/.env`. **Migrate it once**, with the image that will read
the files (the same Python and the same schema) — a dry run first, which prints the planned `settings.yaml`, the
secret **keys** (never a value) and the keys it can't place, and checks that the new files load to exactly the same
configuration:

```bash
i=dev; tag=$(docker inspect -f '{{index .Config.Labels "io.lumi.build"}}' lumi-$i-server-1)   # or a newer tag
cp -p ~/lumi/$i/.env ~/lumi/backups/$i.env.pre-v2.9                                         # a copy, 0600
docker run --rm -v ~/lumi/$i:/instance ghcr.io/ichmaster/lumi:$tag \
  python scripts/env_to_settings.py /instance/.env --out /instance              # dry run: read it
docker run --rm -v ~/lumi/$i:/instance ghcr.io/ichmaster/lumi:$tag \
  python scripts/env_to_settings.py /instance/.env --out /instance --apply      # settings.yaml + secrets.yaml (0600)
ls -l ~/lumi/$i                                        # no .env; .env.migrated kept; secrets.yaml -rw-------
```

The image must be v2.9 or newer (it carries `scripts/env_to_settings.py`). The deploy agent **refuses to deploy or
roll back** an instance that lacks `settings.yaml`, `secrets.yaml` or `.env.migrated` — before it changes anything
(`last deploy …: not deployed: the instance is not migrated`).

**Change a setting** with `lumi-cli config` through the image — it validates (a wrong value is refused with the
allowed ones), edits only that line, keeps `secrets.yaml` at `0600` and never prints a secret:

```bash
cfg() {  # cfg <dev|prod> <config args…> — e.g. `cfg dev get memory.window`, `cfg dev set recall.rag true`
  local i=$1; shift
  docker run --rm -v ~/lumi/$i:/instance \
    ghcr.io/ichmaster/lumi:$(docker inspect -f '{{index .Config.Labels "io.lumi.build"}}' lumi-$i-server-1) \
    python -m cli config "$@" --dir /instance
}
cfg dev validate && lumi dev restart                   # a setting applies after a restart (the `lumi` function below)
```

`docker compose restart` is enough — the files are read at start, not baked into the container. A `LUMI_*` variable
in the compose `environment:` block (`LUMI_HOME`, the in-container host/port) still wins over the files.

**The way back below v2.9** is automatic: compose mounts `.env.migrated` at `/app/.env`, where only a pre-v2.9 image
looks (its `load_dotenv`), so `rollback` (or the agent's own way back after a failed smoke) starts the older image
with the settings it had — the ones from before the migration. A v2.9+ image never reads it. The v2.6 units read
`~/lumi/<instance>/.env`: before going back to them by hand, `cp -p .env.migrated .env`.

For the "scheduled thought" check later, turn the scheduler on as in
[SERVER_SETUP §Her time on the server](SERVER_SETUP.md) (its quick-check schedule recipe).

### 3. Prod — with her memory, never empty

Prod's directory is the same shape (`~/lumi/prod/{settings.yaml,secrets.yaml,data,app}`, port 8765, its own token
and bot). **Never start
prod on an empty data root** — the first start marks it `prod` and begins a blank Лілі. Her memory comes in first.
This was done once, on **2026-10-20** (v2.1.1 on the Mac → v2.6.0 on the box):

1. **Stop everything on the Mac prod** — the TUI and both Telegram daemons (nothing may write `data/` meanwhile).
2. **Archive** on the Mac: `tar -czf ~/lumi/prod/backups/data-<time>-<tag>-premove.tar.gz -C ~/lumi/prod data`
   (2026-10-20: `data-20261020-0116-v2.1.1-premove.tar.gz`, ~290 MB — the whole data root: the store, vectors,
   journal, her files, the bus files and pointers, the logs, `env-marker`).
3. **Copy** it to the box (`scp` → `~/lumi/backups/`) and unpack: `tar -xzf ~/lumi/backups/data-…tar.gz -C ~/lumi/prod`.
4. **Settings:** (then) the Mac prod `.env` copied to `~/lumi/prod/.env` (`chmod 600`) with `LUMI_ENV=prod`, the
   box's `LUMI_SERVER_HOST`, `LUMI_SERVER_PORT=8765` and a **new** `LUMI_SERVER_TOKEN`, every per-path override
   deleted — migrated to `settings.yaml` + `secrets.yaml` at v2.9 ([§2b](#2b-settings-as-files-v29)).
5. **Start** prod and **verify** before trusting it: the counts match the Mac (2026-10-20: 18 844 messages, 27 913
   vectors, 2 951 facts, 372 summaries, 4 543 thoughts), `/mood` answers, an old memory is recalled, a Telegram
   reply arrives.
6. **The Mac prod becomes a client** (step 5); its `data/` stays untouched as the fallback.

In a container prod runs **only a release image**: the env guard refuses a build that isn't `vA.B.C` (an `:edge` or
`sha-` image) — the container twin of "a clean checkout at a tag".

**Back to the Mac** (if the box ever fails her): stop the box's prod containers, restore the Mac's
`~/lumi/prod/.env.bak-premove-20261020-0118`, copy the box's current `data/` back if it's newer, start the Mac prod
and its Telegram daemons — **never both machines' pollers at once**. Details in [PROD_SETUP.md](PROD_SETUP.md).

### 4. The image on GHCR

CI publishes `ghcr.io/ichmaster/lumi`. Like agora's packages it is **public**, so the box pulls without a login. The
first time a package appears, GitHub makes it private: open it (GitHub → your profile → Packages → `lumi` → Package
settings → *Change visibility* → Public). Check from the box:

```bash
docker pull ghcr.io/ichmaster/lumi:edge              # works without `docker login`
```

### 5. One Telegram consumer per bot

A bot token can be polled by **one** process. The box polls **both** bots (dev and prod) from their
`telegram-inbound` containers, so the Mac never starts a Telegram daemon — two pollers fight over the updates.

### 6. The Mac clients

In the settings the Mac client reads (the dev tree's `~/development/lumi/settings.yaml` + `secrets.yaml`; a
not-yet-migrated tree reads its `.env`, deprecated in v2.9):

```yaml
# settings.yaml
server:
  host: 192.168.1.197               # the box — the address the containers publish on
  port: 8766                        # the box's dev port
# secrets.yaml
server:
  token: <the box's dev token>
```

```bash
./lumi-client                         # the TUI, talking to the box
./lumi-cli status                     # env, version, model, session, mood, theme — from the box
```

After the first turn, `/latency` ends with `NETWORK client↔server: last … ms · median … ms`. `/image` and `/web`
follow what the **box** has on. With `server.host` naming the box, a local `./lumi-server` on the Mac refuses to
start ("not an address of this machine") — set it back to `127.0.0.1` for local work.

**Her own (prod) client** is the Mac prod checkout: its settings (`~/lumi/prod/.env`, read as the deprecated layer
until it is migrated like any instance) have `LUMI_SERVER=on` + the box's prod host, port `8765` and the prod token,
so `~/lumi/prod/app/lumi` talks to the box. It must stay at a release tag (the env
guard); move it with `scripts/prod_update.py --tag …` when you like — it only moves the client.

### 7. The deploy agent

```bash
cd ~/lumi/dev/app && git fetch -q --tags origin
stage=$(mktemp -d) && git archive origin/main scripts/deploy_agent.py deploy | tar -x -C "$stage"
python3 "$stage/scripts/deploy_agent.py" install && rm -rf "$stage"
```

`install` copies the agent and `deploy/compose/` to `~/lumi/deploy/` (it never runs from a checkout), installs
`lumi-deploy.service` + `.timer` into `~/.config/systemd/user/`, enables the timer and records what each instance
runs today — no deploy. The system `python3` runs it (stdlib only, Python ≥ 3.10). The first tick after a green
`main` is dev's **first container deploy**: it stops and disables dev's v2.6 units, then starts `lumi-dev`; prod's
happens at its next `confirm`.

```bash
systemctl --user list-timers lumi-deploy.timer      # the next run, every 5 minutes
python3 ~/lumi/deploy/deploy_agent.py status        # dev: <sha> (image sha-…) · prod: vA.B.C · what waits
```

**When a release changes `scripts/deploy_agent.py`, `deploy/compose/` or the deploy units, run `install` again** (the
three lines above) — the agent never updates itself.

---

## Check it

**The v2.9 DoD:**

1. **Both instances run from `settings.yaml` + `secrets.yaml` with no `.env`**: `ls -l ~/lumi/{dev,prod}` shows the
   two files (`secrets.yaml` `-rw-------`), `.env.migrated`, no `.env`; `docker ps` shows all six containers healthy.
2. **A setting changed with `lumi-cli config set` is validated and applies after a restart**: `cfg dev set
   persona.think_show loud` is refused with `one of debug, open, off`; a valid change + `lumi dev restart` shows in
   `cfg dev get …` and in her behaviour.
3. **No secret in any CLI output or log**: `cfg dev get server.token` says `•••set•••`; grepping the token's value
   over `docker logs lumi-dev-server-1` and `data/lumi.log` finds nothing.
4. **The dev tree runs the same way** (`./lumi`, `./lumi-server` from its `settings.yaml` + `secrets.yaml`).

**The v2.8 DoD:**

1. **Both instances run as containers**: `docker ps --filter name=lumi-` lists `lumi-prod-server-1`,
   `lumi-prod-telegram-inbound-1`, `lumi-prod-telegram-outbound-1` and the same three for `lumi-dev`.
2. **A merge into `main` → the dev containers on the new image** within minutes (`status`: `image sha-<short>`).
3. **A confirmed tag → prod on that image** (`status`: `image vA.B.C`; `curl …:8765/v1/health` shows the version).
4. **`docker stop lumi-dev-server-1` closes her session** (a summary lands, exactly like Ctrl+C);
   `docker start lumi-dev-server-1` brings her back.
5. **A killed container comes back by itself**: kill its process from the host (`kill -9 $(docker inspect -f
   '{{.State.Pid}}' lumi-dev-server-1)` — it runs as your user) and Docker restarts it within seconds (`docker ps`
   shows a fresh *Up*). (`docker kill` counts as a manual stop and is not restarted.)
6. **Every deploy leaves the data roots and settings untouched; the v2.6 units are disabled**
   (`systemctl --user is-enabled lumi-server@dev lumi-server@prod` → `disabled`).

**The v2.7 DoD** (still true with containers): CI green on every push; a merge into `main` reaches dev by itself; a
release reaches prod only after `confirm`; rollback is one command; no secret in CI output.

**The v2.6 DoD** (still true): a full conversation from the Mac TUI; a scheduled thought reaches Telegram with no
client connected anywhere; a reconnect resumes cleanly (`· while you were away ·`, nothing twice); killing the client
changes nothing server-side.

---

## Deploys

```
v2-dev ── merge (ff) ──► main ── CI: gates + image ──► :sha-<short> ── the agent ──► lumi-dev   (≤ 5 min)
                          └── /release-version ──► tag vA.B.C ── CI ──► :vA.B.C ── confirm ──► lumi-prod
```

- **Branches.** Work lands on `v2-dev`; it reaches the box's dev when it is **merged into `main`**
  (`git checkout main && git merge --ff-only v2-dev && git push`, then back to `v2-dev`). A release is a tag cut by
  `/release-version`; prod runs only release images.
- **CI** (`.github/workflows/`): `ci.yml` runs ruff + pytest; `image.yml` checks the compose files, builds the image,
  starts it with a test config (`/v1/health` must answer, a stop must exit 0) and only then pushes it. The agent
  waits for **every** check run of a commit, so dev deploys a commit only once its image exists.
- **The agent.** `lumi-deploy.timer` runs `~/lumi/deploy/deploy_agent.py tick` every 5 minutes. It reads GitHub
  itself (the repo is public: no runner in GitHub, no deploy secret anywhere, nothing reaches into the box):
  - **dev** — the newest `main` commit whose checks are **all green** and which **contains** what dev runs (an older
    `main` is never deployed) runs as its image `sha-<short>`: `docker compose pull`, then `up -d` — each container
    stops (SIGTERM → her session close, inside the old image) before its new one starts. Red or unfinished CI → it
    waits. A pull that fails (no image yet, the package not public) deploys and stops nothing.
  - **prod** — the newest `vA.B.C` **waits for your confirmation**; then its image is pulled, the server stopped
    (her session closes) and her data **archived** — `~/lumi/prod/backups/data-<YYYYmmdd-HHMM>-<the tag that
    ran>.tar.gz` (~290 MB, about a minute) — and then `up -d` → smoke. A failed archive (a full disk) deploys
    nothing: prod is started again on its tag and the release waits for a fresh `confirm`. It never moves prod to an
    older tag.
  - **The first container deploy** of an instance stops and disables its three v2.6 units before any container
    starts (the port and the brain lock are free; two brains never share her root).
- **The smoke** (no model is called): `/v1/health` answers as this instance (`env`) and the expected version (prod:
  the tag; dev: the commit's `VERSION`); `/v1/state` answers with the instance's token (read from its `secrets.yaml`
  — a pre-v2.9 instance's `.env` — never logged); all three containers stay up the **whole** minute — the same container, the same restart count (Docker
  would bring a crashed one back within seconds) — and `/v1/health` still answers at its end; no new `ERROR` line in
  `data/lumi.log` since the start.
- **A failed smoke goes back** at once to what ran before — the previous image, or **the v2.6 units** right after a
  first container deploy (no image exists for older code; the units' checkout still holds it) — and marks the
  failed ref **bad**: never retried by itself (a newer commit is; a bad tag deploys again only after a fresh
  `confirm`).
- **A deploy moves code only.** The agent never writes under a data root (it only reads prod's, into the archive)
  and never touches the settings files; its own files are `~/lumi/deploy/{deploy_agent.py,compose/,state.json,deploy.log,
  agent.lock}` and the archives in `~/lumi/prod/backups/` (never pruned — delete old ones by hand when the disk wants
  it). After a deploy that passes, the agent removes the Lumi images no instance runs or would roll back to (GHCR
  keeps every tag). Settings changes are yours (`lumi-cli config set`, [§2b](#2b-settings-as-files-v29)); after one, restart the
  instance's containers. A deploy or rollback of an instance missing its settings files is refused before anything
  changes.

The commands (on the box, or from the Mac as `ssh slate-server 'python3 ~/lumi/deploy/deploy_agent.py …'`):

```bash
python3 ~/lumi/deploy/deploy_agent.py status           # what runs where (image or units), what waits, what failed
python3 ~/lumi/deploy/deploy_agent.py confirm v2.8.0   # approve a release for prod — deploys it now (a few minutes)
python3 ~/lumi/deploy/deploy_agent.py rollback prod    # back to what ran before, one command (also: rollback dev)
python3 ~/lumi/deploy/deploy_agent.py tick             # one step now, without waiting for the timer
tail -f ~/lumi/deploy/deploy.log                       # every decision and deploy, with the reason
```

A rollback **sticks**: the rolled-back ref is marked bad, so the next tick doesn't redeploy it. To return prod to the
newer tag, `confirm` it again. A rollback moves **code** only — her data stays as it is, which is right unless the
newer release changed stored data badly. Then restore the archive taken when it was deployed (it is named after the
tag you rolled back to):

```bash
systemctl --user stop lumi-deploy.timer && docker stop lumi-prod-server-1
cd ~/lumi/prod && mv data data.broken && tar -xzf backups/data-<time>-<tag>.tar.gz -C ~/lumi/prod
docker start lumi-prod-server-1 && systemctl --user start lumi-deploy.timer
```

**Pause the pipeline** (e.g. to work on the box by hand): `systemctl --user stop lumi-deploy.timer` — `start` it
again afterwards.

## Monitoring

```bash
docker ps --filter name=lumi-                            # six containers, their state and health
docker logs -f lumi-dev-server-1                         # the server's start line, refusals, crashes
tail -f ~/lumi/dev/data/lumi.log                         # her log (turns, thoughts, mood, the scheduler) — unchanged
tail -f ~/lumi/dev/data/telegram-inbound.log ~/lumi/dev/data/telegram-outbound.log
python3 ~/lumi/deploy/deploy_agent.py status             # the pipeline: what runs, what waits, what failed
journalctl --user -u lumi-deploy -n 50                   # the agent's last steps
```

The same with `lumi-prod-…` / `~/lumi/prod/…` for her real instance. From the Mac:

```bash
curl -s http://192.168.1.197:8766/v1/health             # {"ok": true, "env": "dev", "version": "…"} — open, no token
./lumi-cli status                                        # the full picture, with the token
```

## Stop, restart, update

```bash
docker stop lumi-dev-server-1            # SIGTERM → her session close (summary + facts), up to 300 s, then exit
docker start lumi-dev-server-1
docker restart lumi-dev-telegram-outbound-1
```

A container you stop by hand **stays stopped** — also across a reboot (`restart: unless-stopped`); a crashed one is
restarted by itself. For compose commands on a running project, take its tag from the image it runs:

```bash
lumi() {  # lumi <dev|prod> <compose args…> — e.g. `lumi dev ps`, `lumi prod logs -f server`, `lumi dev restart`
  local i=$1; shift
  LUMI_IMAGE_TAG=$(docker inspect -f '{{index .Config.Labels "io.lumi.build"}}' "lumi-$i-server-1") \
    docker compose --env-file ~/lumi/deploy/compose/$i.env -f ~/lumi/deploy/compose/compose.yaml "$@"
}
```

**Updates go through the agent** ([§Deploys](#deploys)) — merge into `main` for dev, tag + `confirm` for prod. Never
`up` another tag by hand while the timer runs: the agent's state would no longer say what runs.

## The admin panel (v2.10)

One panel for both instances — `lumi-admin`, its own compose project (`deploy/admin/`) from the **same image**
(`python -m admin`). It shows prod and dev side by side (Overview: up/down, version + image, uptime + restarts,
model, today's mood, turns, tokens and cost, latency, errors, data-root size, the box), eight health checks every
minute with their history, the containers with start / stop / restart, usage per day / model / kind, and the logs.
It **never runs the core, never writes a data root** (both instance folders are mounted read-only) and **makes no
paid call** (only `/v1/health` and `/v1/state` of each server). A prod action asks for the typed confirmation
`prod`. It holds `docker.sock` — root-equivalent on the box — so it is LAN-only, behind its own token.

**Install** (once — a staged copy of `deploy/admin/` from `main`, like the agent's install):

```bash
mkdir -p ~/lumi/admin && cd ~/lumi/admin
stage=$(mktemp -d) && git -C ~/lumi/dev/app fetch -q --tags origin \
  && git -C ~/lumi/dev/app archive origin/main deploy/admin | tar -x -C "$stage"
mkdir -p ~/lumi/deploy/admin && cp "$stage"/deploy/admin/* ~/lumi/deploy/admin/ && rm -rf "$stage"
cp ~/lumi/deploy/admin/admin.example.yaml ~/lumi/admin/admin.yaml          # the registry: prod + dev
python3 -c "import secrets; print('admin:\n  token: ' + secrets.token_urlsafe(32))" > ~/lumi/admin/secrets.yaml
chmod 600 ~/lumi/admin/secrets.yaml                                         # the token — never printed
LUMI_IMAGE_TAG=v2.10.0 docker compose --env-file ~/lumi/deploy/admin/admin.env \
  -f ~/lumi/deploy/admin/compose.yaml up -d
sudo ufw allow from 192.168.1.0/24 to any port 8767 proto tcp              # keeps the box's rules consistent
```

**What keeps it off other networks is the publish address**, not ufw: Docker publishes past ufw's input chain (as
§1 explains), so `LUMI_PUBLISH_HOST` in `admin.env` (the LAN address, never `0.0.0.0`) is the fence, and the token
the gate. Never forward port 8767 on the router.

From the Mac: `http://192.168.1.197:8767`, and paste the token (`ssh slate-server 'cat ~/lumi/admin/secrets.yaml'`
shows it to you). The page keeps it for the browser tab only (`sessionStorage`).

**Update** to a new release: the same `up -d` with the new `LUMI_IMAGE_TAG` (re-stage `deploy/admin/` first if the
release changed it). The deploy agent doesn't manage the panel. **Stop:** `docker compose -p lumi-admin down`.

| You see | Meaning | Fix |
|---|---|---|
| `docker logs lumi-admin-admin-1`: `admin.token is missing / too short` | no `~/lumi/admin/secrets.yaml` token of 32+ characters | write it as above, `chmod 600`, `up -d` again |
| `… only prod and dev are panel instances` / `instances.<i>.url` | `admin.yaml` names something else or lacks a field | copy `admin.example.yaml` again |
| every container `unknown` | the panel can't reach the Docker socket | `DOCKER_GID` in `admin.env` = `getent group docker` (999 on the box) |
| the `server` check red for a healthy instance | the panel can't reach the instance's published port | `curl` the URL from `admin.yaml` on the box; the instance's `deploy/compose/<i>.env` |
| the `state` check amber | no `server.token` in the instance's `secrets.yaml` | migrate the instance (§2b) |
| a 401 in the page | the token was refused | sign out, paste the token again |

## The way back: the v2.6 units

The units (`deploy/systemd/lumi-*@.service`) stay installed in `~/.config/systemd/user/`, disabled. Right after an
instance's first container deploy, `deploy_agent.py rollback <instance>` returns to them by itself (`compose down`,
then the units on their checkout). Later, by hand:

```bash
systemctl --user stop lumi-deploy.timer
lumi prod down                                          # the containers go (the function above)
git -C ~/lumi/prod/app checkout <tag> && (cd ~/lumi/prod/app && uv sync --frozen --all-extras)   # needs uv
systemctl --user enable --now lumi-server@prod lumi-telegram-inbound@prod lumi-telegram-outbound@prod
```

Under the units the server binds `LUMI_SERVER_HOST` (the LAN address) and `LUMI_HOME` places the data root. A unit's
checkout older than v2.9 reads only `~/lumi/<instance>/.env` — `cp -p .env.migrated .env` before starting it; from
v2.9 a unit reads `~/lumi/<instance>/settings.yaml` + `secrets.yaml` (the repo's parent folder).

## Troubleshooting

| You see | Meaning | Fix |
|---|---|---|
| `status`: `last deploy …: not deployed: the image pull failed` | the image isn't on GHCR yet, or the package is private | wait for `image.yml`; make the package public (setup §4) |
| `docker logs lumi-prod-server-1`: `prod runs only released code — this image's build is 'sha-…'` | a non-release image was started as prod | only `confirm` a release tag; never `up` prod with `:edge` |
| `compose`: `the image tag is set by the deploy agent` | a hand-run compose command without `LUMI_IMAGE_TAG` | use the `lumi` function (§Stop, restart) |
| `up` fails: `port is already allocated` | a v2.6 unit (or something else) still holds the port | `systemctl --user disable --now lumi-server@<instance>`; `ss -ltnp 'sport = :<port>'` |
| server log: `another brain already runs on this data root` | a unit and a container on the same root | stop and disable the unit — one brain per memory |
| the server container restarts in a loop | it crashes at start — or refuses its configuration (a taken port, a non-release prod build): unlike the v2.6 units (exit 78 never restarted), Docker restarts every exit, with a growing back-off | `docker logs lumi-<i>-server-1` names the reason (a wrong setting: the file, the key and the allowed values); fix it with `cfg <i> set …` / the port; the agent's smoke rolls back a deploy that does this |
| Mac: `server unreachable at http://192.168.1.197:8766` (a timeout) | the container is down, ufw has no rule, or the port differs | `docker ps`; the ufw rule (setup §1); compare `server.port` in `settings.yaml` and `deploy/compose/<i>.env` |
| Mac: `the server at … rejected the token` | the Mac's token isn't this instance's | copy the box instance's `server.token` (its `secrets.yaml`) |
| `status`: `last deploy …: not deployed: the instance is not migrated` / `deploy.log`: `missing settings.yaml` | the instance still has only a `.env` | migrate it ([§2b](#2b-settings-as-files-v29)), then `confirm` again (prod) / wait for the next tick (dev) |
| `up` fails: `bind source path does not exist: …/settings.yaml` | a hand-run `up` on an unmigrated instance | migrate it first ([§2b](#2b-settings-as-files-v29)) |
| `WARNING (settings): … secrets.yaml is readable by others` | the secrets file lost its `0600` | `chmod 600 ~/lumi/<i>/secrets.yaml` |
| TUI: `… rejected the token — no pushes until a restart` | the server restarted with another token | fix the token, restart the client |
| no Telegram messages arrive, `telegram-inbound.log` shows a *Conflict* (another getUpdates) | another machine polls the same bot | stop the other poller (setup §5) |
| her day starts at the wrong hour (mood, quiet hours) | the container's clock zone | `LUMI_TZ` in `deploy/compose/<i>.env` (default `Europe/Kyiv`) |
| `status`: `waiting: main <sha> (CI pending)` / `(CI red)` / `(CI none)` | CI (gates or image) hasn't finished, failed, or hasn't started | wait / fix it on `v2-dev` and merge again |
| `status`: `waiting: main <sha> (behind what dev runs)` | dev runs a commit `main` doesn't contain | merge `v2-dev` into `main` (fast-forward) |
| `status`: `prod … waiting: vA.B.C` | a release awaits your approval | `deploy_agent.py confirm vA.B.C` |
| `status`: `bad: …`, `last deploy …: rolled back: <reasons>` | a deploy failed its smoke and went back | read the reasons + `deploy.log`; fix forward with a new commit / patch tag |
| `another deploy step is running — try again in a minute` | the timer's step holds the lock (a deploy can take minutes) | wait, then repeat the command |
| `rollback`: `ran before the containers — move the units' checkout by hand` | both refs ran as units (a v2.7 record) | [the way back](#the-way-back-the-v26-units) by hand |

## Security & scope

- **LAN/Tailscale only.** The compose file publishes on the box's LAN address; outside a container the server
  itself refuses a wildcard or public bind. Never forward the port on the router. On the home LAN the token crosses
  the wire in the clear (no TLS until v4.1) — the same trust as agora's panel.
- **Secrets only in `~/lumi/<instance>/secrets.yaml`** (`chmod 600`; the pre-v2.9 `.env.migrated` too) — never in the
  image, the compose files, a unit or a log; `lumi-cli config` masks them on every output. The image's build context
  excludes `.env*`, `settings.yaml`, `secrets.yaml`, `.git` and `.lumi` (`.dockerignore`).
- **The pipeline holds no secret:** CI needs none beyond the workflow's own `GITHUB_TOKEN` (which pushes the image),
  the agent reads GitHub and GHCR without a token, and the instance token it sends to `/v1/state` stays on the box,
  unlogged.
- **The containers run as your user** (uid 1000), so her data root stays yours; `init: true` reaps her ffmpeg
  children and passes SIGTERM to Python. Docker's own copy of their stderr is capped at 3 × 10 MB per container;
  her logs live in the data root.
- Not here yet: editing settings from the panel (**v2.11**); TLS, public exposure and the security
  scans (**v4.1**); voice mode, the voicer and dictation beside the server (revisited after v2.6).
