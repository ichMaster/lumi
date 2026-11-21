"""v5.1 (LUMI-341): the media ledger and the tool ledger — every paid call that isn't a model call, and every tool call.

Two append-only JSONL files in the instance's data root, each written by the process that does the work (the server,
the Telegram daemons, the core's tool loop), never by the admin panel:

* ``media-ledger.jsonl`` — one line per paid call that is not a model call::

      {ts, provider, service, model, mode?, units: {chars | audio_s | images | requests, input?, output?},
       latency_ms, outcome, origin, channel}

  ``service`` is ``tts`` (her voice, ElevenLabs), ``stt`` (the window's dictation, a Telegram voice note), ``image``
  (a picture, Gemini) or ``search`` (the web lookup, one grounded Gemini call; its tokens in ``input`` / ``output``
  when the answer reports them). ``mode`` (``stream`` / ``batch``) is on a recognition only, since the two are priced
  apart.
* ``tools.jsonl`` — one line per tool call::

      {ts, tool, outcome, ms, result_chars, turn_id, session_id, origin, channel, input}

  ``input`` is the one bounded line the window's trace shows (``key=value`` pairs, cut at the trace's limit);
  ``turn_id`` is the turn's ``turn_ts`` (the stamp both of its stored messages carry), empty outside a reply.

``outcome`` is ``ok``, ``error:<ExceptionType>``, ``refused`` (media) / ``refused:<guard>`` (a tool: ``sandbox``,
``cap``, ``exists``, ``size``, ``off``) or ``not_found`` (a tool). The origin and the channel come from
:mod:`core.usage_origin` unless the writer knows them (a daemon, a worker thread that captured them).

**Never** a message text, a prompt, audio, a tool's result or a secret: a record holds names, counts and the bounded
input line only. An append never raises — a failure logs one warning (the ``cache-log`` rule): recording must never
break a turn or a voice line.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core import usage_origin

log = logging.getLogger("lumi.ledgers")

MEDIA_LEDGER = "media-ledger.jsonl"
TOOLS_LEDGER = "tools.jsonl"

SERVICES = ("tts", "stt", "image", "search")
UNIT_KEYS = ("chars", "audio_s", "images", "requests")


@dataclass(frozen=True)
class MediaRecord:
    """One paid call that is not a model call (see the module doc)."""

    ts: str
    provider: str
    service: str
    model: str
    units: dict[str, float]
    latency_ms: int | None
    outcome: str
    origin: str = ""
    channel: str = ""
    mode: str = ""  # ``stream`` / ``batch`` for a recognition; empty otherwise


@dataclass(frozen=True)
class ToolRecord:
    """One tool call (see the module doc)."""

    ts: str
    tool: str
    outcome: str
    ms: int
    result_chars: int
    turn_id: str = ""
    session_id: str = ""
    origin: str = ""
    channel: str = ""
    input: str = ""


MEDIA_FIELDS = tuple(MediaRecord.__dataclass_fields__)
TOOL_FIELDS = tuple(ToolRecord.__dataclass_fields__)


def now_ts() -> str:
    """The local time with its offset, to the second — a record's ``ts`` outside the core (the core uses its clock)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def error_outcome(exc: BaseException) -> str:
    """``error:<ExceptionType>`` — the type only: a message could quote a prompt, a query or the words heard."""
    return f"error:{type(exc).__name__}"


def media_record(*, provider: str, service: str, model: str, units: dict[str, float], latency_ms: int | None,
                 outcome: str, ts: str | None = None, origin: str | None = None, channel: str | None = None,
                 mode: str = "") -> MediaRecord:
    """A media record; the origin and the channel default to the work in progress (:mod:`core.usage_origin`)."""
    cur = usage_origin.current()
    return MediaRecord(ts=ts or now_ts(), provider=provider, service=service, model=model or "",
                       units={k: v for k, v in units.items() if v is not None}, latency_ms=latency_ms,
                       outcome=outcome, origin=cur.origin if origin is None else origin,
                       channel=cur.channel if channel is None else channel, mode=mode)


def _append(path: str | Path | None, data: dict[str, Any], what: str) -> bool:
    if path is None:
        return False
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 — recording never breaks the work it records
        log.warning("%s ledger append failed", what, exc_info=True)
        return False


def append_media(path: str | Path | None, record: MediaRecord) -> bool:
    """Append one media record (``path`` ``None`` → nothing). Never raises; ``True`` when written."""
    data = asdict(record)
    if not data["mode"]:
        del data["mode"]  # only a recognition carries it
    return _append(path, data, "media")


def append_tool(path: str | Path | None, record: ToolRecord) -> bool:
    """Append one tool record (``path`` ``None`` → nothing). Never raises; ``True`` when written."""
    return _append(path, asdict(record), "tool")


def read_records(path: str | Path) -> list[dict[str, Any]]:
    """Every well-formed line of a ledger as a dict (a malformed line skipped); a missing file → ``[]``."""
    p = Path(path)
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


# --- a tool's outcome, read from its result (the executors return strings and never raise) ---------------------
# Her tools answer in English and in Ukrainian (the recall and date tools speak her language), so both are read.
_NOT_FOUND = re.compile(r"\bunknown (?:\w+ )?tool\b|\bnot found\b|\bno such\b|\bdoes not exist\b|\bno results?\b"
                        r"|не знайдено|нічого не згадалося|немає повідомлень|невідомий id", re.IGNORECASE)
_REFUSED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("exists", re.compile(r"\balready exists\b|\bno overwrite\b", re.IGNORECASE)),
    ("sandbox", re.compile(r"\boutside\b|\bescapes?\b|\bnot allowed\b|\bsymlink\b|\babsolute path\b|\btraversal\b",
                           re.IGNORECASE)),
    ("size", re.compile(r"\btoo (?:large|big|long)\b", re.IGNORECASE)),
    ("cap", re.compile(r"\blimit reached\b|\blimit\b|\bcap\b|\bmax(?:imum)?\b|\btoo many\b|\btoo wide\b|\bbudget\b"
                       r"|завеликий|максимум", re.IGNORECASE)),
    ("off", re.compile(r"\bis off\b|\bdisabled\b|\bnot enabled\b|\bturned off\b", re.IGNORECASE)),
)
_TYPE_NAME = re.compile(r"\b([A-Z][A-Za-z]*(?:Error|Exception|Timeout))\b")
_HTTP = re.compile(r"\bHTTP (\d{3})\b")
_TIMEOUT = re.compile(r"\btimed? ?out\b|\btimeout\b", re.IGNORECASE)
_UNREACHABLE = re.compile(r"\bunreachable\b|\bconnection (?:refused|reset)\b", re.IGNORECASE)


def tool_outcome(result: Any) -> str:
    """A tool call's outcome from its result: ``ok``; ``not_found`` (an unknown tool, a missing file, no results);
    ``refused:<guard>`` (the sandbox, a cap, an existing file, a size, an off flag); else ``error:<Type>`` — the
    exception's type when the text names one, ``TimeoutError`` / ``HTTP<code>`` / ``ConnectionError`` when it says
    so, otherwise ``error:Error``. Only these words are kept: never the result's text."""
    if not isinstance(result, str):
        return "ok"  # an image block (a dict) — the tool answered
    text = result.strip()
    if text.startswith("("):  # a short notice: a cap reached, nothing found — or plain information (ok)
        if _NOT_FOUND.search(text):
            return "not_found"
        for guard, pat in _REFUSED:
            if guard in ("cap", "size") and pat.search(text):
                return f"refused:{guard}"
        return "ok"
    if not text.lower().startswith("error"):
        return "ok"
    if _NOT_FOUND.search(text):
        return "not_found"
    for guard, pat in _REFUSED:
        if pat.search(text):
            return f"refused:{guard}"
    if m := _TYPE_NAME.search(text):
        return f"error:{m.group(1)}"
    if m := _HTTP.search(text):
        return f"error:HTTP{m.group(1)}"
    if _TIMEOUT.search(text):
        return "error:TimeoutError"
    if _UNREACHABLE.search(text):
        return "error:ConnectionError"
    return "error:Error"


def result_chars(result: Any) -> int:
    """The size of a tool's result (its text length; an image block's serialized length) — never the result."""
    if isinstance(result, str):
        return len(result)
    try:
        return len(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        return 0


def one_line(text: str, limit: int) -> str:
    """``text`` flattened to one line and cut at ``limit`` characters with ``…``."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def input_line(args: dict | None, limit: int) -> str:
    """A tool call's input as the window's trace shows it: ``key=value`` pairs on one line, cut at ``limit``."""
    pairs = " ".join(f"{k}={v}" for k, v in (args or {}).items())
    return one_line(pairs, limit)


# --- the core's two paid callers, metered -----------------------------------------------------------------------
def metered_image_gen(gen: Callable[..., bytes], *, path: str | Path | None, model: str, provider: str = "google",
                      ts: Callable[[], str] | None = None,
                      timer: Callable[[], float] = time.monotonic) -> Callable[..., bytes]:
    """Wrap an ``ImageGen`` so each generation writes one ``image`` record (one picture, the model, its time and
    outcome); the prompt is never recorded. ``path`` ``None`` → the generator itself, unchanged."""
    if path is None:
        return gen

    def generate(prompt: str, **kwargs: Any) -> bytes:
        started, outcome = timer(), "ok"
        try:
            return gen(prompt, **kwargs)
        except Exception as exc:
            outcome = error_outcome(exc)
            raise
        finally:
            append_media(path, media_record(provider=provider, service="image", model=model, units={"images": 1},
                                            latency_ms=int((timer() - started) * 1000), outcome=outcome,
                                            ts=ts() if ts else None))

    return generate


def metered_search(search: Callable[..., str], *, path: str | Path | None, model: str, provider: str = "google",
                   ts: Callable[[], str] | None = None,
                   timer: Callable[[], float] = time.monotonic) -> Callable[..., str]:
    """Wrap a ``GeminiSearch`` so each web lookup writes one ``search`` record: one request, plus the tokens the
    answer reported (``search.last_usage``, when the caller keeps it). The query and the answer are never recorded.
    ``path`` ``None`` → the search itself, unchanged."""
    if path is None:
        return search

    def wrapped(query: str, **kwargs: Any) -> str:
        started, outcome = timer(), "ok"
        try:
            return search(query, **kwargs)
        except Exception as exc:
            outcome = error_outcome(exc)
            raise
        finally:
            units: dict[str, float] = {"requests": 1}
            usage = getattr(search, "last_usage", None)
            if isinstance(usage, dict):
                units.update({k: int(v) for k, v in usage.items() if k in ("input", "output") and v is not None})
            append_media(path, media_record(provider=provider, service="search", model=model, units=units,
                                            latency_ms=int((timer() - started) * 1000), outcome=outcome,
                                            ts=ts() if ts else None))

    return wrapped
