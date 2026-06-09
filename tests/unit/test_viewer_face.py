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
    assert read_signal(sig) == (None, "joy", 0.80)  # (theme, emotion, intensity)


def test_read_signal_emotion_only(tmp_path):
    sig = tmp_path / "face.txt"
    sig.write_text("sad", encoding="utf-8")
    assert read_signal(sig) == (None, "sad", None)


def test_read_signal_missing_or_garbled(tmp_path):
    assert read_signal(tmp_path / "nope.txt") == (None, "calm", None)  # missing file
    sig = tmp_path / "face.txt"
    sig.write_text("bogus xyz", encoding="utf-8")
    assert read_signal(sig) == (None, "calm", None)  # unknown emotion, bad intensity


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


def test_parse_signal_ignores_trailing_datetime():
    from viewer.face import parse_signal

    assert parse_signal("joy 0.80 2026-06-08 14:30:00") == (None, "joy", 0.80)


def test_idle_relax_wakes_on_a_repeated_emotion_with_a_new_timestamp(tmp_path):
    sig = tmp_path / "face.txt"
    sig.write_text("joy 0.9 2026-06-08 14:00:00", encoding="utf-8")
    sw = FaceSwitcher(sig, FACES, exists=_present("calm.png", "joy_high.png"), idle_timeout=60)
    assert sw.poll(now=0) == FACES / "joy_high.png"
    assert sw.poll(now=60) == FACES / "calm.png"  # idled to calm
    sig.write_text("joy 0.9 2026-06-08 14:05:00", encoding="utf-8")  # SAME emotion, new time
    assert sw.poll(now=70) == FACES / "joy_high.png"  # the changed line wakes it


# --- v0.11: themed variants + extended signal -----------------------------
import random  # noqa: E402

from viewer.face import parse_signal, pick_variant, resolve_variants  # noqa: E402


def _tree(*paths):
    """A fake faces tree: a lister returning the given .png paths under each folder."""
    have = {Path(p) for p in paths}

    def lister(folder):
        return sorted(p for p in have if p.parent == Path(folder))

    return lister


def test_resolve_variants_returns_a_folders_pngs():
    ls = _tree("faces/cozy/joy/01.png", "faces/cozy/joy/02.png", "faces/cozy/joy/03.png")
    got = resolve_variants("joy", theme="cozy", faces_dir="faces", lister=ls)
    assert got == [Path("faces/cozy/joy/01.png"), Path("faces/cozy/joy/02.png"), Path("faces/cozy/joy/03.png")]


def test_resolve_variants_missing_emotion_falls_to_theme_calm():
    ls = _tree("faces/cozy/calm/01.png", "faces/cozy/calm/02.png")  # no cozy/joy/
    assert resolve_variants("joy", theme="cozy", faces_dir="faces", lister=ls) == [
        Path("faces/cozy/calm/01.png"), Path("faces/cozy/calm/02.png"),
    ]


def test_resolve_variants_missing_theme_falls_to_default_theme():
    ls = _tree("faces/base/joy/01.png")  # the "cozy" theme has nothing; "base" is default
    got = resolve_variants("joy", theme="cozy", default_theme="base", faces_dir="faces", lister=ls)
    assert got == [Path("faces/base/joy/01.png")]


def test_resolve_variants_no_themes_falls_to_flat_v07():
    # No theme folders at all → the flat faces/<emotion>.png (one-element list).
    got = resolve_variants("joy", faces_dir="faces", lister=_tree(),
                           exists=lambda p: p == Path("faces/joy.png"))
    assert got == [Path("faces/joy.png")]


def test_pick_variant_no_immediate_repeat():
    variants = [Path("a.png"), Path("b.png"), Path("c.png")]
    rng = random.Random(1)
    for _ in range(40):  # never returns `previous` when alternatives exist
        prev = Path("b.png")
        assert pick_variant(variants, previous=prev, rng=rng) != prev
    assert pick_variant([Path("only.png")], previous=Path("only.png")) == Path("only.png")  # 1 → itself
    assert pick_variant([]) is None


def test_parse_signal_with_theme():
    assert parse_signal("cozy sad 0.30") == ("cozy", "sad", 0.30)
    assert parse_signal("cozy joy 0.80 2026-06-08 14:30:00") == ("cozy", "joy", 0.80)
    assert parse_signal("joy 0.80") == (None, "joy", 0.80)  # leading emotion → no theme


def test_face_switcher_picks_themed_variants_no_repeat(tmp_path):
    sig = tmp_path / "face.txt"
    ls = _tree("faces/cozy/joy/01.png", "faces/cozy/joy/02.png")
    sw = FaceSwitcher(sig, "faces", lister=ls, default_theme="cozy", rng=random.Random(0))
    seen = []
    for i in range(6):  # each new turn (new timestamp) re-picks; never an immediate repeat
        sig.write_text(f"cozy joy 0.5 2026-06-08 14:0{i}:00", encoding="utf-8")
        got = sw.poll()
        assert got in (Path("faces/cozy/joy/01.png"), Path("faces/cozy/joy/02.png"))
        if seen:
            assert got != seen[-1]  # no immediate repeat
        seen.append(got)
    assert set(seen) == {Path("faces/cozy/joy/01.png"), Path("faces/cozy/joy/02.png")}  # both used
