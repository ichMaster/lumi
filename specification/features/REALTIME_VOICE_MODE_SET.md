# Realtime voice mode-set - OpenAI GPT-Realtime mini

A new **voice interface mode** for Lumi: `/mode-set voice` turns the current TUI session into a live
microphone/speaker loop backed by OpenAI Realtime, using **`gpt-realtime-mini`** by default.

This is intentionally a **mode-set** feature, not only a `/model-set` profile. `/model-set` chooses the
models that power Lumi's text brain. `/mode-set` chooses the interaction surface: typed TUI, local
dictation plus voicer, or live Realtime voice. Voice mode still talks to the same `Core` and the same
repository so memory, emotion, intent, tools, usage logging, and per-user isolation do not split into a
second Lumi.

> Naming note: the requested label `gtp-realtime2.1mini` looks like a shorthand or typo. The current
> official public model page lists the canonical mini realtime model as `gpt-realtime-mini`, with
> `gpt-realtime-mini-2025-12-15` as a snapshot. OpenAI examples also show the broader
> `gpt-realtime-2.1` family for live audio sessions. Use `gpt-realtime-mini` in config unless the
> account exposes a newer exact model id.

## Why this exists

The current voice stack is a chained local pipeline:

- Dictation: mic audio -> STT -> text into the TUI.
- Reply: text -> `Core.reply(...)` -> validated `{reply, emotion, intensity}`.
- Voice: outbox text -> ElevenLabs TTS -> local playback.

That pipeline is predictable and preserves every existing contract, but it feels like three separate
steps. A realtime voice mode should feel like one continuous conversation: live turn detection,
barge-in, lower first-audio latency, and a single on/off command.

OpenAI's voice-agent guidance separates two designs:

- Speech-to-speech live sessions are best when natural turn-taking and low latency matter.
- Chained voice pipelines are better when the app must keep explicit control over transcript,
  business logic, and durable state.

Lumi needs both. The MVP therefore uses Realtime for **audio transport and turn-taking**, while keeping
`Core.reply()` as the text brain that commits memory and state.

## User experience

```
/mode-set
  text            current keyboard-first TUI
  voice           OpenAI Realtime voice mode

/mode-set voice
  starts a live audio session, keeps the transcript in the chat, and lets Лілі speak aloud

/mode-set text
  closes the realtime session and returns to normal typed turns
```

Status line examples:

```
status: ready - profile:openai:gpt-5.5 - mode:text
status: listening - profile:openai:gpt-5.5 - mode:voice realtime:gpt-realtime-mini voice:marin
status: speaking - profile:openai:gpt-5.5 - mode:voice realtime:gpt-realtime-mini
```

The chat transcript remains text-first even in voice mode:

- User speech is shown as the committed transcript.
- Лілі's spoken reply is shown as the exact text returned by `Core.reply()`.
- The Thinking box continues to show `core.last_thinking` when available.
- `/memory`, `/forget`, `/model-set`, `/style`, `/mood`, and other slash commands remain typed commands.

## Architecture

### Recommended MVP: Realtime shell around Core

```
mic -> Realtime session -> committed transcript -> Core.reply(text)
                                     |                 |
                                     |                 v
                                     |          EmotionState + text
                                     v                 |
                             TUI transcript <----------+
                                                       |
                                                       v
                                      Realtime response speaks the returned text
```

The Realtime session owns audio capture, VAD, barge-in, and playback. The Lumi core owns the answer.

This keeps the important invariants:

- One memory path: every real turn still passes through `Core.reply()`.
- One emotion path: the returned `EmotionState` is still validated by `core.emotion.validate`.
- One model stack: `/model-set` still controls the text brain; `/mode-set voice` only controls the
  audio interface.
- One transcript: the durable message is the committed transcript plus the exact validated reply text.

### Why not let Realtime replace Core directly?

`gpt-realtime-mini` supports function calling, but the model page currently says structured outputs are
not supported. Lumi's reply contract depends on structured `{reply, emotion, intensity}` and sometimes
`intent` / `thinking_summary`. Replacing `Core.reply()` with a native Realtime answer would either weaken
that contract or require a second validation/repair layer that duplicates the existing one.

Native Realtime-as-brain can be a later experiment. The MVP should be a Realtime interface adapter.

## Components

### `core/modes.py`

Small enum-like module:

```python
MODES = ("text", "voice")
DEFAULT_MODE = "text"
```

Mode is session-local and not persisted by default, matching `/model` runtime switching.

### `voice/realtime.py`

New adapter that hides OpenAI event details from the TUI:

```python
class RealtimeVoiceSession:
    def start(self, *, session_id: str, user_id: str) -> None: ...
    def stop(self) -> None: ...
    def update_config(self, config: RealtimeVoiceConfig) -> None: ...
    def on_user_transcript(self, callback: Callable[[str], None]) -> None: ...
    def speak(self, text: str, *, emotion: str | None = None) -> None: ...
```

The adapter should expose callbacks, not know about `Core`. The TUI owns orchestration:

1. Realtime emits a committed user transcript.
2. TUI calls `core.reply(transcript, session, on_delta=...)`.
3. TUI renders the reply text and thinking.
4. TUI sends the returned text to Realtime for spoken output.

### `tui/app.py`

Add `/mode-set` command handling:

- `/mode-set` lists modes and active config.
- `/mode-set voice` checks `OPENAI_API_KEY`, starts `RealtimeVoiceSession`, and marks status as listening.
- `/mode-set text` stops the realtime session and returns to typed operation.
- If voice startup fails, leave the mode unchanged and show a readable error line.

Input buffering rules:

- Typed user messages still work while voice mode is active.
- Voice transcripts enter the same FIFO turn queue as typed input.
- A barge-in cancels audio playback only; it must not cancel an in-flight `Core.reply()` after the text
  turn has been committed.

### `core/models.toml`

Add interface mode config separately from provider profiles:

```toml
[modes.voice]
provider = "openai"
realtime_model = "gpt-realtime-mini"
voice = "marin"
transport = "webrtc"
turn_detection = "semantic_vad"
eagerness = "medium"
```

Do not put `realtime_model` into `[profiles.openai]`. A profile answers text turns; voice mode handles
audio transport.

### `.env`

```ini
LUMI_MODE_SET=text
LUMI_REALTIME_MODEL=gpt-realtime-mini
LUMI_REALTIME_VOICE=marin
LUMI_REALTIME_TRANSPORT=webrtc
LUMI_REALTIME_TURN_DETECTION=semantic_vad
LUMI_REALTIME_EAGERNESS=medium
OPENAI_API_KEY=sk-...
```

`DEEPGRAM_API_KEY` remains for the older dictation path. Realtime voice mode should not require it.

## OpenAI Realtime configuration

Use a realtime session with:

```json
{
  "type": "realtime",
  "model": "gpt-realtime-mini",
  "audio": {
    "input": {
      "turn_detection": {
        "type": "semantic_vad",
        "create_response": false,
        "eagerness": "medium",
        "interrupt_response": true
      }
    },
    "output": {
      "voice": "marin",
      "format": { "type": "audio/pcm", "rate": 24000 }
    }
  },
  "output_modalities": ["audio"],
  "instructions": "You are Лілі's voice transport. Speak only the exact text supplied by Lumi."
}
```

Design choices:

- `create_response=false` keeps the Realtime model from answering directly after VAD. The app waits for
  the transcript, calls `Core.reply()`, then asks Realtime to speak the returned text.
- `semantic_vad` is the default because it waits for a semantically complete user turn better than a
  plain silence threshold.
- `interrupt_response=true` gives barge-in behavior while keeping the core turn durable.
- `voice` must be chosen before the first audio response, because Realtime cannot change the voice after
  audio output has already happened in a session.

For desktop/local TUI, prefer server-side WebSocket first: it keeps `OPENAI_API_KEY` only in the local
process and avoids a browser token server. For a future web client, prefer WebRTC with ephemeral tokens.

## Event flow

### Start

1. User runs `/mode-set voice`.
2. TUI builds `RealtimeVoiceConfig` from `.env` plus `[modes.voice]`.
3. TUI starts a Realtime WebSocket session with `OpenAI-Safety-Identifier` set to a stable hashed
   `user_id`.
4. TUI updates the status line to `listening`.

### User speaks

1. Realtime receives microphone audio.
2. VAD detects a complete utterance.
3. Adapter emits the committed transcript.
4. TUI appends `You: <transcript>` to the chat and queues the turn.
5. The normal turn worker calls `Core.reply(transcript, session)`.

### Лілі answers

1. Core returns `EmotionState(reply, emotion, intensity)`.
2. TUI renders the text reply and updates the Thinking box.
3. TUI calls `RealtimeVoiceSession.speak(state.reply, emotion=state.emotion.value)`.
4. Realtime is instructed to speak the returned text verbatim with voice guidance derived from emotion:
   - `joy/playful`: slightly brighter delivery.
   - `tender/sad`: softer delivery.
   - `serious/doubt`: steadier delivery.
5. On `response.done`, status returns to `listening`.

The text returned by `Core.reply()` remains authoritative. If Realtime speech ever paraphrases instead of
speaking the supplied text closely enough, keep the Realtime session for mic/VAD/transcript and route
output audio through the existing exact TTS renderer (ElevenLabs today, OpenAI Speech API later).

## Contracts

### Mode contract

```
ModeState{
  name: "text" | "voice",
  provider?: "openai",
  realtime_model?: str,
  voice?: str,
  transport?: "webrtc" | "websocket",
  status: "off" | "connecting" | "listening" | "speaking" | "error"
}
```

### Voice transcript contract

```
VoiceTranscript{
  user_id: str,
  session_id: str,
  text: str,
  source: "openai-realtime",
  model: str,
  started_at?: str,
  ended_at: str
}
```

This is not stored as a new memory type in the MVP. It is only the source event that becomes a normal
`Message(role="user")` when passed to `Core.reply()`.

### Spoken reply contract

The only durable assistant reply is still the `Message(role="lili")` created by `Core.reply()`.
Realtime audio is a renderer, like the current local voicer.

## Failure handling

- Missing `OPENAI_API_KEY`: reject `/mode-set voice` with a readable line and keep `mode:text`.
- Realtime connection drops: stop playback, mark `mode:error`, keep the typed TUI usable.
- Transcript is empty: ignore it and keep listening.
- `Core.reply()` fails: show the existing TUI error line; do not ask Realtime to improvise a reply.
- Realtime `speak()` fails after Core succeeds: keep the text reply; voice failure is renderer-only.
- Realtime speech paraphrases the supplied reply: keep the text transcript authoritative and fall back to
  the exact TTS renderer for output audio.
- User switches `/model-set` while voice is active: keep the audio session, but the next committed
  transcript uses the new text model. If the realtime voice config changes, require restarting voice mode.

## Security and privacy

- Never expose `OPENAI_API_KEY` to a browser. Local TUI can use server-side WebSocket. Future web clients
  must mint ephemeral tokens server-side.
- Send only the live audio and minimal session voice instructions to Realtime. The full Lumi prompt and
  memory stay inside the normal `Core.reply()` call.
- Use a stable, privacy-preserving `OpenAI-Safety-Identifier` derived from `user_id`.
- Do not send `.env` values, local file contents, memory blocks, or tool results into the Realtime session
  unless they are already part of the user's spoken turn or the final text being spoken.
- Barge-in cancels audio rendering, not persisted memory.

## Implementation phases

### Phase 1 - spec and config

- Add this feature document.
- Add `ModeState` parsing and config defaults.
- Add `[modes.voice]` support in `core/models.toml` loading.
- Add unit tests for config merge and unknown mode fallback.

### Phase 2 - local WebSocket prototype

- Add `voice/realtime.py`.
- Connect with `websocket-client` or the OpenAI SDK if it exposes the needed Realtime helpers.
- Mock all network events in tests.
- Prove that an emitted transcript calls `Core.reply()` exactly once.

### Phase 3 - TUI switch

- Add `/mode-set`.
- Add status text and graceful error lines.
- Route voice transcripts into the same queued turn path as typed messages.
- Add integration tests with a fake `RealtimeVoiceSession`.

### Phase 4 - speaking exact Core replies

- Add `speak(text, emotion=...)`.
- Add barge-in cancellation.
- Add tests that audio failure does not remove the text reply or corrupt memory.

### Phase 5 - WebRTC for future web client

- Add a server endpoint that mints ephemeral Realtime tokens.
- Browser connects over WebRTC.
- Keep the server as the only holder of standard OpenAI API keys.

## Tests

- `tests/unit/test_modes.py`: parse defaults, env override, invalid mode fallback.
- `tests/unit/test_realtime_voice.py`: mocked event stream, transcript callback, reconnect, empty transcript.
- `tests/integration/test_tui_mode_set.py`: `/mode-set voice`, `/mode-set text`, missing key, status lines.
- `tests/integration/test_voice_core_bridge.py`: transcript -> one `Core.reply()` -> persisted user/Lili messages.
- `tests/contract/test_voice_mode_contract.py`: voice mode never bypasses `{reply, emotion, intensity}`.

No paid API calls in CI. Every OpenAI Realtime call must be behind a fake transport in tests.

## Open questions

- Should voice mode use OpenAI's built-in voices only (`marin`/`cedar`) or support custom voice IDs later?
- Should Realtime audio transcripts be logged separately for debugging, or is normal chat history enough?
- Should `/mode-set voice` also enable `LUMI_INPUT_BUFFER` automatically, or only recommend it?
- Do we want a native Realtime-as-brain experiment after the MVP, knowing it cannot use structured outputs
  the same way the current text brain does?

## Source notes

- OpenAI model docs list `gpt-realtime-mini` as an audio/text realtime model over WebRTC, WebSocket, or
  SIP, with audio input and output, function calling support, and no structured outputs:
  https://developers.openai.com/api/docs/models/gpt-realtime-mini
- OpenAI voice-agent guidance recommends live speech-to-speech for natural, low-latency conversations and
  chained pipelines when the application needs explicit control over transcript, reasoning, and speech:
  https://developers.openai.com/api/docs/guides/voice-agents
- OpenAI Realtime WebRTC docs recommend WebRTC for browser/mobile clients and describe ephemeral-token
  setup through a developer-controlled server:
  https://developers.openai.com/api/docs/guides/realtime-webrtc
- OpenAI Realtime WebSocket docs describe WebSocket as a server-to-server option using a standard API key
  on the secure backend:
  https://developers.openai.com/api/docs/guides/realtime-websocket
- Realtime API reference documents `session.update`, VAD, `output_modalities`, voice selection, and the
  voice immutability rule after audio output starts:
  https://developers.openai.com/api/reference/resources/realtime
