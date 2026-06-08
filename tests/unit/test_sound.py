"""Unit tests for the TUI send/receive sound (v0.7.x) — backend choice, no real playback."""

import sys

import tui.sound as sound
from tui.sound import SoundPlayer, synth_tone


def test_synth_tone_length_and_format():
    # 70 ms @ 44100 Hz mono int16 → 3087 samples × 2 bytes.
    data = synth_tone(880, 70)
    assert len(data) == int(44100 * 70 / 1000) * 2
    assert isinstance(data, bytes)


def test_silent_no_op_when_no_backend(monkeypatch):
    # Non-macOS + this build has no pygame.mixer → no backend; must never raise.
    monkeypatch.setattr(sys, "platform", "linux")
    player = SoundPlayer()
    assert player.ensure() is False
    player.send()
    player.receive()  # silent no-op


def test_afplay_backend_on_macos(monkeypatch):
    # On darwin the player uses afplay + system sounds; mock Popen so nothing plays.
    import pytest

    if sys.platform != "darwin":
        pytest.skip("afplay backend is macOS-only")
    calls: list[list[str]] = []
    monkeypatch.setattr(sound.subprocess, "Popen", lambda args, **kw: calls.append(args))
    player = SoundPlayer()
    assert player.ensure() is True
    assert player._backend == "afplay"
    player.send()
    player.receive()
    assert len(calls) == 2
    assert all(c[0].endswith("afplay") for c in calls)  # the afplay binary
    assert calls[0][1].endswith("Tink.aiff")  # send
    assert calls[1][1].endswith("Glass.aiff")  # receive
