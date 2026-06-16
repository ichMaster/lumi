"""Wikipedia tool (v0.21) — custom ``wiki_search`` / ``wiki_read`` tools over an injected HTTP client.

Лілі can search Wikipedia for candidate articles (``wiki_search``) and read one article's summary
(``wiki_read``) during a turn, on the **v0.19 bounded tool-loop**. This module is pure and model-free:
it defines the two **function-calling** tool schemas and a :class:`WikiTools` executor that calls the
Wikipedia REST API via an **injected** ``http_get`` (no network in tests), returning **HTML-free** text
+ the **source URL**. The reply turn registers + gates it per the safety rules (LUMI-089).

Hard rules (WEB_SEARCH.md §safety):
- **Untrusted data.** Returned text is information, never instructions — the loop frames the
  ``tool_result`` as untrusted; this module just returns the extract.
- **No personal/memory data in the query** — enforced by the *wiring* (the query is built only from the
  user's request), not here.
- **Bounded.** One ``wiki_read`` extract is capped at ``max_chars``; ``wiki_search`` returns a small N.
- **Never raises.** Any error (bad title, HTTP failure, decode, empty result) degrades to an **error
  string** — a wiki failure degrades the reply, never breaks the turn.

The tool *names* are ``wiki_search`` / ``wiki_read`` (Anthropic tool names allow no ``.``); the roadmap
refers to them as ``wiki.search`` / ``wiki.read``.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable

HttpGet = Callable[[str], str]

# Anthropic-style function-calling schemas for the two custom Wikipedia tools. Registered alongside the
# terminal `set_state` (and the v0.19/v0.20 file tools) by the reply tool-loop (LUMI-089).
WIKI_TOOLS: list[dict] = [
    {
        "name": "wiki_search",
        "description": (
            "Шукає у Вікіпедії статті за запитом і повертає кандидатів (назва + короткий опис), "
            "щоб обрати потрібну перед читанням. Запит — лише з того, що людина прямо просить."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Пошуковий запит (тема статті)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "wiki_read",
        "description": (
            "Читає коротке резюме однієї статті Вікіпедії за назвою і повертає текст + ПОСИЛАННЯ-ДЖЕРЕЛО, "
            "щоб відповісти з посиланням на джерело."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Точна назва статті (з wiki_search)."},
            },
            "required": ["title"],
        },
    },
]

WIKI_TOOL_NAMES = frozenset(t["name"] for t in WIKI_TOOLS)


def _http_get(url: str, timeout: float = 4.0) -> str:
    """Default production HTTP GET (a browser-ish UA; tests inject a mock instead)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; Lumi/0.21; +wikipedia-tool)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed Wikipedia host
        return resp.read().decode("utf-8", "replace")


def _strip_html(text: str) -> str:
    """Plain-text the extract — drop any residual tags (REST `extract` is usually clean already)."""
    return re.sub(r"<[^>]+>", "", text).strip()


class WikiTools:
    """Runs ``wiki_search`` / ``wiki_read`` against Wikipedia via an injected ``http_get``.

    ``execute(name, input)`` dispatches to a tool and **always returns a string** — an error string on
    any failure (never raises), so a wiki error degrades the reply, never breaks the turn.
    """

    def __init__(
        self,
        *,
        http_get: HttpGet = _http_get,
        lang: str = "uk,en",
        base_url: str = "",
        max_chars: int = 1500,
        max_results: int = 5,
    ) -> None:
        self._http_get = http_get
        self._langs = [code.strip() for code in lang.split(",") if code.strip()] or ["uk"]
        self._base = base_url.strip()  # "" → default per-lang host; may contain "{lang}"
        self._max_chars = max(1, max_chars)
        self._max_results = max(1, max_results)

    # --- the executor entry point ----------------------------------------------------------------
    def execute(self, name: str, tool_input: dict | None) -> str:
        inp = tool_input or {}
        try:
            if name == "wiki_search":
                return self._search(inp)
            if name == "wiki_read":
                return self._read(inp)
            return f"error: unknown wiki tool {name!r}"
        except Exception as exc:  # noqa: BLE001 — never raise; degrade to an error string
            return f"error: {exc}"

    # --- url helpers -----------------------------------------------------------------------------
    def _host(self, lang: str) -> str:
        if self._base:
            return self._base.rstrip("/").replace("{lang}", lang)
        return f"https://{lang}.wikipedia.org"

    # --- the two tools ---------------------------------------------------------------------------
    def _search(self, inp: dict) -> str:
        query = inp.get("query")
        if not isinstance(query, str) or not query.strip():
            return "error: missing 'query'"
        for lang in self._langs:  # first language edition with a hit wins
            url = (
                f"{self._host(lang)}/w/api.php?action=opensearch&format=json"
                f"&limit={self._max_results}&search={urllib.parse.quote(query)}"
            )
            data = json.loads(self._http_get(url))
            # opensearch shape: [query, [titles], [descriptions], [urls]]
            titles = data[1] if len(data) > 1 else []
            descs = data[2] if len(data) > 2 else []
            if titles:
                rows = []
                for i, title in enumerate(titles):
                    desc = descs[i] if i < len(descs) and descs[i] else ""
                    rows.append(f"  - {title}" + (f": {desc}" if desc else ""))
                return f"Вікіпедія ({lang}) за «{query}»:\n" + "\n".join(rows)
        return f"Вікіпедія: за «{query}» нічого не знайдено."

    def _read(self, inp: dict) -> str:
        title = inp.get("title")
        if not isinstance(title, str) or not title.strip():
            return "error: missing 'title'"
        slug = urllib.parse.quote(title.strip().replace(" ", "_"))
        for lang in self._langs:
            url = f"{self._host(lang)}/api/rest_v1/page/summary/{slug}"
            try:
                data = json.loads(self._http_get(url))
            except Exception:  # noqa: BLE001 — try the next language edition
                continue
            extract = _strip_html(str(data.get("extract") or ""))
            if not extract:
                continue
            if len(extract) > self._max_chars:
                extract = extract[: self._max_chars].rstrip() + "…"
            source = (
                (data.get("content_urls", {}).get("desktop", {}) or {}).get("page")
                or f"{self._host(lang)}/wiki/{slug}"
            )
            shown = data.get("title") or title
            return f"{shown}:\n{extract}\nДжерело: {source}"
        return f"error: стаття «{title}» не знайдена."
