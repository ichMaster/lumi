"""Local file tool — the sandboxed read executor + read tool definitions (v0.19).

Лілі can **list**, **search** (`find_in_file` → line numbers), and **read** files by line in a
**per-user sandbox** during a turn. This module is pure and model-free: it defines the three read
tools and a :class:`FileTools` executor that runs them against one root. The bounded tool-loop
(LUMI-081) calls ``FileTools.execute(name, input)``; the reply turn wires it per-user (LUMI-082).

Hard rules (FILE_TOOL.md §Sandbox and safety):
- **Sandboxed.** Every path resolves under the root; ``..`` / absolute / symlink-out escapes are
  rejected **before any I/O** — the wider filesystem is never reachable.
- **File content is untrusted data, never instructions** (the framing is applied by the loop).
- **Bounded.** Per-call line cap (``read_lines``) and find-match cap (``find_max``); the per-turn
  total-read cap arrives in LUMI-083 (a fresh :class:`FileTools` per turn carries that budget).
- **Never raises.** Any error (missing file, denied path, bad input) degrades to an **error string**.
- **Read-only.** No create/overwrite/delete here — writing is v0.20.
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


class _Denied(Exception):
    """A sandbox/validation rejection — caught by ``execute`` and returned as an error string."""


class FileTools:
    """Runs the read tools against one sandbox ``root``. One instance per turn (LUMI-082).

    ``execute(name, input)`` dispatches to a tool and **always returns a string** — an error string
    on any failure (never raises), so a file error degrades the reply, never breaks the turn.
    """

    def __init__(self, root: str | Path, *, read_lines: int = 200, find_max: int = 50) -> None:
        self._root = Path(root)
        self._read_lines = max(1, read_lines)
        self._find_max = max(1, find_max)

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
        body = "\n".join(f"{start + idx}: {ln}" for idx, ln in enumerate(window))
        last = start + len(window) - 1
        return f"{rel} (lines {start}–{last}, total_lines={total}):\n{body}"
