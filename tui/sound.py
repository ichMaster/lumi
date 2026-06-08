"""Optional UI sounds for the TUI (v0.7.x) — a short blip on send + receive.

Reuses pygame's **mixer** (already a dependency for the viewer) with two short
runtime-synthesized tones, so there are no bundled audio files. The mixer is initialized
**lazily on first play**, and everything is best-effort: with no audio device (headless /
CI) it silently no-ops. *Whether* a sound plays is the app's call (it gates on the toggle
and never sounds the idle nudge); this module only knows *how*.
"""

from __future__ import annotations

import array
import math
import os


def synth_tone(freq: float, ms: int, *, rate: int = 44100, volume: float = 0.55) -> bytes:
    """A short mono int16 sine tone with an 8 ms fade in/out (so there's no click)."""
    n = int(rate * ms / 1000)
    fade = max(1, int(rate * 0.008))
    amp = volume * 32767
    out = array.array("h")
    for i in range(n):
        if i < fade:
            env = i / fade
        elif i > n - fade:
            env = (n - i) / fade
        else:
            env = 1.0
        out.append(int(amp * env * math.sin(2.0 * math.pi * freq * i / rate)))
    return out.tobytes()


class SoundPlayer:
    """Plays a send / receive blip via pygame.mixer, initialized lazily on first use.

    No audio device (or pygame missing) → a silent no-op. Call :meth:`ensure` to probe
    availability (used by the toggle); :meth:`send` / :meth:`receive` play the blips.
    """

    _RATE = 44100

    def __init__(self) -> None:
        self._ready: bool | None = None  # None = not tried yet
        self._send_snd: object | None = None
        self._receive_snd: object | None = None

    def ensure(self) -> bool:
        """Initialize the mixer on first call; return True if audio is available."""
        if self._ready is not None:
            return self._ready
        try:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame

            pygame.mixer.init(frequency=self._RATE, size=-16, channels=1)
            self._send_snd = pygame.mixer.Sound(buffer=synth_tone(880, 70))  # send: higher blip
            self._receive_snd = pygame.mixer.Sound(buffer=synth_tone(523, 120))  # receive: softer
            self._send_snd.set_volume(0.4)
            self._receive_snd.set_volume(0.4)
            self._ready = True
        except Exception:  # noqa: BLE001 — no audio device / pygame missing → stay silent
            self._ready = False
        return self._ready

    def send(self) -> None:
        self._play(self._send_snd)

    def receive(self) -> None:
        self._play(self._receive_snd)

    def _play(self, sound: object | None) -> None:
        if not self.ensure() or sound is None:
            return
        try:
            sound.play()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — never let a sound error reach the UI
            pass
