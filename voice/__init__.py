"""Local voice (v0.14) — voice Лілі's replies via ElevenLabs, reusing the v0.13 outbox bus.

`/voice` holds the shared **TTS adapter** (`tts.py`) and the **voicer** (`voicer.py`, the twin of the
v0.13 `outbox→telegram` daemon — here `outbox → speaker`). The voicer is a separate process coupled to
the chat only by files: it reads the existing `outbox.jsonl` via `state/fifo` and voices her replies.
`elevenlabs` is an optional dependency, imported lazily — the pure logic + the mock import without it.
"""
