"""Unit tests for the v0.3 emotion renderer (LUMI-017, EMOTION.md §5)."""

import logging

from core.emotion import Emotion, EmotionState, IEmotionRenderer, LogRenderer

STATE = EmotionState(reply="Привіт!", emotion=Emotion.JOY, intensity=0.8)


def test_log_renderer_satisfies_the_interface():
    assert isinstance(LogRenderer(), IEmotionRenderer)


def test_render_logs_the_validated_field(caplog):
    r = LogRenderer()
    r.session_id, r.turn = "s1", 2
    with caplog.at_level(logging.INFO, logger="lumi.emotion.render"):
        r.render(STATE)
    rec = next(rr for rr in caplog.records if rr.name == "lumi.emotion.render")
    assert "joy" in rec.getMessage()
    assert rec.emotion == "joy" and rec.intensity == 0.8
    assert rec.session_id == "s1" and rec.turn == 2


def test_set_speaking_and_tick_are_no_ops():
    r = LogRenderer()
    r.set_speaking(True)  # must not raise
    r.tick(16)
