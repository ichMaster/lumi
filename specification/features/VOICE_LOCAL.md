# Local voice — a separate console app (ElevenLabs)

A separate console voicing app that runs fully locally, without a server. It **reads Лілі's replies from a log**, voices them with the ElevenLabs voice, and **writes the `id` of voiced messages to another log**. The core and the voicer are fully decoupled: the core only appends replies, the voicer catches up and marks what's done.

It is **another decoupled local renderer** of the core's output — the sibling of the v0.7 emotion viewer (the viewer needs only the emotion word; the voicer needs the reply text) — and the **local-stage sibling of the web voice (v2.2)**: both use the **same ElevenLabs TTS adapter** in `/voice`. It lands as **v0.20**.

## Essence

Two file queues (logs):

- **Inbox log** (`outbox.jsonl`) — written by the core, read by the voicer. Each record: `id` + `text` (+ optionally `emotion`).
- **Confirmation log** (`spoken.jsonl`) — written by the voicer. Each record: `id` that has been voiced.

The voicer takes from the inbox log the records not yet in the confirmation log, voices them one by one, and appends their `id` to the confirmation log. The confirmation log is its memory of "what has already been said": it survives a restart, so after a restart it continues from where it left off.

## Log formats

Simplest — JSON Lines (one record per line, append-only).

**Inbox log `outbox.jsonl`** (written by the core):
```
{"id": 41, "text": "Лілі's first reply", "emotion": "calm", "ts": "..."}
{"id": 42, "text": "Лілі's second reply", "emotion": "joy", "ts": "..."}
```

**Confirmation log `spoken.jsonl`** (written by the voicer):
```
{"id": 41, "ts": "..."}
{"id": 42, "ts": "..."}
```

## Voicer logic (loop)

1. Read `spoken.jsonl` → the set of already-voiced `id`s.
2. Periodically (polling) or on file change (watch) read `outbox.jsonl`.
3. For each record whose `id` is not in the voiced set, in ascending order:
   - take `text` → send via the `/voice` **ElevenLabs TTS adapter** (`tts(text, voice_id, emotion?) -> audio`) → play the audio locally;
   - on success, append `{"id": ..., "ts": ...}` to `spoken.jsonl`.
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
- The core knows nothing about voice — it only appends replies to the inbox log; the voicer catches up on its own. (Same decoupling as the v0.7 viewer.)

## Streaming vs queue — no separate queue

Streaming and a queue are different axes: streaming speeds up the **start of one** reply (ElevenLabs returns audio in chunks), while a queue would order **multiple** replies. A separate internal queue is not needed: `outbox.jsonl` itself is the queue.

The voicer works linearly: take the smallest new `id` (not in `spoken`) → voice it to the end → mark it in `spoken` → take the next. The only critical thing is that **playback is strictly sequential**: while one reply plays, do not start the next, or they overlap. That is, "one at a time, in ascending `id` order". An in-memory queue is a later option (smoothness, priorities); unnecessary at the start.

## Connection to the rest of Lumi

- **Another local renderer** of the core's output, alongside the v0.7 emotion viewer. The viewer needs only the emotion word; the voicer needs the text — so it reads `outbox` (text + id), not just the emotion signal.
- **The local-stage sibling of the web voice (v2.2):** both use the same `/voice` ElevenLabs TTS adapter; v0.20 voices locally from the log, v2.2 voices server-side in the browser.
- **A second cloud dependency.** The model is already cloud (Claude Haiku from v0.1); the voicer adds **ElevenLabs (cloud synthesis)** on top — it needs `ELEVENLABS_API_KEY` + internet, and is **optional/toggle-able**. The offline alternative is **Piper (uk)**, but that is not her signature voice.

## Contract

- Input: `outbox.jsonl` — `{ id, text, emotion?, ts }` (written by the core).
- Output: `spoken.jsonl` — `{ id, ts }` (written by the voicer).
- Action: voice the new `id`s in order via the `/voice` ElevenLabs TTS adapter (`voice_id`), mark them in `spoken`.

## Where it lives in the Lumi roadmap

**v0.20 — Local voice (ElevenLabs)**, after the v0.7 emotion viewer: real spoken replies locally, without a server. Stack — a simple console Python app + the ElevenLabs SDK (the shared `/voice` adapter) + local audio playback. Depends on: v0.1 (the core appends replies) and v0.3 (the emotion field). The web sibling is v2.2.
</content>
