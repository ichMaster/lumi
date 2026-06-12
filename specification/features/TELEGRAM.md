# Telegram bot — Лілі in your pocket (v0.13)

Reach Лілі from **Telegram**: the same mind, a new window. The cleanest possible proof of the #1
design contract — **the core is interface-independent**. Crucially, the **TUI stays the only brain**
(it's the one process that calls `core.reply`); Telegram is reached through a **file bus** plus two
**dumb daemons**, so the TUI never imports a Telegram library and there's only ever **one writer to
the conversation store** (no concurrency clobber).

## The architecture: one brain, a file bus, two daemons

```
keyboard ─┐
Telegram ─┴─► inbox.jsonl ─► [ TUI = the brain ] ─► core.reply ─► (Лілі's reply) ─► outbox.jsonl ─► Telegram
                  ▲   (poll on idle, FIFO)              │                                  ▲   (FIFO, N-batch)
            daemon 1 appends                         (store)                         daemon 2 sends
```

- The **TUI** runs the turns. When it's idle (your turn), it reads the next record from
  **`inbox.jsonl`** and runs it as a turn — exactly as if you'd typed it. It writes **only Лілі's
  own messages** to **`outbox.jsonl`** (never your input — that's what prevents an echo).
- **Daemon 1 (`telegram → inbox`)** receives Telegram messages and appends them to `inbox.jsonl`.
- **Daemon 2 (`outbox → telegram`)** reads new `outbox.jsonl` records and sends them to Telegram.

The TUI is Telegram-agnostic — it only knows two files. The two daemons are ~30 lines each and never
touch the core. **One brain, two ears/mouths.**

## The file bus (FIFO + id pointers)

Both queues are **append-only JSONL FIFOs**, each `{"id": <monotonic>, "text": …, "ts": …}`, with
**exactly one writer and one reader** (so no locks):

| file | writer | reader (via pointer) |
|---|---|---|
| `inbox.jsonl` | daemon 1 | the **TUI** (`inbox.pos` = last id it ran) |
| `outbox.jsonl` | the **TUI** | daemon 2 (`outbox.sent` = last id it sent) |

Each consumer remembers the **last id** it processed (a tiny pointer file) and reads records with
`id > last` — id-based, so trimming old consumed records later doesn't break it (the voicer's
`spoken.jsonl` pattern). Anything that crosses a process boundary is a file; the rest is in-memory.

## Daemon 1 — inbound (`telegram → inbox.jsonl`)

- **Buffer + flush.** Incoming Telegram messages accumulate in an **in-memory** buffer; every
  `LUMI_TELEGRAM_FLUSH_S` (default **2 s**) the buffer is flushed as **one consolidated** `inbox`
  record (the lines joined) — so a quick burst ("привіт" … "як справи") becomes **one turn → one
  coherent reply**, not three. An empty buffer writes nothing.
- **No buffer file needed.** The buffer is transient and Telegram already replays un-acked updates,
  so durability is free: **ack Telegram (advance the `getUpdates` offset) only *after* the flush** —
  a crash before a flush → Telegram re-delivers on restart → nothing lost.
- **Allowlist at the edge.** Only the configured id(s) (`LUMI_TELEGRAM_ALLOWLIST`) are buffered; any
  other sender is ignored and **never enters the bus** (close-circle, no open sign-up).

## Daemon 2 — outbound (`outbox.jsonl → telegram`)

- **FIFO from the pointer.** Reads records with `id > outbox.sent`, in order, advances the pointer
  after sending.
- **Consolidate by N (config).** Up to **`LUMI_TELEGRAM_BATCH` = N** consecutive records are merged
  into **one** Telegram message — which also **bounds a backlog** (M records → ⌈M/N⌉ messages,
  **never** "the last several days as one message"). N is a config knob.
- **Catch-up cap.** After a long downtime, records older than `LUMI_TELEGRAM_CATCHUP_H` are **skipped**
  (pointer advanced silently) so a week offline doesn't flood you on restart.

## What the outbox carries (a symmetric mirror, echo-free by construction)

The TUI writes to `outbox.jsonl`:
- **Лілі's replies** (`kind="lili"` + emotion) — and her `💭` open thoughts if you want those on the phone;
- **your *keyboard* lines** (`kind="user"`) — so a turn you type in the terminal also shows on the
  phone (daemon 2 renders it with a `💻` prefix), the mirror twin of the `📱` inbox line the TUI shows
  for a Telegram message.

It **never** writes **Telegram-originated input** (that's already on the phone — re-sending it would
echo) and **never technical chrome** (`"Compacted 20 messages…"`). So the rule is *mirror the lines
that originated here* — a keyboard line and her reply — and the echo is solved by the rule itself, no
`source`-comparison needed: an inbox line simply never re-enters the outbox.

> On your phone you see: a Telegram message you sent (Telegram's own bubble) → Лілі's reply; and for a
> terminal turn, `💻 your line` → Лілі's reply. Both surfaces mirror both sides, once each.

## Scope: single-owner now, multi-user at v1.3

v0.13 is the **personal** bot: the Telegram user **is the owner** — the same `user_id`, the same
relationship (memory, closeness, the global thought-stream), and **one ongoing session**. Because the
TUI is the single brain over the local JSON store, the assumption is **one active interface at a time**
(you use the bot *or* the TUI; running both at once is the two-writer case). **Multiple users, parallel
isolated sessions, real auth, and concurrency** are the **v1.3 server** step — not pulled into v0.

## The one constraint

The **TUI must be running** for Telegram to get answers (it's the brain). With the TUI down, daemon 1
keeps buffering into `inbox.jsonl` and daemon 2 idles; replies flow when the TUI is back. A truly
**always-on, standalone** Telegram Лілі (the core running headless without the TUI) is the **v1.1
server**. So the laptop version: run the TUI + the two daemons; she's reachable on the phone while the
laptop is on.

## Proactive push — the payoff

The v0.12 idle-think timer runs **in the TUI** (where the brain is). A thought that **graduates to
spoken** is a normal Лілі message → it lands in `outbox.jsonl` → daemon 2 → a Telegram message. So she
**reaches out first**, as a notification — the thing the TUI alone can't do. Silent thoughts stay in
the diary; only spoken ones reach the phone (the v0.12 ratio/quiet-hours apply).

## Emotion + face

Her `outbox` line carries the **v0.5 emoji**; daemon 2 sends it. Optionally (`LUMI_TELEGRAM_PHOTO`)
daemon 2 attaches the **v0.11 `<theme>/<emotion>` portrait** as a Telegram photo — graceful to
text-only when no portrait exists.

## Config (`.env`, gitignored)

`LUMI_TELEGRAM_TOKEN` (the bot token — never committed) · `LUMI_TELEGRAM_ALLOWLIST` (the owner's
Telegram id) · `LUMI_TELEGRAM_FLUSH_S` (inbound buffer flush, default 2) · `LUMI_TELEGRAM_BATCH`
(outbound consolidation N) · `LUMI_TELEGRAM_CATCHUP_H` (skip records older than this on restart) ·
`LUMI_TELEGRAM_PHOTO` (probability 0..1 of attaching the face photo; default 0 — `0.2` ≈ 1/5, `on`/`off` = 1/0).

## Contract & tests

- **No core change.** The TUI gains an `inbox` poller + an `outbox` writer; the core and the emotion
  contract are untouched (the interface-independence contract, demonstrated). The `inbox`/`outbox`
  FIFO is shared infrastructure the v0.14 voicer / v0.18 dictator later ride.
- **Mock everything external.** Tests exercise the **FIFO + id pointers** (append/read/advance), the
  TUI's **inbox→turn→outbox** path (mock model), daemon 1's **buffer→2 s flush + ack-after-flush**
  (mock Telegram + fixed clock), daemon 2's **FIFO + N-batch + catch-up cap** (mock Telegram), and
  the **allowlist** gate — no network, no real sleeps, no paid calls.

## Roadmap

**v0.13 — Telegram bot**, right after the thought-stream (v0.12) whose proactive push it carries, and
before the inner-life phases. A **bridge** (TUI brain + file bus + two daemons), single-owner; the
multi-user, always-on **server** incarnation is v1.1 (server) + v1.3 (accounts/auth). Depends on
**v0.2** (user-scoped core + Repository), **v0.3** (the emotion channel), **v0.12** (proactive
thoughts → push).
