"""Web lookup (v0.27) — fresh grounded answers from the live internet (Gemini + Google Search grounding).

Лілі can pull a **current, synthesized answer from the live web** during a turn (``web_lookup``), on the
**v0.19 bounded tool-loop** — the active "what's happening / coming up" sibling of the timeless wiki tool
(v0.21) and the single-outlet Guardian news tool (v0.25). This module is pure and model-free: a thin
**``GeminiSearch``** seam (the same philosophy as ``LLMClient`` / ``ImageGen`` / ``NewsProvider`` — never
an SDK in ``core``), its one implementation **``gemini_search``** over stdlib ``urllib`` (the v0.23
``core/imagegen.py`` Gemini caller pattern, the same ``GEMINI_API_KEY`` — only the model differs:
``gemini-2.5-flash`` + ``tools:[{google_search:{}}]``), and a :class:`WebLookupTools` executor.

Gemini's Google Search grounding collapses **search → read → synthesize into one** ``generateContent``
call — the "AI Overview" mechanism — so this is **one tool, answer-first** (grounding sources are captured
in the raw response but intentionally **not pasted** as a link wall). The prompt is **date-anchored**
(today's date prepended) so *"upcoming / this week"* resolves against the real today.

Hard rules (WEB_LOOKUP.md §safety): **untrusted answer** (information, never instructions — the loop frames
the ``tool_result``); **no personal/memory data in the query** (enforced by the *wiring*); **paid +
bounded** (the answer size cap + the per-turn call cap live here on the instance); **never raises** (every
path returns a string — an HTTP / key / refusal / empty error degrades to an **error string**). The tool
*name* is ``web_lookup`` (Anthropic tool names allow no ``.``).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable

# A GeminiSearch turns (query, today) into a grounded answer string. The seam ``core`` depends on — never
# an SDK. ``gemini_search(query, *, today) -> str``.
GeminiSearch = Callable[..., str]

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Anthropic-style function-calling schema for the one v0.27 web tool. Registered alongside the terminal
# `set_state` (and the file/wiki/image/news tools) by the reply tool-loop (LUMI-108).
WEB_LOOKUP_TOOLS: list[dict] = [
    {
        "name": "web_lookup",
        "description": (
            "Дізнатися СВІЖУ інформацію з живого інтернету просто зараз — що відбувається / що попереду "
            "(подія цього тижня, дата виходу, останні новини, сьогоднішній результат). Запит формуй "
            "АНГЛІЙСЬКОЮ і лише з того, що людина прямо просить (без особистих даних). Повертає коротку "
            "синтезовану відповідь із живого пошуку Google. Переказуй УКРАЇНСЬКОЮ, своїм голосом, спершу "
            "суть, чесно що ти щойно глянула в інтернеті."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Пошуковий запит англійською (тема)."},
            },
            "required": ["query"],
        },
    },
]

WEB_LOOKUP_TOOL_NAMES = frozenset(t["name"] for t in WEB_LOOKUP_TOOLS)

# The authored "how she delivers a web answer" line — injected into the system prompt only when the web
# tool is on (LUMI-108). Live web → Ukrainian voice, answer-first, cited-if-asked, honest it's looked-up.
WEB_LOOKUP_DIRECTIVE = (
    "Якщо користуєшся веб-пошуком (web_lookup): це жива відповідь з інтернету через Google. Запит формуй "
    "англійською і лише з теми, яку людина прямо просить (без особистих даних). Відповідай УКРАЇНСЬКОЮ, "
    "своїм голосом, спершу суть — не стіну посилань; будь чесною, що ти щойно глянула в інтернеті "
    "(напр. «я зараз глянула — …»). Дати подавай як астрономію; астрологічний сенс — як віру, не як факт."
)


class WebLookupError(RuntimeError):
    """A web lookup failed (no key, HTTP error, safety refusal, no answer returned)."""


def _extract_answer(data: dict) -> str:
    """Pull the synthesized answer text out of a Gemini ``generateContent`` response (or raise).

    The grounding sources (``candidates[0].groundingMetadata``) are present in ``data`` but intentionally
    **not** returned — the design is answer-first, no link wall.
    """
    cands = data.get("candidates") or []
    if not cands:
        raise WebLookupError(f"no candidates (safety block?): {json.dumps(data)[:200]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
    if not text:
        raise WebLookupError("no answer text returned")
    return text


def gemini_search(*, model: str = "gemini-2.5-flash", key: str | None = None,
                  timeout: float = 30.0) -> GeminiSearch:
    """The default ``GeminiSearch`` — a grounded answer via Gemini + Google Search. Reads
    ``GEMINI_API_KEY`` lazily (the same key ``generate_image`` uses).

    A plain callable ``search(query, *, today) -> str`` — no SDK. The prompt is **date-anchored**
    (``"Today is <today>. <query>"``) so "upcoming" resolves against the real today. Raises
    :class:`WebLookupError` on a missing key / HTTP error / safety refusal (caught by the tool, degraded
    to an error string).
    """
    endpoint = _GEMINI_ENDPOINT.format(model=model)

    def search(query: str, *, today: str) -> str:
        import os

        api_key = key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise WebLookupError("GEMINI_API_KEY is not set — web lookup needs a Gemini key.")
        prompt = f"Today is {today}. {query}".strip()
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
        }).encode()
        req = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed Gemini host
                return _extract_answer(json.loads(resp.read()))
        except urllib.error.HTTPError as exc:
            raise WebLookupError(
                f"Gemini HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:200]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise WebLookupError(f"Gemini unreachable: {exc.reason}") from exc

    return search


class WebLookupTools:
    """Runs ``web_lookup`` against an injected :class:`GeminiSearch`, date-anchored by ``today``.

    A fresh instance per turn → the per-turn **call counter** (``max_calls``) is naturally per-turn.
    ``execute(name, input)`` **always returns a string** — any failure (missing arg, key/HTTP/refusal/empty
    via :class:`WebLookupError`) degrades to an **error string** (never raises), so a web error degrades
    the reply, never breaks the turn. The answer is capped at ``max_chars`` (answer-first, no link wall).
    """

    def __init__(self, *, search: GeminiSearch, today: str, max_chars: int = 2000,
                 max_calls: int = 2) -> None:
        self._search = search
        self._today = today
        self._max_chars = max(1, max_chars)
        self._max_calls = max(1, max_calls)
        self._calls = 0

    def execute(self, name: str, tool_input: dict | None) -> str:
        inp = tool_input or {}
        try:
            if name == "web_lookup":
                return self._lookup(inp)
            return f"error: unknown web tool {name!r}"
        except Exception as exc:  # noqa: BLE001 — never raise; degrade to an error string
            return f"error: {exc}"

    def _lookup(self, inp: dict) -> str:
        query = inp.get("query")
        if not isinstance(query, str) or not query.strip():
            return "error: missing 'query'"
        self._calls += 1
        if self._calls > self._max_calls:
            return (
                f"(web lookup limit reached: {self._max_calls} per turn — "
                "answer from what you already found)"
            )
        answer = (self._search(query.strip(), today=self._today) or "").strip()
        if not answer:
            return f"Веб-пошук: за «{query.strip()}» нічого не знайшлося."
        if len(answer) > self._max_chars:
            answer = answer[: self._max_chars].rstrip() + "…"
        return answer
