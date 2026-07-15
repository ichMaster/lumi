"""v1.4 LUMI-188 — the pure streaming helpers: partial-JSON reply extraction + the tag filter."""

from core.prompt import split_reasoning
from core.streaming import StreamTagFilter, decode_json_string_value

# --- decode_json_string_value (the growing `reply` field out of partial JSON) --------------------

def test_decode_reply_from_complete_json():
    assert decode_json_string_value('{"reply": "Привіт світ", "emotion": "joy"}') == "Привіт світ"


def test_decode_reply_from_partial_json_grows():
    # Feed the JSON one prefix at a time (as input_json_delta / SSE would): the value only grows.
    full = '{"reply": "Привіт, як ти?"}'
    seen = [decode_json_string_value(full[:i]) for i in range(len(full) + 1)]
    # Monotonic prefixes of the final value; never a wrong char.
    for a, b in zip(seen, seen[1:], strict=False):
        assert b.startswith(a)
    assert seen[-1] == "Привіт, як ти?"


def test_decode_stops_before_incomplete_escape():
    # A dangling backslash or half \uXXXX must not emit a wrong char.
    assert decode_json_string_value('{"reply": "line\\') == "line"          # dangling backslash
    assert decode_json_string_value('{"reply": "snow \\u26') == "snow "       # half \uXXXX
    assert decode_json_string_value('{"reply": "a\\nb"}') == "a\nb"           # complete escape decodes


def test_decode_absent_field_is_empty():
    assert decode_json_string_value('{"emotion": "calm"}') == ""
    assert decode_json_string_value('{"repl') == ""


def test_decode_field_before_reply():
    assert decode_json_string_value('{"emotion": "joy", "reply": "hi there"}') == "hi there"


# --- StreamTagFilter -----------------------------------------------------------------------------

def _run(filt: StreamTagFilter, chunks: list[str]) -> str:
    return "".join(filt.feed(c) for c in chunks) + filt.flush()


def test_filter_shows_plain_prose_unchanged():
    f = StreamTagFilter()
    assert _run(f, ["Привіт ", "світ!"]) == "Привіт світ!"
    assert f.think == ""


def test_filter_routes_leading_think_and_shows_prose():
    # The common shape: <think>reasoning</think>prose — routed away, prose shown, matches split_reasoning.
    raw = "<think>зважую відповідь</think>Привіт, друже"
    f = StreamTagFilter()
    shown = _run(f, [raw])
    assert shown == "Привіт, друже"
    assert f.think == "зважую відповідь"
    assert shown == split_reasoning(raw)[1]  # assembled shown == the batch reply


def test_filter_never_leaks_a_half_tag_across_chunk_boundaries():
    raw = "<think>x</think>Готово"
    # Split at every index so the tag straddles chunk boundaries every possible way.
    for i in range(1, len(raw)):
        f = StreamTagFilter()
        shown = _run(f, [raw[:i], raw[i:]])
        assert shown == "Готово"
        assert "<" not in shown and ">" not in shown


def test_filter_char_by_char_never_leaks():
    raw = "<thinking>reason</thinking>Hello <emotion>joy 0.8</emotion>"
    f = StreamTagFilter()
    shown = _run(f, list(raw))  # one char per feed — the meanest boundary case
    assert shown == "Hello "
    assert "reason" in f.think
    assert "<" not in shown and ">" not in shown


def test_filter_hides_emotion_intent_style_markers():
    raw = "Привіт<emotion>joy 0.7</emotion><intent>deepen</intent><style>лагідна</style>"
    f = StreamTagFilter()
    assert _run(f, [raw]) == "Привіт"


def test_filter_shows_non_recognized_tags_literally():
    # A stray '<3' or an unknown tag is shown, not swallowed.
    f = StreamTagFilter()
    assert _run(f, ["I <3 you"]) == "I <3 you"
    g = StreamTagFilter()
    assert _run(g, ["see <code>x</code> ok"]) == "see <code>x</code> ok"
