# Telegram bot — Лілі in your pocket (v0.13)

Reach Лілі from **Telegram**: the same mind, a new face. This is the first interface besides the
TUI, and it's the cleanest possible proof of the #1 design contract — **the core is
interface-independent**. The bot adds **zero** logic to `core`; it's a *driver* that translates
Telegram updates into `core.reply(...)` calls and renders what comes back.

## The one idea: the core has one door

Everything Лілі is — canon, memory, mood, closeness, the thought-stream, the emotion channel —
lives behind a single entry point:

```
core.reply(text, session) -> EmotionState{reply, emotion, intensity}
```

The **TUI** is just a driver around that door (keyboard in → `reply` → terminal out). A **Telegram
bot** is the *same* driver with different ends (a Telegram message in → `reply` → a Telegram
message out). The core never knows which interface called it.

```
Telegram app → Telegram servers → [bot driver] → core.reply(text, session) → [Core] →
              ← Telegram servers ← [bot driver] ← EmotionState{reply, emotion} ←┘
```

The whole "reaction" is a few lines:

```python
@dp.message()                                   # on each incoming message…
async def on_message(msg):
    user_id = f"tg:{msg.from_user.id}"          # this Telegram user → a Lumi user_id
    if not allowed(user_id):                    # allowlist gate — never reaches the core
        return await msg.answer("…")
    core, session = session_for(user_id)        # their own core + session
    state = await asyncio.to_thread(core.reply, msg.text, session)   # THE core call
    await msg.answer(f"{state.reply} {emoji(state)}")                # send her reply back
```

## How it "reacts" — long polling

The bot is a **long-running process** that stays connected to Telegram and **asks** for new
messages (**long polling**: one held-open request that returns the instant a message arrives, then
re-asks). It's the laptop/bot reaching out to Telegram, not the reverse — so **no public URL is
needed** (works behind a home network). A webhook (Telegram pushes to your URL) is the alternative,
but needs a server; long polling is the laptop-friendly default.

**Consequence:** the bot answers only **while the process runs** (laptop on, online). Closed → she's
"offline"; messages **queue on Telegram** and arrive when the bot reconnects (nothing lost, just
delayed). For 24/7, run it on a small always-on box — which is the **v1.1 server** step.

## What maps to Telegram

| Lumi piece | on Telegram |
|---|---|
| `core.reply` | message → reply (same call as the TUI) |
| **per-user memory + isolation** (v0.2) | Telegram id → one `user_id`; A's memory never reaches B (the existing invariant) |
| emotion + **emoji** (v0.5) | appended to her reply |
| **face themes/portraits** (v0.11) | send the `faces/<theme>/<emotion>` image as a **photo** |
| **proactive thoughts** (v0.12) | the idle timer runs in the bot; a *spoken* thought → `send_message` (she reaches out) |
| `/mood` `/closeness` `/thoughts` `/theme` `/inner` | native Telegram bot commands (thin core reads) |
| vision (v4.1) / voice (v0.21+) | users send photos / voice ↔ the vision + TTS/STT adapters |

The **proactive inner life** is the standout: the TUI can only show a message when you're looking,
but a bot can **push** — so the v0.12 musings become real "Лілі reached out" notifications.

## Access — allowlist, close-circle

Per the mission: **allowlist-only, admin-managed, no open sign-up.** The bot serves only a
configured set of Telegram ids (`LUMI_TELEGRAM_ALLOWLIST`); any other sender gets a polite refusal
and **never reaches the core** (a hard gate at the edge). This is the personal / close-circle bot;
the full **accounts + auth** story (argon2id, invite codes) is **v1.3** on the server.

## Sessions, secrets, isolation

- **Sessions:** a Telegram chat maps to a Lumi `Session` (the existing lifecycle — ended/summarized
  on inactivity), so memory + compaction work as in the TUI.
- **Secrets:** the bot **token** lives in `.env` (gitignored), like `ANTHROPIC_API_KEY` — never
  committed.
- **Isolation (contract):** the Telegram id → `user_id` mapping means the v0.2 per-user isolation
  holds — a reply to A never carries B's memory/closeness (pinned by a test). Thoughts stay global
  to Лілі but surface per-conversation (the v0.12 rule).

## Contract & tests

- **No core change.** The bot is a new `/telegram` client; `core.reply` and the emotion contract
  are untouched (the interface-independence contract, demonstrated).
- **Mock the Telegram API** — tests exercise the update→`reply`→send adapter, the allowlist gate
  (a blocked id never calls the core), the user→`user_id` isolation, the proactive `send_message`
  push, and the command handlers, all against a **fake bot + the mock model**. No network, no paid
  calls.

## Roadmap

**v0.13 — Telegram bot**, right after the thought-stream (v0.12) whose proactive push it carries,
and before the inner-life phases. A standalone long-polling bot; the multi-user **server**
incarnation rides the v1.1 (server) + v1.3 (accounts/auth) steps later. Depends on **v0.2**
(user-scoped core + Repository), **v0.3** (the emotion channel), **v0.12** (proactive thoughts).
