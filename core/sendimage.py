"""Send a sandbox image to Telegram — the ``send_image`` tool (v0.24).

The **send-it half** of the image tool: ``send_image(path[, caption])`` lets Лілі **choose** to send a
picture from her per-user sandbox (one she generated in v0.23, or one you dropped in) to **your
Telegram** — the explicit, in-character complement to the v0.23 auto-display (``LUMI_IMAGE_SHOW=telegram``).

The one architectural care is the **single-writer outbox**: the core **never** touches Telegram or
``outbox.jsonl``. ``send_image`` calls an **injected ``telegram_sink``** (a callable the TUI supplies —
it is already the sole outbox writer, so it appends the ``photo`` record). So **no core ↔ bridge coupling
and no second writer**. **Sandboxed + per-user** (the shared v0.19 ``safe_path`` guard + the v0.22
image-type check); **never raises** — any failure (non-image / traversal / missing / **no sink**) degrades
to an **error string**. Off by default + gated by the reply-turn wiring (``Core``); the recipient is the
**owner** (the single Telegram user).
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.files import _Denied, safe_path
from core.images import media_type_for

# The sink the TUI supplies: ``sink(abs_path, caption)`` appends a Telegram ``photo`` record to the
# outbox. The core depends on this callable seam — it never imports the bridge or writes the outbox.
TelegramSink = Callable[[str, str], None]

# Anthropic-style function-calling schema for the one v0.24 send tool.
SEND_TOOLS: list[dict] = [
    {
        "name": "send_image",
        "description": (
            "Надіслати зображення з пісочниці Лілі у Telegram власнику — коли Лілі САМА вирішує "
            "поділитися картинкою (своєю згенерованою або тією, що ти лишив). Можна додати підпис. "
            "Потрібен підключений Telegram-міст."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Файл-зображення відносно кореня пісочниці."},
                "caption": {"type": "string", "description": "Необовʼязковий підпис до фото."},
            },
            "required": ["path"],
        },
    },
]

SEND_TOOL_NAMES = frozenset(t["name"] for t in SEND_TOOLS)


class SendImageTools:
    """Runs ``send_image`` against one sandbox ``root`` via an injected ``telegram_sink``.

    ``execute(name, input)`` resolves the picture under the sandbox and calls the sink, returning a
    string notice — or an **error string** on any failure (non-image / traversal / missing / no sink).
    **Never raises** (a send error degrades the reply, never the turn). On failure the sink is **not**
    called.
    """

    def __init__(self, root: str | Path, *, telegram_sink: TelegramSink | None) -> None:
        self._root = Path(root)
        self._sink = telegram_sink

    def execute(self, name: str, tool_input: dict | None) -> str:
        inp = tool_input or {}
        try:
            if name == "send_image":
                return self._send(inp)
            return f"error: unknown image tool {name!r}"
        except _Denied as exc:
            return f"error: {exc}"
        except OSError as exc:
            return f"error: {exc.strerror or exc}".strip()
        except Exception as exc:  # noqa: BLE001 — never raise; degrade to an error string
            return f"error: {exc}"

    def _send(self, inp: dict) -> str:
        rel = inp.get("path")
        media_type = media_type_for(rel) if isinstance(rel, str) else None
        if media_type is None:
            return f"error: not an image (png/jpg/gif/webp): {rel!r}"
        f = safe_path(self._root, rel)  # rejects ../absolute/symlink-out before any I/O (sink not called)
        if not f.is_file():
            return f"error: image not found: {rel!r}"
        if self._sink is None:  # LUMI_IMAGE on but the bridge isn't connected
            return "Telegram not connected (the bridge is off)."
        caption = inp.get("caption")
        caption = caption.strip() if isinstance(caption, str) else ""
        self._sink(str(f), caption)  # the TUI appends a photo record (single outbox writer)
        return f"sent {Path(rel).name} to Telegram"
