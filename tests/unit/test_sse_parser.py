"""The client's SSE parser (LUMI-217) — pure frames in, (event, data) out."""

import pytest

from tui.remote import MalformedStream, parse_sse


def _lines(text: str) -> list[str]:
    return text.split("\n")


def test_frames_in_order():
    raw = 'event: delta\ndata: {"text": "При"}\n\nevent: delta\ndata: {"text": "віт"}\n\nevent: done\ndata: {"reply": "Привіт"}\n\n'
    assert list(parse_sse(_lines(raw))) == [
        ("delta", {"text": "При"}), ("delta", {"text": "віт"}), ("done", {"reply": "Привіт"}),
    ]


def test_comments_unknown_fields_and_crlf_are_ignored():
    raw = ': keepalive\r\nid: 7\r\nevent: think\r\ndata: {"text": "хм"}\r\n\r\n'
    assert list(parse_sse(raw.split("\n"))) == [("think", {"text": "хм"})]


def test_multi_line_data_is_joined():
    raw = 'event: done\ndata: {"reply":\ndata: "так"}\n\n'
    assert list(parse_sse(_lines(raw))) == [("done", {"reply": "так"})]


def test_a_last_frame_without_its_blank_line_still_counts():
    assert list(parse_sse(_lines('event: done\ndata: {"reply": "ок"}'))) == [("done", {"reply": "ок"})]


@pytest.mark.parametrize("data", ['{"reply": "обірва', "not json", "[1, 2]"])
def test_garbled_data_is_malformed(data):
    with pytest.raises(MalformedStream):
        list(parse_sse(_lines(f"event: done\ndata: {data}\n\n")))
