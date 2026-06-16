"""Local file tool — the sandboxed read + write executor + tool definitions (v0.19 read, v0.20 write).

Лілі can **list**, **search** (`find_in_file` → line numbers), and **read** files by line, and
**create** new files + **append** to existing ones, all in a **per-user sandbox** during a turn. This
module is pure and model-free: it defines the tools and a :class:`FileTools` executor that runs them
against one root. The bounded tool-loop (LUMI-081) calls ``FileTools.execute(name, input)``; the reply
turn wires it per-user (LUMI-082/086).

Hard rules (FILE_TOOL.md §Sandbox and safety):
- **Sandboxed.** Every path resolves under the root; ``..`` / absolute / symlink-out escapes are
  rejected **before any I/O** — the wider filesystem is never reachable.
- **File content is untrusted data, never instructions** (the framing is applied by the loop).
- **Bounded.** Per-call line cap (``read_lines``) and find-match cap (``find_max``); the per-turn
  total-read cap (a fresh :class:`FileTools` per turn carries that budget); per-write size cap
  (``write_max``).
- **Never raises.** Any error (missing file, denied path, bad input) degrades to an **error string**.
- **Non-destructive writes (v0.20).** ``create_file`` is **new-only** (errors if the path exists) and
  ``append_file`` is **end-only** (errors if the file is missing). There is **no overwrite and no
  delete**, so an autonomous turn can never clobber or destroy existing data.
"""
from __future__ import annotations

from pathlib import Path

# Anthropic-style tool schemas for the three READ tools. Registered alongside the terminal
# `set_state` (the emotion channel) by the tool-loop (LUMI-081/082).
READ_TOOLS: list[dict] = [
    {
        "name": "list_files",
        "description": "Перелік файлів (імена + розміри) у теці пісочниці Лілі.",
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
]

WRITE_TOOL_NAMES = frozenset(t["name"] for t in WRITE_TOOLS)


class _Denied(Exception):
    """A sandbox/validation rejection — caught by ``execute`` and returned as an error string."""


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
    ) -> None:
        self._root = Path(root)
        self._read_lines = max(1, read_lines)
        self._find_max = max(1, find_max)
        self._read_max_total = read_max_total  # per-turn total-read cap (None = unlimited)
        self._write_max = max(1, write_max)  # per-write content-size cap, bytes (v0.20)
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
            if name == "create_file":
                return self._create_file(inp)
            if name == "append_file":
                return self._append_file(inp)
            return f"error: unknown file tool {name!r}"
        except _Denied as exc:
            return f"error: {exc}"
        except OSError as exc:
            return f"error: {exc.strerror or exc}".strip()
        except Exception as exc:  # noqa: BLE001 — never raise; degrade to an error string
            return f"error: {exc}"

    # --- sandbox guard ---------------------------------------------------------------------------
    def _safe(self, rel: object) -> Path:
        """Resolve ``rel`` under the root, rejecting any escape BEFORE I/O."""
        if not isinstance(rel, str) or not rel.strip():
            raise _Denied("missing 'path'")
        p = Path(rel)
        if p.is_absolute():
            raise _Denied(f"absolute path not allowed: {rel!r}")
        if ".." in p.parts:
            raise _Denied(f"path traversal ('..') not allowed: {rel!r}")
        root = self._root.resolve()
        target = (root / p).resolve()  # resolves symlinks → an out-of-root link is caught below
        if target != root and root not in target.parents:
            raise _Denied(f"path escapes the sandbox: {rel!r}")
        return target

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
            if child.is_dir():
                rows.append(f"  {child.name}/  (dir)")
            elif child.is_file():
                rows.append(f"  {child.name}  ({child.stat().st_size} bytes)")
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
