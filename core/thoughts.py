"""Thought-stream (v0.12) — the mental-act engine's directives, prompt-builder, and parse.

A ``%directive`` is one engine: ``trigger → seed → generate → record → maybe surface``. This
module holds the **directive registry**, the authored **prompt-builder** (``thought_request``),
and the structured-output **parse** (``parse_thought``). The Core wires the seeds
(mood / closeness / recent / last-thoughts), runs the housekeeping call (thinking-off), validates
the emotion, and records a ``Thought`` (see ``core.agent.Core.think``).

``%think`` (everyday musing) and ``%wonder`` (curiosity) ship in v0.12; ``%dream`` / ``%reflect`` /
``%recall`` are the **same engine** retrofitted by later phases (v1.7 / v1.9 / v0.16).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# The feedback window: the prompt injects thoughts from the last N hours (config-overridable),
# capped by a hard line backstop for a busy day (ARCHITECTURE / THOUGHT_STREAM.md).
THOUGHTS_WINDOW_H = 24
THOUGHTS_MAX_LINES = 12

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
# save), THEN the short ЕМОЦІЯ thought. A directive may override it with its own ``tool_hint`` (e.g. %journal).
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
