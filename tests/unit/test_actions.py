"""v4.5 LUMI-321 — the owner's actions on one sandbox (``core/actions.py``).

Pinned: an upload is create-only under a free name, typed by its bytes and capped; rename never lands on a taken name;
trash moves into ``.trash/<date>/`` and restore brings it back to where the log says it was; no action follows a link
out, moves a link, or deletes anything; the log holds no content; the rest of the sandbox never changes."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image

import core.actions as actions
from core.actions import ActionLog, Conflict, clean_name, free_name, rename, restore, trash, upload
from core.browse import BadPath, NotFound, NotServed, TooLarge


def _png(size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "files" / "owner"
    (root / "notes").mkdir(parents=True)
    (root / "art").mkdir()
    (root / "notes" / "ideas.md").write_text("# Ідеї\n", encoding="utf-8")
    (root / "art" / "rain.png").write_bytes(_png())
    return root


def _fingerprint(root: Path, skip: tuple[str, ...] = ()) -> list[tuple]:
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            if rel.startswith(skip):
                continue
            is_file = p.is_file() and not p.is_symlink()  # a folder's own size moves as its entries come and go
            digest = hashlib.sha256(p.read_bytes()).hexdigest() if is_file else ""
            rows.append((rel, p.lstat().st_size if is_file else 0, digest))
    return sorted(rows)


# --- names ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "  ", ".", "..", ".hidden", "a/b", "a\\b", "x\x00y", "x\ny", None, 5])
def test_a_name_that_cannot_be_one_segment_is_refused(bad):
    with pytest.raises(BadPath):
        clean_name(bad)


def test_names_are_trimmed_and_capped_keeping_the_extension():
    assert clean_name("  мій кіт.png ") == "мій кіт.png"
    long = clean_name("я" * 300 + ".png")
    assert len(long) == actions.NAME_MAX and long.endswith(".png")


def test_a_taken_name_gets_the_next_number(tmp_path):
    (tmp_path / "rain.png").write_bytes(b"x")
    (tmp_path / "rain (2).png").write_bytes(b"x")
    assert free_name(tmp_path, "rain.png") == "rain (3).png"
    assert free_name(tmp_path, "new.md") == "new.md"


# --- upload --------------------------------------------------------------------------------------------------------
def test_upload_writes_a_picture_and_a_text_file_create_only(tmp_path):
    root = _sandbox(tmp_path)
    before = _fingerprint(root)
    got = upload(root, "art", "rain.png", _png((16, 16)))  # the name is taken
    assert got == {"path": "art/rain (2).png", "size": len(_png((16, 16)))}
    assert (root / "art" / "rain.png").read_bytes() == _png()  # the old one untouched
    assert upload(root, "", "лист.txt", "Привіт".encode()) == {"path": "лист.txt", "size": len("Привіт".encode())}
    after = [row for row in _fingerprint(root) if row[0] not in ("art/rain (2).png", "лист.txt")]
    assert after == before


@pytest.mark.parametrize(("name", "data"), [
    ("x.svg", b"<svg><script>alert(1)</script></svg>"[:0] + b"\x00<svg/>"),  # not text (a NUL), not a picture
    ("x.bin", bytes(range(256))),
    ("x.txt", b"\xff\xfe\xfd not utf-8"),
])
def test_upload_refuses_anything_but_a_picture_or_text(tmp_path, name, data):
    root = _sandbox(tmp_path)
    with pytest.raises(NotServed):
        upload(root, "", name, data)


def test_upload_caps_and_folders(tmp_path, monkeypatch):
    root = _sandbox(tmp_path)
    monkeypatch.setattr(actions, "UPLOAD_MAX_TEXT", 8)
    with pytest.raises(TooLarge):
        upload(root, "", "big.txt", b"123456789")
    monkeypatch.setattr(actions, "UPLOAD_MAX_PICTURE", 16)
    with pytest.raises(TooLarge):
        upload(root, "", "big.png", _png())
    with pytest.raises(NotFound):
        upload(root, "nope", "a.txt", b"a")
    with pytest.raises(BadPath):
        upload(root, "../out", "a.txt", b"a")
    with pytest.raises(BadPath):
        upload(root, "", "../a.txt", b"a")
    assert not (tmp_path / "files" / "a.txt").exists()


def test_upload_into_a_fresh_sandbox_makes_only_its_root(tmp_path):
    root = tmp_path / "files" / "owner"
    assert upload(root, "", "a.md", b"# a\n")["path"] == "a.md"
    assert sorted(p.name for p in (tmp_path / "files").iterdir()) == ["owner"]


# --- rename --------------------------------------------------------------------------------------------------------
def test_rename_within_its_folder_never_over_a_taken_name(tmp_path):
    root = _sandbox(tmp_path)
    assert rename(root, "notes/ideas.md", "думки.md") == {"path": "notes/ideas.md", "to": "notes/думки.md"}
    assert (root / "notes" / "думки.md").read_text(encoding="utf-8") == "# Ідеї\n"
    (root / "notes" / "b.md").write_text("b", encoding="utf-8")
    with pytest.raises(Conflict):
        rename(root, "notes/b.md", "думки.md")
    assert rename(root, "art", "pictures")["to"] == "pictures"  # a folder too


@pytest.mark.parametrize(("path", "name"), [("notes/b.md", "../x.md"), ("notes/b.md", "a/b.md"),
                                            ("notes/b.md", ".x"), ("", "x"), ("..", "x"), ("../x", "y")])
def test_rename_refuses_what_leaves_its_folder_or_the_sandbox(tmp_path, path, name):
    root = _sandbox(tmp_path)
    (root / "notes" / "b.md").write_text("b", encoding="utf-8")
    with pytest.raises(BadPath):
        rename(root, path, name)


# --- trash and restore ---------------------------------------------------------------------------------------------
def test_trash_and_restore_move_and_never_delete(tmp_path):
    root = _sandbox(tmp_path)
    before = _fingerprint(root)
    moved = trash(root, "notes/ideas.md", "2026-11-19")
    assert moved == {"path": "notes/ideas.md", "to": ".trash/2026-11-19/ideas.md"}
    assert not (root / "notes" / "ideas.md").exists() and (root / moved["to"]).read_text(encoding="utf-8") == "# Ідеї\n"
    (root / "notes" / "ideas.md").write_text("new", encoding="utf-8")
    second = trash(root, "notes/ideas.md", "2026-11-19")  # a second one the same day: a free name
    assert second["to"] == ".trash/2026-11-19/ideas (2).md"
    back = restore(root, ".trash/2026-11-19/ideas.md", "notes/ideas.md")
    assert back == {"path": ".trash/2026-11-19/ideas.md", "to": "notes/ideas.md"}
    assert (root / "notes" / "ideas.md").read_text(encoding="utf-8") == "# Ідеї\n"
    assert _fingerprint(root, skip=(".trash",)) == before


def test_trash_refuses_the_trash_and_restore_refuses_a_taken_place(tmp_path):
    root = _sandbox(tmp_path)
    trash(root, "notes/ideas.md", "2026-11-19")
    with pytest.raises(Conflict):
        trash(root, ".trash/2026-11-19/ideas.md", "2026-11-19")
    with pytest.raises(Conflict):
        trash(root, ".trash", "2026-11-19")
    (root / "notes" / "ideas.md").write_text("taken", encoding="utf-8")
    with pytest.raises(Conflict):
        restore(root, ".trash/2026-11-19/ideas.md", "notes/ideas.md")
    with pytest.raises(Conflict):
        restore(root, ".trash/2026-11-19/ideas.md", "gone/ideas.md")  # its folder is gone
    with pytest.raises(BadPath):
        restore(root, "notes/ideas.md", "notes/x.md")  # not in the trash
    with pytest.raises(BadPath):
        restore(root, ".trash/2026-11-19/ideas.md", ".trash/x.md")
    with pytest.raises(Conflict):
        restore(root, ".trash/2026-11-19/ideas.md", None)


def test_no_action_moves_a_link_or_follows_one_out(tmp_path):
    root = _sandbox(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("not hers", encoding="utf-8")
    (root / "out.txt").symlink_to(outside)
    (root / "inner.png").symlink_to(root / "art" / "rain.png")
    for call in (lambda: rename(root, "out.txt", "x.txt"), lambda: trash(root, "out.txt", "2026-11-19"),
                 lambda: rename(root, "inner.png", "x.png")):
        with pytest.raises(BadPath):
            call()
    (tmp_path / "elsewhere").mkdir()
    (root / "away").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(BadPath):
        upload(root, "away", "a.txt", b"a")
    assert outside.read_text(encoding="utf-8") == "not hers" and not list((tmp_path / "elsewhere").iterdir())


# --- the log -------------------------------------------------------------------------------------------------------
def test_the_log_has_one_line_per_action_and_no_content(tmp_path):
    log = ActionLog(tmp_path / "data" / "actions.jsonl")
    log.append({"ts": "2026-11-19T10:00:00+02:00", "action": "upload", "path": "a.txt", "size": 5, "client": "w1",
                "data": "the file's text"})
    log.append({"action": "trash", "path": "notes/ideas.md", "to": ".trash/2026-11-19/ideas.md"})
    lines = [json.loads(line) for line in (tmp_path / "data" / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2 and "data" not in lines[0] and lines[0]["client"] == "w1"
    assert set(lines[1]) == {"ts", "action", "path", "to"}
    assert log.original_of(".trash/2026-11-19/ideas.md") == "notes/ideas.md"
    assert log.original_of(".trash/2026-11-19/other.md") is None
    assert ActionLog(tmp_path / "none.jsonl").original_of("x") is None
