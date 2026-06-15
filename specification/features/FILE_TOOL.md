# Local file tool — Лілі lists, reads, and writes files in a sandbox

This feature gives Лілі the ability to **see a list of files, search inside a file, read it by line
to its end, create new files, and append to existing ones**, all within a per-user sandbox
directory, during a normal reply turn. To do this it introduces the one piece of machinery the core does not yet have: a **bounded
tool-loop** inside `Core.reply`. That same loop is the foundation the later MCP tools (web search at
v4.2, world context at v4.3) and the creative layer (v5) all reuse, so building it here for local
files pulls a reusable foundation forward and de-risks those versions.

Today `reply_structured` issues a single model call with one forced tool (`set_state`, the emotion
channel). This feature turns that single call into a short, capped loop: Лілі may call file tools,
the core executes them and feeds the results back, and the turn ends when she emits the terminal
`set_state` tool. The emotion contract is untouched — `{reply, emotion, intensity}` is still the
final, validated output.

## The tools

| Tool | Read / Write | What it does |
|---|---|---|
| `list_files` | read | Returns the names (and sizes) of files in a directory under the sandbox root. |
| `find_in_file` | read | Searches a file for a string and returns the **line numbers** of matches, each with a short preview of the line, so Лілі can locate a section before reading it. |
| `read_file` | read | Reads `line_count` lines starting at a 1-based `start_line`, and reports the file's `total_lines`, so Лілі can read from anywhere and page to the end (see below). |
| `create_file` | write | Creates a **new** file with the given content. Fails if the path already exists. |
| `append_file` | write | Appends content to the **end** of an existing file. |

There is deliberately **no overwrite and no delete** in this first version. `create_file` only
creates, `append_file` only adds to the end, so an autonomous turn can never clobber or destroy
existing data. Overwrite, edit, and delete are a later, separately-gated addition if ever needed.

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

## Sandbox and safety

- **Per-user sandbox root.** All paths resolve under one directory per user (e.g.
  `.lumi/files/<user_id>/`, set by `LUMI_FILES_DIR`). A resolved path that escapes the root — via
  `..`, an absolute path, or a symlink — is rejected before any I/O. The whole filesystem is never
  reachable.
- **File contents are untrusted data, not instructions.** A file may contain text like "ignore your
  previous instructions"; the same rule the web (v4.2) and creative (v5) layers follow applies here —
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
at v0.17. Nothing between v0.17 and v4.2 needs the loop, so v0.19 lands as the next phase, and the loop
it adds is reused by web search (v4.2), world context (v4.3), and the creative layer (v5).
