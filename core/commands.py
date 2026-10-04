"""The command layer (v2.2, LUMI-211) — the slash commands as a core service any client renders.

Every command that reads or changes the core's state lives here once: it takes the core, the session
and its argument string and returns a :class:`CommandResult` — the text plus a **semantic kind** that
each client maps to its own look (the TUI keeps exactly the colors it always used; a web client styles it
its way). Client-bound commands (the local voice mode, image files, session lifecycle) stay in the
clients. No TUI / Rich / Textual import here — the core stays interface-independent.

Two protocols a client honors:

- **confirm** — a destructive command (``/forget``) answers with ``confirm=<question>`` and changes
  nothing; the client asks, then re-runs the same line with ``confirmed=True``.
- **turn** — a command that is really a request to Лілі (``/journal write``) answers with
  ``turn=<text>``; the client runs that text as a normal turn.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.biorhythm import format_biorhythms
from core.cycle import format_cycle
from core.llm import LLMError
from core.prompt import mark_cache_breakpoint

# The semantic kinds a client maps to a look: info = markdown · notice = a system line · ack = an
# applied change · meta = a dim diagnostic block · warning · error · toast = a transient notice.
KINDS = ("info", "notice", "ack", "meta", "warning", "error", "toast")

MEMORY_EMPTY = "_Memory is empty so far._"
MOOD_PENDING = "_Лілі ще не визначила настрій сьогодні — напиши їй, і він складеться._"
BIORHYTHM_OFF = "_Біоритми вимкнені або немає дати народження — напиши їй, щоб порахувати._"
FORGET_QUESTION = "Clear Лілі's memory about you? This can't be undone."
CLEARED_LINE = "Memory cleared (short- and long-term)."
CANCELLED_LINE = "Cancelled."
JOURNAL_WRITE_TURN = "Запиши, будь ласка, підсумок сьогоднішнього дня у щоденник."


@dataclass(frozen=True)
class CommandResult:
    """What a command produced. ``confirm``/``turn`` set → the client acts first (see the module doc)."""

    text: str
    kind: str = "info"
    confirm: str | None = None
    turn: str | None = None


def fmt_tokens(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def fmt_latency(ms: int) -> str:
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"


def tokens_line(stats: Any) -> str | None:
    """The last turn's token usage — in/out + cache + latency — or ``None`` before any call."""
    if stats is None:
        return None
    bits = [f"in {fmt_tokens(stats.input_tokens or 0)}", f"out {fmt_tokens(stats.output_tokens or 0)}"]
    if stats.cache_read_tokens:
        bits.append(f"cache {fmt_tokens(stats.cache_read_tokens)}↩")
    if stats.cache_write_tokens:
        bits.append(f"wrote {fmt_tokens(stats.cache_write_tokens)}↑")
    bits.append(fmt_latency(stats.latency_ms))
    return "[TOKENS] " + " · ".join(bits)


# --- the commands -------------------------------------------------------------------------------------
# Each: (core, session, arg, confirmed) -> CommandResult. ``arg`` is the stripped text after the name.


def _memory(core, session, arg, confirmed) -> CommandResult:
    mem = core.view_memory()
    lines: list[str] = []
    if mem.facts:
        lines.append("**What Лілі remembers about you:**")
        lines += [f"- {f}" for f in mem.facts]
    if mem.summaries:
        lines.append("**Memory of past conversations:**")
        lines += [f"- {s}" for s in mem.summaries]
    return CommandResult("\n".join(lines) if lines else MEMORY_EMPTY)


def _forget(core, session, arg, confirmed) -> CommandResult:
    if not confirmed:
        return CommandResult(FORGET_QUESTION, kind="notice", confirm=FORGET_QUESTION)
    core.clear_memory()
    return CommandResult(CLEARED_LINE, kind="ack")


def _prompt(core, session, arg, confirmed) -> CommandResult:
    p = getattr(core, "last_prompt", None)
    if not p:
        return CommandResult("No prompt yet — make a turn first.", kind="notice")
    system = mark_cache_breakpoint(p["system"], p.get("cache_prefix"))  # show the cache split
    head = ["── last turn's prompt ──"]
    if (tokens := tokens_line(core.last_stats)) is not None:
        head.append(tokens)
    parts = [*head, "", "[SYSTEM]", system, "", "[MESSAGES]"]
    parts += [f"{m['role']}: {m['content']}" for m in p["messages"]]
    return CommandResult("\n".join(parts), kind="meta")


def _latency(core, session, arg, confirmed) -> CommandResult:
    s = core.latency_summary() if hasattr(core, "latency_summary") else None
    if not s:
        return CommandResult("No latency yet — make a turn first.", kind="notice")
    last, med, n = s["last"], s["median"], s["n"]

    def _s(ms: int) -> str:
        return f"{ms / 1000:.1f}s"

    ttft = last.get("ttft_ms")
    first = f" · first symbol {_s(ttft)}" if ttft is not None else ""  # v1.4: TTFT when streaming
    lines = [
        "── turn latency (S0) ──",
        f"last turn: PRE {_s(last['pre_ms'])} · MODEL {_s(last['llm_ms'])} · "
        f"POST {_s(last['post_ms'])}  =  {_s(last['total_ms'])}  (think {last['think_chars']} chars)"
        + first,
        f"median /{n} turns: PRE {_s(med['pre_ms'])} · MODEL {_s(med['llm_ms'])} · "
        f"POST {_s(med['post_ms'])}  =  {_s(med['total_ms'])}",
    ]
    return CommandResult("\n".join(lines), kind="meta")  # a client may append its own local stages


def _style(core, session, arg, confirmed) -> CommandResult:
    if not arg:
        metas = ", ".join(core.meta_names()) or "—"
        bases = ", ".join(core.base_names())
        rec = core.recommendation or "—"
        line = (
            f"Лілі обирає стиль сама (зараз: {core.style} · рекомендація: {rec}).\n"
            f"Мега-стилі: {metas}\nБазові: {bases}\n"
            "/style <назва> — порадити · /style auto — без поради"
        )
        return CommandResult(line, kind="notice")
    if core.set_style(arg):
        rec = core.recommendation
        line = (
            f"Рекомендація стилю → {rec}. Лілі врахує (вирішує сама)."
            if rec else "Стиль → авто. Лілі обирає сама."
        )
        return CommandResult(line, kind="ack")
    names = ", ".join(core.style_names())
    return CommandResult(f"Unknown style in '{arg}'. Available: {names}", kind="error")


def _mood(core, session, arg, confirmed) -> CommandResult:
    resolution = core.mood
    return CommandResult(f"**Настрій Лілі сьогодні:**\n\n{resolution}" if resolution else MOOD_PENDING)


def _model_set(core, session, arg, confirmed) -> CommandResult:
    profiles = core.model_profiles
    if not arg:
        active = core.profile
        if not profiles:
            return CommandResult("Профілі не налаштовані.", kind="warning")
        lines = []
        for name in sorted(profiles):
            p = profiles[name]
            mark = " **← активний**" if name == active else ""
            lines.append(f"- **{name}** ({p.provider}): reply `{p.reply}` · think `{p.think}` · "
                         f"mood `{p.mood}` · housekeeping `{p.housekeeping}`{mark}")
        return CommandResult("**Профілі моделей:**\n" + "\n".join(lines) +
                             "\n\n`/model-set <назва>` — перемкнути весь стек (reply + tiers) без рестарту.")
    try:
        core.switch_profile(arg)
    except ValueError as exc:  # unknown profile → a clear, non-fatal message
        return CommandResult(str(exc), kind="warning")
    except LLMError as exc:  # missing key / unknown provider → the old stack stays in place
        return CommandResult(f"Не вдалося перемкнути профіль: {exc}", kind="error")
    p = profiles[core.profile]
    return CommandResult(f"**Профіль:** {core.profile} ({p.provider}) ✓ — reply `{p.reply}`, "
                         f"think `{p.think}`, mood `{p.mood}`, housekeeping `{p.housekeeping}`")


def _model(core, session, arg, confirmed) -> CommandResult:
    alias_list = ", ".join(sorted(core.model_aliases)) or "(none configured)"
    if not arg:
        current = f"{core.provider or '?'} / {core.model}"
        return CommandResult(f"**Двигун:** {current}\n\nАліаси: {alias_list}\n"
                             "`/model <аліас>`, `/model <повний-id>` або `/model provider:id` — перемкнути без рестарту.")
    try:
        provider, model = core.resolve_model_target(arg)
    except ValueError as exc:  # unknown alias / malformed → a clear, non-fatal message
        return CommandResult(str(exc), kind="warning")
    try:
        core.switch_model(provider, model)
    except LLMError as exc:  # missing key / unknown provider → the old engine stays in place
        return CommandResult(f"Не вдалося перемкнути двигун: {exc}", kind="error")
    return CommandResult(f"**Двигун:** {provider} / {model} ✓")


def _biorhythm(core, session, arg, confirmed) -> CommandResult:
    b, c = core.biorhythms, core.cycle
    if not b and not c:
        return CommandResult(BIORHYTHM_OFF)
    parts: list[str] = []
    if b:
        parts.append(f"**Біоритми Лілі сьогодні:**\n\n{format_biorhythms(b)}")
    if c:
        parts.append(f"**Цикл:** {format_cycle(c)}")
    return CommandResult("\n\n".join(parts))


def _closeness(core, session, arg, confirmed) -> CommandResult:
    level, name = core.closeness_status()  # only the level + its name; raw scores stay internal
    return CommandResult(f"**Близькість:** {name or f'рівень {level}'} (рівень {level} з 5)")


def _thoughts(core, session, arg, confirmed) -> CommandResult:
    if core.thoughts_show == "off":
        return CommandResult("Перегляд думок вимкнено.")
    view = core.thoughts_view()
    return CommandResult(f"**Що в мене на думці:**\n\n{view}" if view else "Поки що жодних думок.")


def _regen_summaries(core, session, arg, confirmed) -> CommandResult:
    n = core.regenerate_summaries()
    return CommandResult(f"Regenerated {n} day/week digest(s) from the kept session summaries.", kind="notice")


def _recall(core, session, arg, confirmed) -> CommandResult:
    if not core.recall_enabled:
        return CommandResult("Семантичний пошук вимкнено (увімкни `LUMI_RECALL=on`).")
    # By default `/recall` searches PAST conversations, skipping the current session's own echoes (they're
    # in the live window); `!all` / `!here` includes this conversation. `after:`/`before:` filter by date.
    include_current = False
    before: str | None = None
    after: str | None = None
    kept: list[str] = []
    for tok in arg.split():
        low = tok.lower()
        if low in ("!all", "!here"):
            include_current = True
        elif low.startswith("before:"):
            before = tok[len("before:"):]
        elif low.startswith("after:"):
            after = tok[len("after:"):]
        else:
            kept.append(tok)
    query = " ".join(kept)
    if not query:
        return CommandResult("Що згадати? `/recall <запит>` — фільтри: `!all` (і ця розмова), "
                             "`after:РРРР-ММ-ДД`, `before:РРРР-ММ-ДД` (за датою)")
    exclude = None if include_current or session is None else session.id
    moments = core.recall_moments(query, exclude_session=exclude, before=before, after=after)
    parts = ([] if exclude else ["з цією розмовою"]) \
        + ([f"від {after}"] if after else []) + ([f"до {before}"] if before else [])
    flt = f" ({', '.join(parts)})" if parts else ""
    if not moments:
        return CommandResult(f"Нічого не згадалося про «{query}»{flt}.")
    return CommandResult(f"**Згадую про «{query}»{flt}:**\n\n" + "\n\n".join(moments))


def _journal(core, session, arg, confirmed) -> CommandResult:
    if not core.journal_enabled:
        return CommandResult("Journal is off — set LUMI_JOURNAL=on.", kind="toast")
    if arg == "write":  # she writes today's summary herself — a turn, not a read
        return CommandResult(JOURNAL_WRITE_TURN, turn=JOURNAL_WRITE_TURN)
    if arg == "list":
        return CommandResult(core.journal_list())
    return CommandResult(core.journal_read(arg) if arg else core.journal_read())


def _theme(core, session, arg, confirmed) -> CommandResult:
    available = ", ".join(core.themes) or "(жодної)"
    if not arg:
        current = core.theme or "(плоский v0.7)"
        return CommandResult(f"**Тема обличчя:** {current}\nДоступні: {available}\n"
                             "`/theme <назва>` — поставити, `/theme auto` — за настроєм дня.")
    if arg.lower() == "auto":
        core.set_theme(None)
        return CommandResult("Тема обличчя: **авто** (за настроєм дня).")
    if core.set_theme(arg):
        return CommandResult(f"Тема обличчя: **{arg}**.")
    return CommandResult(f"Невідома тема «{arg}». Доступні: {available}")


Handler = Callable[[Any, Any, str, bool], CommandResult]

# name → (handler, takes_args). A no-arg command matches only its bare name (``/mood``, not ``/mood x``),
# exactly as the TUI always matched them; anything else falls through to the client as before.
COMMANDS: dict[str, tuple[Handler, bool]] = {
    "/memory": (_memory, False),
    "/forget": (_forget, False),
    "/prompt": (_prompt, False),
    "/latency": (_latency, False),
    "/style": (_style, True),
    "/mood": (_mood, False),
    "/model-set": (_model_set, True),
    "/model": (_model, True),
    "/biorhythm": (_biorhythm, False),
    "/closeness": (_closeness, False),
    "/thoughts": (_thoughts, False),
    "/regen-summaries": (_regen_summaries, False),
    "/recall": (_recall, True),
    "/journal": (_journal, True),
    "/theme": (_theme, True),
}


def run_command(core: Any, session: Any, line: str, *, confirmed: bool = False) -> CommandResult | None:
    """Run one slash-command line against the core. ``None`` → not a layer command (the client goes on)."""
    text = (line or "").strip()
    name, _, arg = text.partition(" ")
    entry = COMMANDS.get(name)
    if entry is None:
        return None
    handler, takes_args = entry
    arg = arg.strip()
    if arg and not takes_args:
        return None
    return handler(core, session, arg, confirmed)
