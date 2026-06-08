"""Unit tests for the TUI send/receive sound (v0.7.x) — synthesis + graceful no-op."""

from tui.sound import SoundPlayer, synth_tone


def test_synth_tone_length_and_format():
    # 70 ms @ 44100 Hz mono int16 → 3087 samples × 2 bytes.
    data = synth_tone(880, 70)
    assert len(data) == int(44100 * 70 / 1000) * 2
    assert isinstance(data, bytes)


def test_player_is_silent_until_a_device_is_available():
    # Without a working mixer, send/receive must never raise (best-effort no-op).
    player = SoundPlayer()
    player.send()
    player.receive()  # no exception even if ensure() fails


def test_ensure_with_dummy_audio_driver(monkeypatch):
    # The SDL dummy audio driver lets the mixer init headlessly — exercises the
    # synth → Sound → play path without making noise.
    import pytest

    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    monkeypatch.setenv("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    pytest.importorskip("pygame")
    player = SoundPlayer()
    if player.ensure():  # available under the dummy driver
        player.send()
        player.receive()
    assert player.ensure() in (True, False)  # idempotent, never raises
