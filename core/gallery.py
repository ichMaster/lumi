"""The window's gallery over one sandbox: every picture, newest first, and cached thumbnails (v4.2 LUMI-305).

The desktop window's Gallery view reads her per-user sandbox through two functions, beside :mod:`core.browse`:

- :func:`list_pictures` — every picture in the sandbox, newest first, paged by an opaque cursor, with the total and
  the per-folder counts (the view's header and folder filter);
- :func:`thumbnail` — a JPEG of one picture, longest side :data:`THUMB_SIZE`, made by Pillow on the first request and
  cached in a folder **outside** the sandbox (the server passes ``<data root>/cache/thumbs/<user_id>``).

The rules are :mod:`core.browse`'s:

- **Read-only over the sandbox** — nothing under the root is opened for writing, created or touched; the only write
  is the thumbnail into the cache folder, which must not be inside the root (her file tools never see it).
- **One sandbox guard** — a path goes through :func:`core.files.safe_path`; the walk never follows a symlinked folder
  and skips a file whose target leaves the root.
- **Typed by content** — a picture by its magic bytes (png / jpeg / gif / webp, never SVG), whatever its name.
- **Bounded** — the walk visits at most :data:`GALLERY_MAX_SCAN` files, a page holds at most
  :data:`GALLERY_MAX_LIMIT` pictures, a thumbnail's source is at most ``BROWSE_MAX_PICTURE`` bytes and
  :data:`THUMB_MAX_PIXELS` pixels (read from its header before any decode).

Errors are :mod:`core.browse`'s (:class:`BadPath`, :class:`NotFound`, :class:`NotServed`, :class:`TooLarge`), so the
server maps them the same way.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import logging
import os
import stat
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

from core import browse
from core.browse import (
    BadPath,
    NotFound,
    NotServed,
    TooLarge,
    _inside,
    _rel,
    _resolve,
    picture_type,
)
from core.files import _stat_dates

log = logging.getLogger("lumi.gallery")

GALLERY_MAX_SCAN = 10_000  # files the walk looks at (the rest → "truncated")
GALLERY_MAX_LIMIT = 100  # pictures a page holds
THUMB_SIZE = 320  # a thumbnail's longest side, px
THUMB_MAX_PIXELS = 50_000_000  # a source larger than this (from its header) is never decoded
THUMB_QUALITY = 82
THUMB_BACKGROUND = (236, 234, 230)  # a transparent picture is flattened onto this neutral colour


@dataclass(frozen=True)
class _Picture:
    path: str  # sandbox-relative, "/" separators
    folder: str  # its folder, "" for the root
    size: int
    mtime_ns: int
    modified: str  # the v0.29 "YYYY-MM-DD HH:MM"
    abs: Path

    @property
    def key(self) -> tuple[int, str]:
        """The sort key: newest first, then by path — equal times page stably."""
        return (-self.mtime_ns, self.path)


# --- the cursor ------------------------------------------------------------------------------------------------
def encode_cursor(mtime_ns: int, path: str) -> str:
    """The opaque cursor after the picture ``(mtime_ns, path)`` — URL-safe, no padding."""
    raw = f"{int(mtime_ns)}:{path}".encode("utf-8", "surrogateescape")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, str]:
    """``(mtime_ns, path)`` from :func:`encode_cursor`'s output; anything else is :class:`BadPath`."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8", "surrogateescape")
        stamp, path = raw.split(":", 1)
        if not stamp.isdigit():
            raise ValueError(stamp)
        return int(stamp), path
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise BadPath("a malformed cursor") from None


# --- the listing -----------------------------------------------------------------------------------------------
def _walk(base: Path) -> tuple[list[_Picture], bool]:
    """Every picture under ``base`` (typed by its bytes) and whether the scan cap cut the walk short."""
    found: list[_Picture] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(base):  # never descends into a symlinked folder
        dirnames.sort()
        folder = Path(dirpath)
        for name in sorted(filenames):
            if scanned >= GALLERY_MAX_SCAN:
                return found, True
            scanned += 1
            path = folder / name
            if path.is_symlink() and not _inside(base, path):
                continue  # a link out of the sandbox is neither listed nor read
            try:
                st = path.stat()
                if not stat.S_ISREG(st.st_mode):
                    continue
                with path.open("rb") as fh:
                    head = fh.read(12)
            except OSError:
                continue
            if picture_type(head) is None:
                continue
            rel = path.relative_to(base).as_posix()
            parent = Path(rel).parent.as_posix()
            found.append(_Picture(path=rel, folder="" if parent == "." else parent, size=st.st_size,
                                  mtime_ns=st.st_mtime_ns, modified=_stat_dates(st)[1], abs=path))
    return found, False


def _dimensions(path: Path) -> tuple[int | None, int | None]:
    """The picture's width and height from its header (no decode), or ``(None, None)``."""
    from PIL import Image

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as im:
                width, height = im.size
        return int(width), int(height)
    except Exception:  # noqa: BLE001 — an unreadable header (or a decompression bomb) has no dimensions
        return None, None


def list_pictures(root: str | Path, *, before: str | None = None, limit: int = GALLERY_MAX_LIMIT,
                  folder: str | None = None) -> dict:
    """One page of the sandbox's pictures, newest first.

    Returns ``{items:[{path, folder, size, modified, width, height}], next, total, folders:[{folder, count}],
    truncated}``. ``folder`` (``None`` = all) matches one folder exactly (``""`` is the root; ``art`` doesn't include
    ``art/sketches``). ``before`` is the previous page's ``next``; ``next`` is ``None`` on the last page. ``total``
    and ``folders`` count the whole sandbox, whatever the page or the filter. A sandbox that doesn't exist yet lists
    empty."""
    if not isinstance(limit, int) or not 1 <= limit <= GALLERY_MAX_LIMIT:
        raise BadPath(f"limit must be 1..{GALLERY_MAX_LIMIT}")
    after = decode_cursor(before) if before else None
    base = Path(root).resolve()
    pictures, truncated = _walk(base) if base.is_dir() else ([], False)
    counts: dict[str, int] = {}
    for pic in pictures:
        counts[pic.folder] = counts.get(pic.folder, 0) + 1
    chosen = pictures if folder is None else [p for p in pictures if p.folder == folder.strip("/")]
    chosen.sort(key=lambda p: p.key)
    if after is not None:
        mark = (-after[0], after[1])
        chosen = [p for p in chosen if p.key > mark]
    page, rest = chosen[:limit], chosen[limit:]
    items = []
    for pic in page:
        width, height = _dimensions(pic.abs)
        items.append({"path": pic.path, "folder": pic.folder, "size": pic.size, "modified": pic.modified,
                      "width": width, "height": height})
    return {
        "items": items,
        "next": encode_cursor(page[-1].mtime_ns, page[-1].path) if page and rest else None,
        "total": len(pictures),
        "folders": [{"folder": name, "count": counts[name]} for name in sorted(counts)],
        "truncated": truncated,
    }


# --- the thumbnail ---------------------------------------------------------------------------------------------
def _make_thumb(path: Path, shown: str) -> bytes:
    """A JPEG of ``path``, longest side :data:`THUMB_SIZE` (never enlarged): the first frame, the EXIF orientation
    applied, transparency flattened onto :data:`THUMB_BACKGROUND`."""
    from PIL import Image, ImageOps

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as im:
                width, height = im.size
                if width * height > THUMB_MAX_PIXELS:
                    raise TooLarge(f"the picture is too large to show as a thumbnail: {shown!r}")
                im.draft("RGB", (THUMB_SIZE, THUMB_SIZE))  # a JPEG decodes at a reduced scale; others ignore it
                pic = ImageOps.exif_transpose(im)  # a copy, upright, of the first frame
        pic.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
        if pic.mode in ("RGBA", "LA", "PA") or (pic.mode == "P" and "transparency" in pic.info):
            pic = pic.convert("RGBA")
            flat = Image.new("RGB", pic.size, THUMB_BACKGROUND)
            flat.paste(pic, mask=pic.getchannel("A"))
            pic = flat
        elif pic.mode != "RGB":
            pic = pic.convert("RGB")
        out = io.BytesIO()
        pic.save(out, "JPEG", quality=THUMB_QUALITY, optimize=True)
        return out.getvalue()
    except Image.DecompressionBombError:
        raise TooLarge(f"the picture is too large to show as a thumbnail: {shown!r}") from None
    except (OSError, SyntaxError, ValueError):  # truncated or broken data behind a picture's header
        raise NotServed(f"the picture can't be read: {shown!r}") from None


def _cache_folder(root: str | Path, cache_dir: str | Path) -> Path:
    base = Path(root).resolve()
    cache = Path(cache_dir).resolve()
    if cache == base or base in cache.parents:
        raise ValueError("the thumbnail cache must be outside the sandbox")
    return cache


def thumbnail(root: str | Path, rel: object, cache_dir: str | Path) -> bytes:
    """A JPEG thumbnail of the picture ``rel`` (longest side :data:`THUMB_SIZE`), cached in ``cache_dir``.

    The cache file is ``<sha256(path)[:32]>-<mtime_ns>.jpg``: a hit is returned without opening the source; a miss
    makes it, writes it atomically and drops the same path's older thumbnails. A cache that can't be written still
    returns the thumbnail. ``cache_dir`` inside ``root`` is a ``ValueError``."""
    cache = _cache_folder(root, cache_dir)
    base = Path(root).resolve()
    path = _resolve(root, rel)
    shown = _rel(base, path)
    if not path.is_file():
        raise NotFound(f"no such file: {shown!r}")
    st = path.stat()
    if st.st_size > browse.BROWSE_MAX_PICTURE:
        raise TooLarge(f"the picture is larger than {browse.BROWSE_MAX_PICTURE // (1024 * 1024)} MB: {shown!r}")
    key = hashlib.sha256(shown.encode("utf-8", "surrogateescape")).hexdigest()[:32]
    target = cache / f"{key}-{st.st_mtime_ns}.jpg"
    try:
        return target.read_bytes()
    except OSError:
        pass  # a miss (or an unreadable cache file): make it again
    with path.open("rb") as fh:
        if picture_type(fh.read(12)) is None:
            raise NotServed(f"not a picture this window shows: {shown!r}")
    data = _make_thumb(path, shown)
    _store(cache, key, target, data)
    return data


def _store(cache: Path, key: str, target: Path, data: bytes) -> None:
    """Write the thumbnail atomically and drop the path's older ones — best-effort: a failure is logged."""
    tmp = cache / f".{key}-{uuid.uuid4().hex}.tmp"
    try:
        cache.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, target)
        for old in cache.glob(f"{key}-*.jpg"):
            if old.name != target.name:
                old.unlink(missing_ok=True)
    except OSError:
        log.warning("the thumbnail cache can't be written", exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # the cache folder itself is unusable — nothing was left behind
