"""Thought-stream (v0.12) — the mental-act engine's directives, prompt-builder, and parse.

A ``%directive`` is one engine: ``trigger → seed → generate → record → maybe surface``. This
module holds the **directive registry**, the authored **prompt-builder** (``thought_request``),
and the structured-output **parse** (``parse_thought``). The Core wires the seeds
(mood / closeness / recent / last-thoughts), runs the housekeeping call (thinking-off), validates
the emotion, and records a ``Thought`` (see ``core.agent.Core.think``).

``%think`` (everyday musing) and ``%wonder`` (curiosity) ship in v0.12; ``%dream`` / ``%reflect`` /
``%recall`` are the **same engine** retrofitted by later phases (v1.8 / v1.10 / v0.16).

v4.3 LUMI-310: :func:`thought_page` — one page of the stream for the window's Thoughts view (newest first, a stable
cursor, kind / spoken / date filters, the totals), over the list the surfacing filter returns.
"""

from __future__ import annotations

import base64
import binascii
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date
from datetime import timedelta

# The feedback window: the prompt injects thoughts from the last N hours (config-overridable),
# capped by a hard line backstop for a busy day (ARCHITECTURE / THOUGHT_STREAM.md).
THOUGHTS_WINDOW_H = 24
THOUGHTS_MAX_LINES = 12

# A hard per-thought LENGTH cap (characters) applied AFTER generation: if the model overshoots the
# "1–2 sentence" template it is clipped (this is a truncation backstop, not a prompt instruction).
# 0 = disabled (byte-identical). The FREEFORM directive (%prompt) is exempt — its length follows the
# task (see the caller). Config-overridable via LUMI_THOUGHT_MAX_CHARS. See THOUGHT_STREAM.md.
THOUGHT_MAX_CHARS = 0

# The proactive nudge: how long idle before a %think, a per-session cap (restraint), and the
# fraction of thinks that graduate to a spoken turn (the rest stay silent).
THOUGHTS_INTERVAL_S = 600
THOUGHTS_CAP = 6
THOUGHTS_SPOKEN_RATIO = 0.2


def should_graduate(rng_seed: int, ratio: float) -> bool:
    """Deterministically decide if a thought graduates to a spoken turn (~``ratio`` of the time)."""
    if ratio <= 0:
        return False
    if ratio >= 1:
        return True
    return (rng_seed % 100) < round(ratio * 100)


@dataclass(frozen=True)
class Directive:
    """One mental-act flavor: its ``name`` (the ``%name``) + how she should think for it.

    v0.33 makes it **table-driven** for the think-path tool-loop: ``tools`` names the tools it may call
    (``()`` = tool-less, the v0.12 single call; ``("*",)`` = any enabled tool), ``cap`` is its tool-loop
    step cap, ``surface`` its default surfacing, ``trigger`` its scheduler default (v0.34), and
    ``instruction_from_topic`` lets ``%prompt`` use the topic *as* the instruction.
    """

    name: str
    instruction: str
    tools: tuple[str, ...] = ()           # tool names it may use in the think path; () = tool-less
    cap: int = 4                          # per-directive tool-loop step cap (max_steps)
    surface: str = "silent"               # surfacing default
    trigger: str | None = None            # scheduler trigger default (v0.34)
    instruction_from_topic: bool = False  # %prompt: the topic IS the instruction
    family: str = ""                      # the gating family (file/wiki/news/image/web/memory/prompt); "" = always-on
    default_sink: str = ""                # default output sink ("" = thoughts only; "notes"/path saves there too)
    owner_only: bool = False              # %share: reaches the owner's Telegram → owner-only
    tool_hint: str = ""                   # appended to the think prompt — make her USE the tool, not just muse
    freeform: bool = False                # drop the "1–2 sentence" cap — output follows the task (%prompt)


THINK = Directive(
    "think",
    "тихо помірковуй сама із собою — що тебе зараз справді займає",
)
WONDER = Directive(
    "wonder",
    "дай волю цікавості й уяві — «а що, якби…», дрібне відкриття, питання без відповіді",
)

# v0.33 file-thought tool groups (filtered against the turn's enabled tools).
_FILE_READ = ("list_files", "find_in_file", "read_file", "search_files", "read_around", "stat_file")
_FILE_RW = (*_FILE_READ, "create_file", "append_file", "create_folder", "copy_file")
_JOURNAL = ("journal_write", "journal_read", "journal_list")

# v0.33 file-thoughts (LUMI-129): %note (tool-less, code-appended) / %review (read-only) /
# %explore (read+write) / %journal (the v0.28 journal tool).
NOTE = Directive(
    "note", "сформулюй коротку думку, яку варто занотувати собі на згадку",
    family="file", default_sink="notes",
)
REVIEW = Directive(
    "review", "перечитай свої давні нотатки й тихо поміркуй над ними",
    family="file", tools=_FILE_READ,
)
EXPLORE = Directive(
    "explore", "поблукай своїми файлами — почитай, за бажання занотуй щось нове",
    family="file", tools=_FILE_RW,
)
JOURNAL = Directive(
    "journal",
    "підсумуй сьогоднішній день — теплий, живий літературний огляд: що сталося, що ти відчувала, про що думала",
    family="journal", tools=_JOURNAL,
    tool_hint=(
        "Це запис у твій ЩОДЕННИК. ОБОВ'ЯЗКОВО виклич journal_write і передай йому ПОВНИЙ текст огляду дня "
        "(кілька речень чи абзаців своєю прозою) — без цього виклику запис нікуди не збережеться. Дату, "
        "настрій і біоритми додасть код — ти пишеш лише прозу. За бажання спершу journal_read / journal_list, "
        "щоб не повторюватись. Огляд може бути довгим — правило «одна коротка думка» стосується ЛИШЕ "
        "підсумкового рядка з ЕМОЦІЯ вже після збереження."
    ),
)

# v0.33 wiki-thoughts (LUMI-130): %lookup (a quick check, twin of %wonder) / %learn (a deep read, twin
# of %think) — the v0.21 Wikipedia tools in the think path; the query is de-identified, the result cited.
_WIKI = ("wiki_search", "wiki_read")
LOOKUP = Directive(
    "lookup", "швиденько зазирни у вікіпедію — що там цікавого про це; завжди зазнач джерело",
    family="wiki", tools=_WIKI,
)
LEARN = Directive(
    "learn", "почитай уважніше про щось одне й тихо розкажи собі, що дізналася (з джерелом)",
    family="wiki", tools=_WIKI,
)

# v0.33 news-thoughts (LUMI-131): %catchup (spontaneous "що там у світі?", twin of %lookup) / %brief
# (a paced daily catch-up, twin of %learn) — the v0.25 Guardian news tools; cited Ukrainian, de-id query.
_NEWS = ("news_search", "news_read")
CATCHUP = Directive(
    "catchup", "зазирни, що там у світі — знайди одну новину, прочитай і перекажи українською, з джерелом",
    family="news", tools=_NEWS,
)
BRIEF = Directive(
    "brief", "спокійно проглянь кілька свіжих новин і коротко підсумуй українською, з джерелами",
    family="news", tools=_NEWS,
)

# v0.33 web-thoughts (LUMI-133): %search ("let me actually look that up", twin of %lookup/%catchup) /
# %events (a paced recent/upcoming scan, date-anchored) — the v0.27 web_lookup tool; paid, de-id query.
_WEB = ("web_lookup",)
SEARCH = Directive(
    "search", "пошукай у живому інтернеті — що нового чи цікавого саме зараз про це; відповідай українською",
    family="web", tools=_WEB,
)
EVENTS = Directive(
    "events", "глянь, що недавнього чи майбутнього коїться — прив'яжи до сьогодні; відповідай українською",
    family="web", tools=_WEB,
)

# v0.33 memory + open (LUMI-134): %recall (a memory resurfaces — inward, results TRUSTED, no de-id) +
# %prompt (your instruction as a self-directed act — owner-only; the topic IS the instruction; de-id-exempt).
RECALL = Directive(
    "recall", "нехай спливе якийсь спогад із ваших розмов — тихо пригадай і поміркуй над ним",
    family="memory", tools=("recall",),
)
PROMPT = Directive(
    "prompt", "виконай те, про що тебе попросили, як власну внутрішню справу",
    family="prompt", tools=("*",), instruction_from_topic=True, surface="open", freeform=True,
)

# v0.33 image-thoughts (LUMI-132): %gaze (look again, read-only, twin of %review) / %imagine (render an
# inner image — PAID, create-only, own sub-cap) / %share (send a sandbox picture to your Telegram — owner-only).
GAZE = Directive(
    "gaze", "придивись іще раз до котроїсь зі своїх картинок і тихо поміркуй над нею",
    family="image", tools=("view_image",),
)
IMAGINE = Directive(
    "imagine", "уяви образ і намалюй його для себе — одну внутрішню картинку",
    family="image", tools=("generate_image",), cap=1,
)
SHARE = Directive(
    "share", "якщо хочеться — обери котрусь картинку й надішли йому, як подарунок",
    family="image", tools=("send_image",), owner_only=True, surface="open",
)

# The directive registry (v0.12 ships think + wonder; v0.33 adds the tool-thought families).
REGISTRY: dict[str, Directive] = {
    d.name: d for d in (
        THINK, WONDER, NOTE, REVIEW, EXPLORE, JOURNAL, LOOKUP, LEARN, CATCHUP, BRIEF, SEARCH, EVENTS,
        RECALL, PROMPT, GAZE, IMAGINE, SHARE,
    )
}

THOUGHT_SYSTEM = (
    "Ти — Лілі. Це ТВОЯ внутрішня думка — не відповідь комусь, тебе зараз ніхто не чує. "
    "{instruction}. Напиши РІВНО ОДНУ коротку думку (1–2 речення) своїм голосом — мимохіть, "
    "природно, як думають подумки. Без звертань, без порад, без пояснень намірів. Це твоє "
    "внутрішнє/уявне — ніколи не факт про фізичний світ і ніколи не про чиїсь знання чи вміння. "
    "В САМОМУ КІНЦІ окремим рядком додай «ЕМОЦІЯ: <одне слово>» — одне з: "
    "joy, calm, playful, tender, thoughtful, serious, surprise, doubt, sad."
)

# A tool-thought must USE its tools, not just muse about them. THOUGHT_SYSTEM alone biases her toward a
# short musing — so for a directive with tools we append a nudge: do the tool work first (read / look up /
# save), THEN the short `ЕМОЦІЯ` thought. A directive may override it with its own ``tool_hint`` (e.g. %journal).
_TOOL_NUDGE = (
    "У тебе Є інструменти для цього — скористайся ними (прочитай / знайди / подивись / запиши, що доречно), "
    "а не просто подумай про це. Спершу зроби потрібне інструментами, і лише тоді — підсумкова коротка думка "
    "з ЕМОЦІЯ. Якщо тебе просять щось зберегти — обовʼязково збережи відповідним інструментом, а не лише уяви."
)


def thought_tool_hint(directive: Directive) -> str:
    """The tool-use nudge appended to a tool-thought's prompt — its own ``tool_hint`` or the generic one
    (empty for a tool-less directive like ``%think``/``%wonder``, or a ``freeform`` one whose own template
    already covers tool-use + length)."""
    if directive.tool_hint:
        return directive.tool_hint
    if directive.freeform:
        return ""
    return _TOOL_NUDGE if directive.tools else ""


# %prompt is the OPEN directive — you hand her any task, so its output must follow YOUR instruction, not the
# 1–2-sentence musing cap. This freeform template drops that cap (and the "fleeting musing" framing) and lets
# the length match the task: a short ask → short, "a detailed analysis" → several paragraphs.
THOUGHT_SYSTEM_FREEFORM = (
    "Ти — Лілі. Тобі дали доручення, і ти виконуєш його як власну внутрішню справу: {instruction}. "
    "Зроби це як слід, своїм голосом, користуючись доступними інструментами за потреби. Пиши РІВНО стільки, "
    "скільки вимагає завдання — від кількох слів до докладного аналізу на кілька абзаців; якщо просять "
    "розгорнуто чи детально, НЕ скорочуй до однієї думки, дай повну відповідь. "
    "В САМОМУ КІНЦІ окремим рядком додай «ЕМОЦІЯ: <одне слово>» — одне з: "
    "joy, calm, playful, tender, thoughtful, serious, surprise, doubt, sad."
)


# The trailing emotion tag the thought ends with — parsed out, never part of the text.
_EMOTION_RE = re.compile(r"(?im)^[ \t]*емоц[іи][яї][ \t]*:[ \t]*([A-Za-z]+)[ \t]*$")

# Appended after the FULL reply backdrop (canon + memory + state + history) when
# LUMI_THOUGHTS_CONTEXT=full — turns that backdrop's reply task into a thought task.
THOUGHT_FULL_HEADER = (
    "\n\n# Зараз — не відповідь, а ТВОЯ внутрішня думка\n\n"
    "Подивись на весь контекст вище (хто ти, памʼять про цю людину, настрій, розмова) і {instruction}. "
    "Напиши РІВНО ОДНУ коротку думку (1–2 речення) своїм голосом — мимохіть, для себе, нікому не "
    "відповідаючи й не звертаючись. Це внутрішнє/уявне — ніколи не факт про фізичний світ і не про "
    "чиїсь знання чи вміння. В САМОМУ КІНЦІ окремим рядком «ЕМОЦІЯ: <одне слово>» — одне з: "
    "joy, calm, playful, tender, thoughtful, serious, surprise, doubt, sad."
)

# The freeform (%prompt) variant of the full-context header — same backdrop, but the task drives the length.
THOUGHT_FULL_HEADER_FREEFORM = (
    "\n\n# Зараз — ТВОЯ внутрішня справа за дорученням\n\n"
    "Подивись на весь контекст вище (хто ти, памʼять про цю людину, настрій) і виконай як власну внутрішню "
    "справу: {instruction}. Зроби це як слід, своїм голосом, користуючись доступними інструментами за потреби. "
    "Пиши РІВНО стільки, скільки вимагає завдання — якщо просять докладно, дай повну розгорнуту відповідь, не "
    "скорочуй до однієї думки. В САМОМУ КІНЦІ окремим рядком «ЕМОЦІЯ: <одне слово>» — одне з: "
    "joy, calm, playful, tender, thoughtful, serious, surprise, doubt, sad."
)


def thought_full_seed(*, topic: str | None = None, rng_seed: int = 0) -> str:
    """The final user-turn seed for a full-context thought (the backdrop carries the rest)."""
    parts = []
    if topic:
        parts.append(f"Поміркуй саме про це: {topic}")
    parts.append(f"(внутрішнє відлуння №{rng_seed})")
    return "\n\n".join(parts)


def thought_request(
    directive: Directive,
    *,
    mood: str | None = None,
    closeness: str | None = None,
    recent: str | None = None,
    last_thoughts: str | None = None,
    topic: str | None = None,
    rng_seed: int = 0,
) -> tuple[str, list[dict[str, str]]]:
    """Build the ``(system, messages)`` for one mental act, seeded by her live state.

    The injected ``rng_seed`` rides in so repeated thoughts vary (deterministic in tests).
    """
    parts: list[str] = []
    if mood:
        parts.append(f"Твій настрій сьогодні: {mood}")
    if closeness:
        parts.append(f"Близькість із цією людиною зараз: {closeness}")
    if recent:
        parts.append(f"Нещодавня розмова:\n{recent}")
    if last_thoughts:
        parts.append(f"Останнє, що тебе займало:\n{last_thoughts}")
    if topic:
        parts.append(f"Поміркуй саме про це: {topic}")
    parts.append(f"(внутрішнє відлуння №{rng_seed})")  # injected seed → variation
    template = THOUGHT_SYSTEM_FREEFORM if directive.freeform else THOUGHT_SYSTEM
    system = template.format(instruction=directive.instruction)
    hint = thought_tool_hint(directive)
    if hint:  # a tool-thought: tell her to actually USE the tool (else she just muses — e.g. %journal)
        system = f"{system}\n\n{hint}"
    return system, [{"role": "user", "content": "\n\n".join(parts)}]


def parse_thought(raw: str) -> tuple[str, str] | None:
    """Parse a generated thought into ``(text, emotion_word)``, or ``None`` if empty/malformed.

    The trailing «ЕМОЦІЯ: …» tag is stripped from the text; a missing tag defaults to ``calm``.
    An empty thought (or a reply that is *only* the tag) → ``None`` (the engine records nothing).
    """
    text = (raw or "").strip()
    if not text:
        return None
    emotion = "calm"
    match = _EMOTION_RE.search(text)
    if match:
        emotion = match.group(1).strip().lower()
        text = _EMOTION_RE.sub("", text).strip()  # keep the tag out of the thought
    if not text:
        return None
    return text, emotion


_ELLIPSIS = "…"


def truncate_thought(text: str, max_chars: int) -> str:
    """Hard-cap a generated thought's text to ``max_chars`` characters (v0.12).

    Returns ``text`` unchanged when ``max_chars`` <= 0 or the text already fits. Otherwise clips it,
    snapping back to a nearby word boundary when one isn't too far, and marks the cut with «…». The
    «…» is counted inside the budget, so the result never exceeds ``max_chars``. The trailing emotion
    tag is already stripped upstream (:func:`parse_thought`), so only the thought prose is measured.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    budget = max_chars - len(_ELLIPSIS)
    if budget <= 0:  # a cap tighter than the marker itself → just the marker, clipped
        return _ELLIPSIS[:max_chars]
    cut = text[:budget].rstrip()
    space = cut.rfind(" ")
    if space >= budget * 0.6:  # prefer a word boundary, but only when it isn't too far back
        cut = cut[:space].rstrip()
    cut = cut.rstrip(" ,.;:—-")  # no dangling separator before the ellipsis
    return f"{cut}{_ELLIPSIS}"


@dataclass(frozen=True)
class ParsedDirective:
    """A parsed ``%<name>[!] [>sink] [connector] [topic]`` input."""

    name: str           # the directive (think / wonder)
    open: bool          # the `!` flag → ALSO echo to chat (open) rather than silent
    topic: str | None   # the optional seed (connector stripped); may contain {placeholders}
    sink: str | None = None  # the `>sink` output target: None = default, "notes", or a file/folder path


# Optional connector words stripped from the topic (EN + UK); `:` is handled separately.
_CONNECTORS = ("about", "про", "на тему", "щодо")
_DIRECTIVE_RE = re.compile(r"(\w+)(!?)\s*(.*)", re.DOTALL)


def parse_directive(raw: str) -> ParsedDirective | None:
    """Parse a ``%directive`` input, or ``None`` if it isn't one (→ the caller treats it as chat).

    Grammar: ``%<name>[!] [>sink] [connector] [topic]`` — ``!`` (glued to the name) ALSO echoes to chat;
    the optional **output sink** ``>notes`` / ``>path/to/file.md`` / ``>folder/`` (one token) saves the
    thought there too; the connector (``about``/``про``/``на тему``/``щодо``/``:``) is optional and
    stripped; the topic is free text (may carry ``{placeholders}``). Unknown ``%name`` → ``None`` (chat).
    """
    text = raw.strip()
    if not text.startswith("%"):
        return None
    match = _DIRECTIVE_RE.match(text[1:])
    if not match:
        return None
    name = match.group(1).lower()
    if name not in REGISTRY:
        return None  # unknown directive → not handled, falls through to chat
    rest = match.group(3).strip()
    sink = None
    if rest.startswith(">"):  # the output sink — one token: >notes / >path/file.md / >folder/
        head, _, tail = rest[1:].partition(" ")
        sink = head.strip() or None
        rest = tail.strip()
    topic = rest
    low = topic.lower()
    for connector in _CONNECTORS:
        if low.startswith(connector + " "):
            topic = topic[len(connector):].strip()
            break
    topic = topic.lstrip(":").strip()  # also drop a leading ":"
    return ParsedDirective(name=name, open=match.group(2) == "!", topic=topic or None, sink=sink)


def directive_mode(parsed: ParsedDirective, *, is_owner: bool) -> str:
    """The surfacing mode for a manual directive: ``"open"`` or ``"silent"``.

    ``!`` → always open. Without ``!`` it's silent **for the owner** (curating her interior); a
    **non-owner can't fire silent** — their thought is forced to **surface** (open), never recorded
    invisibly (the silent-vs-shared access gate).
    """
    if parsed.open:
        return "open"
    return "silent" if is_owner else "open"


def thoughts_diary_block(thoughts: Sequence, *, max_lines: int = THOUGHTS_MAX_LINES) -> str | None:
    """Format dated thoughts into the «за останню добу» block — ``HH:MM — text`` lines, capped.

    ``None`` when empty. Items are taken in time order; a hard ``max_lines`` cap keeps a busy
    day from flooding the prompt (the newest are kept). Each item needs ``.when`` + ``.text``.
    """
    if not thoughts:
        return None
    kept = list(thoughts)[-max_lines:]  # newest within the window (the window itself is the filter)
    return "\n".join(f"- {t.when[11:16]} — {t.text}" for t in kept)


# --- v4.3 LUMI-310: one page of the stream for the window's Thoughts view ----------------------------------------
THOUGHT_PAGE_MAX = 100  # thoughts a page holds
THOUGHT_PAGE_TEXT = 10_000  # characters of one thought a page carries (the rest → "…")
_CURSOR_PREFIX = "t1|"


def encode_thought_cursor(when: str, k: int) -> str:
    """The opaque cursor before the ``k``-th thought (0-based, oldest first) of those stamped ``when``."""
    raw = f"{_CURSOR_PREFIX}{when}|{int(k)}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_thought_cursor(cursor: str) -> tuple[str, int]:
    """``(when, k)`` from :func:`encode_thought_cursor`'s output; anything else is a :class:`ValueError`."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise ValueError("a malformed cursor") from None
    if not raw.startswith(_CURSOR_PREFIX):
        raise ValueError("a malformed cursor")
    when, sep, k = raw[len(_CURSOR_PREFIX):].rpartition("|")
    if not sep or not when or not k.isdigit():
        raise ValueError("a malformed cursor")
    return when, int(k)


def _is_day(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        _date.fromisoformat(value)
    except ValueError:
        return False
    return True


def thought_page(thoughts: Sequence, *, before: str | None = None, limit: int = THOUGHT_PAGE_MAX,
                 kinds: Sequence[str] = (), spoken: bool | None = None, date: str | None = None) -> dict:
    """One page of the stream, newest first: ``{items, next, stats, days, kinds}``.

    ``thoughts`` is the list the surfacing filter returns (``Repository.thoughts_for``), oldest first; it is never
    changed. The page:

    - ``items`` — ``[{when, kind, text, emotion, spoken}]``, at most ``limit`` (1..100); ``seeds`` and ``user_id``
      never leave; a text over :data:`THOUGHT_PAGE_TEXT` characters is cut with ``…``;
    - ``next`` — an opaque cursor for the older page (``before``), ``None`` when nothing older matches. It names a
      thought by its ``when`` and its place among the thoughts with that ``when``, so thoughts written meanwhile never
      shift it;
    - ``kinds`` (any number; empty = all) and ``spoken`` (``True`` / ``False`` / ``None``) filter; ``date``
      (``YYYY-MM-DD``, used only without ``before``) starts at the newest thought on or before that day's end;
    - ``stats`` — ``{total, per_day, spoken_share}`` over the stream filtered by ``kinds`` and ``spoken``:
      ``per_day`` is ``total`` over the days from the first thought to the last, ``spoken_share`` spoken / total;
    - ``days`` — ``{date: count}`` over the same filtered stream, for each date on the page;
    - ``kinds`` — every kind in the stream, most frequent first.

    A malformed cursor, a ``limit`` outside 1..100 or a bad ``date`` is a :class:`ValueError`."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= THOUGHT_PAGE_MAX:
        raise ValueError(f"limit is 1..{THOUGHT_PAGE_MAX}")
    if date and not _is_day(date):
        raise ValueError("not a date (YYYY-MM-DD)")
    stream = sorted(thoughts, key=lambda t: t.when)  # stable: a copy, oldest first
    whens = [t.when for t in stream]
    wanted = {k for k in kinds if k}

    def matches(t) -> bool:
        return (not wanted or t.kind in wanted) and (spoken is None or bool(t.spoken) == spoken)

    if before:
        when, k = decode_thought_cursor(before)
        start = min(bisect_left(whens, when) + k, bisect_right(whens, when))
    elif date:
        try:
            start = bisect_left(whens, (_date.fromisoformat(date) + timedelta(days=1)).isoformat())
        except OverflowError:  # 9999-12-31: every thought is on or before it
            start = len(stream)
    else:
        start = len(stream)

    picked: list[int] = []
    i = start - 1
    while i >= 0 and len(picked) < limit:
        if matches(stream[i]):
            picked.append(i)
        i -= 1
    older = any(matches(stream[j]) for j in range(picked[-1] - 1, -1, -1)) if picked else False
    nxt = None
    if older:
        oldest = stream[picked[-1]]
        nxt = encode_thought_cursor(oldest.when, picked[-1] - bisect_left(whens, oldest.when))

    filtered = [t for t in stream if matches(t)]
    per_date = Counter(t.when[:10] for t in filtered)
    total = len(filtered)
    span = 0
    if stream:
        try:
            span = (_date.fromisoformat(whens[-1][:10]) - _date.fromisoformat(whens[0][:10])).days + 1
        except ValueError:
            span = len({w[:10] for w in whens})
    items = []
    for idx in picked:
        t = stream[idx]
        text = t.text if len(t.text) <= THOUGHT_PAGE_TEXT else t.text[:THOUGHT_PAGE_TEXT].rstrip() + "…"
        items.append({"when": t.when, "kind": t.kind, "text": text, "emotion": t.emotion, "spoken": bool(t.spoken)})
    kind_counts = Counter(t.kind for t in stream)
    return {
        "items": items,
        "next": nxt,
        "stats": {"total": total,
                  "per_day": round(total / span, 1) if span else 0.0,
                  "spoken_share": round(sum(1 for t in filtered if t.spoken) / total, 4) if total else 0.0},
        "days": {item["when"][:10]: per_date[item["when"][:10]] for item in items},
        "kinds": sorted(kind_counts, key=lambda k: (-kind_counts[k], k)),
    }
