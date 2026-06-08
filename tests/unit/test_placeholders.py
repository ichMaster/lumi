"""Unit tests for the v0.7 placeholder face generator (LUMI-030) — headless (Pillow)."""

from PIL import Image

from core.emotion import Emotion
from viewer.placeholders import generate_placeholders


def test_generates_a_png_for_every_emotion(tmp_path):
    paths = generate_placeholders(tmp_path)
    assert len(paths) == len(list(Emotion))  # all 9
    for e in Emotion:
        p = tmp_path / f"{e.value}.png"
        assert p.is_file()
        with Image.open(p) as img:
            assert img.size == (768, 768)  # a valid PNG


def test_keeps_existing_art_unless_overwrite(tmp_path):
    (tmp_path / "calm.png").write_bytes(b"real-art")  # pretend the user dropped art
    written = generate_placeholders(tmp_path)
    assert (tmp_path / "calm.png") not in written          # not clobbered
    assert (tmp_path / "calm.png").read_bytes() == b"real-art"
    assert (tmp_path / "calm.png") in generate_placeholders(tmp_path, overwrite=True)


def test_png_to_surface_loads_a_png_via_pillow(tmp_path, monkeypatch):
    # pygame's bundled loader only does BMP here; the viewer decodes PNGs with Pillow.
    import pytest

    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")  # headless
    monkeypatch.setenv("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    pygame = pytest.importorskip("pygame")
    pygame.init()
    pygame.display.set_mode((32, 32))
    try:
        from viewer.app import png_to_surface

        generate_placeholders(tmp_path)
        surf = png_to_surface(tmp_path / "calm.png", (32, 32))
        assert surf.get_size() == (32, 32)
    finally:
        pygame.quit()
