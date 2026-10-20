# What runs on the server — status

**As of 2026-10-20.** What works when Лілі runs as a server on the Linux box, what doesn't, and when the missing
parts are planned (or whether they were dropped). Update this page when a phase ships.

**Legend:** ✅ works · ⚠️ works with a caveat · ❌ not available on the server · 🗓 planned (phase) ·
🗑 deprecated / dropped. **Seen** = actually exercised on the box since the move; **set** = enabled in the
box's settings but not exercised there yet.

## Where everything runs today

| Instance | Where | How it runs | Code | Status |
|---|---|---|---|---|
| **prod** — her real memory | Linux box `slate-server` (192.168.1.197), `~/lumi/prod` | systemd user services: `lumi-server@prod` (port 8765), `lumi-telegram-inbound@prod`, `lumi-telegram-outbound@prod` | tag `v2.6.0` | ✅ since **2026-10-20 01:19** — moved from the Mac (messages, vectors, facts, summaries, thoughts all present) |
| **dev** — test data | Linux box `slate-server` (192.168.1.197), `~/lumi/dev` | systemd user services: `lumi-server@dev`, `lumi-telegram-inbound@dev`, `lumi-telegram-outbound@dev` | `v2-dev` (2.6.0 + its review fixes); from the v2.7 install it **follows `main`** through the deploy agent | ✅ since 2026-10-19 23:07 |
| **Mac prod** | Mac, `~/lumi/prod` | a **client** of the box's prod (`.env`: `LUMI_SERVER=on`) — `~/lumi/prod/app/lumi` talks to the box; its local `data/` is the untouched **fallback** (+ the pre-move archive in `backups/`) | `v2.6.0` | ✅ — never start its Telegram daemons again (the box polls the prod bot) |
| **Mac dev tree** | `./lumi-client`, `./lumi-cli` → `192.168.1.197:8766` (the box's dev) | the TUI/CLI over the API (LAN, ufw rule for 192.168.1.0/24) | `v2-dev` | ✅ seen |

The box also runs **matrix-agora** (Matrix :8008, its panel :8090); Lumi's ports are 8765 (prod) and 8766 (dev).

## The server's own services (on the box)

| Service | Status | Seen | Notes |
|---|---|---|---|
| Server API v1 — turns (streamed), commands, typed `%directives`, sessions | ✅ | seen | a turn answered at 2026-10-19 23:27 |
| Pushes to clients — state, `💭` thoughts, conversation lines, the "while you were away" replay | ✅ | seen | the replay showed on the first client start |
| Brain host — the scheduler, the fast tick (needs / inner life), the idle think / nudge | ✅ | seen | 4 thoughts fired with no client attached since the move |
| Telegram in — text messages | ✅ | set | dev bot, allowlist = the owner |
| Telegram in — voice messages → text (STT) | ✅ | set | `LUMI_TELEGRAM_STT=on` |
| Telegram out — text + **voice replies** | ✅ | seen | needed `ffmpeg` on the box — installed 2026-10-19 (two stuck replies then went out) |
| Telegram out — her face photo (`LUMI_TELEGRAM_PHOTO=0.2`), pictures she sends (`send_image`) | ✅ | set | the face pack is in the checkout |
| `systemctl stop` closes her session (summary + facts), like Ctrl+C | ✅ | set | the v2.6 fix (LUMI-230); a manual DoD check still to run on the box |
| Restart on failure; a taken port / a bad bind refused without a restart loop | ✅ | set | v2.6 (exit 78) |
| Reachable only from the LAN | ✅ | seen | bind 192.168.1.197 + ufw `8765:8766` from 192.168.1.0/24 |
| **CI** — ruff + pytest on every push / pull request (GitHub Actions, no secret) | ✅ | seen | v2.7 (LUMI-233), green on Linux since 2026-10-20 |
| **The deploy agent** — a green `main` → dev by itself; a release tag → prod after `confirm` (her data archived first); smoke + automatic rollback; `rollback dev\|prod` in one command | 🗓 | — | v2.7 (LUMI-235) — written and tested; installed on the box with the v2.7 rollout (LUMI-237). Guide: [LINUX_DEPLOY §Deploys](LINUX_DEPLOY.md) |
| **The bounded turn** — one 180 s deadline per turn across the stream, the fallback and the retries; a stuck turn ends with a readable line | 🗓 | — | v2.7 (LUMI-234) — in `v2-dev`; reaches dev with the merge into `main`, prod with `v2.7.0` |

## Her features through the server

| Feature | On the server | Seen | Notes / when |
|---|---|---|---|
| Chat, streamed replies, the think box | ✅ | seen | |
| Slash commands (`/mood`, `/biorhythm`, `/closeness`, `/style`, `/model`, `/model-set`, `/recall`, `/memory`, `/forget`, `/journal`, `/new`, `/latency`, …) | ✅ | partly | run in the server's command layer |
| `/latency` with the `NETWORK client↔server` line | ✅ | set | v2.6 (LUMI-229) |
| Daily mood + biorhythms + closeness + the face theme | ✅ | seen | computed on the server (2026-10-19: theme `vigil`) |
| Memory — summaries, facts, semantic recall (Voyage) | ✅ | seen | the recall tool ran 3× since the move |
| Thoughts — `%think`, `%wonder`, `%catchup`, `%learn`, scheduled acts | ✅ | seen | spoken ones reach Telegram |
| Tools — files (sandbox), Wikipedia, news (Guardian), web lookup, journal, date | ✅ | set | her files now live on the box: `~/lumi/dev/data/files` |
| `/image` — you show her a picture | ✅ | set | the client sends the file; follows the **server's** `LUMI_IMAGE` (v2.6) |
| Image generation | ⚠️ | set | the PNG is saved on the box and reaches you **only through Telegram**; not shown on the Mac — 🗓 inline in the window in **v2.12** |
| **Her portrait in the viewer window (Mac)** | ❌ | — | the server writes `face.txt` on the box (no display) and the TUI client ignores the face state it receives. **Owner decision (2026-10-20): no interim fix** — her portrait follows a remote server in the 🗓 **v2.12** window (pywebview). Until then the Mac viewer doesn't follow a server — **prod included since the 2026-10-20 move**; her face photos still reach Telegram |
| Voice mode (`/mode-set voice`, realtime) | ❌ | — | needs the mic and speakers next to the core; the client says "not yet in server mode". No v2 phase — the remote voice path is to be revisited after v2.6; the roadmap's answer is web voice (🗓 **v5.2**). Telegram voice in/out covers it meanwhile |
| The voicer (her replies spoken on the Mac's speakers) | ❌ | — | same as voice mode — unscheduled in v2; 🗓 web voice **v5.2**; Telegram voice replies meanwhile |
| Dictation (the Mac mic → her inbox) | ❌ | — | same — unscheduled in v2; 🗓 web dictation **v5.4**; Telegram voice messages meanwhile |

## Known problems (open)

1. **Gemini 3.1 Pro preview is slow and sometimes times out** (Google-side; 2026-10-17/19). Turns take 1–4 min, and
   a turn can hang up to ~8 min (a streaming timeout → a blocking call with retries) while your input waits in the
   queue. Workaround: `/model gemini:gemini-2.5-flash` (faster, shallower). **Fixed in v2.7** (LUMI-234): a turn
   ends at 180 s with "the model didn't answer within 3 minutes — try again, or switch to a faster one with
   /model", and a line typed meanwhile waits at most that long — live on the box once v2.7 is deployed.
2. **Gemini 2.5 Flash sometimes returns an empty answer** for a thought — re-asked automatically; an occasional
   `%learn came back empty` remains.
3. A replayed thought in the client once showed a stray `ЕМОЦІЯ: thoughtfulthoughtful 0.7лагідна` tail — the stored
   thought is clean; **to investigate**.
4. ~~Prod (Mac, v2.1.1) lacked the dev fixes~~ — **resolved 2026-10-20**: prod runs v2.6.0 on the box, with the
   v2.5.1 mood fixes, the v2.4 single mood call and every server fix of v2.2–v2.6.

## Planned

| Phase | What changes on the server |
|---|---|
| **v2.6** | ✅ released 2026-10-20 (`v2.6.0`) — the dev instance on the box, the Mac as its client |
| **v2.7** | **in progress** — CI ✅ live; the deploy agent (a green `main` → dev, a confirmed tag → prod, smoke + rollback) and the bounded turn are written and reach the box with the v2.7 rollout. **Prod already moved** (2026-10-20) |
| **v2.8** | Lumi in **Docker** — one image (GHCR, like matrix-agora), a compose project per instance; the systemd units retired |
| **v2.9** | settings move from `.env` to `settings.yaml` + `secrets.yaml` under one schema |
| **v2.10** | the admin panel — watch: prod + dev health, uptime, containers, tokens / cost / latency, logs |
| **v2.11** | the admin panel — configure: settings editor, write-only secrets, rollback, audit, alerts through a dedicated **admin bot** |
| **v2.12** | the pywebview client — chat + her face + inline pictures in one window; **the portrait follows the server again** |
| **v4.1–v4.4** | TLS and security scans, multi-user, the web UI, user admin (extending the v2.10 panel) |
| **v5.1 / v5.2 / v5.4** | the web face, web voice, web dictation |

## Deprecated or dropped

| What | Status |
|---|---|
| `.env` as the settings file | 🗑 replaced by `settings.yaml` + `secrets.yaml` in **v2.9** (still read for one more release, then dropped) |
| Prod running in-process on the Mac | 🗑 **retired 2026-10-20** — the Mac prod is a client, its `data/` the fallback. `./lumi` in-process stays available for dev |
| "Switch model / config; restart" as part of the v4.4 user admin | 🗑 moved into the **v2.10–v2.11** panel |
| The v2.6 systemd user units | 🗑 retired by the **v2.8** Docker containers |
| The voicer / dictator apps next to a remote server | not deprecated, **not scheduled**: no phase brings them to the server; Telegram voice in/out and later web voice/dictation (v5.2/v5.4) take their place |

## How to check

```bash
curl -s http://192.168.1.197:8766/v1/health                 # from the Mac: {"ok": true, "env": "dev", …}
./lumi-cli status                                           # model, session, today's mood + theme
ssh slate-server 'systemctl --user status "lumi-*@dev"'     # the three services
ssh slate-server 'tail -f ~/lumi/dev/data/lumi.log'         # her log
ssh slate-server 'python3 ~/lumi/deploy/deploy_agent.py status'   # the pipeline (from the v2.7 install)
```

Guides: [LINUX_DEPLOY.md](LINUX_DEPLOY.md) (the box), [SERVER_SETUP.md](SERVER_SETUP.md) (the server and clients),
[TELEGRAM_SETUP.md](TELEGRAM_SETUP.md) (the bridge). The plan: [ROADMAP §v2](../specification/ROADMAP.md),
[ADMIN_PANEL.md](../specification/features/ADMIN_PANEL.md).
