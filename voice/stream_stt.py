"""Streaming STT adapter (v1.6.2 LUMI-200) — Deepgram over WebSocket, the streaming twin of
:mod:`voice.stt` (batch REST).

The chain-WS probe measured WHY this exists: the REST dictator path pays ~1.4 s recognizing the
whole utterance AFTER it ends; streaming recognizes DURING speech, so at the endpointing decision
(«he finished») the transcript is already in hand — the STT stage collapses to ~0.

Three pieces, all seam-shaped for tests (no network, no paid CI):

* :func:`build_deepgram_url` — the ``wss://`` listen URL: raw linear16 mono, Ukrainian, interim
  results flowing, server **endpointing** (its VAD ends the turn — no local silence timer).
* :class:`UtteranceTracker` — assembles ``Results``/``UtteranceEnd`` events into whole utterances,
  with the **phrase-hold**: an un-punctuated ``speech_final`` (a breathing pause mid-sentence) is
  HELD for the continuation or the ``UtteranceEnd`` backstop, so she never answers a half-phrase.
* :class:`DeepgramStream` — the thin async WS seam: send pcm frames, iterate parsed event dicts.
  The connector is injectable (``_connect``), so tests feed scripted events through a fake socket;
  the real path lazy-imports ``websockets`` (an optional dep — never needed by core/tests).
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import AsyncIterator

DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"
MIC_RATE = 16_000             # the dictator's capture rate — plenty for Nova-3, smaller upload
DEFAULT_ENDPOINT_MS = 500     # endpointing silence window (300 cut the owner off mid-phrase live)
UTTERANCE_END_MS = 1000       # the backstop flush when endpointing never fires (trailing hum etc.)


def build_deepgram_url(*, model: str, lang: str = "uk", rate: int = MIC_RATE,
                       endpoint_ms: int = DEFAULT_ENDPOINT_MS,
                       utterance_end_ms: int = UTTERANCE_END_MS) -> str:
    """The Deepgram streaming URL: raw linear16 mono + interim results (utterance_end needs them) +
    endpointing — the server, not a local VAD, decides when the utterance ended."""
    params = {
        "model": model, "language": lang,
        "encoding": "linear16", "sample_rate": str(rate), "channels": "1",
        "interim_results": "true", "smart_format": "true",
        "endpointing": str(endpoint_ms), "utterance_end_ms": str(utterance_end_ms),
    }
    return f"{DEEPGRAM_WS_URL}?{urllib.parse.urlencode(params)}"


_TERMINAL_PUNCT = (".", "!", "?", "…")


class UtteranceTracker:
    """Assemble Deepgram streaming events into whole utterances. Pure — fed parsed JSON dicts.

    ``Results`` with ``is_final`` contribute transcript segments; ``speech_final`` (the endpointing
    decision) flushes the joined utterance — **but an UNFINISHED phrase is held**: when the joined
    text does not end in terminal punctuation (smart_format punctuates completed speech), the
    endpointing likely fired inside a natural mid-sentence pause (it cut the owner off live at
    300 ms — «Я його спробував» / «…збирав»), so the tracker waits for the continuation (the parts
    join into ONE utterance) or the ``UtteranceEnd`` backstop (~1 s), which flushes regardless.
    Empty finals are ignored; a flush with nothing pending returns None."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def feed(self, event: dict) -> str | None:
        etype = event.get("type", "")
        if etype == "Results":
            alt = (((event.get("channel") or {}).get("alternatives")) or [{}])[0]
            text = (alt.get("transcript") or "").strip()
            if event.get("is_final"):
                if text:
                    self._parts.append(text)
                if event.get("speech_final") and self._parts:
                    joined = " ".join(self._parts)
                    if joined.endswith(_TERMINAL_PUNCT):
                        return self._flush()
                    return None  # mid-phrase pause — hold for the continuation / the backstop
        elif etype == "UtteranceEnd" and self._parts:
            return self._flush()
        return None

    def _flush(self) -> str:
        out = " ".join(self._parts)
        self._parts = []
        return out


class DeepgramStream:
    """The thin async WS seam: ``open`` → ``send_pcm`` frames → iterate :meth:`events` dicts.

    ``_connect`` is an injectable ``async (url, headers) -> ws`` returning any object with
    ``send``/``close`` coroutines and async iteration of raw JSON strings — tests pass a fake;
    the default lazy-imports ``websockets`` (the ``realtime`` extra) and connects for real.
    Malformed frames are skipped, never raised (a garbled line must not kill the mic loop).
    """

    def __init__(self, api_key: str, *, url: str, _connect=None) -> None:
        self._api_key = api_key  # secret — never logged
        self._url = url
        self._connect = _connect
        self._ws = None

    async def open(self) -> DeepgramStream:
        headers = {"Authorization": f"Token {self._api_key}"}
        if self._connect is not None:
            self._ws = await self._connect(self._url, headers)
        else:  # pragma: no cover — the real network path (manual + paid, never CI)
            import websockets

            self._ws = await websockets.connect(
                self._url, additional_headers=headers, max_size=1 << 24
            )
        return self

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def send_pcm(self, pcm: bytes) -> None:
        """One raw linear16 mic frame → Deepgram (binary WS message)."""
        await self._ws.send(pcm)

    async def events(self) -> AsyncIterator[dict]:
        """Parsed server events, in order; non-JSON frames are skipped."""
        async for raw in self._ws:
            try:
                yield json.loads(raw)
            except (ValueError, TypeError):
                continue
