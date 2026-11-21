"""v5.1 LUMI-341 — core/ledgers.py: the media and the tool records, their appenders, a tool's outcome, the meters of
the core's two paid callers, and the unit prices (core/usage.py). No network, no paid call."""

from __future__ import annotations

import json
from datetime import date

import pytest

from core import ledgers, usage_origin
from core.agent import TOOL_INPUT_CHARS, tool_calls_view
from core.logtrim import LOG_FILES, trim_log_days
from core.usage import UnitPrice, load_unit_prices, media_cost, unit_price
from core.weblookup import usage_of


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- the appenders ----------------------------------------------------------------------------------------------
def test_a_media_record_is_one_line_and_mode_only_on_a_recognition(tmp_path):
    path = tmp_path / ledgers.MEDIA_LEDGER
    assert ledgers.append_media(path, ledgers.media_record(
        provider="elevenlabs", service="tts", model="eleven_flash_v2_5", units={"chars": 42}, latency_ms=900,
        outcome="ok", ts="2026-11-21T10:00:00+02:00", origin="turn", channel="window"))
    assert ledgers.append_media(path, ledgers.media_record(
        provider="deepgram", service="stt", model="nova-3", units={"audio_s": 3.5}, latency_ms=4000, outcome="ok",
        origin="turn", channel="window", mode="stream"))
    tts, stt = _lines(path)
    assert tts == {"ts": "2026-11-21T10:00:00+02:00", "provider": "elevenlabs", "service": "tts",
                   "model": "eleven_flash_v2_5", "units": {"chars": 42}, "latency_ms": 900, "outcome": "ok",
                   "origin": "turn", "channel": "window"}
    assert stt["mode"] == "stream" and stt["units"] == {"audio_s": 3.5}


def test_a_record_takes_the_work_s_origin_and_channel():
    with usage_origin.usage_origin("schedule:catchup", "telegram"):
        rec = ledgers.media_record(provider="google", service="image", model="m", units={"images": 1},
                                   latency_ms=1, outcome="ok")
    assert (rec.origin, rec.channel) == ("schedule:catchup", "telegram")
    assert ledgers.media_record(provider="google", service="image", model="m", units={"images": 1},
                                latency_ms=1, outcome="ok").origin == ""  # no work around it


def test_an_append_never_raises(tmp_path):
    blocked = tmp_path / "dir"
    blocked.mkdir()  # a directory where the file should be: the append fails
    rec = ledgers.ToolRecord(ts="t", tool="read_file", outcome="ok", ms=1, result_chars=3)
    assert ledgers.append_tool(blocked, rec) is False
    assert ledgers.append_tool(None, rec) is False
    assert ledgers.append_media(blocked, ledgers.media_record(provider="p", service="tts", model="m", units={},
                                                              latency_ms=None, outcome="ok")) is False


def test_read_records_skips_a_malformed_line(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": 1}\nnot json\n[1]\n{"b": 2}\n', encoding="utf-8")
    assert ledgers.read_records(path) == [{"a": 1}, {"b": 2}]
    assert ledgers.read_records(tmp_path / "missing.jsonl") == []


# --- a tool's outcome, read from its result -----------------------------------------------------------------------
@pytest.mark.parametrize(("result", "outcome"), [
    ("created todo.md (12 bytes)", "ok"),
    ({"type": "image", "source": {"data": "…"}}, "ok"),
    ("(empty directory: notes)", "ok"),
    ("error: unknown tool 'nope'", "not_found"),
    ("error: file not found: 'x.md'", "not_found"),
    ("(нічого не згадалося про «кит»)", "not_found"),
    ("error: невідомий id 'n9'", "not_found"),
    ("error: file already exists (no overwrite): 'a.md'", "refused:exists"),
    ("error: path escapes the sandbox: '../x'", "refused:sandbox"),
    ("error: path traversal ('..') not allowed: '../x'", "refused:sandbox"),
    ("(image generation limit reached: 2 per turn)", "refused:cap"),
    ("(діапазон завеликий: максимум 31 днів)", "refused:cap"),
    ("error: content too large: 900000 bytes > 65536 cap", "refused:size"),
    ("error: image generation failed: Gemini HTTP 500: boom", "error:HTTP500"),
    ("error: web lookup failed: Gemini unreachable: timed out", "error:TimeoutError"),
    ("error: ImageGenError something", "error:ImageGenError"),
    ("error: Gemini unreachable: connection refused", "error:ConnectionError"),
    ("error: something odd", "error:Error"),
])
def test_a_tool_s_outcome(result, outcome):
    assert ledgers.tool_outcome(result) == outcome


def test_the_outcome_never_carries_the_result_s_text():
    secret_words = "error: стаття «таємний рецепт бабусі» — HTTP 404"
    out = ledgers.tool_outcome(secret_words)
    assert "рецепт" not in out and out in ("not_found", "error:HTTP404")


def test_the_input_line_is_the_window_s_trace_line():
    args = {"path": "notes/" + "x" * 300, "query": "оплата"}
    line = ledgers.input_line(args, TOOL_INPUT_CHARS)
    assert line == tool_calls_view([("read_file", args, "")])[0]["input"]
    assert len(line) <= TOOL_INPUT_CHARS and line.endswith("…")


def test_result_chars_is_a_size_only():
    assert ledgers.result_chars("абв") == 3
    assert ledgers.result_chars({"type": "image"}) == len(json.dumps({"type": "image"}))


# --- the meters of the core's two paid callers --------------------------------------------------------------------
def test_a_picture_writes_one_image_record_without_its_prompt(tmp_path):
    path = tmp_path / ledgers.MEDIA_LEDGER
    gen = ledgers.metered_image_gen(lambda prompt, **kw: b"PNG", path=path, model="gemini-2.5-flash-image",
                                    ts=lambda: "2026-11-21T10:00:00+02:00")
    with usage_origin.usage_origin("turn", "window"):
        assert gen("таємне небо", size=768) == b"PNG"
    (rec,) = _lines(path)
    assert rec["service"] == "image" and rec["units"] == {"images": 1} and rec["outcome"] == "ok"
    assert (rec["origin"], rec["channel"], rec["provider"]) == ("turn", "window", "google")
    assert "таємне" not in path.read_text(encoding="utf-8")


def test_a_failed_picture_is_recorded_and_still_raises(tmp_path):
    path = tmp_path / ledgers.MEDIA_LEDGER

    def boom(prompt, **kw):
        raise TimeoutError("slow")

    gen = ledgers.metered_image_gen(boom, path=path, model="m")
    with pytest.raises(TimeoutError):
        gen("x")
    assert _lines(path)[0]["outcome"] == "error:TimeoutError"


def test_no_ledger_leaves_the_caller_unchanged():
    def gen(prompt, **kw):
        return b""

    assert ledgers.metered_image_gen(gen, path=None, model="m") is gen
    assert ledgers.metered_search(gen, path=None, model="m") is gen


def test_a_web_lookup_records_its_request_and_tokens_never_its_query(tmp_path):
    path = tmp_path / ledgers.MEDIA_LEDGER

    def search(query, *, today):
        search.last_usage = {"input": 120, "output": 300}
        return "відповідь про комету"

    metered = ledgers.metered_search(search, path=path, model="gemini-2.5-flash")
    assert metered("comet december", today="2026-11-21") == "відповідь про комету"
    (rec,) = _lines(path)
    assert rec["service"] == "search" and rec["units"] == {"requests": 1, "input": 120, "output": 300}
    text = path.read_text(encoding="utf-8")
    assert "comet" not in text and "комету" not in text


def test_usage_of_reads_gemini_s_usage_metadata():
    assert usage_of({"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20}}) == {
        "input": 10, "output": 20}
    assert usage_of({"candidates": []}) is None


# --- the unit prices ------------------------------------------------------------------------------------------------
PRICES = [
    UnitPrice("elevenlabs", "tts", "chars", 1000, 0.10, model="eleven_multilingual"),
    UnitPrice("elevenlabs", "tts", "chars", 1000, 0.05, model="eleven_flash"),
    UnitPrice("deepgram", "stt", "audio_s", 60, 0.0077, model="nova-3", mode="stream"),
    UnitPrice("deepgram", "stt", "audio_s", 60, 0.0043, model="nova-3", mode="batch"),
    UnitPrice("google", "search", "requests", 1, 0.035),
]


def test_a_unit_price_matches_provider_service_model_and_mode():
    assert unit_price("elevenlabs", "tts", "eleven_multilingual_v2", prices=PRICES).usd == 0.10
    assert unit_price("elevenlabs", "tts", "eleven_flash_v2_5", prices=PRICES).usd == 0.05
    assert unit_price("deepgram", "stt", "nova-3", "stream", prices=PRICES).usd == 0.0077
    assert unit_price("deepgram", "stt", "nova-3", "batch", prices=PRICES).usd == 0.0043
    assert unit_price("elevenlabs", "stt", "scribe_v1", prices=PRICES) is None  # not priced, never guessed
    assert unit_price("elevenlabs", "tts", "some_new_model", prices=PRICES) is None


def test_a_media_record_s_cost():
    tts = {"provider": "elevenlabs", "service": "tts", "model": "eleven_multilingual_v2",
           "units": {"chars": 500}, "outcome": "ok"}
    assert media_cost(tts, PRICES) == pytest.approx(0.05)
    assert media_cost({**tts, "outcome": "error:TimeoutError"}, PRICES) == 0.0  # only an ok call costs
    stt = {"provider": "deepgram", "service": "stt", "model": "nova-3", "mode": "stream",
           "units": {"audio_s": 120}, "outcome": "ok"}
    assert media_cost(stt, PRICES) == pytest.approx(0.0154)
    unpriced = {"provider": "elevenlabs", "service": "stt", "model": "scribe_v1", "units": {"audio_s": 60},
                "outcome": "ok"}
    assert media_cost(unpriced, PRICES) is None
    search = {"provider": "google", "service": "search", "model": "gemini-2.5-flash",
              "units": {"requests": 1, "input": 1_000_000, "output": 0}, "outcome": "ok"}
    assert media_cost(search, PRICES) == pytest.approx(0.035 + 0.30)  # the request + its tokens


def test_the_price_list_has_the_four_services_and_a_malformed_one_prices_nothing(tmp_path):
    services = {(row.provider, row.service) for row in load_unit_prices()}
    assert {("elevenlabs", "tts"), ("deepgram", "stt"), ("google", "image"), ("google", "search")} <= services
    bad = tmp_path / "pricing.yaml"
    bad.write_text("units: [oops\n", encoding="utf-8")
    assert load_unit_prices(bad) == []


# --- the trim ---------------------------------------------------------------------------------------------------------
def test_both_ledgers_are_trimmed_with_the_logs(tmp_path):
    assert {"media-ledger.jsonl", "tools.jsonl"} <= set(LOG_FILES)
    path = tmp_path / "tools.jsonl"
    path.write_text('{"ts": "2026-10-01T10:00:00+02:00", "tool": "a"}\n'
                    '{"ts": "2026-11-20T10:00:00+02:00", "tool": "b"}\n', encoding="utf-8")
    assert trim_log_days(path, 10, today=date(2026, 11, 21))
    assert [r["tool"] for r in _lines(path)] == ["b"]
