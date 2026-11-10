# Prod & dev — two Лілі, one codebase (v2.1)

The Лілі you live with runs from a **prod home outside `~/development`**, on a **release tag**, with her
real memory. The working tree stays **dev** — test data, free to break. This is the operator's guide:
install prod once, migrate her memory once, then update / roll back with one script. The design is in
[ROADMAP §v2.1](../specification/ROADMAP.md) and [ARCHITECTURE §Configuration](../specification/ARCHITECTURE.md).

> **Prod moved to the Linux box on 2026-10-20** (v2.1.1 on the Mac → v2.6.0 in the box's `~/lumi/prod`; the steps in
> [LINUX_DEPLOY §3](LINUX_DEPLOY.md)).
>
> - **What moved:** the whole data root — the store (18 844 messages, 2 951 facts, 372 summaries, 4 543 thoughts),
>   27 913 vectors, the journal, her files, the bus files and pointers, the logs, `env-marker` — as one archive,
>   `data-20261020-0116-v2.1.1-premove.tar.gz` (~290 MB): here in `~/lumi/prod/backups/`, and on the box in
>   `~/lumi/backups/`. Her `.env` was copied too and given the box's `LUMI_HOME`, host, port and a new token.
> - **This Mac prod is now a client:** its `.env` has `LUMI_SERVER=on` + the box's prod host/port/token, so
>   `~/lumi/prod/app/lumi` talks to the box. Its `data/` is the untouched **fallback**; its Telegram daemons never
>   start again (the box polls the prod bot).
> - **Prod code moves only by a confirmed tag through the box's deploy agent** (v2.7): a release waits until
>   `deploy_agent.py confirm vA.B.C`, then archives her data (the box's `~/lumi/prod/backups/`, the naming below)
>   and deploys with a smoke and an automatic rollback; `deploy_agent.py rollback prod` is the one-command rollback
>   ([LINUX_DEPLOY §Deploys](LINUX_DEPLOY.md)). `prod_update.py` below now only
>   moves this Mac client's checkout.
> - **Back to the Mac** (if the box fails her): stop the box's prod services, copy the box's `~/lumi/prod/data`
>   back here if it is newer than the fallback (it is, after any conversation), restore
>   `~/lumi/prod/.env.bak-premove-20261020-0118`, start the Mac prod and its Telegram daemons — never both machines'
>   pollers at once.
>
> - **v2.9 — settings as files:** an instance's settings are `settings.yaml` + `secrets.yaml` (`0600`) beside its
>   `app/` (`~/lumi/prod/settings.yaml`), described by one schema; `.env` is a deprecated layer for this one release
>   (warned at start-up), migrated with `scripts/env_to_settings.py` (a dry run first; the old file kept as
>   `.env.migrated`). The box's instances: [LINUX_DEPLOY §2b](LINUX_DEPLOY.md#2b-settings-as-files-v29). This Mac
>   client: `cd ~/lumi/prod/app && uv run python scripts/env_to_settings.py ../.env` → the same with `--apply`
>   (the client's checkout at v2.9+; the files land beside `app/`).
>
> The rest of this guide describes the Mac prod as it ran from v2.1 to v2.6 — read each `.env` below as
> `settings.yaml` / `secrets.yaml` from v2.9 (the same names; each setting's env name is in `settings.example.yaml`).

## The 30-second model

```
~/development/lumi/          dev:  the working tree · .lumi/ (test data) · settings.yaml with instance.env: dev
~/lumi/prod/
  app/                       prod: a git checkout AT A RELEASE TAG (its venv: app/.venv)
  settings.yaml              instance.home: ~/lumi/prod/data · instance.env: prod   (v2.9; .env before)
  secrets.yaml               the keys (0600)
  data/                      her memory (store, vectors, journal, files, bus, logs)
  backups/                   data-<YYYYmmdd-HHMM>-<tag>.tar.gz — one per update
```

Three rules hold it together:

- **Code moves by tag, never by copy.** Prod changes only through `scripts/prod_update.py --tag …` — a
  `git checkout` of a release. Nothing is ever copied from the dev tree.
- **Updates move code, never data or config.** `data/` is only read (into a backup); the settings files are
  never written. Config is yours; the script only *tells* you what's new.
- **The env guard enforces it.** `data/` carries an `env-marker` (`prod`); the dev tree pointed at it
  refuses to start, and prod refuses to run from a dirty or untagged checkout. Every process shows which
  instance it is: the TUI status line starts with **`prod v2.1.0`** (bold red) or `dev v2.1.0` (dim).

Settings discovery needs no flag: the loader looks in the checkout (`app/`), then its parent, and finds
`~/lumi/prod/settings.yaml` + `secrets.yaml` (v2.9; a pre-v2.9 `.env` there is read as the deprecated layer). The
checkout itself has none — they're gitignored. `LUMI_SETTINGS_DIR` names another folder (the containers).

## Install (once)

```bash
mkdir -p ~/lumi/prod && cd ~/lumi/prod
git clone https://github.com/ichMaster/lumi.git app
git -C app checkout v2.1.0                 # the release to run (an annotated tag)
(cd app && uv sync --frozen --all-extras)  # app/.venv, exactly the tag's lock
cp app/settings.example.yaml settings.yaml && cp app/secrets.example.yaml secrets.yaml && chmod 600 secrets.yaml
```

Edit them (v2.9 — or `app/lumi-cli config set <key> <value> --dir ~/lumi/prod`, validated): copy your real keys
into `secrets.yaml` and your settings into `settings.yaml`, then set:

```yaml
instance:
  home: ~/lumi/prod/data
  env: prod
```

**Set no per-path setting** — `store.path`, `files.dir`, `journal.dir`, `bridge.inbox_path`,
`bridge.outbox_path`, `dictation.listen_flag`. An explicit path beats `instance.home`, and a relative one
(`files.dir: .lumi/files`) would resolve **inside `app/`** — her journal and files would quietly land in the
checkout instead of `data/`.

## Migrate her memory (once)

Her live memory is the dev tree's in-repo `.lumi/` today (~324 MB; the store runs on SQLite in WAL
mode, so **everything must be stopped** — a live copy can be inconsistent).

1. **Stop everything:** the TUI, both Telegram daemons, the voicer/dictator if running.
2. **Back up:** `tar -czf ~/lumi/lumi-pre-migration-$(date +%Y%m%d).tar.gz -C ~/development/lumi .lumi`
3. **Move it:** `mkdir -p ~/lumi/prod/data && mv ~/development/lumi/.lumi/* ~/lumi/prod/data/`
   (includes `store.db` + `store.db-wal` + `store.db-shm` — keep them together).
4. **Start prod first** (it writes the `prod` marker into `data/`): `~/lumi/prod/app/lumi`
5. **Verify:** the status line says `prod v…`; `/mood` shows today's mood; ask about something old
   (memory recall works); send a Telegram message after starting the daemons (below) and get a reply.
6. **Point dev at test data:** in `~/development/lumi/settings.yaml` set `instance.env: dev` (and keep
   `instance.home` unset — dev uses the fresh, empty `.lumi/`). A cheap model profile for dev saves money.

Start prod *before* dev touches anything: the first instance to open an unmarked root marks it.

## Run

*From v2.2 prod can also run as a server + client — give prod its own `server.port` (8765)
+ `server.token` and follow [SERVER_SETUP.md](SERVER_SETUP.md); the in-process way below stays the
default until the brain moves into the server (v2.5).*

```bash
~/lumi/prod/app/lumi                                          # the prod TUI
cd ~/lumi/prod/app && uv run python -m telegram.inbound      # daemon 1 (separate terminal)
cd ~/lumi/prod/app && uv run python -m telegram.outbound     # daemon 2 (separate terminal)
```

Dev runs as always from `~/development/lumi` (`./lumi`). Both can run the same evening — they share no
file. Use a separate Telegram bot token for dev, or keep the dev bridge off (`bridge.enabled: false`), so
your Telegram chat talks to prod only.

## Update

Release from dev as usual (`/release-version` tags it and pushes). Then:

```bash
# stop the prod TUI + daemons first (the script refuses while they run)
python3 ~/development/lumi/scripts/prod_update.py --tag v2.1.1             # --home defaults to ~/lumi/prod
python3 ~/development/lumi/scripts/prod_update.py --tag v2.1.1 --dry-run   # see the plan first
```

It: refuses while prod runs → fetches tags and verifies the target (unknown tag → nothing changes) →
backs up `data/` into `backups/` → checks out the tag → `uv sync --frozen --all-extras` → prints the
running tag → lists the settings **new in this release's** example files that prod doesn't set (its
`settings.yaml` / `secrets.yaml`, or a pre-v2.9 `.env`). Add the ones you want (`lumi-cli config set`), then start
prod again.

**Hotfix:** never edit `~/lumi/prod/app` — the guard refuses a dirty checkout. Fix in dev (a branch from
the prod tag if dev has unrelated work), tag the patch release (`v2.1.1`), update prod to it.

## Roll back

A backup is named after the tag that was running when it was taken: `data-…-v2.1.0.tar.gz` is her
memory *as it was under v2.1.0*.

```bash
python3 ~/development/lumi/scripts/prod_update.py --tag v2.1.0     # code back (backs up the current data too)
cd ~/lumi/prod && mv data data.broken
tar -xzf backups/data-<time>-v2.1.0.tar.gz -C ~/lumi/prod          # the latest backup labelled v2.1.0
~/lumi/prod/app/lumi
```

Only restore data if the newer release changed it badly — otherwise rolling back the code is enough.

## When the guard refuses

| Message | Meaning | Fix |
|---|---|---|
| `this data root belongs to prod, but LUMI_ENV is unset/dev` | the dev tree is pointed at her real memory | unset `instance.home` in the dev `settings.yaml` |
| `LUMI_ENV=prod, but this data root is marked 'dev'` | prod is pointed at test data | fix `instance.home` in `~/lumi/prod/settings.yaml` |
| `Lumi settings — refusing to start: … is not valid — allowed: …` | a wrong value in a settings file (v2.9) | fix that key (`lumi-cli config set` names the allowed values) |
| `HEAD is not at a release tag` | prod is on a branch/commit | `prod_update.py --tag <release>` |
| `modified tracked files: …` | someone edited the prod checkout | `git -C ~/lumi/prod/app checkout -- .`, fix in dev |

Emergency only: `LUMI_ALLOW_DIRTY_PROD=1` (or `instance.allow_dirty_prod: true`) lets prod start from a dirty/untagged checkout (warned on every
start). It never bypasses the marker checks.

## Before v2.1.0 exists (the first rehearsal)

The guard accepts any exact tag, so the very first prod install can run on a pre-release tag
(`git tag -a v2.1.0-rc1 -m "…" && git push origin v2.1.0-rc1`). The badge then shows the tree's
`VERSION` (still the previous release) — expected. When `v2.1.0` is cut, `prod_update.py --tag v2.1.0`
brings prod onto it, which doubles as the first real update.
