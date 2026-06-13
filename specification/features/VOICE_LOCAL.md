# Local voice — a separate console app (ElevenLabs)

A separate console voicing app that runs fully locally, without a server. It **reads Лілі's replies from the `outbox.jsonl` bus**, voices them with the ElevenLabs voice, and **marks what it has voiced** (a `spoken` pointer). The chat and the voicer are fully decoupled: the TUI only appends replies to the outbox, the voicer catches up and marks what's done — voicing never blocks the chat.

**It reuses the v0.13 file bus — it doesn't introduce one.** `outbox.jsonl` + `state/fifo.py` already exist (the Telegram bridge), with records `{id, text, emotion, ts, kind}`. So the voicer is the **twin of the v0.13 `outbox→telegram` daemon** — here `outbox → speaker`: same `fifo.read_since` from a pointer, same one-at-a-time-in-order processing, same retry-on-failure and first-run-skip. It is also the **local-stage sibling of the web voice (v3.2)** (both use the same ElevenLabs TTS adapter in `/voice`) and **another decoupled local renderer** alongside the v0.7 emotion viewer (the viewer needs only the emotion word; the voicer needs the reply text). It lands as **v0.14**.

## Essence

The bus + the voicer's marker:

- **`outbox.jsonl`** (the v0.13 bus) — written by the TUI, read by the voicer. Each record: `{id, text, emotion, ts, kind}`. The voicer voices **only `kind="lili"`** (her replies); **`kind="user"`** records (your keyboard lines, mirrored for the Telegram view) are **skipped, never voiced**.
- **`spoken` pointer** — written by the voicer: the last voiced `id` (the twin of daemon 2's `outbox.sent`, via `fifo.load_pointer`/`save_pointer`). It survives a restart, so the voicer resumes where it left off; on a **first run** (no pointer) it **skips the pre-existing backlog** (starts from the current tail — it never replays the accumulated outbox).

The voicer reads `fifo.read_since(outbox, pointer)`, voices each new `kind="lili"` record in ascending `id` order one at a time, and advances the pointer (past skipped `user` lines too).

## Log formats

`outbox.jsonl` is the **v0.13 JSON-Lines bus** (one record per line, append-only, via `state/fifo`).

**`outbox.jsonl`** (written by the TUI — the voicer voices only `kind="lili"`):
```
{"id": 41, "text": "Лілі's reply", "emotion": "calm", "ts": "...", "kind": "lili"}
{"id": 42, "text": "your terminal line", "ts": "...", "kind": "user"}   ← skipped, never voiced
{"id": 43, "text": "Лілі's reply", "emotion": "joy",  "ts": "...", "kind": "lili"}
```

**`spoken` pointer** (written by the voicer — the last voiced `id`, like daemon 2's `outbox.sent`):
```
43
```

## Voicer logic (loop)

1. On first run (no pointer): set the `spoken` pointer to the current last outbox `id` — **skip the backlog**.
2. Periodically (polling), `records = fifo.read_since(outbox.jsonl, spoken_pointer)`.
3. For each record in ascending `id` order:
   - if `kind == "user"` → **skip** (advance the pointer past it, don't voice);
   - else (`kind == "lili"`) → `tts(text, voice_id, emotion?) -> audio` via the `/voice` **ElevenLabs adapter** → play locally; on success advance the pointer;
   - on a synth/playback **failure** → **stop** (leave the pointer before this `id`, retry next loop — nothing lost or repeated).
4. Repeat.

## Details

- **Deduplication by `id`.** `id` (a monotonic counter from the core) distinguishes a new reply from an already-voiced one; without it you get a repeat or a skip.
- **Order.** Voice in ascending `id` order, so replies sound in the same order as in the chat.
- **Streaming.** ElevenLabs can return audio as a stream — Лілі starts speaking almost immediately, without waiting for the whole synthesis.
- **Emotion into delivery (optional).** If the record has `emotion`, delivery (tone/tempo) may be biased by it — presentation only, never changing the text ([EMOTION.md](EMOTION.md) §9).
- **Toggle.** Voicing can be turned on/off — simply by stopping/starting the app, or via a flag in its config.
- **Resilience.** If the ElevenLabs call fails — do NOT write the `id` to `spoken`, so it can be retried later; nothing is lost.

## Why a separate process, not part of the core

- Voicing is slow (network + synthesis + playback); a separate process doesn't block the chat.
- It can be turned on/off independently, restarted, without touching the core.
- The core knows nothing about voice — the TUI appends replies to the existing `outbox.jsonl`; the voicer catches up on its own. (Same decoupling as the v0.7 viewer and the v0.13 Telegram daemons.)

## Streaming vs queue — no separate queue

Streaming and a queue are different axes: streaming speeds up the **start of one** reply (ElevenLabs returns audio in chunks), while a queue would order **multiple** replies. A separate internal queue is not needed: `outbox.jsonl` itself is the queue.

The voicer works linearly: take the smallest new `id` (not in `spoken`) → voice it to the end → mark it in `spoken` → take the next. The only critical thing is that **playback is strictly sequential**: while one reply plays, do not start the next, or they overlap. That is, "one at a time, in ascending `id` order". An in-memory queue is a later option (smoothness, priorities); unnecessary at the start.

## Connection to the rest of Lumi

- **Another local renderer** of the core's output, alongside the v0.7 emotion viewer. The viewer needs only the emotion word; the voicer needs the text — so it reads `outbox` (text + id), not just the emotion signal.
- **The local-stage sibling of the web voice (v3.2):** both use the same `/voice` ElevenLabs TTS adapter; v0.14 voices locally from the log, v3.2 voices server-side in the browser.
- **A second cloud dependency.** The model is already cloud (Claude Haiku from v0.1); the voicer adds **ElevenLabs (cloud synthesis)** on top — it needs `ELEVENLABS_API_KEY` + internet, and is **optional/toggle-able**. The offline alternative is **Piper (uk)**, but that is not her signature voice.

## Contract

- Input: **`outbox.jsonl`** (the v0.13 bus) — `{ id, text, emotion, ts, kind }` (written by the TUI; `kind ∈ {lili, user}`).
- Output: the **`spoken` pointer** — the last voiced `id` (`fifo.load_pointer`/`save_pointer`), like daemon 2's `outbox.sent`.
- Action: voice the new **`kind="lili"`** records in ascending `id` order via the `/voice` ElevenLabs adapter (`voice_id`), advancing the pointer; `kind="user"` skipped; first-run skips the backlog.
- **The one core/TUI change:** the TUI writes the outbox when **voice OR bridge** is on (today bridge-only) — a one-line gate. No emotion/memory contract change.

## Where it lives in the Lumi roadmap

**v0.14 — Local voice (ElevenLabs)**: real spoken replies locally, without a server — pulled forward (ahead of the inner-life phases) because its `outbox.jsonl` input already exists from v0.13. Stack — a small console Python app + the ElevenLabs SDK (the shared `/voice` adapter, an **optional dep**) + local audio playback, reusing `state/fifo`. Depends on: **v0.13** (the outbox bus + `state/fifo`), v0.1 (the core produces replies), v0.3 (the emotion field). The web sibling is v3.2.
</content>
