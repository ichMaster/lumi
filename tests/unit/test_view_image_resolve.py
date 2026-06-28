"""v0.22 view_image path resolution — a bare name finds art/<name> (the generate_image convention) and
anywhere in the sandbox; an explicit missing path is not guessed. No network."""
from __future__ import annotations

from core.images import is_image_block
from core.imagetool import ImageTools

_PNG = b"\x89PNG-fake"


def _mk(root, rel):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(_PNG)
    return f


def test_bare_name_resolves_in_art(tmp_path):
    # generate_image saves into art/; view_image("foo.png") must find art/foo.png.
    _mk(tmp_path, "art/agnika-new.png")
    out = ImageTools(tmp_path).execute("view_image", {"path": "agnika-new.png"})
    assert is_image_block(out)  # found + returned an image block (not an error string)


def test_exact_path_still_works(tmp_path):
    _mk(tmp_path, "art/cat.png")
    assert is_image_block(ImageTools(tmp_path).execute("view_image", {"path": "art/cat.png"}))


def test_bare_name_resolves_anywhere(tmp_path):
    _mk(tmp_path, "pictures/Pissarro/boulevard.png")
    assert is_image_block(ImageTools(tmp_path).execute("view_image", {"path": "boulevard.png"}))


def test_root_takes_precedence_over_art(tmp_path):
    _mk(tmp_path, "top.png")
    _mk(tmp_path, "art/top.png")
    # the as-given path (root) wins; both exist, the root one is used (exact match first)
    assert is_image_block(ImageTools(tmp_path).execute("view_image", {"path": "top.png"}))


def test_explicit_missing_path_is_not_guessed(tmp_path):
    _mk(tmp_path, "art/cat.png")
    # an explicit folder path that doesn't exist must NOT silently resolve to art/cat.png
    out = ImageTools(tmp_path).execute("view_image", {"path": "wrong/cat.png"})
    assert isinstance(out, str) and "image not found" in out


def test_missing_everywhere_errors(tmp_path):
    out = ImageTools(tmp_path).execute("view_image", {"path": "nope.png"})
    assert isinstance(out, str) and "image not found" in out
