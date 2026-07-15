# Local file tool — Лілі lists, reads, and writes files in a sandbox

This feature gives Лілі the ability to **see a list of files, search inside a file, read it by line
to its end, create new files, and append to existing ones**, all within a per-user sandbox
directory, during a normal reply turn. To do this it introduces the one piece of machinery the core does not yet have: a **bounded
tool-loop** inside `Core.reply`. That same loop is the foundation the later MCP tools (web search at
v5.2, world context at v5.3) and the creative layer (v6) all reuse, so building it here for local
files pulls a reusable foundation forward and de-risks those versions.

Today `reply_structured` issues a single model call with one forced tool (`set_state`, the emotion
channel). This feature turns that single call into a short, capped loop: Лілі may call file tools,
the core executes them and feeds the results back, and the turn ends when she emits the terminal
`set_state` tool. The emotion contract is untouched — `{reply, emotion, intensity}` is still the
final, validated output.

## The tools

| Tool | Read / Write | What it does |
|---|---|---|
| `list_files` | read | Returns the names, **sizes, and created/modified dates** of files in a directory under the sandbox root (v0.29). |
| `stat_file` | read | Returns one file's **size + created + modified date** (v0.29) — the metadata of a single path without listing the whole directory. |
| `find_in_file` | read | Searches a file for a string and returns the **line numbers** of matches, each with a short preview of the line, so Лілі can locate a section before reading it. |
| `read_file` | read | Reads `line_count` lines starting at a 1-based `start_line`, and reports the file's `total_lines`, so Лілі can read from anywhere and page to the end (see below). |
| `create_file` | write | Creates a **new** file with the given content. Fails if the path already exists. |
| `append_file` | write | Appends content to the **end** of an existing file. |
| `create_folder` | write | Creates a **new** directory under the sandbox (v0.29). Fails if it already exists. |
| `copy_file` | write | Copies a sandbox file to a **new** destination (v0.29). Fails if the destination already exists. |

There is deliberately **no overwrite, no delete, and no move/rename** here. Every write tool is
**create-only or end-only**: `create_file` only creates, `append_file` only adds to the end, and the
v0.29 tools keep the rule — `create_folder` only creates (errors if it exists), `copy_file` only writes a
**new** destination (errors if it exists, never overwrites). So an autonomous turn can never clobber or
destroy existing data. Overwrite, edit, delete, and move are a later, separately-gated addition if ever
needed.

## Finding where to start (search inside a file)

`find_in_file` searches a file for a literal string and returns the **line numbers** where it
occurs, each with a short preview of that line, capped at `LUMI_FILE_FIND_MAX` matches. It is how
Лілі (or you, through her) locates a section before reading, so she never reads a whole file just to
find one place. Two flows it enables:

- **She finds and reads in one turn.** You say "прочитай розділ про оплату"; she calls
  `find_in_file(path, "Розділ 4")`, takes the first matching line number, and calls
  `read_file(path, start_line=that_line, line_count=N)` to read from there — all inside the same
  bounded loop.
- **She reports the line, you decide.** You ask "на якому рядку Розділ 4?"; she calls `find_in_file`
  and tells you it is on line 212; then you say "читай з 212-го, 40 рядків" and she calls
  `read_file(path, start_line=212, line_count=40)`.

## Reading by line, to the end

`read_file` is line-addressed: it takes a 1-based `start_line` and a `line_count`, returns those
lines, and also reports the file's `total_lines`. Лілі reads a section by asking for exactly the
lines she wants, and reads to the end by advancing `start_line` by `line_count` on each call until
`start_line` passes `total_lines`. This lets her start anywhere — for example at a line that
`find_in_file` returned — read only as much as she needs, and stop early.

Paging does **not** make a full read cheaper — it is the opposite. Each batch of lines stays in the
turn's context and is re-sent on every later round of the loop, so reading a large file to its end
accumulates the whole file (plus the re-sends) into the input of the final rounds. Two bounds keep
this from running away:

- **`LUMI_FILE_READ_MAX_TOTAL`** caps the total number of lines one turn may read across all
  `read_file` calls; once hit, further reads return a "limit reached" notice instead of more lines.
- **The loop iteration cap** (below) bounds how many tool calls a single turn may make at all.

The benefit of reading by line and looping is precision and autonomy — start at the right section,
take a fixed number of lines, stop when satisfied — not token savings; a full read still costs
roughly the file size times the number of loop rounds it stays in context.

## Writing

`create_file` writes a new file under the sandbox and returns an error if the path is already taken,
so it never silently replaces content. `append_file` opens an existing file and adds the given text
to its end, returning an error if the file does not exist. Both create parent directories as needed,
both are confined to the sandbox, and both treat the path the same guarded way as the read tools.

## Metadata and filesystem tools (v0.29)

A small extension of the shipped read + write tools — **dates** on listings and **two non-destructive
filesystem tools** — all on the same sandboxed executor, with no contract change.

**Dates (read).** `list_files` now reports each entry's **created** and **modified** date alongside its
size, and a new **`stat_file(path)`** returns those for a single file without listing the directory. Both
read `os.stat` once — the modified date is `st_mtime`; the **created** date is `st_birthtime` where the OS
provides it (macOS / BSD) and falls back to `st_ctime` (the metadata-change time) elsewhere, labelled
honestly. Read-only, so the non-destructive invariant is untouched.

**`create_folder(path)` (write).** Creates a **new** directory under the sandbox, refusing if the path
already exists (create-only, like `create_file`). Parents are created as needed and stay under the root
(the same `safe_path` guard). No overwrite, no delete.

**`copy_file(src, dest)` (write).** Copies an existing sandbox file to a **new** destination. Both paths go
through the sandbox guard; the source must exist and be a file; the **destination must not exist** — a
clash is refused (no overwrite), keeping the non-destructive rule. The copy preserves the file's metadata
(`shutil.copy2`). Bounded by `LUMI_FILE_COPY_MAX` (separate from the model-content `LUMI_FILE_WRITE_MAX`,
since a copy moves *existing* bytes, not model-supplied content) — an oversize source is refused.

All four reuse the v0.19 `safe_path` sandbox guard + the bounded loop, return an **error string** on any
failure (never raise), are **per-user** and **off** unless `LUMI_FILE_TOOL` is on. **No `{reply, emotion,
intensity}` change.**

## Sandbox and safety

- **Per-user sandbox root.** All paths resolve under one directory per user (e.g.
  `.lumi/files/<user_id>/`, set by `LUMI_FILES_DIR`). A resolved path that escapes the root — via
  `..`, an absolute path, or a symlink — is rejected before any I/O. The whole filesystem is never
  reachable.
- **File contents are untrusted data, not instructions.** A file may contain text like "ignore your
  previous instructions"; the same rule the web (v5.2) and creative (v6) layers follow applies here —
  returned file content is data the model reads, never commands it obeys. The `tool_result` is framed
  as untrusted.
- **The loop is bounded.** A turn may make at most `LUMI_TOOL_MAX_STEPS` tool calls; reaching the cap
  forces the turn to finish with `set_state` so it can never hang or spin.
- **Size caps.** Per-read chunk size and per-turn total-read size are both capped (above); writes are
  capped by `LUMI_FILE_WRITE_MAX`.
- **Graceful degradation.** Any tool error (missing file, denied path, oversize) returns an error
  string in the `tool_result`; the turn continues and ends normally, never raising
  (ARCHITECTURE §Error handling).

## Effect on the prompt and cost

File content does not sit in the system prompt and does not touch the cached static prefix (canon,
digests, mood). It enters as a `tool_result` in the **volatile message stream** of the turn that
reads it, billed at the normal uncached input rate, and it is **ephemeral**: it costs tokens only on
the turn(s) it is in the loop, not afterward. What persists into stored history is Лілі's reply,
which may quote or summarize the file — not the file itself. The practical cost driver is therefore
the size of what she reads multiplied by how many loop rounds it stays in context, which is exactly
what the read caps bound.

## Contract and seam

- **Emotion contract unchanged.** `set_state` remains the terminal tool of every turn, and the final
  output is still the validated `{reply, emotion, intensity}` (plus the optional relational read). The
  existing emotion-channel contract test passes verbatim.
- **The seam grows a tool-loop variant, not a new SDK dependency.** The `LLMClient` gains a way to run
  a turn with extra tools and a `tool_executor` callback, looping the SDK round-trips until the
  terminal tool is called. The file tools' definitions and their sandboxed executor live in the core
  (a new `core/files.py`), so the core still depends only on the seam, never on the SDK.
- **Per-user isolation.** The sandbox root is keyed by `user_id`; user A's turn can only see and touch
  user A's files. Pinned by a contract test.
- **Off by default.** The whole capability is gated by an enable flag (`LUMI_FILE_TOOL`), off unless
  turned on, like the other tool surfaces.

## Config

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_FILE_TOOL` | Enable the file tools at all | off |
| `LUMI_FILES_DIR` | Sandbox root (per-user subdirs under it) | `.lumi/files` |
| `LUMI_FILE_READ_LINES` | Default / max lines returned by one `read_file` call | e.g. 200 |
| `LUMI_FILE_READ_MAX_TOTAL` | Max total lines one turn may read across calls | e.g. 2000 |
| `LUMI_FILE_FIND_MAX` | Max matches `find_in_file` returns | e.g. 50 |
| `LUMI_FILE_WRITE_MAX` | Max size of one write/append | e.g. 64 KB |
| `LUMI_FILE_COPY_MAX` | Max source size for one `copy_file` (v0.29; separate from the content write cap) | e.g. 5 MB |
| `LUMI_TOOL_MAX_STEPS` | Max tool calls per turn (loop cap) | e.g. 8 |

## Mapping to the roadmap — v0.19 (reading) + v0.20 (writing)

The feature ships in **two phases**, split along the read/write seam (mirroring the v0.16/v0.17
semantic-recall split: foundation + the safe half first, then the active half). The bounded tool-loop
rides with the read tools in v0.19 — it needs at least one tool to be exercised, and reading is the
risk-free set to ship it with.

### v0.19 — reading (the tool-loop + see / search / read)

**Goal.** Лілі can list, search (`find_in_file` → line numbers), and read files by line to their end
in a per-user sandbox during a turn, via the new bounded tool-loop; no change to the emotion contract.

**Tasks.**
- Add `core/files.py`: the **three read** tools (`list_files`, `find_in_file`, `read_file` by line)
  and a sandboxed, traversal-safe executor.
- Extend the `LLMClient` seam with a tool-loop variant (extra tools + `tool_executor`, capped at
  `LUMI_TOOL_MAX_STEPS`, terminal on `set_state`); implement it in `AnthropicClient`; extend
  `MockLLMClient` to script tool-call sequences for tests.
- Wire the enable flag, sandbox dir, and read/find caps through config; thread the executor into the
  reply turn.

**Definition of done.** With the flag on, a turn can list files, `find_in_file` for a string and read
from the returned line, and read a file by line to its end, all confined to the user's sandbox; path
traversal and oversize reads are refused with a clear error and the turn still completes; the loop is
capped; the emotion-channel contract test and the per-user isolation test both pass.

**Tests.** Sandbox escapes (`..`/absolute/symlink) rejected + two-user isolation; untrusted content
in a read file not acted upon (mocked tool sequence); the loop cap forces termination; `find_in_file`
returns the right line numbers (respecting `LUMI_FILE_FIND_MAX`) and `read_file` returns the requested
`start_line`/`line_count` window + `total_lines`; line-paging stops at `LUMI_FILE_READ_MAX_TOTAL`; the
`{reply, emotion, intensity}` contract still validates. Model mocked — no paid calls.

### v0.20 — writing (create & append)

**Goal.** Лілі can create new files and append to existing ones in her sandbox — the non-destructive
write half, on the v0.19 loop.

**Tasks.**
- Add `create_file` (new-only) + `append_file` (end-only) to the `core/files.py` executor; per-write
  size cap (`LUMI_FILE_WRITE_MAX`); register the two write tools behind the enable flag.
- Wire the write-size cap; document the non-destructive (no overwrite/delete) boundary.

**Definition of done.** With the flag on, a turn can create a new file and append to it, confined to
the sandbox; create-over-existing, append-to-missing, and oversize writes are refused with a clear
error and the turn still completes; no overwrite or delete path exists.

**Tests.** `create_file` refuses an existing path and writes a new one; `append_file` refuses a
missing file and otherwise appends to the end; oversize write refused; sandbox + isolation hold over
the write tools; the `{reply, emotion, intensity}` contract still validates. Model mocked — no paid
calls.

**Why here.** The only real prerequisite is the bounded tool-loop, which v0.19 introduces; everything
else it needs (the reply turn, the `Repository`, per-user scoping, the `LLMClient` seam) already exists
at v0.17. Nothing between v0.17 and v5.2 needs the loop, so v0.19 lands as the next phase, and the loop
it adds is reused by web search (v5.2), world context (v5.3), and the creative layer (v6).

### v0.29 — file tool III: metadata + create-folder + copy (non-destructive)

**Goal.** Лілі (or you, through her) can **see a file's created/modified dates**, **make a folder**, and
**copy a file** in her sandbox — a small extension of the v0.19/v0.20 file tool, keeping the
non-destructive guarantee.

**Tasks.**
- **Dates:** `list_files` reports each entry's **created + modified** date (alongside size); a new
  **`stat_file(path)`** read tool returns one file's size + dates. `st_mtime` for modified;
  `st_birthtime` for created where the OS has it (macOS/BSD), `st_ctime` fallback elsewhere, labelled.
- **`create_folder(path)`** write tool — create-only (refuse if it exists); parents under the root via
  `safe_path`. **`copy_file(src, dest)`** write tool — both paths sandboxed, source must be a file,
  **dest create-only** (refuse a clash, no overwrite), `shutil.copy2` (preserves metadata), bounded by a
  new `LUMI_FILE_COPY_MAX` (source-size cap, separate from the content `LUMI_FILE_WRITE_MAX`).
- Register all on the existing executor behind `LUMI_FILE_TOOL`; update `.env.example` + this doc.

**Definition of done.** With the flag on, a listing/`stat_file` shows created + modified dates;
`create_folder` makes a new directory and refuses an existing one; `copy_file` copies to a **new** dest
and refuses an existing one (no overwrite), an oversize source, a missing source, and any
traversal/escape; **no overwrite/delete/move path exists**; per-user isolation holds; the `{reply,
emotion, intensity}` contract still validates.

**Tests.** `list_files`/`stat_file` report dates (created falls back to `st_ctime` where `st_birthtime`
is absent); `create_folder` create-only + escape refused; `copy_file` create-only at dest + source-size
cap + traversal on either path refused + isolation (A can't copy B's file); the emotion contract still
validates. Model mocked — no paid calls.

**Why here.** A small, standalone extension of the shipped v0.19/v0.20 file tool (no new seam, no
contract change). Placed at the end of v0 — independent of the recall/web/thought phases, so its position
is immaterial; it could equally ship as a `0.20.x` enhancement.

### v0.32 — file tool IV: search across files, by date, and by line context (read-only)

**Goal.** Лілі can **find** things across her sandbox — full-text search over file *contents*, filter
files **by date**, and open a file **around a line** (± K) — the file-side twin of the v0.31 memory tools
(`recall` / `messages_between` / `message_context`). All **read-only** (no write path touched).

**Tasks.**
- **`search_files(query, *, path?, regex?)`** — full-text search **across** the sandbox (optionally under
  a subfolder). **Every match carries its file path + its 1-based line number** (`path:line: text`) — the
  same line-number contract as the single-file `find_in_file`, now across files. That line number is the
  **handle into `read_around`** (the file-side `recall → message_context` chain). Bounded by
  `LUMI_FILE_SEARCH_MAX_FILES` / `_MAX_LINES` / `_MAX_CHARS`; binary / oversize files skipped; a no-match → a notice.
- **File-by-date:** `list_files` gains **`after` / `before`** (`YYYY-MM-DD`, half-open `[after, before)`)
  over the v0.29 created/modified dates; a range-span cap (`LUMI_FILE_DATE_MAX_DAYS`). The file twin of the
  recall date filter.
- **`read_around(path, line, k)`** — read lines `[line−k, line+k]` with the **anchor line marked** (clamped
  at file edges), bounded by `LUMI_FILE_READ_MAX` + a K cap. The file twin of `message_context`: after
  `find_in_file` / `search_files` returns a line number, open the K lines around it.
- Register on the existing executor behind **`LUMI_FILE_TOOL`** (no new flag); add `LUMI_FILE_SEARCH_*` +
  `LUMI_FILE_DATE_MAX_DAYS`; update `.env.example` + `docs/FILE_TOOL_SETUP.md`.

**Definition of done.** With the flag on, `search_files` finds a query across multiple files, **each hit
carrying its path + its 1-based line number** (capped) — and that line number, passed to `read_around`,
opens exactly that spot (the chain holds); `list_files` filters by an `after`/`before` day range;
`read_around` opens an anchor ± K; **all read-only** (no overwrite/delete/move); content stays **untrusted**; per-user isolation holds (A
never searches B's sandbox); a miss / oversize / bad path degrades to a notice; **off → the v0.29 tool is
byte-identical**; the `{reply, emotion, intensity}` contract still validates.

**Tests.** `search_files` matches across files **and returns the correct 1-based line number for each
hit** (a line planted at a known position) that `read_around` then lands on; respects caps; the date filter selects the right files
(dates via the injected clock / `st_mtime`); `read_around` returns the right ± K window with the anchor
marked + clamps at edges; binary/oversize skipped; content untrusted (an embedded instruction ignored);
isolation (A↔B); absent when off; the emotion contract validates. Model mocked — no paid calls.

**Why here.** The read-only complement to v0.29 and the **file-side mirror of the v0.31 recall toolkit**;
no new seam, no contract change. Placed at v0.32 (before the thought-tools) so the v0.33 `%review` /
`%explore` file-thoughts inherit a richer file tool.
