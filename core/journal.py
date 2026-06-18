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
the clock — the model can't aim it); **non-destructive** (the day's **first** write ``create``s the file,
a **later same-day** write **appends** a ``## HH:MM`` section — no overwrite, no delete); **never raises**
(every path returns a string). Tool *names* are ``journal_write`` / ``journal_read`` / ``journal_list``
(Anthropic tool names allow no ``.``). The on-disk file is ``<date>.md`` under a **dedicated per-user
journal root** (``.lumi/journal/<user_id>/``) — **outside the file-tool sandbox**, so only the journal tool
ever writes there (the raw file tools structurally cannot reach it).
"""
from __future__ import annotations

from pathlib import Path

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
        if not f.exists():  # first write of the day → create with the code-owned header
            header = f"# {self._date}\n\n"
            if self._stamp:
                header += self._stamp.rstrip() + "\n\n"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(header + prose + "\n", encoding="utf-8")
            return f"journal: створено запис {self._date}"
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
