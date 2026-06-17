# Telegram bridge — setup & monitoring (v0.13)

Reach Лілі from **Telegram** while she runs on your laptop. This is the operator's guide: how to
connect it, run it, watch that it's working, and fix it when it isn't. The design rationale lives in
[../specification/features/TELEGRAM.md](../specification/features/TELEGRAM.md).

## How it works (30-second mental model)

The **TUI is the only brain** (the one process that calls `core.reply`). Telegram is reached through
a **file bus** plus two **dumb daemons** — so the TUI never touches Telegram and there's only one
writer to the conversation store.

```
keyboard ─┐
Telegram ─┴─► inbox.jsonl ─► [ TUI = brain ] ─► core.reply ─► outbox.jsonl ─► Telegram
                ▲  (poll, FIFO)        │                            ▲  (FIFO, N-batch)
          daemon 1 (telegram→inbox)  (store)               daemon 2 (outbox→telegram)
```

You run **three processes**: the **TUI** + **daemon 1** (inbound) + **daemon 2** (outbound).

**Two facts to keep in mind:**
- The **TUI must be running** for replies — it's the brain. With it down, daemon 1 keeps buffering
  into `inbox.jsonl`; nothing answers until the TUI is back.
- This is the **single-owner** bot: the Telegram user *is* the owner, one relationship, one session.
  Multi-user / always-on belong to the v2.1/v2.3 server.

---

## Setup

### 0. Install the Telegram dependency (once)

`aiogram` ships as an optional extra (the core + tests never need it). Sync **all** extras so the
dev tools stay too:

```bash
uv sync --all-extras
```

> ⚠️ Don't run `uv sync --extra telegram` alone — it drops the dev tools (pytest/ruff). Use
> `--all-extras`.

### 1. Create the bot → get the token (Telegram, ≈2 min)

1. Open **[@BotFather](https://t.me/BotFather)** → **Start**.
2. Send `/newbot`; pick a **name** (e.g. `Лілі`) and a **username** ending in `bot` (must be unique).
3. BotFather replies with a **token** like `8123456789:AAH9x...`. Copy it. **This is a secret** —
   anyone with it controls the bot.

### 2. Get your Telegram user id (≈30 sec)

1. Open **[@userinfobot](https://t.me/userinfobot)** → **Start**.
2. It replies `Id: 123456789`. That number is your id — the **allowlist** (only you reach Лілі).

### 3. Open a chat with your bot

Search your bot's `@username`, open it, press **Start** — so it's allowed to message you back.

### 4. Configure `.env` (gitignored — the token is never committed)

```ini
LUMI_BRIDGE=on                                  # the TUI joins the file bus
LUMI_TELEGRAM_TOKEN=8123456789:AAH9x...         # from BotFather (SECRET)
LUMI_TELEGRAM_ALLOWLIST=123456789               # your id from @userinfobot (comma-separate for more)
LUMI_TELEGRAM_FLUSH_S=2                          # daemon 1: consolidate a burst, flush every N s
LUMI_TELEGRAM_BATCH=5                            # daemon 2: max replies merged per message
LUMI_TELEGRAM_CATCHUP_H=24                       # daemon 2: skip replies older than this on restart
LUMI_TELEGRAM_PHOTO=0                            # daemon 2: probability 0..1 of attaching the face photo (0.2 ≈ 1/5)
```

### 5. Pre-flight check (before launching all three)

One command — verifies the config loads **and** the bot actually connects (a `getMe`, with a hard
timeout so it can't hang), **without ever printing the token**:

```bash
uv run python -m telegram.check
```

A healthy result ends with:

```
bridge enabled:   True
token set:        True
token format ok:  True
allowlist:        (123456789,)

✓ Connected as @your_bot (id ...). Bridge is ready.
```

If instead you see `✗`, it names the cause: token not set / wrong format, empty allowlist, a
`getMe timed out` (can't reach `api.telegram.org`), or a `getMe failed` with the Telegram error.

> Don't paste a multi-line `python -c "..."` block — your shell may treat the newlines as an
> unfinished command and just sit at a continuation prompt (looks like a hang). Use the one-liner
> above.

### 6. Run the three processes

Open three terminals (or `tmux` panes) at the repo root:

```bash
./lumi                               # 1) the TUI (the brain) — keep it focused/open
uv run python -m telegram.inbound    # 2) daemon 1: Telegram → inbox
uv run python -m telegram.outbound   # 3) daemon 2: outbox → Telegram
```

Each daemon should log an `… up: …` line on start (in its terminal and `.lumi/telegram-*.log`); if
one exits immediately, it prints the reason (e.g. a missing token). Then message your bot in
Telegram. Within ~3 s a `📱 Telegram` line appears in the TUI, Лілі replies, and her reply lands back
in your Telegram chat.

---

## Monitoring

Two layers of visibility: the **daemon logs** (what each daemon *decided* — flushes, sends, blocks,
errors) and the **bus files** (what's *queued* — the data and the pointers). Logs are the first place
to look; the bus files tell you where a message is stuck.

### The monitor (start here)

One command shows the whole bridge — queue depths, a health verdict, recent traffic both ways, and
the tail of each daemon log (local-only, no network):

```bash
uv run python -m telegram.monitor            # one-shot snapshot
uv run python -m telegram.monitor --watch    # live, refresh every 2s (Ctrl+C to stop)
uv run python -m telegram.monitor --lines 8  # show more recent records / log lines
```

```
Lumi Telegram bridge  2026-06-10 22:44:46
  bridge: ON   inbox=.lumi/inbox.jsonl   outbox=.lumi/outbox.jsonl

  queue    total  pointer  pending
  inbox        1       1        0  ✓
  outbox     112     112        0  ✓

  health: ✓ healthy (both queues drained)

  recent inbox (Telegram → you):
    19:44 id=1  'привіт ))) я вже з телеграму'
  recent outbox (Лілі → Telegram):
    19:44 id=112 tender  'Привіт! З телеграму — то бот таки ожив?...'
  … (+ the tail of telegram-inbound.log / telegram-outbound.log)
```

**Healthy** = `bridge: ON`, both `pending` are 0 (`✓`), and the logs show recent `flushed`/`sent`
lines with no `WARNING`/`ERROR`. A stuck `pending` + a clean log → the **consumer** is down (TUI for
inbox, daemon 2 for outbox); a `⚠` in the health line names which.

The raw building blocks (if you want them directly):

```bash
tail -n 3 .lumi/telegram-inbound.log .lumi/telegram-outbound.log
uv run python -c "from state import fifo; p=lambda f,s: len(fifo.read_since(f, fifo.load_pointer(s))); print('inbox pending:', p('.lumi/inbox.jsonl','.lumi/inbox.pos'), '| outbox pending:', p('.lumi/outbox.jsonl','.lumi/outbox.sent'))"
```

### The bus files at a glance

| file | written by | read by | holds |
|---|---|---|---|
| `.lumi/inbox.jsonl` | daemon 1 | the TUI | your incoming Telegram messages (consolidated bursts) |
| `.lumi/inbox.pos` | the TUI | — | last inbox id the TUI has run |
| `.lumi/outbox.jsonl` | the TUI | daemon 2 | Лілі's replies (emotion + intensity) |
| `.lumi/outbox.sent` | daemon 2 | — | last outbox id daemon 2 has sent |

### Live watch

```bash
# what's arriving from Telegram (daemon 1 → inbox)
tail -f .lumi/inbox.jsonl

# what Лілі is saying (TUI → outbox → daemon 2)
tail -f .lumi/outbox.jsonl

# both pointers (how far each consumer has gotten)
watch -n1 'echo "inbox.pos = $(cat .lumi/inbox.pos 2>/dev/null || echo 0)"; echo "outbox.sent = $(cat .lumi/outbox.sent 2>/dev/null || echo 0)"'
```

### Reading the pending counts

The pending counts (from the quick health check above) localize *where* a message is stuck:
- `inbox pending` stays > 0 → the **TUI isn't consuming** (TUI not running, busy, or `LUMI_BRIDGE` off).
- `outbox pending` stays > 0 → **daemon 2 isn't sending** (daemon down, bad token, or wrong chat id).
- Both ~0 with traffic flowing → healthy.

### Daemon logs

Each daemon logs to **stderr** *and* a file under `.lumi/`, so you can `tail -f` it even when the
daemon runs in the background:

```bash
tail -f .lumi/telegram-inbound.log     # daemon 1: startup, flushes, blocked senders, get_updates errors
tail -f .lumi/telegram-outbound.log    # daemon 2: startup, catch-up skips, sends, send errors
```

What you'll see (counts + ids only — **never the token or message text**):

```
2026-06-10 14:22:01 INFO lumi.telegram.inbound: inbound up: allowlist=[123456789], flush=2s, inbox=.lumi/inbox.jsonl
2026-06-10 14:22:09 INFO lumi.telegram.inbound: flushed 2 message(s) → inbox id=7
2026-06-10 14:22:09 WARNING lumi.telegram.inbound: ignored non-allowlisted id=555
2026-06-10 14:22:10 INFO lumi.telegram.outbound: sent 1 reply(ies) (ids 7..7) → 1 chat(s)
2026-06-10 14:25:40 WARNING lumi.telegram.inbound: get_updates failed (retrying): <error>
```

- A clean start logs an `… up: …` line and then blocks (long-poll).
- A **config** problem exits immediately with the reason (`LUMI_TELEGRAM_TOKEN is not set`, etc.).
- A **transient** Telegram/network error is logged and **retried** (the daemon does **not** die);
  daemon 2 does **not** advance its pointer on a failed send, so nothing is lost.
- Set `LUMI_LOG_LEVEL=DEBUG` for more detail.

---

## Troubleshooting

**Look at the daemon logs first** (`.lumi/telegram-inbound.log` / `-outbound.log`) — a `WARNING`/
`ERROR` line usually names the cause. Then:

| Symptom | Likely cause | Fix |
|---|---|---|
| Bot never replies, but `📱` shows in the TUI | daemon 2 down / wrong chat id | start `telegram.outbound`; check `outbox pending` + `telegram-outbound.log` |
| Bot never replies, **no** `📱` in the TUI | daemon 1 down, or `LUMI_BRIDGE` off, or TUI not running | check `inbox pending`, `LUMI_BRIDGE=on`, that `./lumi` is running |
| A daemon's terminal went quiet / it exited | crash or config error | read the tail of its `.lumi/telegram-*.log` (it logs the reason on exit), then restart it |
| Your messages are ignored entirely | your id isn't in `LUMI_TELEGRAM_ALLOWLIST` | `telegram-inbound.log` shows `ignored non-allowlisted id=…` — put that id in the allowlist |
| `TelegramUnauthorizedError` in a log | bad/rotated token | re-copy from BotFather into `.env` |
| `terminated by other getUpdates request` | **two** inbound daemons polling the same bot | run only **one** `telegram.inbound` |
| A flood of old replies on restart | a long backlog in `outbox.jsonl` | that's the **catch-up cap**; lower `LUMI_TELEGRAM_CATCHUP_H` |
| Burst of messages → several separate replies | flush window too short for your typing | raise `LUMI_TELEGRAM_FLUSH_S` |
| `ModuleNotFoundError: aiogram` | extra not installed | `uv sync --all-extras` |

A **transient** network/Telegram error doesn't need action — the daemon logs it as a `WARNING` and
**retries** on its own (it won't die, and daemon 2 won't drop the message).

**Reset the bus** (start clean — safe; it's only runtime data):
```bash
rm -f .lumi/inbox.jsonl .lumi/inbox.pos .lumi/outbox.jsonl .lumi/outbox.sent
```
(Does **not** touch memory/closeness/thoughts — those live in `.lumi/store.json`.)

---

## Config reference

| env var | default | meaning |
|---|---|---|
| `LUMI_BRIDGE` | `off` | the TUI joins the file bus (reads inbox, writes outbox) |
| `LUMI_TELEGRAM_TOKEN` | — | the BotFather token (**secret**, never logged/committed) |
| `LUMI_TELEGRAM_ALLOWLIST` | — | comma-separated Telegram id(s); only these are served |
| `LUMI_TELEGRAM_FLUSH_S` | `2` | daemon 1: in-memory buffer flush cadence (a burst → one turn) |
| `LUMI_TELEGRAM_BATCH` | `5` | daemon 2: max replies merged per message (bounds a backlog → ⌈M/N⌉, never one blob) |
| `LUMI_TELEGRAM_CATCHUP_H` | `24` | daemon 2: skip replies older than this on restart (no flood) |
| `LUMI_TELEGRAM_PHOTO` | `0` | daemon 2: probability 0..1 of attaching the face photo (`0`=never, `0.2`≈1/5, `1`=always) |
| `LUMI_TELEGRAM_VOICE` | `off` | daemon 2: send replies as **voice messages** (reuses the `LUMI_VOICE_*` key/id + `ffmpeg`) |
| `LUMI_INBOX_PATH` | `.lumi/inbox.jsonl` | inbound queue file |
| `LUMI_OUTBOX_PATH` | `.lumi/outbox.jsonl` | outbound queue file |
| `LUMI_LOG_LEVEL` | `INFO` | daemon log verbosity (`DEBUG`/`INFO`/`WARNING`) |

## Photos (`LUMI_TELEGRAM_PHOTO`)

`LUMI_TELEGRAM_PHOTO` is a **probability 0..1** — the chance daemon 2 attaches the face photo to a
reply (`0` = never, `0.2` ≈ 1 in 5, `1` = always; `on`/`off` still work as `1`/`0`). When it fires,
the reply is sent as a **photo with the text as the caption** — the face matched to the reply's
**emotion + intensity** (`faces/<emotion>.png`); otherwise it's a plain text message. Two things to know:

- It's the **flat** v0.7 face, **not** the themed v0.11 wardrobe — the daemon has no access to the
  mood/theme (that's Core state). The desktop viewer shows the themed face; Telegram gets the base one.
- Telegram caps a **caption at 1024 chars**. A long reply (or a big N-batch) that wouldn't fit falls
  back to a **plain text message** (chunked to ≤4096) — so a long reply never wedges the daemon.

### Pictures she chooses to send (`send_image`, v0.24)

Separate from the random face above: when the **image tool** is on (`LUMI_IMAGE=on`), Лілі can **decide**
to send you a real picture from her sandbox via the `send_image` tool (see [IMAGE_SETUP.md](IMAGE_SETUP.md)).
Daemon 2 sends such a record as a **photo always** (independent of `LUMI_TELEGRAM_PHOTO`, which only governs
the decorative face) and **on its own** — a chosen picture is never merged into an N-batch. Her words ride
as the caption (caption-capped, with the same long-text fallback). If the file has vanished by send time,
the daemon sends just her words rather than crash. This holds **in voice mode too** (`LUMI_TELEGRAM_VOICE`):
her spoken replies are voiced, but a picture she chose to send still arrives as a **photo**, not voiced.

## Security & scope

- The **token is a secret** — it lives only in `.env` (gitignored). Don't paste it into commits,
  logs, or screenshots. If it leaks, `/revoke` in BotFather and replace it.
- The **allowlist** is the access boundary — only listed ids reach Лілі. There's no open sign-up.
- **Single-owner, TUI-must-run.** For a 24/7, multi-user, always-on Лілі (the core hosted without
  the TUI), that's the **v2.1 server** + **v2.3 accounts** — not this bridge.
