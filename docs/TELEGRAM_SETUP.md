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
  Multi-user / always-on belong to the v1.1/v1.3 server.

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
LUMI_TELEGRAM_PHOTO=off                          # daemon 2: also send the face portrait as a photo
```

### 5. Pre-flight check (before launching all three)

Verify the config loads and the bot actually connects — **catches a typo before you start everything**:

```bash
# config loaded? (prints whether the token is set + the allowlist — never prints the token)
uv run python -c "from core.config import load_config as c; x=c(); print('token set:', bool(x.telegram_token), '| allowlist:', x.telegram_allowlist, '| bridge:', x.bridge)"

# does the token connect? (a getMe call — prints the bot's @username on success)
uv run python -c "
import asyncio
from aiogram import Bot
from core.config import load_config
async def main():
    b = Bot(load_config().telegram_token)
    me = await b.get_me(); print('connected as @' + me.username)
    await b.session.close()
asyncio.run(main())"
```

A `TelegramUnauthorizedError` here means a bad token; `bridge: False` means `LUMI_BRIDGE` isn't `on`.

### 6. Run the three processes

Open three terminals (or `tmux` panes) at the repo root:

```bash
./lumi                               # 1) the TUI (the brain) — keep it focused/open
uv run python -m telegram.inbound    # 2) daemon 1: Telegram → inbox
uv run python -m telegram.outbound   # 3) daemon 2: outbox → Telegram
```

Then message your bot in Telegram. Within ~3 s a `📱 Telegram` line appears in the TUI, Лілі replies,
and her reply lands back in your Telegram chat.

---

## Monitoring

Everything observable lives in the bus files under `.lumi/` (or wherever `LUMI_INBOX_PATH` /
`LUMI_OUTBOX_PATH` point). The two queues are append-only JSONL; the two `.pos`/`.sent` files are the
consumer pointers (the last id processed).

### The files at a glance

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

### Is anything stuck? (pending = last id − pointer)

```bash
# pending = unread records past each consumer's pointer (both should hover at 0 when healthy)
uv run python -c "
from state import fifo
inbox  = fifo.read_since('.lumi/inbox.jsonl',  fifo.load_pointer('.lumi/inbox.pos'))
outbox = fifo.read_since('.lumi/outbox.jsonl', fifo.load_pointer('.lumi/outbox.sent'))
print('inbox pending (TUI unread): ', len(inbox))
print('outbox pending (unsent):    ', len(outbox))"
```

**Reading it:**
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

| Symptom | Likely cause | Fix |
|---|---|---|
| Bot never replies, but `📱` shows in the TUI | daemon 2 down / wrong chat id | start `telegram.outbound`; check `outbox pending` and the daemon-2 terminal |
| Bot never replies, **no** `📱` in the TUI | daemon 1 down, or `LUMI_BRIDGE` off, or TUI not running | check `inbox pending`, `LUMI_BRIDGE=on`, that `./lumi` is running |
| Your messages are ignored entirely | your id isn't in `LUMI_TELEGRAM_ALLOWLIST` | put your @userinfobot id there (numbers only) |
| `TelegramUnauthorizedError` on a daemon | bad/rotated token | re-copy from BotFather into `.env` |
| `terminated by other getUpdates request` | **two** inbound daemons polling the same bot | run only **one** `telegram.inbound` |
| A flood of old replies on restart | a long backlog in `outbox.jsonl` | that's the **catch-up cap** working only if set; lower `LUMI_TELEGRAM_CATCHUP_H` |
| Burst of messages → several separate replies | flush window too short for your typing | raise `LUMI_TELEGRAM_FLUSH_S` |
| `ModuleNotFoundError: aiogram` | extra not installed | `uv sync --all-extras` |

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
| `LUMI_TELEGRAM_PHOTO` | `off` | daemon 2: also send the face portrait as a photo |
| `LUMI_INBOX_PATH` | `.lumi/inbox.jsonl` | inbound queue file |
| `LUMI_OUTBOX_PATH` | `.lumi/outbox.jsonl` | outbound queue file |
| `LUMI_LOG_LEVEL` | `INFO` | daemon log verbosity (`DEBUG`/`INFO`/`WARNING`) |

## Security & scope

- The **token is a secret** — it lives only in `.env` (gitignored). Don't paste it into commits,
  logs, or screenshots. If it leaks, `/revoke` in BotFather and replace it.
- The **allowlist** is the access boundary — only listed ids reach Лілі. There's no open sign-up.
- **Single-owner, TUI-must-run.** For a 24/7, multi-user, always-on Лілі (the core hosted without
  the TUI), that's the **v1.1 server** + **v1.3 accounts** — not this bridge.
