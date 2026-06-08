# Local dictation — voice input as a TUI add-on (STT)

A separate local dictation process, **the mirror of the voicer ([VOICE_LOCAL.md](VOICE_LOCAL.md), v0.14)**: the voicer reads Лілі's replies and speaks them; the dictator **listens to the microphone, recognizes Ukrainian, and writes your line into the input log** — the same channel the TUI keyboard uses. The core can't tell typed from dictated. Fully local, a separate process, no server and no desktop GUI. It lands as **v0.15**.

## Control — a toggle key in the TUI

Listening is controlled by the TUI via a key (a keyboard key the TUI already captures, not a physical button):

- **Press the "listen" key** (e.g. F2) → the TUI turns listening on.
- **Press it again** (F2, or another key) → the TUI turns listening off.

This is **toggle** mode (start / stop with separate presses) — most reliable in a terminal, unlike hold-to-talk, which fits a desktop window and is left for later. The TUI never records audio itself; it only sets a flag, and a separate process listens and recognizes.

## Control + data channel (shared files)

Everything is coupled by simple files in a shared folder, like the voicer:

- **`listen.flag`** — the "listen / don't listen" signal. Written by the TUI (on the key), read by the dictator. Contents e.g. `on` / `off`.
- **`inbox.jsonl`** — the user's input lines. Written by **both** the TUI keyboard and the dictator. The TUI consumes them as user turns and calls `core.reply()`.
- (for context) `outbox.jsonl` — Лілі's replies (written by the core, read by the TUI and the voicer).

Record format for `inbox.jsonl` (JSON Lines, append-only):
```
{"id": 17, "text": "the recognized user line", "source": "voice", "ts": "..."}
```
`source` (`voice` / `keyboard`) is for reference only; the core treats both the same.

## Dictator logic (loop)

1. Watch `listen.flag`.
2. When it becomes `on` → start recording from the microphone.
3. When it becomes `off` (the TUI flipped it on the key) → stop recording.
4. Send the recording to the **STT adapter** in `/voice` (Ukrainian: Deepgram Nova-3 uk / Whisper / ElevenLabs Scribe) → get text.
5. Append `{ id, text, source: "voice", ts }` to `inbox.jsonl`.
6. Reset and wait for the next `on`.

> Optional: showing "listening…" is the TUI's job (from `listen.flag`), not the dictator's.

## What the TUI shows

- **Listening state** — a "listening…" line while `listen.flag = on` (the TUI knows it, since it set the flag).
- **Your line** — after recognition, the TUI reads the new record from `inbox.jsonl` and shows "you: …" (so you see exactly what was recognized) before submitting it to `core.reply()`.
- **Лілі's reply** — the TUI reads `outbox.jsonl` and prints the text (and the voicer speaks it in parallel).

The TUI learns the recognized text from `inbox.jsonl` — the same place it writes keyboard input — not from the dictator directly.

## Why a separate process, not part of the TUI

- STT is slow (record + recognize + network) — a separate process doesn't block the TUI.
- It toggles on/off independently (dictate or type, your choice).
- **Mirror of the voicer:** one writes to the input (`inbox`), the other reads from the output (`outbox`); the TUI is the text in/out in the middle, and the core is decoupled from both.
- The terminal doesn't capture audio; this process records via a sound library — no GUI needed.

## The full voice-dialogue picture in the TUI

```
[mic] → dictator (STT) → inbox.jsonl ← TUI keyboard
                              ↓
                       core (reads inbox, writes outbox)
                              ↓
        outbox.jsonl → TUI (prints text) + voicer (TTS) → speakers

TUI → listen.flag → dictator (when to listen)
```

Four independent pieces around the core, coupled by a few files: the TUI, the dictator (voice in, v0.15), the voicer (voice out, v0.14), and the core.

## Details and boundaries

- **Toggle, not hold** — more reliable for the terminal; hold-to-talk is left for the desktop stage.
- **Dedup** — `id` (a counter), as in the voicer, so lines aren't doubled.
- **STT errors** — if recognition is empty/low-confidence, write nothing to `inbox` (better silent than garbage); the TUI may optionally show "didn't catch that".
- **Language** — Ukrainian; the provider is configurable (Deepgram Nova-3 uk / Whisper / ElevenLabs Scribe), via the same STT adapter the web dictation (v2.4) uses.
- **Locality** — recording is local; the internet is needed only for a cloud STT call, or run **offline with local Whisper**.
- **Overall toggle** — enable/disable dictation by running/stopping the process or a flag in its config.

## Contract

- Control: `listen.flag` — `on` / `off` (written by the TUI).
- Output: `inbox.jsonl` — `{ id, text, source: "voice", ts }` (written by the dictator; the TUI keyboard writes here too).
- Action: while `listen.flag = on` record the mic; on `off`, recognize Ukrainian (the `/voice` STT adapter, `stt(audio_uk) -> text`) and append the line to `inbox`.

## Where it lives in the Lumi roadmap

**v0.15 — Local dictation (STT)**, right after the v0.14 voicer: voice *in* to mirror voice *out*, locally, no server. Stack — a simple console Python app + microphone capture + the shared `/voice` STT adapter. Depends on: v0.1 (the core consumes user turns) and v0.14 (the local-process + shared-file pattern). The web sibling is **v2.4** (both use the same STT adapter).
</content>
