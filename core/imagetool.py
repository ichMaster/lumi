"""Local image tool — `view_image` (v0.22): load a sandbox image into the model's view.

`view_image(path)` resolves an image under the per-user sandbox (the shared v0.19 `safe_path` guard),
size-checks it, and returns a **provider-neutral image block** (`core.images.image_block`) — so the
bounded tool-loop hands it back as an **image `tool_result`** and the model can describe it. Like the
file/wiki executors, ``execute`` **never raises**: any failure (traversal, missing, non-image,
oversize) degrades to an **error string**. The image is **untrusted data** — the framing is the loop's.

The tool *name* is ``view_image`` (Anthropic-safe). Off by default; gated + per-turn-capped by the
reply-turn wiring (`Core._image_tool_args`). Generation (`generate_image`) is v0.23.
"""
from __future__ import annotations

from pathlib import Path

from core.files import _Denied, safe_path
from core.images import image_block, media_type_for

# Anthropic-style function-calling schema for the one v0.22 vision tool.
VIEW_TOOLS: list[dict] = [
    {
        "name": "view_image",
        "description": (
            "Подивитися на зображення з пісочниці Лілі (png/jpg/gif/webp) і потім описати/обговорити те, "
            "що на ньому. Повертає саме зображення у поле зору."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Файл-зображення відносно кореня пісочниці."},
            },
            "required": ["path"],
        },
    },
]

VIEW_TOOL_NAMES = frozenset(t["name"] for t in VIEW_TOOLS)


class ImageTools:
    """Runs ``view_image`` against one sandbox ``root``. ``execute(name, input)`` returns an **image
    block** (a dict the loop turns into an image tool_result) or an **error string** — never raises."""

    def __init__(self, root: str | Path, *, max_bytes: int = 5_242_880) -> None:
        self._root = Path(root)
        self._max_bytes = max(1, max_bytes)

    def execute(self, name: str, tool_input: dict | None) -> str | dict:
        inp = tool_input or {}
        try:
            if name == "view_image":
                return self._view(inp)
            return f"error: unknown image tool {name!r}"
        except _Denied as exc:
            return f"error: {exc}"
        except OSError as exc:
            return f"error: {exc.strerror or exc}".strip()
        except Exception as exc:  # noqa: BLE001 — never raise; degrade to an error string
            return f"error: {exc}"

    def _view(self, inp: dict) -> str | dict:
        rel = inp.get("path")
        media_type = media_type_for(rel) if isinstance(rel, str) else None
        if media_type is None:
            return f"error: not an image (png/jpg/gif/webp): {rel!r}"
        f = self._resolve(rel)
        if f is None:
            return f"error: image not found: {rel!r}"
        size = f.stat().st_size
        if size > self._max_bytes:
            return f"error: image too large: {size} bytes > {self._max_bytes} cap"
        return image_block(f, media_type=media_type)  # the neutral image block (dict) → an image tool_result

    def _resolve(self, rel: str) -> Path | None:
        """Resolve an image path within the sandbox. Tries the path as-given; for a **bare name** (no
        folder) also looks in ``art/`` (where ``generate_image`` saves) and then **anywhere** in the
        sandbox — so ``view_image("foo.png")`` finds ``art/foo.png``. ``safe_path`` guards traversal; an
        explicit folder path that doesn't exist is **not** guessed. Returns the file or ``None``."""
        f = safe_path(self._root, rel)  # rejects ../absolute/symlink-out before any I/O
        if f.is_file():
            return f
        if "/" in rel or "\\" in rel:  # an explicit path that's missing — don't guess elsewhere
            return None
        art = safe_path(self._root, f"art/{rel}")  # the generate_image convention
        if art.is_file():
            return art
        matches = sorted(  # last resort: the same basename anywhere in the sandbox (shallowest first)
            (p for p in self._root.rglob("*") if p.is_file() and p.name == rel),
            key=lambda p: (len(p.parts), str(p)),
        )
        return matches[0] if matches else None
