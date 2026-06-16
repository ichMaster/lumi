"""v0.22 LUMI-091 — the image-block seam (core/images.py) + the LLMClient multimodal path. No network."""
from __future__ import annotations

import base64
from types import SimpleNamespace

from core.images import image_block, images_in_messages, is_image_block, media_type_for
from core.llm import AnthropicClient, MockLLMClient, _anthropic_messages

_TOOLS = [{"name": "view_image", "input_schema": {"type": "object"}}]
_STATE = {"reply": "котик", "emotion": "tender", "intensity": 0.7}


# --- core/images.py --------------------------------------------------------------------------------
def test_image_block_from_path_and_bytes(tmp_path):
    p = tmp_path / "cat.png"
    p.write_bytes(b"\x89PNG-fake-bytes")
    blk = image_block(p)
    assert blk["type"] == "image" and blk["media_type"] == "image/png"
    assert base64.b64decode(blk["data"]) == b"\x89PNG-fake-bytes"
    raw = image_block(b"abc", media_type="image/jpeg")
    assert raw["media_type"] == "image/jpeg" and base64.b64decode(raw["data"]) == b"abc"


def test_media_type_inference():
    assert media_type_for("a.PNG") == "image/png" and media_type_for("b.jpg") == "image/jpeg"
    assert media_type_for("c.txt") is None  # not an image


def test_is_image_block_and_images_in_messages():
    img = image_block(b"x")
    assert is_image_block(img) and not is_image_block({"type": "text", "text": "hi"})
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "що це?"}, img]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": [{"type": "text", "text": "untrusted"}, image_block(b"y")]}]},
        {"role": "assistant", "content": "звичайний текст"},  # string content → no images
    ]
    assert len(images_in_messages(messages)) == 2  # one shared, one via tool_result


# --- the Anthropic translation -----------------------------------------------------------------
def test_neutral_image_translated_to_anthropic_source_form():
    img = image_block(b"z", media_type="image/png")
    msgs = _anthropic_messages([{"role": "user", "content": [{"type": "text", "text": "hi"}, img]}])
    out_img = msgs[0]["content"][1]
    assert out_img["type"] == "image" and out_img["source"]["type"] == "base64"
    assert out_img["source"]["media_type"] == "image/png" and out_img["source"]["data"] == img["data"]
    # a plain-string message is unchanged (back-compatible)
    assert _anthropic_messages([{"role": "user", "content": "плоский текст"}])[0]["content"] == "плоский текст"


# --- MockLLMClient records the images the core sent ---------------------------------------------
def test_mock_records_shared_image_input():
    mock = MockLLMClient(states=_STATE)
    img = image_block(b"shared")
    mock.reply_structured("sys", [{"role": "user", "content": [{"type": "text", "text": "що це?"}, img]}], "m")
    assert len(mock.images_seen) == 1 and mock.images_seen[0] is img


def test_mock_records_view_image_tool_result():
    # a scripted view_image whose executor returns an image block → recorded as an image seen
    img = image_block(b"sandbox")
    mock = MockLLMClient(states=_STATE, tool_script=[("view_image", {"path": "cat.png"})])
    mock.reply_structured("sys", [{"role": "user", "content": "що на cat.png?"}], "m",
                          tools=_TOOLS, tool_executor=lambda n, i: img)
    assert mock.images_seen == [img] and mock.tool_calls[0][0] == "view_image"


# --- AnthropicClient passes the translated image to the API ------------------------------------
def _queue_fake(responses):
    class _M:
        def __init__(self):
            self.calls = []
            self._q = list(responses)

        def create(self, **kw):
            self.calls.append(kw)
            return self._q.pop(0)

    return SimpleNamespace(messages=_M())


def test_anthropic_client_sends_image_in_source_form():
    terminal = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="s1", name="set_state", input=_STATE)], usage=None)
    fake = _queue_fake([terminal])
    client = AnthropicClient("sk-test", _client=fake)
    img = image_block(b"pixels", media_type="image/png")
    client.reply_structured("sys", [{"role": "user", "content": [{"type": "text", "text": "що це?"}, img]}], "m")
    sent_img = fake.messages.calls[0]["messages"][0]["content"][1]
    assert sent_img["type"] == "image" and sent_img["source"]["data"] == img["data"]  # translated to source form
