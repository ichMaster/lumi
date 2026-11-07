"""v2.7.1 — emotion in Лілі's voice: ElevenLabs audio tags (ported from matrix-agora's v5.9, Кіра's voice).

The newer ElevenLabs models perform square-bracket directions written inside the text — ``[laughs]``,
``[sighs]``, ``[whispers]`` — while the older ones (``eleven_multilingual_v2``, ``eleven_turbo_v2_5``) read
them aloud as words. When her voice model performs them, she writes one or two tags herself, inline, from a
small palette (``core/voice_tags.md`` in her prompt), and one reply goes out two ways:

* **the speech** — the reply as written, tags and all — reaches the synthesiser only (the outbox record's
  ``speech`` field → the Telegram voice note, the voicer);
* **the text** — the reply with every tag removed, nothing in its place — is what everything else sees: the
  validated ``EmotionState.reply``, the TUI, the server's payloads and pushes, the stored message, recall,
  the summaries, the Telegram caption and text. Her past lines never show a bracket.

A model that doesn't perform tags never receives one (:func:`speakable`). All of it is pure.
"""

from __future__ import annotations

import re

# measured on Кіра's voice (matrix-agora, 2026-10-25): these perform the tags; the v2 models read them aloud
TAG_MODELS = frozenset({"eleven_v3", "eleven_v3_conversational", "eleven_v4", "eleven_v4_turbo"})

# a tag: a bracket that opens with a letter of any case and holds 2–40 characters, none a digit, and is not a
# link's text — `[laughs]`, `[Sighs.]`, `[зітхає]` go; `[1]`, `arr[i]`, `[ОСТРІВ 3]`, `[тут](url)` stay
TAG_RE = re.compile(r"\[([^\W\d_][^\[\]\n\d]{1,39})\](?!\()")
_RUN_RE = re.compile(rf"[ \t]*(?:{TAG_RE.pattern}[ \t]*)+")  # a run of tags with the spaces around it
_TAG_MAX = 42  # "[" + 40 + "]" — a longer open bracket can't be a tag


def renders_tags(model: str | None) -> bool:
    """Whether the ElevenLabs ``model`` performs audio tags (rather than reading them aloud)."""
    return (model or "").strip() in TAG_MODELS


def _gap(m: re.Match) -> str:
    """What a removed run of tags leaves: one space between words; nothing at a line's edge, after an opening
    quote or bracket, or before punctuation."""
    before = m.string[m.start() - 1] if m.start() else "\n"
    after = m.string[m.end()] if m.end() < len(m.string) else "\n"
    return "" if before in "\n(«„“" or after in "\n,.!?…:;)»”" else " "


def split_tags(reply: str) -> tuple[str, str]:
    """``(text, speech)``: her words with every tag removed and the spaces tidied; the reply as written for the
    voice. A reply without tags comes back unchanged on both sides."""
    reply = reply or ""
    if not TAG_RE.search(reply):
        return reply, reply
    text = _RUN_RE.sub(_gap, reply)
    text = "\n".join(line.strip() for line in text.split("\n")).strip()
    return text, reply


def speakable(model: str | None, speech: str) -> str:
    """What the synthesiser gets: the speech as written on a model that performs tags, the words alone on any
    other — a tag is never read aloud."""
    return speech if renders_tags(model) else split_tags(speech)[0]


class VoiceTagStreamFilter:
    """The streamed prose without tags: ``feed(chunk) -> shown``; ``flush() -> rest``.

    An open ``[`` is held back until it closes — dropped when it is a tag, shown as it was when it is not — or
    until it can no longer become one (a digit, a newline, or more than 40 characters), so a half-tag never
    reaches the screen. The spacing is the stream's; the finished reply (via :func:`split_tags`) replaces it."""

    def __init__(self) -> None:
        self._held = ""
        self._closed = ""  # a complete tag-like bracket, waiting one character: `(` makes it a link's text

    def feed(self, chunk: str) -> str:
        out: list[str] = []
        for ch in chunk:
            if self._closed:
                if ch == "(":
                    out.append(self._closed)  # `[тут](url)` — a link's text, not a tag
                self._closed = ""
            if self._held:
                self._held += ch
                if ch == "]":
                    if TAG_RE.fullmatch(self._held):
                        self._closed = self._held  # a tag — unless a link follows
                    else:
                        out.append(self._held)  # `[1]`, `[ОСТРІВ 3]` — not a tag: shown
                    self._held = ""
                elif ch == "[":
                    out.append(self._held[:-1])  # a new bracket — the old one wasn't a tag
                    self._held = "["
                elif ch.isdigit() or ch == "\n" or len(self._held) > _TAG_MAX:
                    out.append(self._held)
                    self._held = ""
            elif ch == "[":
                self._held = "["
            else:
                out.append(ch)
        return "".join(out)

    def flush(self) -> str:
        rest, self._held, self._closed = self._held, "", ""  # a closed tag at the very end is dropped
        return rest
