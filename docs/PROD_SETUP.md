# Prod & dev — two Лілі, one codebase (v2.1)

The Лілі you live with runs from a **prod home outside `~/development`**, on a **release tag**, with her
real memory. The working tree stays **dev** — test data, free to break. This is the operator's guide:
install prod once, migrate her memory once, then update / roll back with one script. The design is in
[ROADMAP §v2.1](../specification/ROADMAP.md) and [ARCHITECTURE §Configuration](../specification/ARCHITECTURE.md).

## The 30-second model

```
~/development/lumi/          dev:  the working tree · .lumi/ (test data) · .env with LUMI_ENV=dev
~/lumi/prod/
  app/                       prod: a git checkout AT A RELEASE TAG (its venv: app/.venv)
  .env                       LUMI_HOME=~/lumi/prod/data · LUMI_ENV=prod · the keys
  data/                      her memory (store, vectors, journal, files, bus, logs)
  backups/                   data-<YYYYmmdd-HHMM>-<tag>.tar.gz — one per update
```

Three rules hold it together:

- **Code moves by tag, never by copy.** Prod changes only through `scripts/prod_update.py --tag …` — a
  `git checkout` of a release. Nothing is ever copied from the dev tree.
- **Updates move code, never data or config.** `data/` is only read (into a backup); `.env` is never
  written. Config is yours; the script only *tells* you what's new.
- **The env guard enforces it.** `data/` carries an `env-marker` (`prod`); the dev tree pointed at it
  refuses to start, and prod refuses to run from a dirty or untagged checkout. Every process shows which
  instance it is: the TUI status line starts with **`prod v2.1.0`** (bold red) or `dev v2.1.0` (dim).

`.env` discovery needs no flag: the config walks up from `app/core/` and finds `~/lumi/prod/.env`
(the checkout itself has no `.env` — it's gitignored).

## Install (once)

```bash
mkdir -p ~/lumi/prod && cd ~/lumi/prod
git clone https://github.com/ichMaster/lumi.git app
git -C app checkout v2.1.0                 # the release to run (an annotated tag)
(cd app && uv sync --frozen --all-extras)  # app/.venv, exactly the tag's lock
cp app/.env.example .env                   # then edit — see below
```

Edit `~/lumi/prod/.env`: copy your real keys and settings over from the dev `.env`, then set:

```bash
LUMI_HOME=~/lumi/prod/data
LUMI_ENV=prod
```

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
6. **Point dev at test data:** in `~/development/lumi/.env` set `LUMI_ENV=dev` (and keep `LUMI_HOME`
   unset — dev uses the fresh, empty `.lumi/`). A cheap model profile for dev saves money.

Start prod *before* dev touches anything: the first instance to open an unmarked root marks it.

## Run

```bash
~/lumi/prod/app/lumi                                          # the prod TUI
cd ~/lumi/prod/app && uv run python -m telegram.inbound      # daemon 1 (separate terminal)
cd ~/lumi/prod/app && uv run python -m telegram.outbound     # daemon 2 (separate terminal)
```

Dev runs as always from `~/development/lumi` (`./lumi`). Both can run the same evening — they share no
file. Use a separate Telegram bot token for dev, or keep the dev bridge off (`LUMI_BRIDGE=off`), so
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
running tag → lists `.env.example` keys **new in this release** that your prod `.env` doesn't set. Add
the ones you want by hand, then start prod again.

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
| `this data root belongs to prod, but LUMI_ENV is unset/dev` | the dev tree is pointed at her real memory | unset `LUMI_HOME` in the dev `.env` |
| `LUMI_ENV=prod, but this data root is marked 'dev'` | prod is pointed at test data | fix `LUMI_HOME` in `~/lumi/prod/.env` |
| `HEAD is not at a release tag` | prod is on a branch/commit | `prod_update.py --tag <release>` |
| `modified tracked files: …` | someone edited the prod checkout | `git -C ~/lumi/prod/app checkout -- .`, fix in dev |

Emergency only: `LUMI_ALLOW_DIRTY_PROD=1` lets prod start from a dirty/untagged checkout (warned on every
start). It never bypasses the marker checks.

## Before v2.1.0 exists (the first rehearsal)

The guard accepts any exact tag, so the very first prod install can run on a pre-release tag
(`git tag -a v2.1.0-rc1 -m "…" && git push origin v2.1.0-rc1`). The badge then shows the tree's
`VERSION` (still the previous release) — expected. When `v2.1.0` is cut, `prod_update.py --tag v2.1.0`
brings prod onto it, which doubles as the first real update.
