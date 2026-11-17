"""The window's read-only view of one sandbox (v4.1 LUMI-301).

The desktop window's Files view and markdown reader read her per-user sandbox through three functions:
:func:`list_folder`, :func:`read_text` and :func:`read_picture`. They are independent of the server (which only maps
them to HTTP) and of her file tools (which run inside a turn and may write):

- **Read-only by construction** — nothing here opens a file for writing, creates a folder or touches a timestamp.
- **One sandbox guard** — every path goes through :func:`core.files.safe_path` (no ``..``, no absolute path, no
  symlink out of the root); a listed entry whose target leaves the root is skipped.
- **Bounded** — at most :data:`BROWSE_MAX_ENTRIES` entries a folder, :data:`BROWSE_MAX_TEXT` bytes of text (cut on
  a character boundary), :data:`BROWSE_MAX_PICTURE` bytes of picture.
- **Typed by content** — a picture by its magic bytes (png / jpeg / gif / webp), text by a NUL-free UTF-8 head; the
  extension only picks the reader's mode (markdown / code / data / plain). SVG and HTML are never served as pictures.

Errors are typed (:class:`BadPath`, :class:`NotFound`, :class:`NotServed`, :class:`TooLarge`) so the server maps them
to 400 / 404 / 415 / 413; their messages name the sandbox-relative path only, never an absolute one.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.files import _Denied, _stat_dates, safe_path

BROWSE_MAX_ENTRIES = 1000  # entries a folder listing returns (the rest → "truncated")
BROWSE_MAX_TEXT = 256 * 1024  # bytes of a text file the reader gets (the rest → "cut")
BROWSE_MAX_PICTURE = 10 * 1024 * 1024  # bytes of a picture the window may load
_HEAD = 4096  # bytes read to tell text from binary

MARKDOWN_EXT = frozenset({".md", ".markdown"})
CODE_EXT = frozenset({".py", ".js", ".ts", ".sh", ".bash", ".zsh", ".css", ".html", ".htm", ".sql", ".c", ".h",
                      ".cpp", ".java", ".go", ".rs", ".rb", ".swift", ".kt", ".xml"})
DATA_EXT = frozenset({".json", ".jsonl", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".ini"})


class BrowseError(Exception):
    """A refusal the window can show as one readable line."""


class BadPath(BrowseError):
    """The path is malformed or leaves the sandbox (→ 400)."""


class NotFound(BrowseError):
    """No such file or folder (→ 404)."""


class NotServed(BrowseError):
    """The file exists but is not the kind this read serves (→ 415)."""


class TooLarge(BrowseError):
    """The file is larger than this read serves (→ 413)."""


def picture_type(head: bytes) -> str | None:
    """The picture's media type from its first bytes, or ``None`` — png / jpeg / gif / webp only (never SVG)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _utf8_prefix(data: bytes, *, partial: bool) -> str | None:
    """``data`` as UTF-8 text, or ``None`` when it isn't. With ``partial`` (the bytes are a cut prefix of a longer
    file) an incomplete last character is dropped instead of refusing the text."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if partial and exc.reason == "unexpected end of data" and exc.start >= len(data) - 3:
            try:
                return data[: exc.start].decode("utf-8")
            except UnicodeDecodeError:
                return None
        return None


def _looks_like_text(head: bytes, *, partial: bool) -> bool:
    return b"\x00" not in head and _utf8_prefix(head, partial=partial) is not None


def text_type(name: str) -> str:
    """The reader's mode for a text file, by its extension: ``markdown`` / ``code`` / ``data`` / ``plain``."""
    ext = Path(name).suffix.lower()
    if ext in MARKDOWN_EXT:
        return "markdown"
    if ext in CODE_EXT:
        return "code"
    if ext in DATA_EXT:
        return "data"
    return "plain"


def _resolve(root: str | Path, rel: object) -> Path:
    """``rel`` under ``root`` through the one sandbox guard; an empty path (or ``.``) is the root itself."""
    if rel is None or (isinstance(rel, str) and rel.strip() in ("", ".")):
        return Path(root).resolve()
    try:
        return safe_path(root, rel)
    except _Denied as exc:
        raise BadPath(str(exc)) from None


def _rel(base: Path, target: Path) -> str:
    """The sandbox-relative path with ``/`` separators (``""`` for the root)."""
    return "" if target == base else target.relative_to(base).as_posix()


def _inside(base: Path, child: Path) -> bool:
    try:
        target = child.resolve()
    except OSError:
        return False
    return target == base or base in target.parents


def _file_type(path: Path, size: int) -> str:
    try:
        with path.open("rb") as fh:
            head = fh.read(_HEAD)
    except OSError:
        return "other"
    if picture_type(head[:12]):
        return "image"
    return "text" if _looks_like_text(head, partial=size > len(head)) else "other"


def _count(path: Path) -> int | None:
    try:
        with os.scandir(path) as it:
            return sum(1 for _ in it)
    except OSError:
        return None


def list_folder(root: str | Path, rel: object = "") -> dict:
    """One folder of the sandbox: ``{path, entries, truncated}``, folders first, then by name.

    Each entry is ``{name, kind: "dir"|"file", type: "text"|"image"|"other"|None, size, count, created, modified}``
    — ``type``/``size`` are ``None`` for a folder, ``count`` (its number of entries) is ``None`` for a file; the dates
    are the v0.29 ones. At most :data:`BROWSE_MAX_ENTRIES` entries. A sandbox that doesn't exist yet lists empty."""
    base = Path(root).resolve()
    folder = _resolve(root, rel)
    shown = _rel(base, folder)
    if not folder.exists():
        if folder == base:
            return {"path": "", "entries": [], "truncated": False}
        raise NotFound(f"no such folder: {shown!r}")
    if not folder.is_dir():
        raise BadPath(f"not a folder: {shown!r}")
    children = [c for c in folder.iterdir() if _inside(base, c)]
    children.sort(key=lambda c: (not c.is_dir(), c.name.casefold(), c.name))
    truncated = len(children) > BROWSE_MAX_ENTRIES
    entries: list[dict] = []
    for child in children[:BROWSE_MAX_ENTRIES]:
        try:
            st = child.stat()
        except OSError:
            continue
        created, modified = _stat_dates(st)
        if child.is_dir():
            entries.append({"name": child.name, "kind": "dir", "type": None, "size": None,
                            "count": _count(child), "created": created, "modified": modified})
        else:
            entries.append({"name": child.name, "kind": "file", "type": _file_type(child, st.st_size),
                            "size": st.st_size, "count": None, "created": created, "modified": modified})
    return {"path": shown, "entries": entries, "truncated": truncated}


def read_text(root: str | Path, rel: object) -> dict:
    """A text file for the reader: ``{path, text, size, cut, type}``.

    ``text`` is at most :data:`BROWSE_MAX_TEXT` bytes, cut on a character boundary; ``size`` is the whole file and
    ``cut`` says it was cut; ``type`` is the reader's mode (:func:`text_type`). A picture or a binary file is
    :class:`NotServed`."""
    base = Path(root).resolve()
    path = _resolve(root, rel)
    shown = _rel(base, path)
    if not path.is_file():
        raise NotFound(f"no such file: {shown!r}")
    size = path.stat().st_size
    with path.open("rb") as fh:
        data = fh.read(BROWSE_MAX_TEXT)
    cut = size > len(data)
    if picture_type(data[:12]) or b"\x00" in data:
        raise NotServed(f"not a text file: {shown!r}")
    text = _utf8_prefix(data, partial=cut)
    if text is None:
        raise NotServed(f"not a text file: {shown!r}")
    return {"path": shown, "text": text, "size": size, "cut": cut, "type": text_type(path.name)}


def read_picture(root: str | Path, rel: object) -> tuple[bytes, str]:
    """A picture's bytes and media type (by its magic bytes) — png / jpeg / gif / webp, at most
    :data:`BROWSE_MAX_PICTURE`. Anything else (SVG and HTML included) is :class:`NotServed`."""
    base = Path(root).resolve()
    path = _resolve(root, rel)
    shown = _rel(base, path)
    if not path.is_file():
        raise NotFound(f"no such file: {shown!r}")
    size = path.stat().st_size
    with path.open("rb") as fh:
        head = fh.read(12)
        media_type = picture_type(head)
        if media_type is None:
            raise NotServed(f"not a picture this window shows: {shown!r}")
        if size > BROWSE_MAX_PICTURE:
            raise TooLarge(f"the picture is larger than {BROWSE_MAX_PICTURE // (1024 * 1024)} MB: {shown!r}")
        data = head + fh.read()
    return data, media_type
