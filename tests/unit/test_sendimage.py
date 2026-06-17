"""v0.24 LUMI-098 — the send_image tool + the injected telegram_sink seam (core/sendimage.py).

No real Telegram: the sink is a fake recording (abs_path, caption). The tool never raises; any failure
(non-image / traversal / missing / no sink) degrades to an error string and does NOT call the sink.
"""
from __future__ import annotations

from core.sendimage import SEND_TOOL_NAMES, SEND_TOOLS, SendImageTools


def _sink():
    """A fake telegram_sink — records each (abs_path, caption); the TUI's append is faked away."""
    calls: list[tuple[str, str]] = []

    def sink(abs_path: str, caption: str) -> None:
        calls.append((abs_path, caption))

    sink.calls = calls  # type: ignore[attr-defined]
    return sink


def _png(root, rel="art/cat.png", data=b"\x89PNG-bytes"):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return f


# --- tool def ---------------------------------------------------------------------------------------
def test_send_tools_shape():
    assert SEND_TOOL_NAMES == {"send_image"}
    for t in SEND_TOOLS:
        assert {"name", "description", "input_schema"} <= t.keys()
        assert t["input_schema"]["required"] == ["path"] and "." not in t["name"]


# --- send_image: the happy path ---------------------------------------------------------------------
def test_send_calls_the_sink_with_resolved_path(tmp_path):
    _png(tmp_path)
    sink = _sink()
    tools = SendImageTools(tmp_path, telegram_sink=sink)
    out = tools.execute("send_image", {"path": "art/cat.png", "caption": "ось малюнок"})
    assert out == "sent cat.png to Telegram"
    assert len(sink.calls) == 1
    abs_path, caption = sink.calls[0]
    assert abs_path == str((tmp_path / "art" / "cat.png").resolve())  # the RESOLVED sandbox path
    assert caption == "ось малюнок"


def test_send_default_caption_is_empty(tmp_path):
    _png(tmp_path)
    sink = _sink()
    SendImageTools(tmp_path, telegram_sink=sink).execute("send_image", {"path": "art/cat.png"})
    assert sink.calls == [(str((tmp_path / "art" / "cat.png").resolve()), "")]


# --- degrade paths: the sink is never called -------------------------------------------------------
def test_send_non_image_refused(tmp_path):
    (tmp_path / "note.txt").write_text("hi")
    sink = _sink()
    out = SendImageTools(tmp_path, telegram_sink=sink).execute("send_image", {"path": "note.txt"})
    assert "not an image" in out and sink.calls == []


def test_send_traversal_refused(tmp_path):
    outside = _png(tmp_path.parent, "secret.png", b"SECRET")
    sink = _sink()
    out = SendImageTools(tmp_path, telegram_sink=sink).execute("send_image", {"path": "../secret.png"})
    assert "traversal" in out and sink.calls == []
    assert outside.read_bytes() == b"SECRET"  # never touched


def test_send_missing_file_refused(tmp_path):
    sink = _sink()
    out = SendImageTools(tmp_path, telegram_sink=sink).execute("send_image", {"path": "art/nope.png"})
    assert "not found" in out and sink.calls == []


def test_send_no_sink_reports_not_connected(tmp_path):
    _png(tmp_path)
    tools = SendImageTools(tmp_path, telegram_sink=None)  # bridge not connected
    out = tools.execute("send_image", {"path": "art/cat.png"})
    assert "Telegram not connected" in out


def test_send_missing_path_refused(tmp_path):
    sink = _sink()
    out = SendImageTools(tmp_path, telegram_sink=sink).execute("send_image", {})
    assert "not an image" in out and sink.calls == []


def test_executor_never_raises(tmp_path):
    tools = SendImageTools(tmp_path, telegram_sink=_sink())
    assert tools.execute("bogus", {"path": "x.png"}).startswith("error: unknown image tool")
