# Admin panel — one window onto prod and dev (v2.9–v2.11)

**Status:** planned (owner request, 2026-10-19). Three phases before the pywebview client (now **v2.12**):
**v2.9** settings as files → **v2.10** the panel: watch → **v2.11** the panel: configure. Builds on v2.7 (the
pipelines; prod already on the box since 2026-10-20) and v2.8 (both instances as Docker containers on the box).

## Why

Each instance is configured by a ~28 KB `.env` (about 205 `LUMI_*` knobs + 9 API keys) plus `core/models.toml`
and `core/schedule.toml`. Changing one setting means SSH, an editor and a restart; knowing whether she is healthy
means reading `lumi.log` and the JSONL ledgers by hand (a slow Gemini, a cut-off mood, a bold `**ТЕМА:**` were all
found that way). The goal: **one admin panel on the box administers both instances** — health, uptime, daemons,
tokens, cost and latency at a glance, and every setting changed safely, with prod guarded.

## Shape

```
Mac browser ── LAN ──► lumi-admin (on the box, :8767, its own admin token)
                         ├─ settings  ~/lumi/{prod,dev}/settings.yaml + secrets.yaml   (read; write in v2.11)
                         ├─ ledgers   ~/lumi/{prod,dev}/data/cache-log.jsonl, usage-ledger.jsonl, lumi.log,
                         │            schedule.state, the bus pointers                  (read-only, always)
                         ├─ servers   GET /v1/health, /v1/state of each instance (its client token)
                         └─ docker    the Docker API (docker.sock): the lumi-prod / lumi-dev containers
```

- **A separate container**, `lumi-admin` (one per box, not per instance, its own compose project — like matrix-agora's
  panel), so it can stop, restart and watch both instances. Published on the LAN only, its own port (8767 — free on
  the box on 2026-10-19), its own `LUMI_ADMIN_TOKEN`, a ufw rule for the home subnet. It holds `docker.sock`, which is
  root-equivalent on the box — one more reason it stays LAN-only behind its own token.
- **It never runs the core and never writes a data root.** It reads ledgers and logs, asks each server over its API,
  changes config files (v2.11), and drives the containers through the Docker API. Her memory is out of its reach
  (the data roots are mounted read-only).
- **An instance registry**, `~/lumi/admin/admin.yaml`: each instance's name, root, server URL and unit names.
- **The UI** is static HTML/JS + a FastAPI JSON API (the matrix-agora panel pattern): no bundler, no node, charts as
  inline SVG. An instance switcher at the top; prod is always shown in red.

## v2.9 — Settings as files

- **Per instance:** `~/lumi/<instance>/settings.yaml` (every non-secret knob, grouped — model, mood, memory, recall,
  thoughts, scheduler, telegram, voice, tools, server, …) and `secrets.yaml` (`0600` — the API keys, the bot token,
  the server token). The dev tree keeps both at the repo root, gitignored; `settings.example.yaml` +
  `secrets.example.yaml` replace `.env.example`.
- **One typed schema** for every setting (the single source the loader, the example file, `lumi-cli` and the panel
  read): key, type, default, group, one-line description, `secret`, `restart` (needs a restart or applies live),
  allowed range / values, and its `LUMI_*` alias.
- **Precedence:** built-in default < `settings.yaml` / `secrets.yaml` < the process environment (`LUMI_*` still wins —
  tests, a systemd override, an emergency hatch). `.env` is read for one more release as a deprecated layer (a
  start-up warning names the migration), then dropped.
- **The authored TOMLs move to YAML** (`core/models.yaml`, `core/schedule.yaml`); an instance may override a model
  profile or a schedule row in its `settings.yaml`.
- **Migration:** `scripts/env_to_settings.py` — `.env` → `settings.yaml` + `secrets.yaml`, a dry-run diff first,
  unknown keys reported, the old file kept as `.env.migrated`. Run once on each instance (owner, on the box).
- **CLI:** `lumi-cli config get | set | validate` — validation errors name the key and the allowed values; secrets are
  masked on every output.

## v2.10 — The panel: watch

- **Overview** — per instance: up or down, version (the image tag), uptime and restarts (each container's start
  time and restart count), model and profile, today's mood and theme (and whether the mood completed),
  session and turns today, last turn age, errors in the last 24 h, data-root size. For the box: uptime, disk,
  load, memory.
- **Health checks** — named checks, each green / amber / red with its recent history: the server's health latency;
  the state readable; today's mood complete; the last successful model call and the recent model failures and
  timeouts; the Telegram daemons active and the inbox / outbox pointer lag; the scheduler's last firing against what
  was due; disk free; the env marker matching the instance. Polled every minute by the admin process into
  `~/lumi/admin/health.jsonl`. **No paid call**: the model is judged from the ledgers; a "probe the model now"
  button is opt-in and says it costs a call.
- **Services** — each instance's containers: state, since when, restarts; start / stop / restart. Prod actions ask
  for a typed confirmation.
- **Usage** — tokens and estimated cost per day, per model and per kind (reply, think, mood, housekeeping, …) from
  `cache-log.jsonl` + `usage-ledger.jsonl`; the cache hit ratio; turn latency median / p95 with the PRE / MODEL /
  POST split; the slowest calls. Prices come from a machine-readable `core/pricing.yaml` (from
  `docs/MODEL_PRICING.md`).
- **Logs** — tail and filter `lumi.log`, the daemons' logs and each container's log; by level, by text.

## v2.11 — The panel: configure

- **Settings editor** — forms generated from the schema, group by group: the current value, the default, the
  description, "needs a restart". Validation before saving, a diff before applying; apply writes the file
  atomically and restarts what needs it. Prod: the diff shown + a typed confirmation.
- **Secrets, write-only** — the panel shows set / unset and the last four characters; set, rotate, clear. The API never
  returns a secret. Rotating a server token says which clients need the new one.
- **History and rollback** — every apply keeps the previous file (`~/lumi/<instance>/config-history/<ts>.yaml`);
  one click restores it.
- **Schedule and model profiles** — edit the instance's overrides of `core/schedule.yaml` / `core/models.yaml`.
- **Promote dev → prod** — a setting tried on dev is copied to prod (the diff + the confirmation).
- **Audit log** — every admin action (when, which instance, what changed — never a secret value) in
  `~/lumi/admin/audit.jsonl`, shown in the panel.
- **Alerts** — a failing prod check or a unit that enters `failed` sends the owner one Telegram message (and one when
  it recovers), rate-limited — through a **dedicated admin bot** (its own BotFather token in the admin's own
  `secrets.yaml`, the owner's chat only), never through her prod or dev bot: alerts never mix with her conversation.

## Rules

- **One panel, two instances.** Prod is guarded (red, a shown diff, a typed confirmation); dev is free.
- **The admin never runs the core and never writes a data root** — config files and the Docker API only.
- **Secrets stay on the box**: `0600` files, never returned by the API, never logged, never in the audit log.
- **LAN/Tailscale only** (the bind guard + a ufw rule), its own token; no TLS until v4.1.
- **No paid calls** from the admin unless the owner presses the probe button.
- **Tests:** the admin API over a fake container runner and fixture data roots — no real Docker, no network, no
  paid calls in CI; contract tests pin the schema ↔ example ↔ loader and that no secret ever leaves the API.

## Not here

- Users, the allowlist, invites, consent, memory views — **v4.4**, which extends this panel.
- TLS, public exposure, security scans — **v4.1**.
- Editing her canon or other authored texts in the panel — not planned (they stay in the repo).

## Decisions

- **Alerts go through a dedicated admin bot** (owner, 2026-10-20) — not the prod bot.
- **The panel shows only the box's instances — prod and dev on the server** (owner, 2026-10-20). The Mac's dev tree
  is not an instance of the panel; it stays a local working copy.
