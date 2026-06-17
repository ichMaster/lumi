"""STT adapter (v0.26) — the `/voice` seam the dictator depends on (never an SDK directly).

`recognize(audio, *, lang="uk") -> str` turns one recording into text — the **mirror of `tts.py`'s
`TTS`** (`synth` → audio out; `recognize` → text in). Cloud impls (`DeepgramSTT` Nova-3 uk /
`ElevenLabsScribeSTT`) call their SDK via a **lazy** import (optional deps), and `WhisperSTT` runs
**offline**; `MockSTT` returns canned text for tests (no audio, no network). Reused by the web dictation
in v3.4.

The module + every adapter **import without any STT SDK installed** (the SDK import is inside
`recognize`), so tests and the TUI never need the optional deps.
"""

from __future__ import annotations

from typing import Protocol


class STT(Protocol):
    """The voice-in seam: recorded audio (+ language) → recognized text."""

    def recognize(self, audio: bytes, *, lang: str = "uk") -> str: ...


def _deepgram_transcript(data: dict) -> str:
    """Pull the transcript out of a Deepgram ``/v1/listen`` JSON response (pure — testable, no network)."""
    channels = ((data.get("results") or {}).get("channels")) or [{}]
    alternatives = (channels[0].get("alternatives")) or [{}]
    return (alternatives[0].get("transcript") or "").strip()


class DeepgramSTT:
    """Recognize via Deepgram's prerecorded **REST** API (Nova-*, Ukrainian) over stdlib ``urllib`` — no
    SDK, so it's immune to SDK version churn (the same raw endpoint the pyramid firmware uses). The audio
    is sent as WAV; the host is fixed."""

    URL = "https://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, *, model: str = "nova-3") -> None:
        self.model = model
        self._api_key = api_key  # secret — never logged

    def recognize(self, audio: bytes, *, lang: str = "uk") -> str:  # pragma: no cover - network
        import json
        import urllib.parse
        import urllib.request

        params = urllib.parse.urlencode({"model": self.model, "language": lang, "smart_format": "true"})
        req = urllib.request.Request(
            f"{self.URL}?{params}", data=audio, method="POST",
            headers={"Authorization": f"Token {self._api_key}", "Content-Type": "audio/wav"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — fixed Deepgram host
            return _deepgram_transcript(json.loads(resp.read()))


class ElevenLabsScribeSTT:
    """Recognize via ElevenLabs Scribe. ``elevenlabs`` is imported lazily inside :meth:`recognize`."""

    def __init__(self, api_key: str, *, model: str = "scribe_v1") -> None:
        self.model = model
        self._api_key = api_key  # secret — never logged

    def recognize(self, audio: bytes, *, lang: str = "uk") -> str:  # pragma: no cover - network
        import io

        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=self._api_key)
        result = client.speech_to_text.convert(
            file=io.BytesIO(audio), model_id=self.model, language_code=lang
        )
        return (getattr(result, "text", "") or "").strip()


class WhisperSTT:
    """Recognize **offline** via local Whisper (``openai-whisper``), imported lazily; the model is
    loaded once on first use. No network, no key — just CPU/GPU."""

    def __init__(self, *, model: str = "base") -> None:
        self.model = model
        self._loaded = None

    def recognize(self, audio: bytes, *, lang: str = "uk") -> str:  # pragma: no cover - local model
        import tempfile

        import whisper

        if self._loaded is None:
            self._loaded = whisper.load_model(self.model)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
            f.write(audio)
            f.flush()
            result = self._loaded.transcribe(f.name, language=lang)
        return (result.get("text") or "").strip()


class MockSTT:
    """A canned STT for tests — returns its configured text, records each call (no audio, no network).

    Construct with the text to "recognize" (``""`` simulates an empty / low-confidence result)."""

    def __init__(self, text: str = "") -> None:
        self._text = text
        self.calls: list[tuple[int, str]] = []  # (audio length, lang) per call

    def recognize(self, audio: bytes, *, lang: str = "uk") -> str:
        self.calls.append((len(audio or b""), lang))
        return self._text


def build_stt(provider: str, *, api_key: str = "", model: str = "") -> STT:
    """Pick an STT adapter by provider name (``deepgram`` / ``elevenlabs`` / ``whisper`` / ``mock``).

    Unknown providers raise ``ValueError``. Constructs without the provider's SDK (lazy imports).
    """
    name = (provider or "").strip().lower()
    if name == "deepgram":
        return DeepgramSTT(api_key, model=model or "nova-3")
    if name == "elevenlabs":
        return ElevenLabsScribeSTT(api_key, model=model or "scribe_v1")
    if name == "whisper":
        return WhisperSTT(model=model or "base")
    if name == "mock":
        return MockSTT()
    raise ValueError(f"unknown STT provider: {provider!r}")
