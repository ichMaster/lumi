"""v0.27 LUMI-107 — the GeminiSearch seam + gemini_search + web_lookup / WebLookupTools (core/weblookup.py).

A stub GeminiSearch returns canned grounded text and records (query, today) — no network, no key. The
executor never raises; it date-anchors, caps the answer, and caps per-turn calls. The default gemini_search
is checked against a monkeypatched urlopen (request shape) + _extract_answer + the no-key path.
"""
from __future__ import annotations

import json

import core.weblookup as wl
from core.weblookup import (
    WEB_LOOKUP_TOOL_NAMES,
    WEB_LOOKUP_TOOLS,
    WebLookupError,
    WebLookupTools,
    _extract_answer,
    gemini_search,
)


# --- a stub GeminiSearch (records what it was asked) -----------------------------------------------
def _stub(answer="Свіжа відповідь з інтернету.", *, boom=None):
    seen: list[tuple[str, str]] = []

    def search(query: str, *, today: str) -> str:
        seen.append((query, today))
        if boom is not None:
            raise boom
        return answer

    search.seen = seen  # type: ignore[attr-defined]
    return search


def _tools(search=None, *, today="2026-06-18", max_chars=2000, max_calls=2):
    return WebLookupTools(search=search or _stub(), today=today, max_chars=max_chars, max_calls=max_calls)


# --- tool def --------------------------------------------------------------------------------------
def test_web_tool_shape():
    assert WEB_LOOKUP_TOOL_NAMES == {"web_lookup"}
    names = {t["name"] for t in WEB_LOOKUP_TOOLS}
    assert names == {"web_lookup"} and all("." not in n for n in names)
    assert next(t for t in WEB_LOOKUP_TOOLS if t["name"] == "web_lookup")["input_schema"]["required"] == ["query"]


# --- web_lookup returns the answer + the stub saw the query AND today ------------------------------
def test_lookup_returns_answer_and_anchors_date():
    search = _stub("Наступний запуск SpaceX — у п'ятницю.")
    out = _tools(search, today="2026-06-18").execute("web_lookup", {"query": "SpaceX next launch"})
    assert out == "Наступний запуск SpaceX — у п'ятницю."
    assert search.seen == [("SpaceX next launch", "2026-06-18")]  # query + injected today reach the seam


def test_answer_is_capped():
    out = _tools(_stub("я" * 9000), max_chars=200).execute("web_lookup", {"query": "x"})
    assert len(out) <= 201 and out.endswith("…")  # capped + ellipsis


def test_missing_query():
    assert "missing 'query'" in _tools().execute("web_lookup", {})


def test_empty_answer_degrades_to_notice():
    out = _tools(_stub("   ")).execute("web_lookup", {"query": "нічого"})
    assert "нічого не знайшлося" in out


# --- graceful degradation (never raises) ----------------------------------------------------------
def test_search_error_degrades():
    out = _tools(_stub(boom=WebLookupError("Gemini HTTP 429"))).execute("web_lookup", {"query": "x"})
    assert out.startswith("error:") and "429" in out  # degrades, never raises


def test_unknown_tool():
    assert _tools().execute("bogus", {}).startswith("error: unknown web tool")


# --- the per-turn call cap (on the instance — fresh per turn) --------------------------------------
def test_per_turn_call_cap():
    tools = _tools(max_calls=2)
    assert not tools.execute("web_lookup", {"query": "a"}).startswith("(web lookup limit")
    assert not tools.execute("web_lookup", {"query": "b"}).startswith("(web lookup limit")
    assert "limit reached" in tools.execute("web_lookup", {"query": "c"})  # the 3rd over the cap


# --- the default gemini_search impl: _extract_answer + no-key + the request shape ------------------
def test_extract_answer_pulls_text():
    data = {"candidates": [{"content": {"parts": [{"text": "Hello"}, {"text": "world"}]}}]}
    assert _extract_answer(data) == "Hello world"


def test_extract_answer_no_candidates_raises():
    try:
        _extract_answer({"candidates": []})
    except WebLookupError:
        return
    raise AssertionError("expected WebLookupError on no candidates")


def test_gemini_search_no_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    try:
        gemini_search(key=None)("q", today="2026-06-18")
    except WebLookupError as exc:
        assert "GEMINI_API_KEY" in str(exc)
        return
    raise AssertionError("expected WebLookupError with no key")


def test_gemini_search_builds_grounded_request(monkeypatch):
    captured: dict = {}

    class _Resp:
        def __init__(self, payload): self._p = payload
        def read(self): return self._p
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode()
        return _Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": "Fresh answer."}]}}]}).encode())

    monkeypatch.setattr(wl.urllib.request, "urlopen", fake_urlopen)
    out = gemini_search(model="gemini-2.5-flash", key="k")("SpaceX next launch", today="2026-06-18")

    assert out == "Fresh answer."
    assert "gemini-2.5-flash:generateContent" in captured["url"]
    body = json.loads(captured["body"])
    assert body["tools"] == [{"google_search": {}}]                                  # grounded
    prompt = body["contents"][0]["parts"][0]["text"]
    assert "Today is 2026-06-18" in prompt and "SpaceX next launch" in prompt        # date-anchored
