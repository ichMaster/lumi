"""Image generation — the ``ImageGen`` seam (text → PNG bytes) + the ``generate_image`` tool (v0.23).

The **write half** of the image tool: ``generate_image(prompt[, filename])`` calls an injected
**``ImageGen``** (text → PNG bytes) and saves a **new** file under the per-user sandbox (**create-only**,
like ``create_file`` — no overwrite, no delete). The default backend is the **Gemini Nano Banana** caller
(``gemini-2.5-flash-image``, ``GEMINI_API_KEY``, stdlib ``urllib`` — the same call proven in the
``/generate-faces`` skill). **No SDK in core** — the generator is a plain callable, **injected** in tests
(a stub returning canned PNG bytes → no paid image calls in CI).

Hard rules (IMAGE_TOOL.md §safety): **non-destructive** (create-only); **sandboxed + per-user** (the
shared v0.19 ``safe_path`` guard); the prompt carries **no personal data** (the wiring passes only the
model's prompt); **never raises** — any failure (refusal, HTTP error, existing path, traversal) degrades
to an **error string**. Off by default + per-turn-capped by the reply-turn wiring (``Core``).
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from core.files import safe_path

# An ImageGen turns a prompt into PNG bytes. The seam the core depends on — never an SDK.
ImageGen = Callable[..., bytes]

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Anthropic-style function-calling schema for the one v0.23 generation tool.
GENERATE_TOOLS: list[dict] = [
    {
        "name": "generate_image",
        "description": (
            "Згенерувати НОВЕ зображення (PNG) за текстовим описом і зберегти у пісочниці Лілі. "
            "Опис — лише з творчого наміру, без особистих даних людини. Не перезаписує наявні файли."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Опис того, що намалювати (англійською або українською)."},
                "filename": {"type": "string", "description": "Необовʼязкова назва файлу (.png) у теці art/."},
            },
            "required": ["prompt"],
        },
    },
]

GENERATE_TOOL_NAMES = frozenset(t["name"] for t in GENERATE_TOOLS)


class ImageGenError(RuntimeError):
    """An image generation failed (no key, HTTP error, safety refusal, no image returned)."""


def _extract_image(data: dict) -> bytes:
    """Pull the PNG bytes out of a Gemini ``generateContent`` response (or raise with the reason)."""
    cands = data.get("candidates") or []
    if not cands:
        raise ImageGenError(f"no candidates (safety block?): {json.dumps(data)[:200]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    for p in parts:
        inl = p.get("inline_data") or p.get("inlineData")  # request snake, response camel
        if inl and inl.get("data"):
            return base64.b64decode(inl["data"])
    said = " ".join(p.get("text", "") for p in parts)[:200]
    raise ImageGenError(f"no image returned; the model said: {said!r}")


def gemini_image_gen(*, model: str = "gemini-2.5-flash-image", key: str | None = None,
                     timeout: float = 180.0) -> ImageGen:
    """The default ``ImageGen`` — text → PNG via Gemini (Nano Banana). Reads ``GEMINI_API_KEY`` lazily.

    A plain callable ``generate(prompt, *, size=...) -> bytes`` — no SDK. Raises :class:`ImageGenError`
    on a missing key / HTTP error / safety refusal (caught by the tool, degraded to an error string).
    """
    endpoint = _GEMINI_ENDPOINT.format(model=model)

    def generate(prompt: str, *, size: int = 768) -> bytes:  # noqa: ARG001 — native size; size reserved
        import os

        api_key = key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ImageGenError("GEMINI_API_KEY is not set — image generation needs a Gemini key.")
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }).encode()
        req = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed Gemini host
                return _extract_image(json.loads(resp.read()))
        except urllib.error.HTTPError as exc:
            raise ImageGenError(f"Gemini HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:200]}") from exc
        except urllib.error.URLError as exc:
            raise ImageGenError(f"Gemini unreachable: {exc.reason}") from exc

    return generate


def _slug(prompt: str, *, limit: int = 40) -> str:
    """A filesystem-safe slug from the prompt for a default filename (deterministic)."""
    s = re.sub(r"[^\w-]+", "-", prompt.strip().lower(), flags=re.UNICODE).strip("-")
    return (s[:limit].rstrip("-")) or "image"


class ImageMaker:
    """Runs ``generate_image`` against one sandbox ``root`` via an injected ``ImageGen``.

    ``execute(name, input)`` saves a **new** PNG (create-only) and returns a string notice, or an
    **error string** on any failure — never raises (a generation error degrades the reply, never the turn).
    """

    def __init__(self, root: str | Path, *, image_gen: ImageGen, size: int = 768, subdir: str = "art") -> None:
        self._root = Path(root)
        self._gen = image_gen
        self._size = size
        self._subdir = subdir.strip("/") or "art"

    def execute(self, name: str, tool_input: dict | None) -> str:
        inp = tool_input or {}
        try:
            if name == "generate_image":
                return self._generate(inp)
            return f"error: unknown image tool {name!r}"
        except Exception as exc:  # noqa: BLE001 — never raise; degrade to an error string
            return f"error: {exc}"

    def _generate(self, inp: dict) -> str:
        prompt = inp.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return "error: missing 'prompt'"
        name = inp.get("filename")
        filename = name.strip() if isinstance(name, str) and name.strip() else f"{_slug(prompt)}.png"
        if not filename.lower().endswith(".png"):
            filename += ".png"
        rel = f"{self._subdir}/{filename}"
        f = safe_path(self._root, rel)  # rejects ../absolute/symlink before any write
        if f.exists():
            return f"error: file already exists (no overwrite): {rel!r}"
        try:
            data = self._gen(prompt, size=self._size)  # ImageGen — may raise ImageGenError
        except ImageGenError as exc:
            return f"error: image generation failed: {exc}"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        return f"created {rel} ({len(data)} bytes)"
