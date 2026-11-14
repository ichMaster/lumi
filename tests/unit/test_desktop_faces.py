"""v2.12.2 LUMI-271 — the window's portrait pick (``desktop/faces.py``) over a generated tmp pack: by intensity in a
01/02/03 folder, the random no-repeat pick otherwise, the fallback chain (the theme's calm, the default theme, the
flat v0.7 image), an unknown emotion or theme."""

from __future__ import annotations

import random

import pytest
from PIL import Image

from desktop.faces import FacePicker


def _png(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4)).save(path)


@pytest.fixture
def pack(tmp_path):
    root = tmp_path / "faces"
    for emo in ("calm", "sad"):
        for n in ("01", "02", "03"):
            _png(root / "quiet" / emo / f"{n}.png")
    for n in ("a", "b", "c"):
        _png(root / "loose" / "calm" / f"{n}.png")  # not named by intensity → the random pick
    for name in ("calm", "joy", "joy_low", "joy_high"):
        _png(root / f"{name}.png")  # the flat v0.7 pack
    return root


def test_intensity_chooses_the_file_in_a_named_folder(pack):
    picker = FacePicker(pack)
    assert picker.pick("quiet", "sad", 0.1).rel == "quiet/sad/02.png"
    assert picker.pick("quiet", "sad", 0.5).rel == "quiet/sad/01.png"
    assert picker.pick("quiet", "sad", 0.9).rel == "quiet/sad/03.png"
    assert picker.pick("quiet", "sad", None).rel == "quiet/sad/01.png"  # no intensity → mid
    got = picker.pick("quiet", "sad", 0.9)
    assert (got.theme, got.emotion, got.band) == ("quiet", "sad", "high")


def test_the_fallback_chain(pack):
    picker = FacePicker(pack)
    assert picker.pick("quiet", "joy", 0.9).rel == "quiet/calm/03.png"  # the theme's calm, still by intensity
    assert picker.pick("missing-theme", "joy", 0.9).rel == "joy_high.png"  # no such theme → the flat pack
    assert picker.pick(None, "joy", 0.1).rel == "joy_low.png"
    assert picker.pick(None, "bogus", 0.5).rel == "calm.png"  # an unknown emotion → calm
    with_default = FacePicker(pack, default_theme="quiet")
    assert with_default.pick("missing-theme", "sad", 0.1).rel == "quiet/sad/02.png"


def test_a_folder_not_named_by_intensity_picks_at_random_without_a_repeat(pack):
    picker = FacePicker(pack, rng=random.Random(7))
    picks = [picker.pick("loose", "calm", 0.5).rel for _ in range(12)]
    assert set(picks) <= {"loose/calm/a.png", "loose/calm/b.png", "loose/calm/c.png"}
    assert all(a != b for a, b in zip(picks, picks[1:], strict=False))


def test_no_pack_means_no_portrait(tmp_path):
    picker = FacePicker(tmp_path / "nowhere")
    assert not picker.available and picker.pick("quiet", "calm", 0.5) is None
