"""v4.2 LUMI-305 — the window's gallery over one sandbox (``core/gallery.py``).

Pinned: every picture, newest first, typed by its bytes; the exact-folder filter; the total and the per-folder counts
over the whole sandbox; the cursor; thumbnails (size, JPEG, the cache hit and miss, the bounds); the cache outside the
sandbox — and that nothing under the root ever changes."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import pytest
from PIL import Image

from core import browse, gallery
from core.browse import BadPath, NotFound, NotServed, TooLarge
from core.gallery import decode_cursor, encode_cursor, list_pictures, thumbnail

NS = 1_000_000_000  # one second in ns


def _png(size=(400, 300), mode="RGB", color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return buf.getvalue()


def _jpeg(size=(640, 480), orientation=None) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", size, (10, 120, 200))
    if orientation is None:
        im.save(buf, "JPEG")
    else:
        exif = Image.Exif()
        exif[0x0112] = orientation
        im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _gif_animated() -> bytes:
    buf = io.BytesIO()
    frames = [Image.new("P", (60, 40), i) for i in (1, 2)]
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buf.getvalue()


def _webp() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (90, 90), (5, 5, 5)).save(buf, "WEBP")
    return buf.getvalue()


def _put(root: Path, rel: str, data: bytes, t: int) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, ns=(t * NS, t * NS))


def _sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "files" / "owner"
    root.mkdir(parents=True)
    _put(root, "art/rooftop.png", _png(), 1_700_000_500)
    _put(root, "art/photo.jpg", _jpeg(), 1_700_000_400)
    _put(root, "art/sketches/cat.gif", _gif_animated(), 1_700_000_300)
    _put(root, "pictures/w.webp", _webp(), 1_700_000_200)
    _put(root, "notes/really_png.txt", _png((32, 32)), 1_700_000_100)  # a picture whatever its name
    _put(root, "top.png", _png((10, 10)), 1_700_000_050)  # a picture in the root
    _put(root, "art/fake.png", b"not a picture", 1_700_000_900)  # a ".png" holding text is not one
    _put(root, "art/x.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>', 1_700_000_800)
    _put(root, "notes/a.md", "# нотатка\n".encode(), 1_700_000_700)
    (tmp_path / "outside.png").write_bytes(_png((8, 8)))
    os.symlink(tmp_path / "outside.png", root / "link_out.png")
    os.symlink(tmp_path, root / "loop")  # a symlinked folder is never walked
    return root


def _fingerprint(root: Path) -> list[tuple]:
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            p = Path(dirpath) / name
            st = p.lstat()
            digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else ""
            rows.append((str(p.relative_to(root)), st.st_size, st.st_mtime_ns, digest))
    return sorted(rows)


# --- the listing -----------------------------------------------------------------------------------------------
def test_every_picture_newest_first_typed_by_its_bytes(tmp_path):
    root = _sandbox(tmp_path)
    got = list_pictures(root)
    assert [i["path"] for i in got["items"]] == [
        "art/rooftop.png", "art/photo.jpg", "art/sketches/cat.gif", "pictures/w.webp", "notes/really_png.txt", "top.png",
    ]  # no fake.png, no x.svg, no markdown, no link out, nothing through the symlinked folder
    assert got["total"] == 6 and got["next"] is None and got["truncated"] is False
    first = got["items"][0]
    assert first == {"path": "art/rooftop.png", "folder": "art", "size": (root / "art/rooftop.png").stat().st_size,
                     "modified": first["modified"], "width": 400, "height": 300}
    assert len(first["modified"]) == 16  # "YYYY-MM-DD HH:MM", as in the Files view
    by = {i["path"]: i for i in got["items"]}
    assert by["top.png"]["folder"] == "" and by["art/sketches/cat.gif"]["folder"] == "art/sketches"
    assert (by["art/photo.jpg"]["width"], by["art/photo.jpg"]["height"]) == (640, 480)


def test_folders_and_the_total_count_the_whole_sandbox_whatever_the_filter(tmp_path):
    root = _sandbox(tmp_path)
    expected = [{"folder": "", "count": 1}, {"folder": "art", "count": 2}, {"folder": "art/sketches", "count": 1},
                {"folder": "notes", "count": 1}, {"folder": "pictures", "count": 1}]
    for kw in ({}, {"folder": "art"}, {"limit": 1}):
        got = list_pictures(root, **kw)
        assert got["folders"] == expected and got["total"] == 6


def test_the_folder_filter_is_exact(tmp_path):
    root = _sandbox(tmp_path)
    assert [i["path"] for i in list_pictures(root, folder="art")["items"]] == ["art/rooftop.png", "art/photo.jpg"]
    assert [i["path"] for i in list_pictures(root, folder="/art/")["items"]] == ["art/rooftop.png", "art/photo.jpg"]
    assert [i["path"] for i in list_pictures(root, folder="")["items"]] == ["top.png"]  # the root itself
    assert list_pictures(root, folder="nowhere")["items"] == []


def test_equal_times_order_by_path_and_page_stably(tmp_path):
    root = tmp_path / "same"
    root.mkdir()
    for name in ("c.png", "a.png", "b.png"):
        _put(root, name, _png((4, 4)), 1_700_000_000)
    assert [i["path"] for i in list_pictures(root)["items"]] == ["a.png", "b.png", "c.png"]


def test_walking_the_pages_returns_every_picture_exactly_once(tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for i in range(23):
        _put(root, f"d{i % 3}/p{i:02d}.png", _png((4, 4)), 1_700_000_000 + i // 2)  # pairs share a time
    seen, cursor, pages = [], None, 0
    while True:
        got = list_pictures(root, before=cursor, limit=5)
        seen += [i["path"] for i in got["items"]]
        pages += 1
        cursor = got["next"]
        if cursor is None:
            break
    assert pages == 5 and len(seen) == 23 and len(set(seen)) == 23
    assert seen == [i["path"] for i in list_pictures(root, limit=100)["items"]]


def test_the_cursor_round_trips_and_a_malformed_one_is_a_bad_path():
    assert decode_cursor(encode_cursor(1_700_000_000_123, "art/дощ.png")) == (1_700_000_000_123, "art/дощ.png")
    assert "=" not in encode_cursor(5, "a")
    for bad in ("%%%", "bm9jb2xvbg", encode_cursor(1, "a")[:-3] + "@@@"):
        with pytest.raises(BadPath):
            list_pictures(Path("."), before=bad)


@pytest.mark.parametrize("limit", [0, 101, -1])
def test_a_limit_outside_1_to_100_is_refused(tmp_path, limit):
    with pytest.raises(BadPath):
        list_pictures(_sandbox(tmp_path), limit=limit)


def test_an_unreadable_header_has_no_dimensions(tmp_path):
    root = tmp_path / "broken"
    root.mkdir()
    _put(root, "half.png", _png()[:20], 1_700_000_000)  # a PNG signature, then nothing usable
    got = list_pictures(root)
    assert got["items"][0]["path"] == "half.png"
    assert (got["items"][0]["width"], got["items"][0]["height"]) == (None, None)


def test_the_walk_stops_at_the_scan_cap_and_says_so(tmp_path, monkeypatch):
    root = _sandbox(tmp_path)
    monkeypatch.setattr(gallery, "GALLERY_MAX_SCAN", 3)
    got = list_pictures(root)
    assert got["truncated"] is True and got["total"] <= 3


def test_a_sandbox_that_does_not_exist_yet_lists_empty(tmp_path):
    got = list_pictures(tmp_path / "files" / "nobody")
    assert got == {"items": [], "next": None, "total": 0, "folders": [], "truncated": False}


# --- the thumbnail ---------------------------------------------------------------------------------------------
def _cache(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "thumbs" / "owner"


def _open(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def test_a_thumbnail_is_a_jpeg_with_its_longest_side_320(tmp_path):
    root = tmp_path / "big"
    root.mkdir()
    _put(root, "wide.png", _png((1000, 500)), 1_700_000_000)
    im = _open(thumbnail(root, "wide.png", _cache(tmp_path)))
    assert im.format == "JPEG" and im.size == (320, 160)


def test_a_small_picture_is_never_enlarged_and_every_type_works(tmp_path):
    root = _sandbox(tmp_path)
    cache = _cache(tmp_path)
    assert _open(thumbnail(root, "top.png", cache)).size == (10, 10)
    assert _open(thumbnail(root, "art/sketches/cat.gif", cache)).format == "JPEG"  # the first frame
    assert _open(thumbnail(root, "pictures/w.webp", cache)).format == "JPEG"
    assert _open(thumbnail(root, "notes/really_png.txt", cache)).format == "JPEG"


def test_transparency_is_flattened_and_the_exif_orientation_applied(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    _put(root, "clear.png", _png((50, 50), mode="RGBA", color=(0, 0, 0, 0)), 1_700_000_000)
    _put(root, "turned.jpg", _jpeg((200, 100), orientation=6), 1_700_000_000)
    clear = _open(thumbnail(root, "clear.png", _cache(tmp_path)))
    assert clear.mode == "RGB"
    assert all(abs(a - b) <= 4 for a, b in zip(clear.getpixel((25, 25)), gallery.THUMB_BACKGROUND, strict=True))
    assert _open(thumbnail(root, "turned.jpg", _cache(tmp_path))).size == (100, 200)  # upright


def test_a_cache_hit_never_opens_the_source(tmp_path, monkeypatch):
    root = _sandbox(tmp_path)
    cache = _cache(tmp_path)
    first = thumbnail(root, "art/rooftop.png", cache)
    assert len(list(cache.glob("*.jpg"))) == 1
    src = root / "art/rooftop.png"
    st = src.stat()
    src.write_bytes(b"garbage, not a picture")  # same mtime as before: the cache must not look at it
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    monkeypatch.setattr(gallery, "_make_thumb", lambda *a: pytest.fail("a hit decoded the source"))
    assert thumbnail(root, "art/rooftop.png", cache) == first


def test_a_new_mtime_is_a_miss_and_the_old_thumbnail_goes(tmp_path):
    root = _sandbox(tmp_path)
    cache = _cache(tmp_path)
    thumbnail(root, "art/rooftop.png", cache)
    thumbnail(root, "art/photo.jpg", cache)
    old = {p.name for p in cache.glob("*.jpg")}
    _put(root, "art/rooftop.png", _png((100, 100), color=(0, 200, 0)), 1_700_009_999)
    assert _open(thumbnail(root, "art/rooftop.png", cache)).size == (100, 100)
    now = {p.name for p in cache.glob("*.jpg")}
    assert len(now) == 2 and len(now - old) == 1  # rooftop's new one replaced its old one; photo's stays
    assert not list(cache.glob(".*.tmp"))


def test_too_many_pixels_is_too_large_before_any_decode(tmp_path, monkeypatch):
    root = _sandbox(tmp_path)
    monkeypatch.setattr(gallery, "THUMB_MAX_PIXELS", 1000)
    with pytest.raises(TooLarge):
        thumbnail(root, "art/rooftop.png", _cache(tmp_path))


def test_a_source_over_the_byte_cap_is_too_large(tmp_path, monkeypatch):
    root = _sandbox(tmp_path)
    monkeypatch.setattr(browse, "BROWSE_MAX_PICTURE", 16)
    with pytest.raises(TooLarge):
        thumbnail(root, "art/rooftop.png", _cache(tmp_path))


def test_a_broken_picture_is_not_served(tmp_path):
    root = tmp_path / "broken"
    root.mkdir()
    _put(root, "half.png", _png()[:40], 1_700_000_000)
    with pytest.raises(NotServed):
        thumbnail(root, "half.png", _cache(tmp_path))


def test_svg_text_missing_and_traversal_are_refused(tmp_path):
    root = _sandbox(tmp_path)
    cache = _cache(tmp_path)
    for rel in ("art/x.svg", "art/fake.png", "notes/a.md"):
        with pytest.raises(NotServed):
            thumbnail(root, rel, cache)
    with pytest.raises(NotFound):
        thumbnail(root, "art/nope.png", cache)
    for bad in ("../outside.png", "/etc/passwd", "link_out.png", "a\x00b.png"):
        with pytest.raises(BadPath):
            thumbnail(root, bad, cache)


def test_a_cache_folder_inside_the_sandbox_is_refused(tmp_path):
    root = _sandbox(tmp_path)
    for inside in (root, root / ".thumbs", root / "art" / "cache"):
        with pytest.raises(ValueError):
            thumbnail(root, "art/rooftop.png", inside)
    assert not (root / ".thumbs").exists() and not (root / "art" / "cache").exists()


def test_a_cache_that_cannot_be_written_still_returns_the_thumbnail(tmp_path):
    root = _sandbox(tmp_path)
    blocker = tmp_path / "cache"
    blocker.write_text("a file where the cache folder should be", encoding="utf-8")
    assert _open(thumbnail(root, "art/rooftop.png", blocker / "thumbs")).format == "JPEG"


def test_nothing_under_the_root_ever_changes(tmp_path):
    root = _sandbox(tmp_path)
    before = _fingerprint(root)
    cache = _cache(tmp_path)
    list_pictures(root)
    list_pictures(root, folder="art", limit=1)
    for rel in ("art/rooftop.png", "art/photo.jpg", "art/sketches/cat.gif", "pictures/w.webp"):
        thumbnail(root, rel, cache)
        thumbnail(root, rel, cache)
    for bad in ("art/x.svg", "nope.png", "../outside.png"):
        with pytest.raises((NotServed, NotFound, BadPath)):
            thumbnail(root, bad, cache)
    assert _fingerprint(root) == before
    assert cache.is_dir() and root not in cache.parents
