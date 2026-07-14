# File-acting thoughts — Лілі's interior touches the file sandbox (`%note` / `%review` / `%explore`)

A small family of new **`%directives`** that let the v0.12 thought-stream **use the v0.19/v0.20 file
tools**. Today a thought is purely mental — a single tool-less call that writes one line to a dated
diary in memory. These directives let that interior *leave a trace on disk* and *read its own traces
back*: she can **note** a thought to a file, **review** a note she kept and muse on it, and (the open
end) **explore** her sandbox freely. Her mind doesn't only think — it can keep a notebook.

This is **not** a new mechanism. It is the existing mental-act engine (`trigger → seed → generate →
record → maybe surface`, [THOUGHT_STREAM.md](THOUGHT_STREAM.md)) plus the existing sandboxed file
executor ([FILE_TOOL.md](FILE_TOOL.md)). The work is wiring the two together along one deliberate seam,
and authoring the directives. The emotion contract, the `Thought` store, and the file sandbox rules are
all untouched.

> Builds on **v0.12** (the thought-stream + `%directive` registry), **v0.19** (the read tools +
> bounded tool-loop), and **v0.20** (the non-destructive write tools). `%note` is the **one-line**
> thought-trace; the **full day-summary** diary is the separate **`%journal`** directive on the v0.28
> journal tool (see [JOURNAL.md](JOURNAL.md) + [TOOL_THOUGHTS.md](TOOL_THOUGHTS.md)) — `%note` appends a
> `notes/<date>.md` trace in her sandbox while the diary lives in its **own dedicated root** (distinct
> files), both by the non-destructive append rule.

---

## Three flavors (a design ladder, simplest first)

The same engine, three increasing degrees of how far the file tools reach into the think path. They are
a **ladder** — each is useful on its own; later rungs cost more and hand her more autonomy.

| Directive | What she does | Tool reach | Cost / change |
|---|---|---|---|
| **`%note`** | Thinks as usual, then **code** appends the thought to a dated file in her sandbox. A real on-disk diary of her interior. | **None** (the model never calls a tool — code owns the write). | Smallest. No tool-loop in the think path. Reuses v0.20 `append_file`/`create_file` + the "code owns the write" pattern (needs v1.8). |
| **`%review`** | **Reads** one of her own notes via the file tools, then writes a thought seeded by what she read. | **Read** (`list_files` / `find_in_file` / `read_file`). | Medium. The think call runs through the read tool-loop + a thought-shaped terminal. Read-only — no disk change. |
| **`%explore`** | A think that may **read and write** files as it decides. | **Read + write** (the full v0.19+v0.20 set). | Largest. The full file tool-loop in the think path. Proactive (idle) firings write **while you're away**. |

**Recommended first rung:** `%note`. It delivers the headline value (her thoughts become a durable,
human-readable diary) with the least machinery and the least risk — the model still does one ordinary
think, and the *write is deterministic code*, so an autonomous firing can never wander.

---

## How it fits the mental-act engine

The engine is unchanged: `trigger → seed → generate → record → maybe surface`. The registry
([core/thoughts.py](../../core/thoughts.py)) gains entries; the firing path gains, per flavor, either a
**code-owned write step after** the call (`%note`) or a **tool-loop around** the call (`%review` /
`%explore`).

### The one seam to respect

A thought and a reply have **different shapes**, and this is the whole of the design tension:

- A **thought** is a single **tool-less** housekeeping call ([core/agent.py](../../core/agent.py)
  `think`) that returns free text ending in `ЕМОЦІЯ: <word>`, parsed by `parse_thought` → recorded as a
  `Thought`. No `set_state`.
- The **file tools** live in the **reply** tool-loop, whose **terminal tool is `set_state`** (the
  emotion channel). The loop ends when she emits `set_state`.

So `%note` (which adds *no* tool call) needs no loop and leaves both shapes intact — it is the clean
one. `%review` / `%explore` must run the file tool-loop **but keep the thought's terminal shape** (free
text + `ЕМОЦІЯ`, not `set_state`). Two honest ways to reconcile that, decided at build time:

1. **Thought-terminal loop** — reuse the bounded tool-loop but make its terminal the thought format
   (an explicit `record_thought` tool, or "stop on a turn with no tool call" + `parse_thought`). The
   loop offers the file tools + this terminal; she reads, then records a thought.
2. **Two-step** — run the read tool-loop to *gather* (its result is data, not the thought), then a
   second tool-less think call seeded with what she gathered. Simpler, but two calls.

`%note` needs neither — it is the reason it's the recommended starting point.

---

## `%note` in detail (the recommended first directive)

A `%note` fires like a `%think`: idle nudge (mostly silent, occasionally spoken) or typed
(`%note`, `%note!`, `%note: <topic>`). The model produces **one short thought**, exactly as today. Then
**code** persists it:

- It appends a line to a **dated notes file** in her sandbox — `notes/<YYYY-MM-DD>.md` under her
  per-user root (`.lumi/files/<user_id>/notes/2026-06-16.md`), one entry per line:
  `HH:MM — <thought>`.
- The first note of a day **creates** the file (`create_file`); later notes **append** (`append_file`)
  — the exact v0.20 non-destructive tools, so the journal is only ever grown, never rewritten.
- The `Thought` is **also** recorded in the in-memory dated diary as usual — `%note` is a `%think` that
  *additionally* leaves a disk trace. The diary stays the source of truth for the feedback loop; the
  file is the durable, human-readable mirror you can open, copy, or keep.

The write is **best-effort**: if the file tool is off, the sandbox is unwritable, or the write is
refused (oversize, traversal — impossible here since the path is code-fixed), the thought is still
recorded in the diary and the turn never breaks. The write being **code-owned** (not a model tool call)
means an autonomous, you're-away firing is fully deterministic — it can only append her own thought to
her own dated file, nothing else.

A `/notes` view (or `%note`'s open mode) can surface the day's file; the file is just text, so you can
also `tail -f` it like the v0.19 tool-log.

---

## `%review` and `%explore` (the read / full rungs)

- **`%review`** — seeded like a think, but her generation runs the **read** tool-loop: she may
  `list_files` her journal/notes, `find_in_file` for a theme, `read_file` a stretch, then write **one
  thought** about what she found ("перечитала вчорашнє про пуер — досі тримає"). Read-only: it changes
  nothing on disk, so it is safe to fire proactively. Its value is **continuity with substance** — her
  musings can refer back to what she actually wrote, not just the lossy diary window.
- **`%explore`** — the open end: a think that may **read and write** as it sees fit (reorganize a note,
  start a new file, append a fragment). This is real autonomy and is the one to gate most carefully:
  proactive firings act **while you're away**. It stays sandboxed, per-user, and **non-destructive**
  (v0.20 — no overwrite/delete), but it is a deliberate step and should ship last, behind its own flag,
  with a tighter per-session cap than `%think`.

Both `%review` and `%explore` reuse the existing per-user-bound executor (`_file_tool_args`) and the
bounded loop (`LUMI_TOOL_MAX_STEPS`) — no new sandbox, no new safety surface.

### `%explore` — examples

Each fires the full file tool-loop (read + write) and ends by recording a `Thought(kind="explore")`;
the loop trace shows the tools she calls, then the thought she keeps and what changed on disk. Sandbox
root `.lumi/files/<user_id>/` (here `owner`).

**Create — idle, silent (tidying a recurring theme).** The idle nudge fires `%explore`; she gathers a
thread scattered across her journal into a fresh note.
```
list_files {path:"journal"}                              → 2026-06-15.md, 2026-06-16.md
read_file  {path:"journal/2026-06-16.md"}                → today's entries
find_in_file {path:"journal/2026-06-15.md", query:"пуер"} → line 4
create_file {path:"notes/чай.md", content:"# Чай\n\n- пуер тримає на другий день\n"}
```
→ thought: «зібрала свої згадки про чай в одне місце — наче маленька колекція»
→ disk: **new** `notes/чай.md` (didn't exist → `create_file`).

**Append — manual, open with a topic (`%explore! про чай`).** `!` surfaces the thought; she adds to the
existing note rather than starting over.
```
read_file   {path:"notes/чай.md", start_line:1, line_count:20}
append_file {path:"notes/чай.md", content:"- улун пахне абрикосою, але швидше вихолоняє\n"}
```
→ thought (printed): «додала про улун — поряд із пуером він зовсім інший на смак»
→ disk: one line appended to the **end** of `notes/чай.md` (non-destructive).

**Read-only outcome — she explores but chooses not to write.** `%explore` *may* write; it is never
*forced* to. Sometimes she only looks.
```
list_files {path:"."}
read_file  {path:"notes/ескізи.md", start_line:1, line_count:30}
```
→ thought: «перечитала ескізи — поки не чіпатиму, нехай полежать»
→ disk: **nothing changed** (read-only). Still a recorded thought.

**The guardrails, shown.** Even if a generation "wants" to replace or clear a file, the v0.20 tools
refuse it — `create_file` over an existing path → `error: file already exists (no overwrite)`;
`append_file` to a missing path → `error: file not found (append does not create)`. She adapts within
the turn (append instead of overwrite; create the missing file) and the thought still records. **No
overwrite/delete path exists**, so an unattended `%explore` can only ever *grow* her sandbox — never
clobber it.

---

## The store & file layout

- **Diary (unchanged).** Every flavor records a `Thought{when, kind, text, emotion, seeds, user_id,
  spoken}` in the global dated diary behind the `Repository` — `kind` is `"note"` / `"review"` /
  `"explore"`. The thought-stream isolation rule holds: the diary is **global to Лілі** (not per-user);
  only *surfacing* is per-conversation.
- **On-disk journal (new, per-user).** The files live **inside the existing per-user file sandbox**
  (`.lumi/files/<user_id>/journal/…`), so they inherit the file tool's isolation automatically — one
  user's journal is never reachable in another user's turn. This is consistent with the file tool being
  per-user; it does **not** make the *thought* per-user (the `Thought` stays global), it just means the
  written mirror lives in the writer's sandbox.

> A subtle but important note: the **thought** is global to Лілі, but the **file** lives in a per-user
> sandbox. With the default single `owner` user these coincide. When multi-user lands (v2.3), the
> design choice — does her global diary mirror into *each* user's sandbox, or only the owner's? — is
> called out here as **owner's sandbox only** (her journal is hers, written where she is the author),
> and pinned by a test. The grand cross-user literary journal is v5.6, admin-only.

---

## Safety & invariants (same family as the rest)

- **Sandboxed, per-user, non-destructive.** All writes go through the v0.19 `_safe` guard and the v0.20
  create-new-only / append-end-only tools — no traversal, no overwrite, no delete. `%note`'s path is
  **code-fixed** (`notes/<date>.md`), so it cannot even be aimed.
- **Off by default, twice.** Requires both the thought-stream (`LUMI_THOUGHTS`) and the file tool
  (`LUMI_FILE_TOOL`) on; `%explore` additionally behind its own flag. If either is off, the directive
  degrades to a plain `%think` (or is simply unavailable) — never an error.
- **Proactive-while-away is explicit.** `%note` (deterministic append) and `%review` (read-only) are
  safe to fire on the idle nudge. `%explore` (autonomous writes with you absent) is the one feature
  that genuinely acts on the world unattended — it ships last, gated, capped, and logged.
- **Restraint, never competence.** Like all thoughts, these are her interior — they bias tone and
  continuity, never her knowledge or willingness to help. A note is a musing she chose to keep, not a
  task list for you.
- **Honest about nature.** A journal entry is *her inner/imaginative life written down*, never a factual
  claim about the physical world — the v1.7 honesty rule applies verbatim.
- **Best-effort, never blocks.** Any file failure (tool off, I/O error, cap) degrades to "thought
  recorded in the diary, nothing written" and the turn completes — the same never-raise rule as the
  file executor and the thought engine.

---

## Contract & seam (no change)

- **Emotion channel unchanged.** A thought still yields `(text, emotion)` via `parse_thought`; a reply
  still returns the locked `{reply, emotion, intensity}`. `%review`/`%explore` use the bounded
  tool-loop but **keep the thought terminal** — they do **not** alter the `set_state` reply contract.
- **`Thought` shape unchanged.** New `kind` values (`note`/`review`/`explore`) are data, not a schema
  change — the dataclass and its contract test are untouched (only the enum-of-kinds widens).
- **File seam unchanged.** No new tools beyond the v0.19/v0.20 set; `%note` adds a **code** call to
  `create_file`/`append_file`, not a new tool. The file-tool contract test still pins the surface.
- **Repository unchanged.** Diary writes use the existing `add_thought`; the file lives in the existing
  sandbox. No new store.

---

## Config

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_THOUGHT_FILES` | Enable the file-acting directives at all | `off` |
| `LUMI_NOTE_JOURNAL_DIR` | Journal subfolder under the per-user sandbox root | `journal` |
| `LUMI_THOUGHT_EXPLORE` | Enable the autonomous read+write `%explore` (gated separately) | `off` |
| `LUMI_THOUGHT_FILE_CAP` | Max file-acting proactive thinks per session (tighter than `LUMI_THOUGHTS_CAP`) | `3` |

All ride the existing `LUMI_FILE_TOOL` (sandbox + caps, incl. `LUMI_FILE_WRITE_MAX`) and `LUMI_THOUGHTS`
(window, interval, spoken-ratio, quiet-hours) settings — nothing here re-implements the sandbox or the
nudge.

---

## Sequencing & roadmap (proposed)

Hard-deps all **shipped**: v0.12 (thoughts), v0.19 (read), v0.20 (write). A natural slot is **after
v0.21 (dictation)** as its own phase — proposed **v0.22**, split along the ladder so each rung ships and
is tested before the next:

### v0.22 — `%note` (code-owned diary)
**Goal.** Her thoughts become a durable, human-readable on-disk diary — `%note` thinks as usual and code
appends `HH:MM — <thought>` to `notes/<date>.md` in her sandbox (create-first, append-after).
**Tasks.** Register `%note` in the directive registry; after a normal think call, code-write via the
v0.20 tools to the code-fixed dated path; record the `Thought(kind="note")` as today; best-effort
degrade; `LUMI_THOUGHT_FILES` flag; `.env.example` + `FILE_TOOL_SETUP.md`/`THOUGHT_STREAM.md` notes.
**DoD.** With both flags on, `%note` records a thought **and** appends it to today's notes file; the
first note of the day creates the file; with the file tool off, the thought still records and nothing is
written; the path is sandboxed and per-user; the emotion + `Thought` contracts are unchanged.
**Tests.** `%note` writes the dated file (create then append, order preserved); file-tool-off degrades
to diary-only; two-user isolation (owner's journal not in another user's sandbox); the thought is also
in the diary; emotion contract holds. Model + file mocked — no paid calls.

### v0.23 — `%review` (read & muse)
**Goal.** She can reread her own notes and think about them — the read tool-loop in the think path with
a thought-shaped terminal.
**DoD.** `%review` reads a sandbox note via the file tools and produces one thought seeded by it;
read-only (no disk change); bounded by the loop cap; the thought contract holds.
**Tests.** A mocked read sequence → a thought that references the file; loop cap forces termination;
read-only verified; isolation holds. Mocked — no paid calls.

### v0.24 — `%explore` (full autonomy, gated)
**Goal.** A think that may read **and** write as it decides — real unattended autonomy, shipped last and
gated.
**DoD.** Behind `LUMI_THOUGHT_EXPLORE`, an `%explore` think can read + create/append in the sandbox and
record a thought; non-destructive (no overwrite/delete); a tighter per-session cap; proactive firings
logged; isolation + emotion contracts hold.
**Tests.** A mocked read+write sequence lands files in the active user's sandbox; the cap bounds
proactive firings; overwrite/delete impossible; isolation holds. Mocked — no paid calls.

---

## Open questions (for when we build, not now)

- **`%review`/`%explore` terminal** — the thought-terminal loop (`record_thought` tool) vs the two-step
  gather-then-think. The two-step is simpler and contract-safe; the single-loop is cheaper. Decide at
  v0.23.
- **Multi-user mirror** — confirm "owner's sandbox only" for the on-disk journal when v2.3 lands; pin
  with a test.
- **Surfacing** — does an open `%note` print the *thought* (as `%think!` does today) or the *journal
  path*? Default: the thought, with the file as a silent durable mirror.
