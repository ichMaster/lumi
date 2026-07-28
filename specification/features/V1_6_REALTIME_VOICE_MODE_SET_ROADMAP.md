# v1.6 Roadmap Rewrite - Realtime voice mode-set

> **MERGED → [VOICE_MODE.md](VOICE_MODE.md)** (2026-07-28). This draft's Phase-A rules, the sourced
> API constraints and the hot-standby rejection were absorbed there; where this draft deferred the
> two-model routing and realtime-as-brain, VOICE_MODE.md sequences them as Phases B/C per the owner's
> decision. Kept for reference; VOICE_MODE.md is the source of truth for v1.6.

Draft replacement for `specification/ROADMAP.md` v1.6. This keeps the roadmap change separate from the
main roadmap until we are ready to replace the current "model roles" section.

## Short answer on model switching

There are two different kinds of "model switching" in voice mode:

- **Realtime session model switching:** not as a hot-swap inside one live Realtime session. The Realtime
  `model` is effectively a session-level choice; OpenAI's `session.update` reference says `model` cannot
  be updated by that event. Moving from `gpt-realtime-2.1-mini` to `gpt-realtime-2.1` means closing or
  replacing the Realtime session at a clean turn boundary.
- **Lumi brain model switching:** yes. If Realtime is used as the microphone/VAD/speaker shell and
  `Core.reply()` remains the text brain, Lumi can route each committed transcript to a cheaper or deeper
  text model while the Realtime voice session stays on `gpt-realtime-2.1-mini`.

Recommended v1.6 decision: **do not build native Realtime mini/full auto-switching in the MVP**. Keep one
Realtime voice model per voice session, defaulting to `gpt-realtime-2.1-mini`, and leave per-turn
complexity routing to the Core text model layer. Native Realtime escalation can be a later experiment
with explicit session restart/handoff behavior.

## Roadmap replacement

### v1.6 - Mode-set voice: OpenAI Realtime shell around Core

**Goal:** Лілі gains a true voice interface mode without becoming a second agent. `/mode-set voice`
starts a live OpenAI Realtime audio session using `gpt-realtime-2.1-mini` by default; `/mode-set text`
returns to the current keyboard-first TUI. Realtime owns microphone input, turn detection, barge-in, and
audio playback. Lumi's existing `Core.reply()` still owns memory, tools, emotion, thinking, and the final
reply text.

This is intentionally a **mode-set** feature, not a `/model-set` profile. `/model-set` chooses the text
brain. `/mode-set` chooses the interaction surface:

- `text`: current typed TUI.
- `voice`: OpenAI Realtime voice mode.

The MVP uses Realtime as an **audio shell around Core**:

```text
mic -> Realtime session -> committed transcript -> Core.reply(text)
                                     |                 |
                                     |                 v
                                     |          EmotionState + text
                                     v                 |
                             TUI transcript <----------+
                                                       |
                                                       v
                                      Realtime speaks the returned text
```

This keeps the important invariants:

- One durable memory path: every real turn still passes through `Core.reply()`.
- One emotion contract: `{reply, emotion, intensity}` is still validated by the existing gate.
- One thinking path: the TUI Thinking block still renders `core.last_thinking` / streamed reasoning
  summaries from the normal reply path.
- One transcript: the chat shows the committed speech transcript plus the exact text returned by Core.

Native Realtime-as-brain is explicitly out of scope for v1.6. It may be useful later, but it would need a
separate contract layer because Lumi currently depends on structured reply state, memory commits, and
provider-specific thinking extraction in `Core.reply()`.

## Tasks

- **Mode state and config:** add a small `ModeState` concept with `text` and `voice`; load `[modes.voice]`
  from `core/models.toml`; support `.env` overrides such as `LUMI_MODE_SET`,
  `LUMI_REALTIME_MODEL`, `LUMI_REALTIME_VOICE`, `LUMI_REALTIME_TRANSPORT`,
  `LUMI_REALTIME_TURN_DETECTION`, and `LUMI_REALTIME_EAGERNESS`.
- **Realtime adapter:** add a `RealtimeVoiceSession` wrapper that hides OpenAI WebSocket/WebRTC events
  from the TUI. It should expose `start()`, `stop()`, `on_user_transcript(...)`, `speak(text, ...)`,
  and cancellation methods, but it should not import or call `Core`.
- **TUI command:** add `/mode-set`, `/mode-set voice`, and `/mode-set text`. Voice startup checks
  `OPENAI_API_KEY`, updates status to connecting/listening/speaking/error, and leaves typed TUI usable
  if startup fails.
- **Core bridge:** route committed voice transcripts into the same turn queue as typed messages. A voice
  transcript becomes a normal user turn; the returned `EmotionState.reply` is rendered in chat and sent
  to the voice renderer.
- **Realtime config:** use `semantic_vad`, `interrupt_response=true`, and `create_response=false` so the
  Realtime model emits turn events but does not improvise the assistant answer before Core replies.
- **Speaking exact replies:** instruct Realtime to speak the exact Core reply text. If Realtime paraphrases
  too much, keep Realtime for mic/VAD/transcript and route output through an exact TTS renderer instead.
- **Model-switching boundary:** keep `realtime_model` fixed for the lifetime of a voice session. If the
  user changes `/model-set`, only the next Core text reply changes. If the user changes Realtime model or
  voice, require `/mode-set text` then `/mode-set voice` or an explicit voice-session restart.
- **Security:** for the local TUI, prefer server-side WebSocket with the standard API key kept only in the
  local process. For future browser/mobile clients, use WebRTC with ephemeral tokens minted by a backend.
- **Docs:** keep the fuller design in `specification/features/REALTIME_VOICE_MODE_SET.md` and link this
  roadmap draft from the main roadmap when v1.6 is replaced.

## Suggested config

```toml
[modes.voice]
provider = "openai"
realtime_model = "gpt-realtime-2.1-mini"
voice = "marin"
transport = "websocket"
turn_detection = "semantic_vad"
eagerness = "medium"
```

```ini
LUMI_MODE_SET=text
LUMI_REALTIME_MODEL=gpt-realtime-2.1-mini
LUMI_REALTIME_VOICE=marin
LUMI_REALTIME_TRANSPORT=websocket
LUMI_REALTIME_TURN_DETECTION=semantic_vad
LUMI_REALTIME_EAGERNESS=medium
```

`DEEPGRAM_API_KEY` remains for the older dictation path. Realtime voice mode should require only
`OPENAI_API_KEY`.

## DoD

With `LUMI_MODE_SET=voice` or `/mode-set voice`, the TUI opens a Realtime session, shows listening status,
commits spoken user text into the chat, calls `Core.reply()` exactly once per committed transcript,
renders the Thinking block through the existing reply path, and speaks the exact returned reply text.

Typed messages and slash commands still work while voice mode is active. Barge-in cancels audio playback,
not an already committed Core turn. Missing API key, connection failure, empty transcript, Core failure,
or voice renderer failure all degrade to readable TUI errors without corrupting memory.

Changing `/model-set` during voice mode affects the next Core text reply without restarting audio.
Changing the Realtime model or voice requires a voice-session restart. Off/default text mode remains
byte-identical to the current TUI behavior.

## Tests

- Unit: mode config parsing, env overrides, invalid mode fallback, missing key behavior.
- Unit: fake Realtime event stream emits transcript callbacks, empty transcript is ignored, disconnect
  marks voice mode as error.
- Unit: voice-session restart is required when `realtime_model` changes.
- Integration: `/mode-set voice` starts fake voice session; `/mode-set text` stops it.
- Integration: voice transcript -> one queued `Core.reply()` -> one persisted user/Lili message pair.
- Integration: `/model-set` during voice mode changes the Core reply model but does not restart Realtime.
- Contract: voice mode never bypasses `{reply, emotion, intensity}` and never writes a separate memory
  path.

No paid API calls in CI; all Realtime traffic is behind a fake transport.

## Later options

- **Native Realtime brain experiment:** let `gpt-realtime-2.1` answer directly and rebuild Lumi's reply
  contract around Realtime events. This is a different architecture, not v1.6 MVP.
- **Realtime model escalation:** if we need native mini/full switching, restart the Realtime session at a
  clean turn boundary and replay a compact conversation summary. This will introduce a short gap and must
  be visible in status.
- **Hot standby sessions:** keep mini and full Realtime sessions open and hand off between them. This is
  likely too costly and complex for local Lumi, so it should stay out unless there is a measured need.
- **Core text routing:** resurrect the old v1.6 model-roles idea later as text-brain routing only, not as
  Realtime session hot-swapping.

## Source notes

- OpenAI lists `gpt-realtime-2.1-mini` as a faster, lower-cost realtime voice model supporting audio and
  text over WebRTC, WebSocket, or SIP:
  https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini
- OpenAI voice-agent guidance distinguishes speech-to-speech sessions from chained voice pipelines; chained
  pipelines are the better fit when the app must keep explicit control over transcription, text reasoning,
  and speech output:
  https://developers.openai.com/api/docs/guides/voice-agents
- OpenAI WebSocket guidance shows the Realtime model selected in the WebSocket URL and recommends WebSocket
  for server-to-server integrations:
  https://developers.openai.com/api/docs/guides/realtime-websocket
- OpenAI Realtime API reference says `session.update` cannot update `model`, and voice cannot be changed
  after the model has produced audio in the session:
  https://platform.openai.com/docs/api-reference/realtime-client-events
