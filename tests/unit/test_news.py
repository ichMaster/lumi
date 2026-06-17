"""v0.25 LUMI-101 — the NewsProvider seam + GuardianProvider + news_search/news_read (core/news.py).

A mock transport returns canned Guardian /search + /{id} JSON — no network, no key. The executor never
raises; the per-turn id registry refuses an off-turn id.
"""
from __future__ import annotations

import json
import urllib.parse

from core.news import (
    NEWS_TOOL_NAMES,
    NEWS_TOOLS,
    GuardianProvider,
    NewsTools,
)

# --- a canned Guardian transport (records the URLs it's asked for) ----------------------------------
_SEARCH = {"response": {"status": "ok", "results": [
    {"id": "world/2026/jun/17/a", "webTitle": "A happens", "webUrl": "https://www.theguardian.com/world/a",
     "sectionName": "World news", "webPublicationDate": "2026-06-17T08:00:00Z",
     "fields": {"headline": "A happens in the world", "trailText": "A short A summary", "byline": "Reporter A"}},
    {"id": "world/2026/jun/17/b", "webTitle": "B happens", "webUrl": "https://www.theguardian.com/world/b",
     "sectionName": "World news", "webPublicationDate": "2026-06-17T07:00:00Z",
     "fields": {"headline": "B happens too", "trailText": "A short B summary", "byline": "Reporter B"}},
]}}

_BODY = "The full article body about A. " * 5


def _article(body=_BODY):
    return {"response": {"status": "ok", "content": {
        "id": "world/2026/jun/17/a", "webTitle": "A happens in the world",
        "webUrl": "https://www.theguardian.com/world/a",
        "fields": {"bodyText": body, "byline": "Reporter A"}}}}


def _transport(article_body=_BODY):
    """A fake http_get — routes /search vs /{id} by URL, records every URL asked for."""
    seen: list[str] = []

    def http_get(url: str) -> str:
        seen.append(url)
        path = urllib.parse.urlparse(url).path
        return json.dumps(_SEARCH if path.endswith("/search") else _article(article_body))

    http_get.seen = seen  # type: ignore[attr-defined]
    return http_get


def _tools(http_get=None, *, max_chars=3000, max_results=8):
    provider = GuardianProvider(http_get=http_get or _transport(), api_key="k",
                                base_url="https://content.guardianapis.com",
                                sections="world,politics,business,technology,science,environment,culture,sport")
    return NewsTools(provider, max_results=max_results, max_chars=max_chars)


# --- tool def ---------------------------------------------------------------------------------------
def test_news_tools_shape():
    assert NEWS_TOOL_NAMES == {"news_search", "news_read"}
    names = {t["name"] for t in NEWS_TOOLS}
    assert names == {"news_search", "news_read"} and all("." not in n for n in names)
    read = next(t for t in NEWS_TOOLS if t["name"] == "news_read")
    assert read["input_schema"]["required"] == ["id"]


# --- search → candidates (no bodies) + the per-turn id registry ------------------------------------
def test_search_returns_candidates_with_ids():
    tools = _tools()
    out = tools.execute("news_search", {"topic": "world"})
    assert "n1: A happens in the world" in out and "A short A summary" in out
    assert "n2: B happens too" in out
    assert _BODY[:20] not in out  # no bodies in a search result


def test_search_query_goes_out_in_the_url():
    http_get = _transport()
    _tools(http_get).execute("news_search", {"query": "climate summit", "topic": "world"})
    url = http_get.seen[0]
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert qs["q"] == ["climate summit"] and qs["section"] == ["world"]  # topic in the allowlist → filter


def test_unknown_topic_is_ignored_gracefully():
    http_get = _transport()
    _tools(http_get).execute("news_search", {"query": "x", "topic": "not-a-section"})
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(http_get.seen[0]).query)
    assert "section" not in qs  # an off-allowlist topic drops the filter, search still runs


# --- read by a this-turn id ------------------------------------------------------------------------
def test_read_by_id_returns_body_and_source():
    tools = _tools()
    tools.execute("news_search", {"topic": "world"})  # populate the registry (n1, n2)
    out = tools.execute("news_read", {"id": "n1"})
    assert "The full article body about A." in out
    assert "Джерело: https://www.theguardian.com/world/a" in out


def test_read_caps_the_body():
    tools = _tools(_transport("x" * 9000), max_chars=200)
    tools.execute("news_search", {"topic": "world"})
    out = tools.execute("news_read", {"id": "n1"})
    body = out.split("\n", 1)[1].rsplit("\nДжерело:", 1)[0]
    assert len(body) <= 201 and body.endswith("…")  # capped + ellipsis


# --- the off-turn / unknown id refusal -------------------------------------------------------------
def test_read_unknown_id_refused():
    tools = _tools()
    tools.execute("news_search", {"topic": "world"})
    assert "невідомий id" in tools.execute("news_read", {"id": "n9"})  # not produced this turn


def test_read_before_any_search_refused():
    assert "невідомий id" in _tools().execute("news_read", {"id": "n1"})  # empty registry


def test_read_missing_id():
    assert "missing 'id'" in _tools().execute("news_read", {})


# --- graceful degradation (never raises) -----------------------------------------------------------
def test_search_no_results():
    def empty(url):
        return json.dumps({"response": {"status": "ok", "results": []}})
    out = _tools(empty).execute("news_search", {"query": "nothingматчиться"})
    assert "нічого не знайдено" in out


def test_transport_error_degrades():
    def boom(url):
        raise OSError("network down")
    out = _tools(boom).execute("news_search", {"topic": "world"})
    assert out.startswith("error:")  # degrades, never raises


def test_unknown_tool():
    assert _tools().execute("bogus", {}).startswith("error: unknown news tool")
