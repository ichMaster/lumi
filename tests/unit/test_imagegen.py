"""v0.23 LUMI-095 — the ImageGen seam + generate_image tool (core/imagegen.py). No network."""
from __future__ import annotations

from core.imagegen import GENERATE_TOOL_NAMES, GENERATE_TOOLS, ImageGenError, ImageMaker, _slug


def _stub(data=b"\x89PNG-fake"):
    """A fake ImageGen — records calls, returns canned PNG bytes (no paid generation)."""
    calls: list = []

    def gen(prompt: str, *, size: int = 768) -> bytes:
        calls.append((prompt, size))
        return data

    gen.calls = calls  # type: ignore[attr-defined]
    return gen


def _boom(prompt, *, size=768):
    raise ImageGenError("safety refusal")


# --- tool def ---------------------------------------------------------------------------------------
def test_generate_tools_shape():
    assert GENERATE_TOOL_NAMES == {"generate_image"}
    for t in GENERATE_TOOLS:
        assert {"name", "description", "input_schema"} <= t.keys()
        assert t["input_schema"]["required"] == ["prompt"] and "." not in t["name"]


# --- generate_image: create-only -------------------------------------------------------------------
def test_generate_writes_a_new_png(tmp_path):
    gen = _stub(b"\x89PNG-bytes")
    maker = ImageMaker(tmp_path, image_gen=gen)
    out = maker.execute("generate_image", {"prompt": "кіт в окулярах", "filename": "cat"})
    assert out.startswith("created art/cat.png")
    saved = tmp_path / "art" / "cat.png"
    assert saved.read_bytes() == b"\x89PNG-bytes"
    assert gen.calls == [("кіт в окулярах", 768)]  # the prompt reached the generator unchanged


def test_generate_default_filename_is_a_slug(tmp_path):
    maker = ImageMaker(tmp_path, image_gen=_stub())
    out = maker.execute("generate_image", {"prompt": "Cat in Glasses!"})
    assert "art/cat-in-glasses.png" in out and (tmp_path / "art" / "cat-in-glasses.png").is_file()


def test_filename_with_art_prefix_is_not_doubled(tmp_path):
    # The model often passes "art/foo.png" itself — it must land in art/, not art/art/.
    maker = ImageMaker(tmp_path, image_gen=_stub(b"PNG"))
    out = maker.execute("generate_image", {"prompt": "x", "filename": "art/mood.png"})
    assert out.startswith("created art/mood.png")
    assert (tmp_path / "art" / "mood.png").is_file()
    assert not (tmp_path / "art" / "art").exists()  # no double-nested folder


def test_filename_with_leading_slash_normalized(tmp_path):
    maker = ImageMaker(tmp_path, image_gen=_stub(b"PNG"))
    out = maker.execute("generate_image", {"prompt": "x", "filename": "/art/sky.png"})
    assert out.startswith("created art/sky.png") and (tmp_path / "art" / "sky.png").is_file()


def test_generate_refuses_existing_no_overwrite(tmp_path):
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "cat.png").write_bytes(b"ORIGINAL")
    maker = ImageMaker(tmp_path, image_gen=_stub(b"NEW"))
    out = maker.execute("generate_image", {"prompt": "x", "filename": "cat.png"})
    assert "already exists" in out and "no overwrite" in out
    assert (tmp_path / "art" / "cat.png").read_bytes() == b"ORIGINAL"  # untouched


# --- safety + graceful degradation -----------------------------------------------------------------
def test_generate_missing_prompt(tmp_path):
    assert "missing 'prompt'" in ImageMaker(tmp_path, image_gen=_stub()).execute("generate_image", {})


def test_generate_sandbox_escape_refused(tmp_path):
    maker = ImageMaker(tmp_path, image_gen=_stub())
    out = maker.execute("generate_image", {"prompt": "x", "filename": "../escape.png"})
    assert "traversal" in out and not (tmp_path.parent / "escape.png").exists()  # nothing written outside


def test_generate_error_degrades(tmp_path):
    maker = ImageMaker(tmp_path, image_gen=_boom)
    out = maker.execute("generate_image", {"prompt": "щось заборонене"})
    assert out.startswith("error: image generation failed") and "safety refusal" in out
    assert not (tmp_path / "art").exists()  # no file on a generation failure


def test_executor_never_raises(tmp_path):
    maker = ImageMaker(tmp_path, image_gen=_stub())
    assert maker.execute("bogus", {"prompt": "x"}).startswith("error: unknown image tool")


def test_slug_helper():
    assert _slug("Cat in Glasses!") == "cat-in-glasses" and _slug("   ") == "image"
