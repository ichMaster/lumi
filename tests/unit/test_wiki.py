"""v0.21 LUMI-088 — the Wikipedia handler (core/wiki.py) over an injected HTTP transport. No network."""
from __future__ import annotations

import json

from core.wiki import WIKI_TOOL_NAMES, WIKI_TOOLS, WikiTools


class FakeHTTP:
    """A mock ``http_get`` — routes by url (opensearch / page/summary) + language edition."""

    def __init__(self):
        self.calls: list[str] = []
        self.search: dict[str, str] = {}   # lang -> opensearch JSON
        self.read: dict[str, str] = {}     # lang -> page/summary JSON
        self.raise_on: str | None = None

    def _lang(self, url: str) -> str:
        return "en" if "//en." in url or "/en/" in url else "uk"

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.raise_on and self.raise_on in url:
            raise OSError("network down")
        lang = self._lang(url)
        if "opensearch" in url:
            return self.search.get(lang, json.dumps(["q", [], [], []]))
        if "page/summary" in url:
            return self.read.get(lang, json.dumps({}))
        raise ValueError(f"unexpected url {url}")


def _opensearch(query, titles, descs):
    return json.dumps([query, titles, descs, [f"https://uk.wikipedia.org/wiki/{t}" for t in titles]])


def _summary(title, extract, source="https://uk.wikipedia.org/wiki/X"):
    return json.dumps({"title": title, "extract": extract,
                       "content_urls": {"desktop": {"page": source}}})


# --- tool defs -------------------------------------------------------------------------------------
def test_wiki_tools_shape():
    assert WIKI_TOOL_NAMES == {"wiki_search", "wiki_read"}
    for t in WIKI_TOOLS:
        assert {"name", "description", "input_schema"} <= t.keys()
    assert {"." not in t["name"] for t in WIKI_TOOLS} == {True}  # Anthropic-safe names (no dots)


# --- wiki_search -----------------------------------------------------------------------------------
def test_search_returns_candidates():
    http = FakeHTTP()
    http.search["uk"] = _opensearch("Сковорода", ["Григорій Сковорода", "Сковорода (значення)"],
                                    ["український філософ", "значення"])
    wt = WikiTools(http_get=http, lang="uk")
    out = wt.execute("wiki_search", {"query": "Сковорода"})
    assert "Григорій Сковорода" in out and "український філософ" in out
    assert "opensearch" in http.calls[0] and "search=" in http.calls[0]


def test_search_no_results():
    wt = WikiTools(http_get=FakeHTTP(), lang="uk")
    assert "нічого не знайдено" in wt.execute("wiki_search", {"query": "zxqw"})


def test_search_missing_query():
    assert "missing 'query'" in WikiTools(http_get=FakeHTTP()).execute("wiki_search", {})


# --- wiki_read -------------------------------------------------------------------------------------
def test_read_returns_summary_and_source():
    http = FakeHTTP()
    http.read["uk"] = _summary("Григорій Сковорода", "Український філософ і поет.",
                               source="https://uk.wikipedia.org/wiki/Григорій_Сковорода")
    wt = WikiTools(http_get=http, lang="uk")
    out = wt.execute("wiki_read", {"title": "Григорій Сковорода"})
    assert "Український філософ і поет." in out
    assert "Джерело: https://uk.wikipedia.org/wiki/Григорій_Сковорода" in out
    assert "page/summary" in http.calls[0]


def test_read_caps_extract():
    http = FakeHTTP()
    http.read["uk"] = _summary("X", "а" * 5000)
    wt = WikiTools(http_get=http, lang="uk", max_chars=100)
    out = wt.execute("wiki_read", {"title": "X"})
    body = out.split(":\n", 1)[1].split("\nДжерело:", 1)[0]
    assert len(body) <= 101 and body.endswith("…")  # capped + ellipsis


def test_read_strips_html():
    http = FakeHTTP()
    http.read["uk"] = _summary("X", "текст <b>жирний</b> і <a href='x'>лінк</a>")
    out = WikiTools(http_get=http, lang="uk").execute("wiki_read", {"title": "X"})
    assert "<b>" not in out and "жирний" in out and "лінк" in out


def test_read_missing_and_not_found():
    wt = WikiTools(http_get=FakeHTTP(), lang="uk")
    assert "missing 'title'" in wt.execute("wiki_read", {})
    assert "не знайдена" in wt.execute("wiki_read", {"title": "Nonexistent"})  # empty summary → not found


# --- language fallback -----------------------------------------------------------------------------
def test_search_falls_back_to_next_language():
    http = FakeHTTP()
    http.search["uk"] = _opensearch("q", [], [])                       # uk: no hit
    http.search["en"] = _opensearch("q", ["Hryhorii Skovoroda"], ["philosopher"])
    wt = WikiTools(http_get=http, lang="uk,en")
    out = wt.execute("wiki_search", {"query": "q"})
    assert "Hryhorii Skovoroda" in out and "(en)" in out              # second edition won


def test_read_falls_back_to_next_language():
    http = FakeHTTP()
    # uk: empty extract → skip; en: a hit
    http.read["en"] = _summary("Skovoroda", "Ukrainian philosopher.",
                               source="https://en.wikipedia.org/wiki/Skovoroda")
    wt = WikiTools(http_get=http, lang="uk,en")
    out = wt.execute("wiki_read", {"title": "Skovoroda"})
    assert "Ukrainian philosopher." in out and "en.wikipedia.org" in out


# --- graceful degradation --------------------------------------------------------------------------
def test_base_url_override():
    http = FakeHTTP()
    http.search["uk"] = _opensearch("q", ["T"], ["d"])
    wt = WikiTools(http_get=http, lang="uk", base_url="https://wiki.example/{lang}")
    wt.execute("wiki_search", {"query": "q"})
    assert http.calls[0].startswith("https://wiki.example/uk/w/api.php")


def test_never_raises():
    http = FakeHTTP()
    http.raise_on = "opensearch"
    wt = WikiTools(http_get=http, lang="uk")
    assert wt.execute("wiki_search", {"query": "q"}).startswith("error:")   # HTTP raised → error string
    assert wt.execute("bogus_tool", {"query": "q"}).startswith("error: unknown wiki tool")
