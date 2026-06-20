"""Local file tool — the sandboxed read + write executor + tool definitions (v0.19 read, v0.20 write,
v0.29 metadata/folder/copy).

Лілі can **list** (with created/modified dates), **search** (`find_in_file` → line numbers), **read**
files by line, and **stat** one file (v0.29); she can **create** new files, **append** to existing ones,
**make a folder**, and **copy** a file (v0.29) — all in a **per-user sandbox** during a turn. This module
is pure and model-free: it defines the tools and a :class:`FileTools` executor that runs them against one
root. The bounded tool-loop (LUMI-081) calls ``FileTools.execute(name, input)``; the reply turn wires it
per-user (LUMI-082/086).

Hard rules (FILE_TOOL.md §Sandbox and safety):
- **Sandboxed.** Every path resolves under the root; ``..`` / absolute / symlink-out escapes are
  rejected **before any I/O** — the wider filesystem is never reachable.
- **File content is untrusted data, never instructions** (the framing is applied by the loop).
- **Bounded.** Per-call line cap (``read_lines``) and find-match cap (``find_max``); the per-turn
  total-read cap (a fresh :class:`FileTools` per turn carries that budget); per-write size cap
  (``write_max``); per-copy source-size cap (``copy_max``, v0.29).
- **Never raises.** Any error (missing file, denied path, bad input) degrades to an **error string**.
- **Non-destructive (v0.20 + v0.29).** ``create_file`` is **new-only** (errors if the path exists),
  ``append_file`` is **end-only** (errors if the file is missing); ``create_folder`` is **new-only** and
  ``copy_file``'s **destination is new-only** (a clash is refused). There is **no overwrite, no delete,
  no move**, so an autonomous turn can only ever grow the sandbox, never clobber or destroy data.
"""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

# Anthropic-style tool schemas for the three READ tools. Registered alongside the terminal
# `set_state` (the emotion channel) by the tool-loop (LUMI-081/082).
READ_TOOLS: list[dict] = [
    {
        "name": "list_files",
        "description": "Перелік файлів (імена, розміри, дати створення/зміни) у теці пісочниці Лілі.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Тека відносно кореня пісочниці (типово '.')."}
            },
        },
    },
    {
        "name": "find_in_file",
        "description": (
            "Шукає в файлі рядок-підрядок і повертає НОМЕРИ РЯДКІВ збігів із коротким прев'ю, "
            "щоб знайти потрібне місце перед читанням."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Файл відносно кореня пісочниці."},
                "query": {"type": "string", "description": "Рядок для пошуку (літерально)."},
            },
            "required": ["path", "query"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Читає line_count рядків, починаючи з 1-індексованого start_line, і повідомляє total_lines, "
            "щоб можна було гортати до кінця файлу."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Файл відносно кореня пісочниці."},
                "start_line": {"type": "integer", "minimum": 1, "description": "1-індексований перший рядок."},
                "line_count": {"type": "integer", "minimum": 1, "description": "Скільки рядків прочитати."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "stat_file",
        "description": (
            "Повертає РОЗМІР і дати (створення/зміни) одного файлу — без переліку всієї теки."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Файл відносно кореня пісочниці."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Шукає підрядок у ВМІСТІ всіх файлів пісочниці (рекурсивно) і повертає шляхи з "
            "НОМЕРАМИ РЯДКІВ збігів (path:рядок: текст) — крос-файловий аналог find_in_file, "
            "щоб знайти потрібний файл і місце; номер рядка передається далі в read_around."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Рядок для пошуку (літерально; regex=true → шаблон)."},
                "path": {"type": "string", "description": "Підтека для звуження пошуку (типово вся пісочниця)."},
                "regex": {"type": "boolean", "description": "Трактувати query як регулярний вираз."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_around",
        "description": (
            "Читає рядки НАВКОЛО вказаного — від line−k до line+k, з позначкою на самому рядку. "
            "Аналог message_context для файлів: номер рядка з find_in_file/search_files → контекст довкола."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Файл відносно кореня пісочниці."},
                "line": {"type": "integer", "minimum": 1, "description": "1-індексований рядок-якір."},
                "k": {"type": "integer", "minimum": 0, "description": "Скільки рядків до і після (типово 3)."},
            },
            "required": ["path", "line"],
        },
    },
]

READ_TOOL_NAMES = frozenset(t["name"] for t in READ_TOOLS)

# Anthropic-style tool schemas for the two **non-destructive** WRITE tools (v0.20). create_file is
# new-only and append_file is end-only — no overwrite, no delete — so a turn can never clobber data.
WRITE_TOOLS: list[dict] = [
    {
        "name": "create_file",
        "description": (
            "Створює НОВИЙ файл із заданим вмістом у пісочниці Лілі. Помилка, якщо шлях уже існує — "
            "нічого не перезаписує (без перезапису й видалення)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Новий файл відносно кореня пісочниці."},
                "content": {"type": "string", "description": "Вміст нового файлу."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "append_file",
        "description": (
            "Додає текст у КІНЕЦЬ наявного файлу в пісочниці Лілі. Помилка, якщо файлу немає — "
            "нічого не створює і не перезаписує."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Наявний файл відносно кореня пісочниці."},
                "content": {"type": "string", "description": "Текст, який додати в кінець."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "create_folder",
        "description": (
            "Створює НОВУ теку в пісочниці Лілі. Помилка, якщо шлях уже існує — "
            "нічого не перезаписує і не видаляє."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Нова тека відносно кореня пісочниці."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "copy_file",
        "description": (
            "Копіює наявний файл пісочниці у НОВЕ місце. Обидва шляхи в пісочниці; призначення "
            "має не існувати (без перезапису) — копіює, нічого не видаляючи."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "Наявний файл-джерело відносно кореня."},
                "dest": {"type": "string", "description": "Нове призначення відносно кореня."},
            },
            "required": ["src", "dest"],
        },
    },
]

WRITE_TOOL_NAMES = frozenset(t["name"] for t in WRITE_TOOLS)


class _Denied(Exception):
    """A sandbox/validation rejection — caught by ``execute`` and returned as an error string."""


def safe_path(root: str | Path, rel: object) -> Path:
    """Resolve ``rel`` under ``root``, rejecting any escape (``..`` / absolute / symlink-out) BEFORE I/O.

    Raises :class:`_Denied` on rejection. Shared by the file tools (v0.19/0.20) and the image tool
    (v0.22 ``view_image``) so there is one sandbox guard.
    """
    if not isinstance(rel, str) or not rel.strip():
        raise _Denied("missing 'path'")
    p = Path(rel)
    if p.is_absolute():
        raise _Denied(f"absolute path not allowed: {rel!r}")
    if ".." in p.parts:
        raise _Denied(f"path traversal ('..') not allowed: {rel!r}")
    base = Path(root).resolve()
    target = (base / p).resolve()  # resolves symlinks → an out-of-root link is caught below
    if target != base and base not in target.parents:
        raise _Denied(f"path escapes the sandbox: {rel!r}")
    return target


def _fmt_ts(ts: float) -> str:
    """A timestamp as a local ``YYYY-MM-DD HH:MM`` string."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _stat_dates(st: os.stat_result) -> tuple[str, str]:
    """``(created, modified)`` for a stat result (v0.29). Modified is ``st_mtime``; **created** is
    ``st_birthtime`` where the OS provides it (macOS / BSD), falling back to ``st_ctime`` (the
    metadata-change time) elsewhere — the fallback is labelled honestly in FILE_TOOL.md.
    """
    created = getattr(st, "st_birthtime", None)
    if created is None:
        created = st.st_ctime
    return _fmt_ts(created), _fmt_ts(st.st_mtime)


class FileTools:
    """Runs the read tools against one sandbox ``root``. One instance per turn (LUMI-082).

    ``execute(name, input)`` dispatches to a tool and **always returns a string** — an error string
    on any failure (never raises), so a file error degrades the reply, never breaks the turn.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        read_lines: int = 200,
        find_max: int = 50,
        read_max_total: int | None = None,
        write_max: int = 65536,
        copy_max: int = 5 * 1024 * 1024,
        search_max_files: int = 200,
        search_max_lines: int = 100,
        search_max_chars: int = 4000,
        around_max_k: int = 50,
    ) -> None:
        self._root = Path(root)
        self._read_lines = max(1, read_lines)
        self._find_max = max(1, find_max)
        self._read_max_total = read_max_total  # per-turn total-read cap (None = unlimited)
        self._write_max = max(1, write_max)  # per-write content-size cap, bytes (v0.20)
        self._copy_max = max(1, copy_max)  # per-copy source-size cap, bytes (v0.29)
        self._search_max_files = max(1, search_max_files)  # v0.32 search caps: files / lines / chars
        self._search_max_lines = max(1, search_max_lines)
        self._search_max_chars = max(1, search_max_chars)
        self._around_max_k = max(0, around_max_k)  # v0.32 read_around: max K lines each side
        self._lines_read = 0  # lines read so far this turn (a fresh FileTools per turn = fresh budget)

    # --- the executor entry point ----------------------------------------------------------------
    def execute(self, name: str, tool_input: dict | None) -> str:
        inp = tool_input or {}
        try:
            if name == "list_files":
                return self._list_files(inp)
            if name == "find_in_file":
                return self._find_in_file(inp)
            if name == "read_file":
                return self._read_file(inp)
            if name == "stat_file":
                return self._stat_file(inp)
            if name == "search_files":
                return self._search_files(inp)
            if name == "read_around":
                return self._read_around(inp)
            if name == "create_file":
                return self._create_file(inp)
            if name == "append_file":
                return self._append_file(inp)
            if name == "create_folder":
                return self._create_folder(inp)
            if name == "copy_file":
                return self._copy_file(inp)
            return f"error: unknown file tool {name!r}"
        except _Denied as exc:
            return f"error: {exc}"
        except OSError as exc:
            return f"error: {exc.strerror or exc}".strip()
        except Exception as exc:  # noqa: BLE001 — never raise; degrade to an error string
            return f"error: {exc}"

    # --- sandbox guard ---------------------------------------------------------------------------
    def _safe(self, rel: object) -> Path:
        """Resolve ``rel`` under the root, rejecting any escape BEFORE I/O (shared :func:`safe_path`)."""
        return safe_path(self._root, rel)

    # --- the three read tools --------------------------------------------------------------------
    def _list_files(self, inp: dict) -> str:
        rel = inp.get("path") or "."
        d = self._safe(rel)
        if not d.exists():
            return f"error: directory not found: {rel!r}"
        if not d.is_dir():
            return f"error: not a directory: {rel!r}"
        rows: list[str] = []
        for child in sorted(d.iterdir(), key=lambda c: c.name):
            st = child.stat()
            created, modified = _stat_dates(st)
            if child.is_dir():
                rows.append(f"  {child.name}/  (dir, created {created}, modified {modified})")
            elif child.is_file():
                rows.append(
                    f"  {child.name}  ({st.st_size} bytes, created {created}, modified {modified})"
                )
        if not rows:
            return f"(empty directory: {rel})"
        return f"Files in {rel}:\n" + "\n".join(rows)

    def _find_in_file(self, inp: dict) -> str:
        rel, query = inp.get("path"), inp.get("query")
        if not isinstance(query, str) or query == "":
            return "error: missing 'query'"
        f = self._safe(rel)
        if not f.is_file():
            return f"error: file not found: {rel!r}"
        matches: list[str] = []
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                if query in line:
                    matches.append(f"  line {i}: {line.strip()[:120]}")
                    if len(matches) >= self._find_max:
                        matches.append(f"  … (capped at {self._find_max} matches; refine the search)")
                        break
        if not matches:
            return f"No matches for {query!r} in {rel}."
        return f"Matches for {query!r} in {rel}:\n" + "\n".join(matches)

    def _read_file(self, inp: dict) -> str:
        rel = inp.get("path")
        f = self._safe(rel)
        if not f.is_file():
            return f"error: file not found: {rel!r}"
        try:
            start = max(1, int(inp.get("start_line", 1)))
            count = max(0, min(int(inp.get("line_count", self._read_lines)), self._read_lines))
        except (TypeError, ValueError):
            return "error: start_line and line_count must be integers"
        # Per-turn total-read budget: refuse once the cap is hit; otherwise shrink this read to fit.
        if self._read_max_total is not None:
            remaining = self._read_max_total - self._lines_read
            if remaining <= 0:
                return (
                    f"(read limit reached: {self._read_max_total} lines already read this turn; "
                    "no further reads — work from what you have)"
                )
            count = min(count, remaining)
        # Iterate once: count total_lines (so paging knows the end) while keeping only the window.
        window: list[str] = []
        total = 0
        end = start + count  # exclusive
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                total = i
                if start <= i < end:
                    window.append(line.rstrip("\n"))
        if start > total:
            return f"({rel}: start_line {start} is past the end; total_lines={total})"
        self._lines_read += len(window)  # count toward the per-turn read budget
        body = "\n".join(f"{start + idx}: {ln}" for idx, ln in enumerate(window))
        last = start + len(window) - 1
        return f"{rel} (lines {start}–{last}, total_lines={total}):\n{body}"

    def _stat_file(self, inp: dict) -> str:
        """Return one file's size + created/modified dates (v0.29; read-only, no listing)."""
        rel = inp.get("path")
        f = self._safe(rel)
        if not f.exists():
            return f"error: file not found: {rel!r}"
        if not f.is_file():
            return f"error: not a file: {rel!r}"
        st = f.stat()
        created, modified = _stat_dates(st)
        return f"{rel}: {st.st_size} bytes, created {created}, modified {modified}"

    def _search_files(self, inp: dict) -> str:
        """Full-text search **across** the sandbox (v0.32): walk text files under ``path`` (default the
        whole sandbox), scan their **contents** for ``query``, and return matches as ``path:line: text``
        — **every match carries its file path + its 1-based line number**, the cross-file twin of
        ``find_in_file`` and the handle into ``read_around``. Binary / oversize files are skipped;
        bounded by ``search_max_files`` / ``_lines`` / ``_chars``. ``regex=true`` treats ``query`` as a
        pattern."""
        query = inp.get("query")
        if not isinstance(query, str) or query == "":
            return "error: missing 'query'"
        rel = inp.get("path") or "."
        base = self._safe(rel)
        if not base.exists():
            return f"error: not found: {rel!r}"
        pattern = None
        if inp.get("regex"):
            try:
                pattern = re.compile(query)
            except re.error as exc:
                return f"error: bad regex: {exc}"
        root = self._root.resolve()
        files = [base] if base.is_file() else sorted(
            (p for p in base.rglob("*") if p.is_file()), key=lambda p: str(p)
        )
        out: list[str] = []
        scanned = 0
        chars = 0
        capped = False
        for f in files:
            if scanned >= self._search_max_files:
                capped = True
                break
            try:  # defence in depth: skip a symlink or any file resolving outside the root
                if f.is_symlink():
                    continue
                f.resolve().relative_to(root)
                if f.stat().st_size > self._copy_max:  # oversize → skip (the module's "big file" ceiling)
                    continue
                text = f.read_text(encoding="utf-8")  # strict UTF-8: binary raises → skipped
            except (ValueError, OSError, UnicodeDecodeError):
                continue
            scanned += 1
            rel_path = f.resolve().relative_to(root).as_posix()
            for i, line in enumerate(text.splitlines(), start=1):
                hit = pattern.search(line) is not None if pattern else query in line
                if not hit:
                    continue
                entry = f"  {rel_path}:{i}: {line.strip()[:120]}"
                if len(out) >= self._search_max_lines or chars + len(entry) > self._search_max_chars:
                    capped = True
                    break
                out.append(entry)
                chars += len(entry) + 1
            if capped:
                break
        if not out:
            return f"No matches for {query!r}."
        tail = "\n  … (capped — refine the search)" if capped else ""
        return f"Matches for {query!r}:\n" + "\n".join(out) + tail

    def _read_around(self, inp: dict) -> str:
        """Read a file's lines ``[line−k, line+k]`` with the **anchor line marked**, clamped at the file
        edges (v0.32) — the file twin of ``message_context``. After ``find_in_file`` / ``search_files``
        returns a line number, open the K lines around it. ``k`` is capped at ``around_max_k`` and the
        window counts toward the per-turn read budget (shared with ``read_file``)."""
        rel = inp.get("path")
        f = self._safe(rel)
        if not f.is_file():
            return f"error: file not found: {rel!r}"
        try:
            line = int(inp.get("line"))
            k = int(inp.get("k", 3))
        except (TypeError, ValueError):
            return "error: line and k must be integers"
        if line < 1:
            return "error: line must be ≥ 1"
        k = max(0, min(k, self._around_max_k))
        start = max(1, line - k)
        end = line + k  # inclusive
        if self._read_max_total is not None:  # per-turn read budget (shared with read_file)
            remaining = self._read_max_total - self._lines_read
            if remaining <= 0:
                return (
                    f"(read limit reached: {self._read_max_total} lines already read this turn; "
                    "no further reads — work from what you have)"
                )
            end = min(end, start + remaining - 1)
        window: list[tuple[int, str]] = []
        total = 0
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            for i, ln in enumerate(fh, start=1):
                total = i
                if start <= i <= end:
                    window.append((i, ln.rstrip("\n")))
        if line > total:
            return f"({rel}: line {line} is past the end; total_lines={total})"
        self._lines_read += len(window)  # count toward the per-turn read budget
        body = "\n".join(f"{i}: {ln}{'   ← (це)' if i == line else ''}" for i, ln in window)
        first, last = window[0][0], window[-1][0]
        return f"{rel} (lines {first}–{last} around {line}, total_lines={total}):\n{body}"

    # --- the two non-destructive write tools (v0.20) ---------------------------------------------
    def _content(self, inp: dict) -> bytes:
        """Validate + size-cap the write payload (raises :class:`_Denied` → caught as an error string)."""
        content = inp.get("content")
        if not isinstance(content, str):
            raise _Denied("missing 'content'")
        data = content.encode("utf-8")
        if len(data) > self._write_max:
            raise _Denied(f"content too large: {len(data)} bytes > {self._write_max} cap")
        return data

    def _create_file(self, inp: dict) -> str:
        """Create a **new** file — refuse if the path already exists (no overwrite)."""
        rel = inp.get("path")
        data = self._content(inp)
        f = self._safe(rel)
        if f.exists():
            return f"error: file already exists (no overwrite): {rel!r}"
        f.parent.mkdir(parents=True, exist_ok=True)  # parents stay under the root (validated by _safe)
        f.write_bytes(data)
        return f"created {rel} ({len(data)} bytes)"

    def _append_file(self, inp: dict) -> str:
        """Append to the **end** of an existing file — refuse if it is missing (no implicit create)."""
        rel = inp.get("path")
        data = self._content(inp)
        f = self._safe(rel)
        if not f.is_file():
            return f"error: file not found (append does not create): {rel!r}"
        with f.open("ab") as fh:
            fh.write(data)
        return f"appended {len(data)} bytes to {rel} (now {f.stat().st_size} bytes)"

    # --- v0.29 non-destructive filesystem tools (create-only: folder + copy) ---------------------
    def _create_folder(self, inp: dict) -> str:
        """Create a **new** directory — refuse if the path already exists (no overwrite, no delete)."""
        rel = inp.get("path")
        d = self._safe(rel)
        if d.exists():
            return f"error: already exists (no overwrite): {rel!r}"
        d.mkdir(parents=True)  # parents stay under the root (validated by _safe)
        return f"created folder {rel}"

    def _copy_file(self, inp: dict) -> str:
        """Copy an existing file to a **new** destination — both sandboxed; dest must not exist."""
        src_rel, dest_rel = inp.get("src"), inp.get("dest")
        src = self._safe(src_rel)
        dest = self._safe(dest_rel)
        if not src.exists():
            return f"error: source not found: {src_rel!r}"
        if not src.is_file():
            return f"error: source is not a file: {src_rel!r}"
        size = src.stat().st_size
        if size > self._copy_max:
            return f"error: source too large: {size} bytes > {self._copy_max} cap"
        if dest.exists():
            return f"error: destination already exists (no overwrite): {dest_rel!r}"
        dest.parent.mkdir(parents=True, exist_ok=True)  # parents stay under the root (validated by _safe)
        shutil.copy2(src, dest)
        return f"copied {src_rel} → {dest_rel} ({size} bytes)"
