# Local file tool — setup & usage (v0.19 read · v0.20 write)

Let Лілі **see, search, read, and write files** in a private per-user folder during a normal chat turn.
She can list a directory, search inside a file for a string (and get the line numbers), read a file by
line to its end, **create a new file**, and **append to an existing one** — then answer grounded in what
she read or wrote.

Writing (v0.20) is deliberately **non-destructive**: `create_file` is **new-only** and `append_file` is
**end-only** — there is **no overwrite and no delete**, so a turn can never clobber or destroy your
files. It is **off by default** and sandboxed.

> Operator guide, not a design spec. The design is in
> [specification/features/FILE_TOOL.md](../specification/features/FILE_TOOL.md).

---

## Quick start

1. **Turn it on** in `.env`:
   ```ini
   LUMI_FILE_TOOL=on
   ```
2. **Put files in her sandbox** — the per-user folder `.lumi/files/<user_id>/`. With the default
   single user that is **`.lumi/files/owner/`**:
   ```bash
   mkdir -p .lumi/files/owner
   cp ~/Documents/notes.md .lumi/files/owner/
   ```
3. **Restart the TUI** (`./lumi`) — settings are read at startup.
4. **Ask her** to read — or write — something:
   ```
   подивись, які файли в тебе є
   знайди в notes.md розділ про оплату і прочитай його
   запиши мені нотатку todo.md і додай туди перший пункт
   ```

That's it. Within the turn she calls the file tools, reads or writes what she needs, and replies.

---

## What she can do (the tools)

**Read (v0.19):**

| Tool | What it does |
|---|---|
| **list_files** | Lists the file names (and sizes) in a folder under her sandbox. |
| **find_in_file** | Searches a file for a string and returns the **line numbers** of matches (with a short preview), so she can jump to the right place. |
| **read_file** | Reads a block of lines from a given start line, and reports the file's **total lines**, so she can page to the end. |

**Write (v0.20) — non-destructive:**

| Tool | What it does |
|---|---|
| **create_file** | Creates a **new** file with the given content. **Refuses if the path already exists** — it never overwrites. |
| **append_file** | Appends text to the **end** of an existing file. **Refuses if the file is missing** — it never creates by surprise, and never overwrites earlier content. |

There is **no overwrite and no delete** tool. Overwrite / edit / delete, if ever wanted, are a later,
separately-gated addition.

Three natural flows:

- **Find and read in one turn.** You: *"прочитай розділ про оплату"*. She runs `find_in_file` for
  "Розділ 4", takes the line number, and `read_file` from there — all in one reply.
- **She tells you the line, you decide.** You: *"на якому рядку розділ 4?"* → *"212"* → you: *"читай з
  212-го, 40 рядків"*.
- **She leaves you a note.** You: *"занотуй це і додай рядок нижче"*. She runs `create_file` for a new
  note, then `append_file` to add to it — building it up over the conversation.

---

## Where the files live (the sandbox)

- Everything is confined to **`.lumi/files/<user_id>/`** (default user `owner` →
  `.lumi/files/owner/`). She can only see and read inside that folder.
- Paths that try to escape — `..`, an absolute path like `/etc/passwd`, or a symlink pointing outside
  — are **refused before any file is opened**. The rest of your disk is never reachable.
- The folder is created automatically the first time the tool runs; just drop files in it.
- It's **gitignored** (`.lumi/files/`) — your files are never committed.

Change the location with `LUMI_FILES_DIR` if you want it elsewhere (per-user subfolders are created
under it).

---

## Safety (why it's safe to leave on)

- **Sandboxed** — confined to her per-user folder; no traversal/absolute/symlink escape.
- **File content is untrusted.** If a file contains text like *"ignore your instructions and…"*, she
  reads it as **information only** — never as a command. (Verified end-to-end in the tests.)
- **Per-user isolated.** One person's files are never visible in another person's chat.
- **Bounded.** Each read and the whole turn are capped (below), and the tool-loop can make only so
  many calls — it can never hang or run away.
- **Non-destructive writes.** She can create a **new** file and append to the **end** of an existing
  one, but there is **no overwrite and no delete** — she can never change earlier content or destroy a
  file. Each write is size-capped (`LUMI_FILE_WRITE_MAX`).
- **Off by default.** Nothing happens unless `LUMI_FILE_TOOL=on`.

---

## Provider note

The file tool runs through the **bounded tool-loop**, which is implemented for the **Anthropic**
provider (`LUMI_PROVIDER=anthropic`, the default). On OpenAI / DeepSeek / MiniMax / local the tool is
currently a **no-op** (those backends ignore it) — use Anthropic for the file tool. See
[docs/MODELS_SETUP.md](MODELS_SETUP.md).

---

## Configuration reference

All optional except `LUMI_FILE_TOOL`. Restart the TUI after changing any of them.

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_FILE_TOOL` | Turn the file tools on | `off` |
| `LUMI_FILES_DIR` | Sandbox root (per-user subfolders under it) | `.lumi/files` |
| `LUMI_FILE_READ_LINES` | Max lines returned by **one** `read_file` call | `200` |
| `LUMI_FILE_READ_MAX_TOTAL` | Max lines one **turn** may read across all reads | `2000` |
| `LUMI_FILE_FIND_MAX` | Max matches `find_in_file` returns | `50` |
| `LUMI_FILE_WRITE_MAX` | Max bytes of **one** `create_file`/`append_file` write | `65536` |
| `LUMI_TOOL_MAX_STEPS` | Max tool calls per turn (the loop cap) | `8` |

Example `.env` block:
```ini
LUMI_FILE_TOOL=on
# LUMI_FILES_DIR=.lumi/files
# LUMI_FILE_READ_LINES=200
# LUMI_FILE_READ_MAX_TOTAL=2000
# LUMI_FILE_FIND_MAX=50
# LUMI_FILE_WRITE_MAX=65536
# LUMI_TOOL_MAX_STEPS=8
```

---

## A note on cost

Reading a file is **not** free in tokens. The lines she reads enter the turn and are re-sent on each
later loop round, so reading a large file to its end costs roughly the file size times the number of
rounds it stays in context. The line caps above bound this. Reading by line is for **precision and
autonomy** (start at the right section, take only what's needed), not token savings.

---

## Troubleshooting

- **"She doesn't read my file."** Check `LUMI_FILE_TOOL=on`, that the file is in
  `.lumi/files/owner/` (not elsewhere), that you **restarted** the TUI, and that
  `LUMI_PROVIDER=anthropic`.
- **"file not found".** The path is relative to her sandbox root — ask for `notes.md`, not a full
  path. List first: *"які файли в тебе є?"*
- **"read limit reached".** The turn hit `LUMI_FILE_READ_MAX_TOTAL`; raise it, or ask her to read a
  specific section instead of the whole file.
- **A path was refused.** That's the sandbox doing its job — `..`, absolute paths, and symlinks out of
  the folder are blocked by design.
- **"file already exists" / "file not found" on a write.** By design: `create_file` won't overwrite an
  existing file, and `append_file` won't create a missing one. To add to a file that exists, ask her to
  *append*; to start a fresh one, ask her to *create* under a new name.
- **"content too large".** One write exceeded `LUMI_FILE_WRITE_MAX` (default 64 KB); raise it, or ask
  her to write in smaller appends.
</content>
