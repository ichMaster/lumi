"""Image content blocks — the provider-neutral multimodal seam (v0.22).

The core hands the model **image content blocks** the same way it hands text — a plain dict, never an
SDK type (ARCHITECTURE §Configuration and secrets). ``image_block`` builds one from a path or raw bytes
(base64 + media type); the ``LLMClient`` adapter (``AnthropicClient``) translates it to the provider's
multimodal format. Vision rides this on the v0.19 tool-loop: a **shared image** (a block on the user
message, v0.22 TUI) and the **`view_image`** tool (an image ``tool_result`` block) both produce neutral
blocks here. **Untrusted** — an image is data the model reads, never instructions (the framing is the
caller's).
"""
from __future__ import annotations

import base64
from collections.abc import Iterable
from pathlib import Path

# File-extension → media type (the formats Anthropic vision accepts).
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def media_type_for(path: str | Path) -> str | None:
    """The image media type for a path's extension, or ``None`` if it isn't a known image type."""
    return MEDIA_TYPES.get(Path(path).suffix.lower())


def image_block(source: str | Path | bytes, media_type: str | None = None) -> dict:
    """Build a **provider-neutral** image content block from a path/bytes → ``{type, media_type, data}``.

    ``data`` is base64. For a path, the media type is inferred from the extension unless given. No SDK —
    the adapter translates this to the provider's multimodal shape.
    """
    if isinstance(source, (str, Path)):
        data = Path(source).read_bytes()
        media_type = media_type or media_type_for(source)
    else:
        data = bytes(source)
    return {
        "type": "image",
        "media_type": media_type or "image/png",
        "data": base64.b64encode(data).decode("ascii"),
    }


def is_image_block(block: object) -> bool:
    """True if ``block`` is an image content block (neutral or already provider-translated)."""
    return isinstance(block, dict) and block.get("type") == "image"


def images_in_messages(messages: Iterable[dict]) -> list[dict]:
    """Every image block carried by ``messages`` — in a message's content list or a ``tool_result``'s.

    Lets tests assert what vision the core actually sent (the ``MockLLMClient`` uses this).
    """
    found: list[dict] = []
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if is_image_block(block):
                found.append(block)
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                inner = block.get("content")
                if isinstance(inner, list):
                    found.extend(b for b in inner if is_image_block(b))
    return found
