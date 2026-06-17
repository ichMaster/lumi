"""News tool (v0.25) — custom ``news_search`` / ``news_read`` tools over an injected ``NewsProvider``.

Лілі can search a topic (``news_search``) and read one article (``news_read``) during a turn, on the
**v0.19 bounded tool-loop** — the active, on-demand sibling of the v0.4 ambient news. This module is pure
and model-free: a thin **``NewsProvider``** seam (the same philosophy as ``LLMClient`` / ``ImageGen`` /
``Embedder`` — never an SDK in ``core``), its one implementation **``GuardianProvider``** over an injected
``http_get`` (no network in tests), and a :class:`NewsTools` executor that returns **HTML-free** text +
the **source URL**. The reply turn registers + gates it per the safety rules (LUMI-102).

The source is one configured outlet — **The Guardian Open Platform** — so the allowlist is a **single
host** by construction and there is no HTML scraper. The two tools mirror ``wiki_search`` / ``wiki_read``:

- ``news_search(query?, topic?, days?)`` → candidates (title + summary + an **opaque per-turn id**), no bodies.
- ``news_read(id)`` → one article **by an id from this turn's search** (the ``web.fetch`` "only this turn's
  ids" rule — an id not in the per-turn registry is refused), body (capped) + the source URL.

Hard rules (NEWS_TOOL.md §safety): **allowlist by construction**; **untrusted data** (bodies are info,
never instructions — the loop frames the ``tool_result``); **no personal/memory data in the query**
(enforced by the wiring); **bounded** (size caps here, the per-turn call cap in the wiring); **never
raises** (every path returns a string). Tool *names* are ``news_search`` / ``news_read`` (Anthropic tool
names allow no ``.``).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

HttpGet = Callable[[str], str]

# Anthropic-style function-calling schemas for the two custom news tools. Registered alongside the
# terminal `set_state` (and the file/wiki/image tools) by the reply tool-loop (LUMI-102).
NEWS_TOOLS: list[dict] = [
    {
        "name": "news_search",
        "description": (
            "Шукає свіжі новини в The Guardian за темою і повертає кандидатів (заголовок + короткий опис "
            "+ id), щоб обрати, що прочитати. Запит формуй АНГЛІЙСЬКОЮ і лише з того, що людина прямо "
            "просить (без особистих даних). Теми (sections): world, politics, business, technology, "
            "science, environment, culture, sport."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Пошуковий запит англійською (тема новини)."},
                "topic": {"type": "string", "description": "Розділ Guardian (world/politics/…), необовʼязково."},
                "days": {"type": "integer", "description": "Свіжість у днях (за замовчуванням 7)."},
            },
            "required": [],
        },
    },
    {
        "name": "news_read",
        "description": (
            "Читає ОДНУ статтю за id з останнього news_search цього ходу і повертає текст + "
            "ПОСИЛАННЯ-ДЖЕРЕЛО. Відповідай УКРАЇНСЬКОЮ, своїм голосом, чесно що це переказ англомовного "
            "джерела, із посиланням."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Opaque id з news_search цього ходу (напр. n1)."},
            },
            "required": ["id"],
        },
    },
]

NEWS_TOOL_NAMES = frozenset(t["name"] for t in NEWS_TOOLS)

# The authored "how she delivers news" line — injected into the system prompt only when the news tool is
# on (LUMI-102). English source → Ukrainian voice, selective, cited, honest it's a summary.
NEWS_DIRECTIVE = (
    "Якщо користуєшся новинним інструментом (news_search/news_read): джерело — англомовний Guardian. "
    "Запит до пошуку формуй англійською і лише з теми, яку людина прямо просить (без особистих даних). "
    "Переказуй новину УКРАЇНСЬКОЮ, своїм голосом, вибірково — не як стрічку заголовків; будь чесною, що це "
    "переказ англомовного джерела (напр. «читала в Guardian…»), і завжди додавай посилання-джерело."
)


@dataclass
class NewsItem:
    """One news candidate. ``content_id`` is the provider's article handle; ``id`` is the opaque
    per-turn handle (``n1``, …) the tool assigns when it builds the registry."""

    title: str
    summary: str
    section: str
    date: str
    byline: str
    content_id: str
    link: str
    id: str = ""  # the opaque per-turn id, set by NewsTools when registering


class NewsProvider(Protocol):
    """The seam ``core`` depends on — never an SDK. One impl now (``GuardianProvider``); a Ukrainian-local
    RSS source could be added later behind the same two methods without touching the tools."""

    def search(self, query: str | None, topic: str | None, days: int, cap: int) -> list[NewsItem]: ...
    def read(self, item: NewsItem, max_chars: int) -> str: ...  # returns body + source


def _http_get(url: str, timeout: float = 5.0) -> str:
    """Default production HTTP GET (tests inject a mock transport instead)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Lumi/0.25 (+news-tool)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed Guardian host
        return resp.read().decode("utf-8", "replace")


class GuardianProvider:
    """``NewsProvider`` over the Guardian Open Platform, via an injected ``http_get``.

    One host (``content.guardianapis.com``); ``read`` fetches **by content-id** (never a raw URL), so it
    structurally cannot leave Guardian. A ``topic`` outside the configured ``sections`` is ignored
    gracefully (the search runs without a section filter). Never raises here — errors surface to
    :class:`NewsTools` which degrades them to an error string.
    """

    def __init__(
        self,
        *,
        http_get: HttpGet = _http_get,
        api_key: str = "",
        base_url: str = "https://content.guardianapis.com",
        sections: str = "",
    ) -> None:
        self._http_get = http_get
        self._key = api_key
        self._base = (base_url or "https://content.guardianapis.com").rstrip("/")
        self._sections = {s.strip() for s in sections.split(",") if s.strip()}

    def search(self, query: str | None, topic: str | None, days: int, cap: int) -> list[NewsItem]:
        from_date = (datetime.now(UTC).date() - timedelta(days=max(0, days))).isoformat()
        params = {
            "order-by": "newest",
            "from-date": from_date,
            "show-fields": "headline,trailText,byline",
            "page-size": str(max(1, cap)),
            "api-key": self._key or "test",
        }
        if query and query.strip():
            params["q"] = query.strip()
        if topic and topic.strip() in self._sections:  # an unknown topic is ignored (no section filter)
            params["section"] = topic.strip()
        url = f"{self._base}/search?" + urllib.parse.urlencode(params)
        data = json.loads(self._http_get(url))
        results = (data.get("response") or {}).get("results") or []
        items: list[NewsItem] = []
        for r in results:
            fields = r.get("fields") or {}
            items.append(NewsItem(
                title=fields.get("headline") or r.get("webTitle") or "",
                summary=_plain(fields.get("trailText") or ""),
                section=r.get("sectionName") or r.get("sectionId") or "",
                date=(r.get("webPublicationDate") or "")[:10],
                byline=fields.get("byline") or "",
                content_id=r.get("id") or "",
                link=r.get("webUrl") or "",
            ))
        return items

    def read(self, item: NewsItem, max_chars: int) -> str:
        params = {"show-fields": "bodyText,byline", "api-key": self._key or "test"}
        url = f"{self._base}/{item.content_id}?" + urllib.parse.urlencode(params)
        data = json.loads(self._http_get(url))
        content = (data.get("response") or {}).get("content") or {}
        fields = content.get("fields") or {}
        body = _plain(fields.get("bodyText") or "")
        if not body:
            return f"error: стаття «{item.title}» без тексту."
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "…"
        source = content.get("webUrl") or item.link
        title = content.get("webTitle") or item.title
        return f"{title}:\n{body}\nДжерело: {source}"


def _plain(text: str) -> str:
    """Collapse whitespace (Guardian ``bodyText`` is already plain text; trailText may have entities)."""
    return " ".join(str(text).split()).strip()


class NewsTools:
    """Runs ``news_search`` / ``news_read`` against a :class:`NewsProvider`, with a **per-turn id
    registry**. A fresh instance per turn → the registry is naturally per-turn (no cross-turn leak).

    ``execute(name, input)`` **always returns a string** — any failure (missing arg, unknown/off-turn id,
    provider/HTTP/decode error, empty result) degrades to an **error string** (never raises), so a news
    error degrades the reply, never breaks the turn.
    """

    def __init__(self, provider: NewsProvider, *, max_results: int = 8, max_chars: int = 3000, days: int = 7) -> None:
        self._provider = provider
        self._max_results = max(1, max_results)
        self._max_chars = max(1, max_chars)
        self._days = max(0, days)
        self._registry: dict[str, NewsItem] = {}  # n1/n2/… → item, built by this turn's news_search

    def execute(self, name: str, tool_input: dict | None) -> str:
        inp = tool_input or {}
        try:
            if name == "news_search":
                return self._search(inp)
            if name == "news_read":
                return self._read(inp)
            return f"error: unknown news tool {name!r}"
        except Exception as exc:  # noqa: BLE001 — never raise; degrade to an error string
            return f"error: {exc}"

    def _search(self, inp: dict) -> str:
        query = inp.get("query")
        topic = inp.get("topic")
        days = inp.get("days")
        days = int(days) if isinstance(days, (int, str)) and str(days).strip().isdigit() else self._days
        items = self._provider.search(
            query if isinstance(query, str) else None,
            topic if isinstance(topic, str) else None,
            days, self._max_results,
        )
        if not items:
            term = (query or topic or "новини").strip() if isinstance(query or topic, str) else "новини"
            return f"Guardian: за «{term}» нічого не знайдено."
        rows = []
        for i, item in enumerate(items, start=1):
            item.id = f"n{i}"
            self._registry[item.id] = item
            summary = f" — {item.summary}" if item.summary else ""
            section = f" [{item.section}]" if item.section else ""
            rows.append(f"  - {item.id}: {item.title}{summary}{section}")
        return "Guardian:\n" + "\n".join(rows)

    def _read(self, inp: dict) -> str:
        rid = inp.get("id")
        if not isinstance(rid, str) or not rid.strip():
            return "error: missing 'id'"
        item = self._registry.get(rid.strip())
        if item is None:  # an id not produced by THIS turn's news_search — allowlist by construction
            return f"error: невідомий id {rid!r} — спершу зроби news_search цього ходу."
        return self._provider.read(item, self._max_chars)
