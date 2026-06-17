# Dictation — setup & usage (v0.26)

Talk *to* Лілі. A separate local process **hears your speech and types it into the chat** — the **mirror
of the voicer**: the voicer reads her replies aloud, the dictator listens to your mic, recognizes
Ukrainian, and writes your line into the same `inbox.jsonl` the keyboard feeds. The core can't tell typed
from dictated.

It is **off by default** (`LUMI_DICTATION`), **local** (the internet is needed only for a cloud STT call —
or run **offline Whisper**), and a **separate process** (the terminal never captures audio).

> Operator guide, not a design spec. The design is in
> [specification/features/DICTATION_LOCAL.md](../specification/features/DICTATION_LOCAL.md).

---

## Quick start

1. **Pick a provider** and set keys in `.env`:
   ```ini
   LUMI_DICTATION=on
   LUMI_STT_PROVIDER=deepgram        # deepgram / elevenlabs / whisper
   DEEPGRAM_API_KEY=...              # for deepgram (Whisper needs no key)
   ```
2. **Restart the TUI** (`./lumi`). It now drains the inbox and shows a **Ctrl+D** listen toggle.
3. **Run the dictator** (a separate terminal):
   ```
   uv run --extra dictation python -m voice.dictator
   ```
4. **Dictate:** press **Ctrl+D** (the TUI shows "🎙 слухаю…"), speak a Ukrainian phrase, press **Ctrl+D**
   again to stop. The dictator recognizes it and writes your line; the TUI shows "🎤 ти: …" and Лілі
   answers — identical to typing it.

---

## The pieces (mirror of the voicer)

```
[mic] → dictator (STT) → inbox.jsonl ← TUI keyboard
                              ↓
                       core (reads inbox, writes outbox)
                              ↓
        outbox.jsonl → TUI (prints text) + voicer (TTS) → speakers

TUI → listen.flag (Ctrl+D) → dictator (when to listen)
```

- **The TUI** owns the **`listen.flag`** (`on`/`off`) — it's the *only* writer; **Ctrl+D** flips it.
- **The dictator** records the mic while the flag is `on`; on `off` it recognizes and appends
  `{id, text, source:"voice", ts}` to `inbox.jsonl`.
- **The TUI** drains the inbox (the same path the Telegram bridge uses) and runs each line as a turn.

Dictation is **independent of the Telegram bridge** — it works on its own; if both are on, the TUI drains
inbox lines from either source and tags them (`🎤 ти` for voice, `📱 Telegram` otherwise).

---

## Providers

| `LUMI_STT_PROVIDER` | What | Needs |
|---|---|---|
| `deepgram` | Deepgram Nova-3 (Ukrainian) — cloud, fast | `DEEPGRAM_API_KEY` + internet |
| `elevenlabs` | ElevenLabs Scribe — cloud | `ELEVENLABS_API_KEY` + internet |
| `whisper` | OpenAI Whisper — **offline**, on your CPU/GPU | the `openai-whisper` extra (no key, no internet) |

The STT runs behind one **adapter** (`voice/stt.py`), the twin of the TTS adapter — swapping the provider
is a config change, no code.

---

## Safety / behaviour

- **The core is untouched.** A dictated line is an ordinary `inbox` record; `source:"voice"` is reference
  only — Лілі answers it exactly like a typed line.
- **Better silent than garbage.** Empty / low-confidence recognition writes **nothing** to the inbox (no
  phantom turns); a recognition error degrades the same way.
- **Dedup.** Each line gets the fifo's next id; the TUI consumes each once (its inbox pointer) — no
  doubling.
- **Local.** Recording is local; only a cloud provider call leaves the machine. Offline Whisper keeps
  everything on-device.
- **Mic permission.** The OS will ask the *dictator process* (not the TUI) for microphone access on first
  run — grant it to your terminal/Python.

---

## Configuration reference

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_DICTATION` | Turn dictation on (the TUI drains inbox + the Ctrl+D toggle) | `off` |
| `LUMI_STT_PROVIDER` | `deepgram` / `elevenlabs` / `whisper` | `deepgram` |
| `LUMI_STT_LANG` | Recognition language | `uk` |
| `DEEPGRAM_API_KEY` | Deepgram key (secret) — Whisper needs none | (none) |
| `LUMI_LISTEN_FLAG` | The on/off signal the TUI writes / the dictator reads | `.lumi/listen.flag` |

---

## Troubleshooting

- **Ctrl+D says "Dictation is off."** Set `LUMI_DICTATION=on` and restart the TUI.
- **Nothing happens when I speak.** Check the dictator process is running, the provider key is set (or
  Whisper installed), and that you pressed Ctrl+D to start *and* again to stop (toggle, not hold).
- **"nothing recognized".** Empty/low-confidence audio is dropped on purpose — speak a bit longer/clearer.
- **No microphone access.** Grant mic permission to the terminal/Python running `voice.dictator` (the TUI
  doesn't record).
