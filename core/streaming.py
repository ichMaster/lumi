"""v1.4 (LUMI-188) — streaming helpers.

Two pure pieces, no I/O, so they unit-test without a network or a model:

* :func:`decode_json_string_value` — pull the *current* value of a string field (``reply``) out of a
  **partial** JSON object. The structured reply's prose lives inside a tool-call / ``responseSchema``
  JSON, so streaming it means decoding the growing ``"reply"`` field from the bytes seen so far
  (Anthropic ``input_json_delta`` accumulates the tool input; Gemini SSE accumulates the answer text).

* :class:`StreamTagFilter` — feed raw reply-field chunks, get back the **shown** prose incrementally:
  ``<think>…</think>`` content is routed to the think-box (never shown); ``<emotion>``/``<intent>``/
  ``<style>`` markers+content are hidden (the core parses them from the full text); and a partial
  ``<…`` at a chunk boundary is held back so a **half-tag never leaks**. Mirrors the batch semantics of
  :func:`core.prompt.split_reasoning` for the common leading-think case (assembled shown == reply).
"""

from __future__ import annotations

import re

_JSON_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


def decode_json_string_value(text: str, key: str = "reply") -> str:
    """Return the decoded value of the string field ``key`` in the (possibly partial) JSON ``text``.

    Decodes as much as is **unambiguously** complete — a dangling ``\\`` or half ``\\uXXXX`` at the end
    of the buffer stops decoding *before* it (so a partial escape never emits a wrong char). Returns
    ``""`` when the field/opening-quote hasn't been seen yet. Not a full JSON parser — it scans a single
    string value, which is all the streaming path needs.
    """
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"', text)
    if not m:
        return ""
    i, n = m.end(), len(text)
    out: list[str] = []
    while i < n:
        c = text[i]
        if c == '"':
            break  # unescaped closing quote → end of the value
        if c == "\\":
            if i + 1 >= n:
                break  # dangling backslash — the escape isn't complete yet
            e = text[i + 1]
            if e in _JSON_ESCAPES:
                out.append(_JSON_ESCAPES[e])
                i += 2
                continue
            if e == "u":
                if i + 6 > n:
                    break  # incomplete \uXXXX
                try:
                    out.append(chr(int(text[i + 2:i + 6], 16)))
                except ValueError:
                    break
                i += 6
                continue
            break  # unknown escape — stop conservatively
        out.append(c)
        i += 1
    return "".join(out)


# Recognized inline tags. `think` (+ t_think/thinking variants) routes to the think-box; emotion/intent/
# style are parsed separately by the core, so their markers+content are hidden from the shown stream.
# Anything else ('<3', '<code>') is shown literally once its '>' arrives.
_TAG_RE = re.compile(r"</?([A-Za-z][\w-]*)\b[^>]*>")
_THINK_RE = re.compile(r"(?:t[_-]?)?think(?:ing)?$", re.IGNORECASE)
_DROP_NAMES = {"emotion", "intent", "style"}


def _classify(tag: str) -> tuple[str, str]:
    """(kind, name) for a complete ``<…>`` tag. kind ∈ open-think/close-think/open-drop/close-drop/other."""
    m = _TAG_RE.match(tag)
    if not m:
        return "other", ""
    name = m.group(1)
    closing = tag[1] == "/"
    if _THINK_RE.match(name):
        return ("close-think" if closing else "open-think"), name
    if name.lower() in _DROP_NAMES:
        return ("close-drop" if closing else "open-drop"), name
    return "other", name


def _partial_tag(buf: str) -> bool:
    """True when ``buf`` starts with ``<`` and *could* still grow into a tag — so we hold it back rather
    than leak a half-tag. ``<`` / ``</`` / ``<letter…`` are held; ``<3`` / ``< `` are not (emit the ``<``)."""
    return buf == "<" or buf == "</" or bool(re.match(r"</?[A-Za-z]", buf))


class StreamTagFilter:
    """Stateful filter: ``feed(chunk) -> shown_delta``; ``flush() -> shown_remainder``.

    ``think`` accumulates the routed ``<think>`` reasoning. In ``show`` mode text is emitted as prose;
    ``think``/``drop`` modes consume until the matching close (routing / dropping the inner content). A
    ``<…`` with no ``>`` yet is held (``_partial_tag``) so a half-tag never reaches the caller.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._mode = "show"  # "show" | "think" | "drop"
        self.think = ""

    def _route(self, text: str) -> None:
        if self._mode == "think" and text:
            self.think += text

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out: list[str] = []
        while self._buf:
            lt = self._buf.find("<")
            if self._mode == "show":
                if lt == -1:
                    out.append(self._buf)
                    self._buf = ""
                    break
                if lt > 0:
                    out.append(self._buf[:lt])
                    self._buf = self._buf[lt:]
                gt = self._buf.find(">")
                if gt == -1:
                    if _partial_tag(self._buf):
                        break  # hold — might still be a tag
                    out.append("<")
                    self._buf = self._buf[1:]
                    continue
                tag = self._buf[:gt + 1]
                kind, _ = _classify(tag)
                self._buf = self._buf[gt + 1:]
                if kind == "open-think":
                    self._mode = "think"
                elif kind == "open-drop":
                    self._mode = "drop"
                elif kind == "other":
                    out.append(tag)  # a real non-ours tag → show literally
                # close-think/close-drop while already in show → a stray close: drop it
            else:  # think / drop — consume inner content up to the matching close
                if lt == -1:
                    self._route(self._buf)
                    self._buf = ""
                    break
                if lt > 0:
                    self._route(self._buf[:lt])
                    self._buf = self._buf[lt:]
                gt = self._buf.find(">")
                if gt == -1:
                    if _partial_tag(self._buf):
                        break
                    self._route("<")
                    self._buf = self._buf[1:]
                    continue
                tag = self._buf[:gt + 1]
                kind, _ = _classify(tag)
                self._buf = self._buf[gt + 1:]
                closes = (self._mode == "think" and kind == "close-think") or (
                    self._mode == "drop" and kind == "close-drop"
                )
                if closes:
                    self._mode = "show"
                else:
                    self._route(tag)  # a nested/other tag inside → inner content
        return "".join(out)

    def flush(self) -> str:
        """Emit any safe remainder at end-of-stream (a held partial that never completed is dropped —
        an unterminated ``<think`` is treated as reasoning; anything else is shown)."""
        if not self._buf:
            return ""
        rest, self._buf = self._buf, ""
        if self._mode != "show":
            self._route(rest)
            return ""
        # In show mode a leftover partial tag that never closed: if it looks like a tag, drop it; else show.
        if _partial_tag(rest) and "<" in rest and ">" not in rest:
            return ""
        return rest
