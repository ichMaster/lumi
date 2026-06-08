"""Unit tests for the v0.7 face resolver + signal reader (LUMI-029)."""

from pathlib import Path

from core.emotion import Emotion
from viewer.face import FaceSwitcher, face_for, read_signal

FACES = Path("faces")


def _present(*names):
    have = {FACES / n for n in names}
    return lambda p: p in have


def test_face_for_is_total_over_the_enum_with_calm_fallback():
    only_calm = _present("calm.png")  # only calm.png on disk
    for e in Emotion:
        assert face_for(e.value, faces_dir=FACES, exists=only_calm) == FACES / "calm.png"


def test_base_image_used_when_present():
    ex = _present("joy.png", "calm.png")
    assert face_for("joy", faces_dir=FACES, exists=ex) == FACES / "joy.png"


def test_unknown_emotion_falls_back_to_calm():
    ex = _present("calm.png", "joy.png")
    assert face_for("ecstatic", faces_dir=FACES, exists=ex) == FACES / "calm.png"


def test_missing_file_falls_back_to_calm():
    assert face_for("joy", faces_dir=FACES, exists=_present()) == FACES / "calm.png"


def test_intensity_variant_used_when_present():
    ex = _present("joy.png", "joy_high.png", "calm.png")
    assert face_for("joy", 0.9, faces_dir=FACES, exists=ex) == FACES / "joy_high.png"
    assert face_for("joy", 0.5, faces_dir=FACES, exists=ex) == FACES / "joy.png"  # mid → base


def test_intensity_variant_missing_falls_to_base():
    ex = _present("joy.png", "calm.png")  # no joy_high.png
    assert face_for("joy", 0.9, faces_dir=FACES, exists=ex) == FACES / "joy.png"


def test_read_signal_parses_emotion_and_intensity(tmp_path):
    sig = tmp_path / "face.txt"
    sig.write_text("joy 0.80", encoding="utf-8")
    assert read_signal(sig) == ("joy", 0.80)


def test_read_signal_emotion_only(tmp_path):
    sig = tmp_path / "face.txt"
    sig.write_text("sad", encoding="utf-8")
    assert read_signal(sig) == ("sad", None)


def test_read_signal_missing_or_garbled(tmp_path):
    assert read_signal(tmp_path / "nope.txt") == ("calm", None)  # missing file
    sig = tmp_path / "face.txt"
    sig.write_text("bogus xyz", encoding="utf-8")
    assert read_signal(sig) == ("calm", None)  # unknown emotion, bad intensity


def test_face_switcher_reports_only_on_change(tmp_path):
    sig = tmp_path / "face.txt"
    sig.write_text("calm 0.5", encoding="utf-8")
    sw = FaceSwitcher(sig, FACES, exists=_present("calm.png", "joy.png"))
    assert sw.poll() == FACES / "calm.png"  # first poll → a change
    assert sw.poll() is None  # unchanged
    sig.write_text("joy 0.5", encoding="utf-8")
    assert sw.poll() == FACES / "joy.png"  # changed
    assert sw.poll() is None


def test_face_switcher_relaxes_to_default_after_idle(tmp_path):
    sig = tmp_path / "face.txt"
    sig.write_text("joy 0.9", encoding="utf-8")
    sw = FaceSwitcher(
        sig, FACES, exists=_present("calm.png", "joy.png", "joy_high.png"), idle_timeout=60
    )
    assert sw.poll(now=0) == FACES / "joy_high.png"  # show joy (high)
    assert sw.poll(now=30) is None                    # still within the idle window
    assert sw.poll(now=60) == FACES / "calm.png"      # idle → relax to default (calm)
    assert sw.poll(now=999) is None                   # stays calm


def test_idle_relax_resets_on_a_new_signal(tmp_path):
    sig = tmp_path / "face.txt"
    sig.write_text("joy 0.9", encoding="utf-8")
    ex = _present("calm.png", "joy_high.png", "sad.png")
    sw = FaceSwitcher(sig, FACES, exists=ex, idle_timeout=60)
    sw.poll(now=0)
    assert sw.poll(now=60) == FACES / "calm.png"      # idled to calm
    sig.write_text("sad 0.5", encoding="utf-8")
    assert sw.poll(now=70) == FACES / "sad.png"       # new signal wakes it
    assert sw.poll(now=120) is None                   # within the new idle window (70+60)
    assert sw.poll(now=131) == FACES / "calm.png"     # idles again


def test_no_idle_timeout_holds_the_face(tmp_path):
    sig = tmp_path / "face.txt"
    sig.write_text("joy 0.9", encoding="utf-8")
    sw = FaceSwitcher(sig, FACES, exists=_present("calm.png", "joy_high.png"))  # idle off
    assert sw.poll(now=0) == FACES / "joy_high.png"
    assert sw.poll(now=10_000) is None                # never relaxes
