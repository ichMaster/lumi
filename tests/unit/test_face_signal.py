"""Unit tests for the v0.7 emotion-face signal (LUMI-028) — written each turn."""

from core.agent import Core
from core.llm import MockLLMClient
from state.local_store import JsonRepository


def _core(tmp_path, signal):
    return Core(
        llm=MockLLMClient(states={"reply": "Радо!", "emotion": "joy", "intensity": 0.8}),
        repository=JsonRepository(tmp_path / "s.json"),
        canon="Ти — Лілі.",
        model="m",
        face_signal=signal,
    )


def test_turn_writes_the_emotion_signal(tmp_path):
    sig = tmp_path / "face.txt"
    core = _core(tmp_path, sig)
    core.reply("привіт", core.start_session())
    text = sig.read_text(encoding="utf-8")
    assert text.split()[0] == "joy"        # the validated emotion word
    assert text.split()[1] == "0.80"       # + its intensity


def test_start_session_seeds_calm(tmp_path):
    sig = tmp_path / "face.txt"
    _core(tmp_path, sig).start_session()    # before any turn → calm
    assert sig.read_text(encoding="utf-8").split()[0] == "calm"


def test_signal_write_error_degrades_silently(tmp_path):
    core = _core(tmp_path, tmp_path)        # a directory, not a file → write fails
    core.reply("привіт", core.start_session())  # must not raise


def test_no_signal_path_is_a_noop(tmp_path):
    core = _core(tmp_path, None)            # signal off
    core.reply("привіт", core.start_session())  # no write, no error
