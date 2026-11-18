"""Journal tool (v0.28) — Лілі's day-summary diary (``journal_write`` / ``journal_read`` / ``journal_list``).

She writes a literary **summary of the day** in her own voice (``journal_write``) and **rereads previous
days by date** (``journal_read`` / ``journal_list``), on the **v0.19 bounded tool-loop**. This module is
pure and model-free: it defines the three tool schemas and a :class:`JournalTools` executor over one
**per-user sandbox root**, reusing the v0.19 ``safe_path`` guard + the v0.20 **non-destructive**
create/append rule.

**She decides the prose; the metadata is code-owned.** The header — date + mood + biorhythms + astrology
forecast (the ``stamp``) — is composed by the *wiring* (LUMI-111) from the day's ``MoodState`` + biorhythms
+ the v0.4 clock and **passed in here**; the model never writes it (the v0.8 "code, not model" merge), so
the header is honest and matches ``/mood`` + ``/biorhythm``. ``journal_write``'s only tool arg is ``text``.

Hard rules (JOURNAL.md §safety): **sandboxed + per-user** (``safe_path``; the path is **code-fixed** from
the clock — the model can't aim it); **non-destructive** (the day's **first** write ``create``s the file —
header + a ``## HH:MM`` section, a **later same-day** write **appends** another ``## HH:MM`` section — no
overwrite, no delete); **never raises**
(every path returns a string). Tool *names* are ``journal_write`` / ``journal_read`` / ``journal_list``
(Anthropic tool names allow no ``.``). The on-disk file is ``<date>.md`` under a **dedicated per-user
journal root** (``.lumi/journal/<user_id>/``) — **outside the file-tool sandbox**, so only the journal tool
ever writes there (the raw file tools structurally cannot reach it).

**The window's read side (v4.3 LUMI-309).** :func:`list_days`, :func:`read_day` and :func:`parse_entry` read a user's
journal root for the window's Journal view: the days newest first, and one day parsed into the code-owned stamp
(mood, biorhythms, forecast) and its ``## HH:MM`` sections. They never write, and they take the v4.1
:mod:`core.browse` errors (:class:`~core.browse.BadPath` / :class:`~core.browse.NotFound` /
:class:`~core.browse.TooLarge`) so the server maps them the same way. The journal is the owner's by rule
(:func:`core.user.may_read_journal`); that check is the server's.
"""
from __future__ import annotations

import os
import re
import stat
from datetime import date as _date
from pathlib import Path

from core.browse import BadPath, NotFound, TooLarge, _inside, _resolve
from core.files import safe_path

# Anthropic-style function-calling schemas for the three journal tools. Registered alongside the terminal
# `set_state` (and the file/wiki/news/web/image tools) by the reply tool-loop (LUMI-111).
JOURNAL_TOOLS: list[dict] = [
    {
        "name": "journal_write",
        "description": (
            "Записати у щоденник СВІЙ підсумок сьогоднішнього дня — літературно, від першої особи, своїм "
            "голосом (тільки твоя проза; дату/настрій/біоритми/прогноз додасть код, не пиши їх сама). "
            "Пиши лише коли день був вартісним. Перший запис дня створює файл; пізніший — додається."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Твоя проза — підсумок дня, твоїми словами."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "journal_read",
        "description": (
            "Перечитати запис щоденника за датою (YYYY-MM-DD). Без дати — найсвіжіший запис. "
            "Повертає текст запису (твоя проза + заголовок із настроєм/біоритмами/прогнозом)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Дата запису YYYY-MM-DD (необовʼязково)."},
            },
            "required": [],
        },
    },
    {
        "name": "journal_list",
        "description": "Перелік дат, за які є записи щоденника (найновіші першими).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

JOURNAL_TOOL_NAMES = frozenset(t["name"] for t in JOURNAL_TOOLS)

# The authored "how she keeps her diary" line — injected into the system prompt only when the journal tool
# is on (LUMI-111). Her intimate literary voice; honest it's inner; writes only when the day was worthwhile.
JOURNAL_DIRECTIVE = (
    "Якщо користуєшся щоденником (journal_write): це твій особистий літературний щоденник, не звіт. Пиши "
    "від першої особи, образно, чесно про власні почуття — своїм голосом. Записуй лише коли день справді "
    "щось додав (не вигадуй «сьогодні нічого»). Це твоє ВНУТРІШНЄ життя, ніколи не факт про фізичний світ. "
    "Дату, настрій, біоритми і прогноз додасть код — ти пишеш тільки прозу. Можеш перечитувати минулі дні "
    "(journal_read / journal_list)."
)


class JournalTools:
    """Runs ``journal_write`` / ``journal_read`` / ``journal_list`` over one per-user sandbox ``root``.

    Constructed **per turn** with the day's ``date`` (ISO — the filename + the ``# `` header), ``time``
    (``HH:MM`` — the append-section header), and the **code-owned ``stamp``** (the mood/biorhythm/forecast
    blockquote, composed by the wiring). ``execute(name, input)`` **always returns a string** — any failure
    (missing ``text``, bad/missing date, traversal, I/O) degrades to an **error string**, never raises.
    """

    def __init__(self, root: str | Path, *, date: str, time: str = "", stamp: str = "",
                 subdir: str = "", max_chars: int = 4000) -> None:
        self._root = Path(root)            # the per-user journal root (entries live directly under it)
        self._date = date
        self._time = time
        self._stamp = stamp.strip()
        self._subdir = subdir.strip("/")   # "" → entries directly under root (the default)
        self._max_chars = max(1, max_chars)

    def execute(self, name: str, tool_input: dict | None) -> str:
        inp = tool_input or {}
        try:
            if name == "journal_write":
                return self._write(inp)
            if name == "journal_read":
                return self._read(inp)
            if name == "journal_list":
                return self._list()
            return f"error: unknown journal tool {name!r}"
        except Exception as exc:  # noqa: BLE001 — never raise; degrade to an error string (incl. safe_path)
            return f"error: {exc}"

    # --- helpers ---------------------------------------------------------------------------------
    def _rel(self, date: str) -> str:
        return f"{self._subdir}/{date}.md" if self._subdir else f"{date}.md"

    def _dir(self) -> Path:
        """The directory the dated entries live in (the root, or a subdir under it)."""
        return safe_path(self._root, self._subdir) if self._subdir else self._root

    def _entries(self) -> list[str]:
        """The dated entry stems (``YYYY-MM-DD``) in the journal dir, newest first."""
        d = self._dir()
        if not d.is_dir():
            return []
        return sorted((p.stem for p in d.glob("*.md")), reverse=True)

    # --- the three tools -------------------------------------------------------------------------
    def _write(self, inp: dict) -> str:
        text = inp.get("text")
        if not isinstance(text, str) or not text.strip():
            return "error: missing 'text'"
        prose = text.strip()
        if len(prose) > self._max_chars:
            prose = prose[: self._max_chars].rstrip() + "…"
        f = safe_path(self._root, self._rel(self._date))  # rejects traversal before any I/O
        if not f.exists():  # first write of the day → create with the code-owned header + a timed section
            header = f"# {self._date}\n\n"
            if self._stamp:
                header += self._stamp.rstrip() + "\n\n"
            if self._time:  # timestamp the day's first entry too, so every section carries its `## HH:MM`
                header += f"## {self._time}\n\n"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(header + prose + "\n", encoding="utf-8")
            return f"journal: створено запис {self._date}" + (f" (## {self._time})" if self._time else "")
        # later same-day write → append a timestamped section (the stamp is a daily constant, not repeated)
        section = f"\n## {self._time}\n\n{prose}\n" if self._time else f"\n{prose}\n"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(section)
        return f"journal: додано до запису {self._date}" + (f" (## {self._time})" if self._time else "")

    def _read(self, inp: dict) -> str:
        date = inp.get("date")
        if isinstance(date, str) and date.strip():
            f = safe_path(self._root, self._rel(date.strip()))
            if not f.is_file():
                return f"error: запис за {date.strip()} не знайдено."
            return f.read_text(encoding="utf-8")
        entries = self._entries()  # no date → today if present, else the most recent
        if not entries:
            return "journal: ще немає записів."
        target = self._date if self._date in entries else entries[0]
        return safe_path(self._root, self._rel(target)).read_text(encoding="utf-8")

    def _list(self) -> str:
        entries = self._entries()
        if not entries:
            return "journal: ще немає записів."
        return "journal:\n" + "\n".join(f"  - {d}" for d in entries)


# --- v4.3 LUMI-309: the window's read side — the days, and one day parsed (never a write) -------------------------
JOURNAL_MAX_DAYS = 5000  # days a listing returns (the rest → "truncated")
JOURNAL_MAX_DAY = 256 * 1024  # bytes of one day the window reads (a larger day is refused)
STAMP_KEYS = {"Настрій": "mood", "Біоритми": "biorhythms", "Прогноз": "forecast"}  # the code-owned stamp's lines
_DAY_FILE = re.compile(r"(\d{4}-\d{2}-\d{2})\.md")
_SECTION = re.compile(r"## (\d{2}:\d{2})[ \t]*")  # a whole line: the tool's `## HH:MM`
_STAMP_LINE = re.compile(r"> \*\*([^*:]+):\*\*[ \t]*(.*)")  # a whole line: `> **Key:** value`
_CELL = re.compile(r"(\S+) ([+\-−]\d+(?:\.\d+)?) \(([^()]+)\)")  # `фізичний +0.52 (rising)` (format_biorhythms)


def is_day(value: object) -> bool:
    """True iff ``value`` is a ``YYYY-MM-DD`` string naming a real date (``2026-02-30`` is not)."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        _date.fromisoformat(value)
    except ValueError:
        return False
    return True


def parse_biorhythms(value: str) -> list[dict] | None:
    """The stamp's biorhythm line as cells ``[{name, value, label}]``, or ``None`` when any cell doesn't parse.

    The line is ``format_biorhythms``'s (``фізичний +0.52 (rising) · емоційний +1.00 (high) · …``); a ``−`` (U+2212)
    minus is read as ``-``."""
    cells = [c.strip() for c in value.split(" · ") if c.strip()]
    out: list[dict] = []
    for cell in cells:
        m = _CELL.fullmatch(cell)
        if m is None:
            return None
        out.append({"name": m.group(1), "value": float(m.group(2).replace("−", "-")), "label": m.group(3)})
    return out or None


def parse_entry(date: str, text: str) -> dict:
    """One journal day: ``{date, stamp, sections, markdown}``.

    - A first line starting with ``# `` is the heading and is dropped (it may be the date or anything else).
    - ``stamp`` is the run of ``> **Key:** value`` lines before the first section: ``Настрій`` → ``mood``,
      ``Біоритми`` → ``biorhythms`` (:func:`parse_biorhythms`), ``Прогноз`` → ``forecast``; a missing key is
      ``None``, and a day with none of them has ``stamp: None``. A line with another key stays in the prose.
    - ``sections`` split on the lines that are exactly ``## HH:MM``: ``[{time, text}]``. Prose before the first one
      (a day written before the tool timed its sections) is a first section with ``time: None``.
    - ``markdown`` is the whole text. Never raises on content."""
    lines = text.replace("\r\n", "\n").split("\n")
    i = 1 if lines and lines[0].startswith("# ") else 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    stamp: dict = {}
    prose: list[str] = []  # the stamp-like lines with another key, kept with the prose
    while i < len(lines):
        m = _STAMP_LINE.fullmatch(lines[i].rstrip())
        if m is None:
            break
        key = STAMP_KEYS.get(m.group(1).strip())
        if key is None:
            prose.append(lines[i])
        elif key not in stamp:
            stamp[key] = m.group(2).strip()
        i += 1
    sections: list[dict] = []
    time: str | None = None
    body: list[str] = prose
    for line in lines[i:]:
        m = _SECTION.fullmatch(line.rstrip())
        if m is None:
            body.append(line)
            continue
        if time is not None or "\n".join(body).strip():
            sections.append({"time": time, "text": "\n".join(body).strip()})
        time, body = m.group(1), []
    if time is not None or "\n".join(body).strip():
        sections.append({"time": time, "text": "\n".join(body).strip()})
    parsed_stamp = None
    if stamp:
        bio = stamp.get("biorhythms")
        parsed_stamp = {"mood": stamp.get("mood") or None,
                        "biorhythms": parse_biorhythms(bio) if bio else None,
                        "forecast": stamp.get("forecast") or None}
    return {"date": date, "stamp": parsed_stamp, "sections": sections, "markdown": text}


def _day_text(path: Path, limit: int) -> str:
    with path.open("rb") as fh:
        return fh.read(limit).decode("utf-8", errors="replace")


def list_days(root: str | Path) -> dict:
    """The journal's days, newest first: ``{days:[{date, sections, size, stamp}], truncated}``.

    Only regular ``YYYY-MM-DD.md`` files that name a real date count; other names, folders, FIFOs and links that leave
    the root are skipped. ``sections`` is the day's number of sections and ``stamp`` whether it carries the code-owned
    stamp (:func:`parse_entry`, over at most :data:`JOURNAL_MAX_DAY` bytes). At most :data:`JOURNAL_MAX_DAYS` days. A
    journal that doesn't exist yet lists empty."""
    base = Path(root).resolve()
    if not base.is_dir():
        return {"days": [], "truncated": False}
    names: list[str] = []
    with os.scandir(base) as it:
        for entry in it:
            m = _DAY_FILE.fullmatch(entry.name)
            if m is not None and is_day(m.group(1)):
                names.append(entry.name)
    names.sort(reverse=True)
    days: list[dict] = []
    for name in names[:JOURNAL_MAX_DAYS]:
        path = base / name
        if not _inside(base, path):
            continue
        try:
            st = path.stat()
            if not stat.S_ISREG(st.st_mode):
                continue
            entry = parse_entry(name[:-3], _day_text(path, JOURNAL_MAX_DAY))
        except OSError:
            continue
        days.append({"date": name[:-3], "sections": len(entry["sections"]), "size": st.st_size,
                     "stamp": entry["stamp"] is not None})
    return {"days": days, "truncated": len(names) > JOURNAL_MAX_DAYS}


def read_day(root: str | Path, date: object) -> dict:
    """One day of the journal, parsed (:func:`parse_entry`).

    ``date`` must be a real ``YYYY-MM-DD`` (else :class:`BadPath`); the file goes through the sandbox guard; a missing
    day is :class:`NotFound` and one over :data:`JOURNAL_MAX_DAY` bytes :class:`TooLarge`. Read as UTF-8 (a broken
    byte becomes U+FFFD)."""
    if not is_day(date):
        raise BadPath("not a date (YYYY-MM-DD)")
    path = _resolve(root, f"{date}.md")
    try:
        st = path.stat()
    except OSError:
        raise NotFound(f"no journal entry for {date}") from None
    if not stat.S_ISREG(st.st_mode):
        raise NotFound(f"no journal entry for {date}")
    if st.st_size > JOURNAL_MAX_DAY:
        raise TooLarge(f"the entry for {date} is larger than {JOURNAL_MAX_DAY // 1024} KB")
    return parse_entry(str(date), _day_text(path, JOURNAL_MAX_DAY))
