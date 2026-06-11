"""ElevenLabs TTS adapter (v0.14) — the `/voice` seam the voicer depends on (never the SDK directly).

`synth(text, *, emotion=None) -> bytes` turns one reply into audio. `ElevenLabsTTS` calls the cloud
(**lazy** `elevenlabs` import — an optional dep); `MockTTS` returns canned bytes for tests (no
network). The `emotion` may **bias delivery** (voice settings) — presentation only, never the text
(EMOTION.md §9). Reused by the web voice in v2.2.
"""

from __future__ import annotations

from typing import Protocol


class TTS(Protocol):
    """The voice seam: text (+ optional emotion) → audio bytes."""

    def synth(self, text: str, *, emotion: str | None = None) -> bytes: ...


# emotion → (stability, style): calmer feelings steadier, livelier ones more expressive. A gentle
# presentation bias only (EMOTION.md §9); an unknown/None emotion → a neutral middle.
_EMOTION_SETTINGS: dict[str, tuple[float, float]] = {
    "calm": (0.70, 0.20), "tender": (0.65, 0.35), "thoughtful": (0.60, 0.30),
    "serious": (0.65, 0.20), "sad": (0.70, 0.20), "doubt": (0.55, 0.30),
    "surprise": (0.35, 0.60), "playful": (0.35, 0.70), "joy": (0.40, 0.60),
}
_NEUTRAL = (0.50, 0.40)


def voice_settings_for(emotion: str | None) -> tuple[float, float]:
    """The (stability, style) bias for an emotion (pure — testable without the SDK)."""
    return _EMOTION_SETTINGS.get(emotion or "", _NEUTRAL)


class ElevenLabsTTS:
    """Synthesize via ElevenLabs. ``elevenlabs`` is imported lazily inside :meth:`synth`, so the
    adapter constructs (and the module imports) without the optional dep installed."""

    def __init__(self, api_key: str, voice_id: str, model: str = "eleven_multilingual_v2") -> None:
        self.voice_id = voice_id
        self.model = model
        self._api_key = api_key  # secret — never logged

    def synth(self, text: str, *, emotion: str | None = None) -> bytes:  # pragma: no cover - network
        from elevenlabs import VoiceSettings
        from elevenlabs.client import ElevenLabs

        stability, style = voice_settings_for(emotion)
        client = ElevenLabs(api_key=self._api_key)
        audio = client.text_to_speech.convert(
            voice_id=self.voice_id,
            model_id=self.model,
            text=text,
            voice_settings=VoiceSettings(stability=stability, similarity_boost=0.75, style=style),
        )
        return b"".join(audio)  # the SDK yields audio chunks


class MockTTS:
    """A canned TTS for tests — records calls, returns fake audio, no network."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def synth(self, text: str, *, emotion: str | None = None) -> bytes:
        self.calls.append((text, emotion))
        return b"AUDIO:" + text.encode("utf-8")
