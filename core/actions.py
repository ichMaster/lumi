"""The owner's actions on one sandbox (v4.5 LUMI-321) — the write side beside :mod:`core.browse`'s read side.

The desktop window lets the owner bring a file in (:func:`upload`), rename an entry (:func:`rename`), move it to the
trash (:func:`trash`) and bring it back (:func:`restore`). Her own file tools stay as they are (create and append only);
these are the owner's actions, each one logged (:class:`ActionLog`).

- **One guard** — every path through :func:`core.files.safe_path` (no ``..``, no absolute path, no link out); an entry
  that is itself a link is never moved.
- **Never an overwrite** — an upload writes a new file (``"xb"``) under a free name; a rename never lands on a taken
  name; a move into the trash takes a free name there; a restore needs its old place free.
- **Never a deletion** — the trash is ``.trash/<YYYY-MM-DD>/`` inside the sandbox, an ordinary folder.
- **Bounded** — pictures (by magic bytes) up to :data:`UPLOAD_MAX_PICTURE`, UTF-8 text up to :data:`UPLOAD_MAX_TEXT`;
  names at most :data:`NAME_MAX` characters.

Errors are :mod:`core.browse`'s (``BadPath`` 400, ``NotFound`` 404, ``TooLarge`` 413, ``NotServed`` 415) plus
:class:`Conflict` (409); their messages name sandbox paths only.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path, PurePosixPath

from core.browse import (
    BROWSE_MAX_PICTURE,
    BadPath,
    BrowseError,
    NotFound,
    NotServed,
    TooLarge,
    _rel,
    picture_type,
)
from core.files import _Denied, safe_path

UPLOAD_MAX_PICTURE = BROWSE_MAX_PICTURE  # 10 MB — what the window may load back
UPLOAD_MAX_TEXT = 1024 * 1024  # 1 MB of UTF-8 text
NAME_MAX = 120  # characters of a file or folder name
TRASH = ".trash"  # the trash folder at the sandbox's root
LOG_FIELDS = ("ts", "action", "path", "to", "size", "client")  # what a log line may hold — never any content


class Conflict(BrowseError):
    """The target name or place is taken, or the item can't go there (→ 409)."""


def clean_name(name: object) -> str:
    """A file or folder name the owner gave, made safe: one path segment, no leading dot, no control characters, at
    most :data:`NAME_MAX` characters (the extension kept). Anything that can't be made one is :class:`BadPath`."""
    if not isinstance(name, str):
        raise BadPath("a name is required")
    clean = name.strip()
    if not clean or clean in (".", "..") or clean.startswith("."):
        raise BadPath("a name can't be empty or start with a dot")
    if "/" in clean or "\\" in clean or any(ord(ch) < 32 or ord(ch) == 127 for ch in clean):
        raise BadPath("a name is one segment, without slashes or control characters")
    if len(clean) > NAME_MAX:
        stem, ext = os.path.splitext(clean)
        ext = ext if len(ext) <= 16 else ""
        clean = stem[: NAME_MAX - len(ext)].rstrip() + ext
    return clean


def free_name(folder: Path, name: str) -> str:
    """``name`` if nothing in ``folder`` has it, else ``stem (2).ext``, ``stem (3).ext`` …"""
    def taken(candidate: str) -> bool:
        path = folder / candidate
        return path.exists() or path.is_symlink()

    if not taken(name):
        return name
    stem, ext = os.path.splitext(name)
    for n in range(2, 10_000):
        candidate = f"{stem} ({n}){ext}"
        if not taken(candidate):
            return candidate
    raise Conflict(f"no free name for {name!r}")


def _base(root: str | Path) -> Path:
    return Path(root).resolve()


def _entry(root: str | Path, rel: object) -> tuple[Path, Path]:
    """``(base, the entry itself)`` for a sandbox path — through the guard, but never a link: an action moves the
    entry it names, so a link (even one inside the sandbox) is refused rather than followed."""
    base = _base(root)
    if not isinstance(rel, str) or not rel.strip() or rel.strip() in (".", "/"):
        raise BadPath("a path inside the sandbox is required")
    try:
        safe_path(root, rel)
    except _Denied as exc:
        raise BadPath(str(exc)) from None
    except ValueError:  # an embedded NUL byte
        raise BadPath("a malformed path") from None
    entry = base / PurePosixPath(rel.strip().strip("/"))
    if entry.is_symlink():
        raise BadPath(f"a link can't be moved: {rel.strip()!r}")
    if not entry.exists():
        raise NotFound(f"no such file or folder: {rel.strip()!r}")
    if entry == base:
        raise BadPath("the sandbox itself can't be moved")
    return base, entry


def _in_trash(base: Path, path: Path) -> bool:
    try:
        return path.relative_to(base).parts[:1] == (TRASH,)
    except ValueError:
        return False


def upload(root: str | Path, folder: object, name: object, data: bytes) -> dict:
    """Write ``data`` as a new file named ``name`` in ``folder`` (``""`` the root): ``{path, size}``.

    A picture by its magic bytes up to :data:`UPLOAD_MAX_PICTURE`, NUL-free UTF-8 text up to :data:`UPLOAD_MAX_TEXT`,
    anything else :class:`NotServed`. Create-only under :func:`free_name`: a taken name gets ``(2)``, nothing is ever
    overwritten, and nothing else in the sandbox changes."""
    base = _base(root)
    rel = folder.strip().strip("/") if isinstance(folder, str) else ""
    if rel:
        try:
            dest = safe_path(root, rel)
        except _Denied as exc:
            raise BadPath(str(exc)) from None
        except ValueError:
            raise BadPath("a malformed path") from None
        if not dest.is_dir():
            raise NotFound(f"no such folder: {rel!r}")
    else:
        dest = base
        dest.mkdir(parents=True, exist_ok=True)  # a fresh sandbox: its root, nothing else
    clean = clean_name(name)
    size = len(data)
    if picture_type(data[:12]):
        cap = UPLOAD_MAX_PICTURE
    elif b"\x00" not in data and _utf8(data):
        cap = UPLOAD_MAX_TEXT
    else:
        raise NotServed(f"not a picture or a text file: {clean!r}")
    if size > cap:
        raise TooLarge(f"{clean!r} is larger than {cap // (1024 * 1024)} MB")
    for _ in range(3):  # a name taken between the look and the write gets the next free one
        target = dest / free_name(dest, clean)
        try:
            with target.open("xb") as fh:
                fh.write(data)
        except FileExistsError:
            continue
        return {"path": _rel(base, target), "size": size}
    raise Conflict(f"no free name for {clean!r}")


def _utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def rename(root: str | Path, path: object, name: object) -> dict:
    """Rename a file or a folder within its folder: ``{path, to}``. The new name is cleaned and must be free
    (:class:`Conflict` otherwise); the root, a link and anything out of the sandbox are refused."""
    base, entry = _entry(root, path)
    clean = clean_name(name)
    target = entry.parent / clean
    if target.exists() or target.is_symlink():
        raise Conflict(f"{_rel(base, target)!r} already exists")
    entry.rename(target)
    return {"path": _rel(base, entry), "to": _rel(base, target)}


def trash(root: str | Path, path: object, today: str) -> dict:
    """Move a file or a folder into ``.trash/<today>/`` under a free name: ``{path, to}``. Nothing is deleted; the
    trash itself and anything already in it are refused."""
    base, entry = _entry(root, path)
    if _in_trash(base, entry):
        raise Conflict("already in the trash")
    day = base / TRASH / today
    day.mkdir(parents=True, exist_ok=True)
    target = day / free_name(day, entry.name)
    entry.rename(target)
    return {"path": _rel(base, entry), "to": _rel(base, target)}


def restore(root: str | Path, path: object, original: object) -> dict:
    """Move an item from ``.trash/`` back to ``original``: ``{path, to}``. ``original`` must be free, inside the
    sandbox and out of the trash, and its folder must still exist (:class:`Conflict` otherwise)."""
    base, entry = _entry(root, path)
    if not _in_trash(base, entry) or len(entry.relative_to(base).parts) < 3:
        raise BadPath("only an item in the trash can be restored")
    if not isinstance(original, str) or not original.strip():
        raise Conflict("where it came from is unknown")
    try:
        safe_path(root, original)
    except _Denied as exc:
        raise BadPath(str(exc)) from None
    target = base / PurePosixPath(original.strip().strip("/"))
    if target == base or _in_trash(base, target):
        raise BadPath("an item can't be restored into the trash or onto the sandbox")
    if target.exists() or target.is_symlink():
        raise Conflict(f"{_rel(base, target)!r} is taken")
    if not target.parent.is_dir():
        raise Conflict(f"its folder is gone: {_rel(base, target.parent)!r}")
    entry.rename(target)
    return {"path": _rel(base, entry), "to": _rel(base, target)}


class ActionLog:
    """The owner's actions, one JSON line each, in a file outside the sandbox (``<data root>/actions.jsonl``).

    A line holds :data:`LOG_FIELDS` only — what happened to which path, never any content."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, entry: dict) -> None:
        line = {k: entry[k] for k in LOG_FIELDS if entry.get(k) is not None}
        line.setdefault("ts", datetime.now().astimezone().isoformat(timespec="seconds"))
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def original_of(self, trashed: str) -> str | None:
        """Where a trashed item came from: the ``path`` of the latest ``trash`` line whose ``to`` is ``trashed``."""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for raw in reversed(lines):
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("action") == "trash" and rec.get("to") == trashed:
                path = rec.get("path")
                return path if isinstance(path, str) else None
        return None
