"""v4.1 LUMI-301 — the window's read-only view of one sandbox (``core/browse.py``).

Pinned: folders first then by name, types by content (magic bytes / UTF-8), text cut on a character boundary,
pictures png/jpeg/gif/webp only, the sandbox guard, the caps — and that nothing under the root ever changes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from core import browse
from core.agent import Core
from core.browse import BadPath, NotFound, NotServed, TooLarge, list_folder, read_picture, read_text
from core.llm import MockLLMClient
from state.local_store import JsonRepository

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
GIF = b"GIF89a" + b"\x00" * 40
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 40
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "files" / "owner"
    (root / "notes").mkdir(parents=True)
    (root / "art").mkdir()
    (root / "notes" / "ideas.md").write_text("# Ідеї\n\n- дощ\n", encoding="utf-8")
    (root / "notes" / "list.txt").write_text("one\ntwo\n", encoding="utf-8")
    (root / "data.json").write_text('{"a": 1}', encoding="utf-8")
    (root / "code.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "Zeta.md").write_text("z", encoding="utf-8")
    (root / "art" / "p.png").write_bytes(PNG)
    (root / "art" / "j.jpg").write_bytes(JPG)
    (root / "art" / "g.gif").write_bytes(GIF)
    (root / "art" / "w.webp").write_bytes(WEBP)
    (root / "art" / "really_png.txt").write_bytes(PNG)  # a picture with a wrong extension
    (root / "art" / "fake.png").write_text("not a picture", encoding="utf-8")  # text with a picture's name
    (root / "art" / "x.svg").write_bytes(SVG)
    (root / "blob.dat").write_bytes(b"\x00\x01\x02\x03")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    os.symlink(tmp_path / "outside.txt", root / "link_out.txt")
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


def test_a_folder_lists_folders_first_then_files_by_name_with_types_and_dates(tmp_path):
    root = _sandbox(tmp_path)
    got = list_folder(root, "")
    assert got["path"] == "" and got["truncated"] is False
    names = [e["name"] for e in got["entries"]]
    assert names[:2] == ["art", "notes"]  # folders first
    assert names[2:] == sorted(names[2:], key=str.casefold)  # then files by name, case-insensitively
    by = {e["name"]: e for e in got["entries"]}
    assert by["art"]["kind"] == "dir" and by["art"]["count"] == 7 and by["art"]["size"] is None
    assert by["data.json"] == {**by["data.json"], "kind": "file", "type": "text", "size": 8, "count": None}
    assert by["blob.dat"]["type"] == "other"
    assert len(by["code.py"]["created"]) == 16 and len(by["code.py"]["modified"]) == 16  # "YYYY-MM-DD HH:MM"


def test_types_come_from_the_bytes_not_the_extension(tmp_path):
    root = _sandbox(tmp_path)
    by = {e["name"]: e["type"] for e in list_folder(root, "art")["entries"]}
    assert by["p.png"] == by["j.jpg"] == by["g.gif"] == by["w.webp"] == "image"
    assert by["really_png.txt"] == "image"  # a picture whatever its name
    assert by["fake.png"] == "text"  # a ".png" holding text is not a picture
    assert by["x.svg"] == "text"  # SVG is text to the listing, never a picture


def test_a_symlink_out_of_the_sandbox_is_not_listed_and_never_read(tmp_path):
    root = _sandbox(tmp_path)
    assert "link_out.txt" not in [e["name"] for e in list_folder(root, "")["entries"]]
    with pytest.raises(BadPath):
        read_text(root, "link_out.txt")


@pytest.mark.parametrize("bad", ["../outside.txt", "/etc/passwd", "notes/../../outside.txt"])
def test_a_path_out_of_the_sandbox_is_refused_without_an_absolute_path_in_the_message(tmp_path, bad):
    root = _sandbox(tmp_path)
    for read in (lambda r: list_folder(root, r), lambda r: read_text(root, r), lambda r: read_picture(root, r)):
        with pytest.raises(BadPath) as err:
            read(bad)
        assert str(tmp_path) not in str(err.value)


def test_missing_paths_and_a_file_listed_as_a_folder(tmp_path):
    root = _sandbox(tmp_path)
    with pytest.raises(NotFound):
        list_folder(root, "nope")
    with pytest.raises(NotFound):
        read_text(root, "nope.md")
    with pytest.raises(NotFound):
        read_picture(root, "art/nope.png")
    with pytest.raises(BadPath):
        list_folder(root, "data.json")


def test_a_sandbox_that_does_not_exist_yet_lists_empty(tmp_path):
    assert list_folder(tmp_path / "files" / "nobody", "") == {"path": "", "entries": [], "truncated": False}


def test_text_reads_whole_with_its_reader_mode(tmp_path):
    root = _sandbox(tmp_path)
    got = read_text(root, "notes/ideas.md")
    assert got == {"path": "notes/ideas.md", "text": "# Ідеї\n\n- дощ\n", "size": len("# Ідеї\n\n- дощ\n".encode()),
                   "cut": False, "type": "markdown"}
    assert read_text(root, "code.py")["type"] == "code"
    assert read_text(root, "data.json")["type"] == "data"
    assert read_text(root, "notes/list.txt")["type"] == "plain"


def test_text_is_cut_at_the_cap_on_a_character_boundary(tmp_path):
    root = _sandbox(tmp_path)
    head = b"a" * (browse.BROWSE_MAX_TEXT - 1)  # the cap falls inside the next two-byte «ї»
    (root / "big.md").write_bytes(head + "ї".encode() + b"tail" * 20000)
    got = read_text(root, "big.md")
    assert got["cut"] is True
    assert got["text"] == "a" * (browse.BROWSE_MAX_TEXT - 1)  # the half character is dropped, not mangled
    assert got["size"] == (root / "big.md").stat().st_size


def test_pictures_and_binaries_are_not_text(tmp_path):
    root = _sandbox(tmp_path)
    for rel in ("art/p.png", "art/really_png.txt", "blob.dat"):
        with pytest.raises(NotServed):
            read_text(root, rel)
    (root / "latin1.txt").write_bytes("caf\xe9".encode("latin-1"))
    with pytest.raises(NotServed):
        read_text(root, "latin1.txt")


def test_pictures_are_served_by_their_bytes_and_svg_or_html_never(tmp_path):
    root = _sandbox(tmp_path)
    assert read_picture(root, "art/p.png") == (PNG, "image/png")
    assert read_picture(root, "art/j.jpg")[1] == "image/jpeg"
    assert read_picture(root, "art/g.gif")[1] == "image/gif"
    assert read_picture(root, "art/w.webp")[1] == "image/webp"
    assert read_picture(root, "art/really_png.txt")[1] == "image/png"
    (root / "page.html").write_text("<html><script>x</script></html>", encoding="utf-8")
    for rel in ("art/x.svg", "art/fake.png", "page.html", "blob.dat"):
        with pytest.raises(NotServed):
            read_picture(root, rel)


def test_a_picture_over_the_cap_is_too_large(tmp_path, monkeypatch):
    root = _sandbox(tmp_path)
    monkeypatch.setattr(browse, "BROWSE_MAX_PICTURE", 16)
    with pytest.raises(TooLarge):
        read_picture(root, "art/p.png")


def test_a_folder_lists_at_most_the_cap_and_says_it_was_cut(tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for i in range(browse.BROWSE_MAX_ENTRIES + 1):
        (root / f"f{i:05d}.txt").write_text("x", encoding="utf-8")
    got = list_folder(root, "")
    assert got["truncated"] is True and len(got["entries"]) == browse.BROWSE_MAX_ENTRIES


def test_nothing_under_the_root_ever_changes(tmp_path):
    root = _sandbox(tmp_path)
    before = _fingerprint(root)
    list_folder(root, "")
    list_folder(root, "art")
    read_text(root, "notes/ideas.md")
    read_picture(root, "art/p.png")
    for bad in ("nope.md", "../outside.txt"):
        with pytest.raises((NotFound, BadPath)):
            read_text(root, bad)
    assert _fingerprint(root) == before


def test_files_root_is_the_users_sandbox_and_creates_nothing(tmp_path):
    files = tmp_path / "files"

    def core(**kw):
        return Core(llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                    repository=JsonRepository(tmp_path / "s.json"), canon="C", model="m", **kw)

    assert core(files_dir=files, user_id="alice").files_root == files / "alice"
    assert not files.exists()  # reading the property makes no folder
    assert core().files_root is None  # no sandbox configured
