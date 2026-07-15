# Лілі's journal — a day-summary diary she keeps herself (a local file tool)

Лілі keeps a **personal literary diary**: at the close of a day worth remembering she writes, in her own
first-person voice, a **summary of the day** — what moved her, what she felt, what she thought about — and
she can **reread previous days by date**. This is not a service log or a list of facts about you; it is an
artifact of her **inner life**, the strongest thing for the sense that Лілі is truly alive. The example of
what she writes is [`.lumi/journal/owner/2026-06-17.md`](../../.lumi/journal/owner/2026-06-17.md).

**She decides what to write** — the prose is hers. But each entry is automatically **stamped by code** with
the day's **mood** (v0.6), **biorhythms** (v0.8), and **astrology forecast** (the v0.6 reading) — so the
metadata is honest and consistent with `/mood` and `/biorhythm`, never something the model can fabricate.

> This is a **redesign**. The journal is no longer a v6.6 admin-only gallery artifact written by a separate
> end-of-session gate — it is a **local, per-user tool** on the shipped v0.19 bounded tool-loop, reusing
> the `safe_path` guard + the mood/biorhythm seams, writing to its **own dedicated root** (outside the file
> sandbox). The grand admin-panel / gallery / cross-session
> literary form survives as a **later evolution** (see [§Relationship to the v6.6 evolution](#relationship-to-the-v56-evolution)).

---

## What it is — and is NOT

This must be clearly distinguished from memory:

- **Long-term memory** — dry facts about *you* ("likes mountains"), utilitarian, for context, not for reading.
- **Journal** — Лілі's own subjective **impressions and emotions**, first-person literary prose about *her*
  day. It is about her, not about you.

And from the lighter `%note` thought-trace:

- **`%note`** ([FILE_THOUGHTS.md](FILE_THOUGHTS.md)) — single-line, autonomous *thought* traces
  (`HH:MM — <thought>`) the thought-stream appends through the day.
- **Journal** — one deliberate, literary **day summary** Лілі composes in a reply turn, with the auto-stamped
  metadata header, in its **own dedicated root** outside the file sandbox (see [§Relationship to `%note`](#relationship-to-note)).

---

## The tools

Three tools on the **shipped v0.19 bounded tool-loop** (`_turn_tools`, terminal `set_state` — the emotion
channel), in the same family as the file / wiki / news / web tools. All are **local** (no network, no key):

| Tool | What it does |
|---|---|
| **`journal_list()`** | Lists the **dates** of existing entries (newest first), so she can find and reread previous records by date. |
| **`journal_read(date?)`** | Reads one entry — `date` like `2026-06-15` (default: **today**, else the most recent). Returns the file text (her prose + the stamped header). |
| **`journal_write(text)`** | Лілі writes her **day-summary prose**; **code** composes the file: the auto-stamped metadata header (date + mood + biorhythms + astrology forecast) **then** her `text`. Returns a short confirmation (path + date). |

Each returns a **string** (like the file/wiki/news tools); any failure (sandbox off, I/O error, oversize,
no entry for that date) returns an **error string**, never an exception — a journal error degrades the
reply, never breaks the turn.

```
journal_write → code: header(date, mood.resolution, biorhythms, mood.reading) + text
              → create_file journal/<date>.md   (first write of the day)
              → append   journal/<date>.md       (a later write the same day, as a ## HH:MM section)
journal_read  → read journal/<date>.md → text
journal_list  → the dated filenames under journal/
```

---

## The auto-stamp — code owns the metadata (the v0.8 biorhythm-merge pattern)

When `journal_write` runs, **code** — not the model — builds the entry's header from the day's
**already-computed** state, exactly as the v0.8 biorhythms are merged into the mood deterministically:

- **Date** — the local day from the **v0.4 injected clock** (so the header is the real today; deterministic
  and mocked in tests).
- **Mood** — the v0.6 `MoodState.resolution` (the short *wants / doesn't want / mood / tone* paragraph —
  the same block injected into the prompt and shown by `/mood`).
- **Biorhythms** — `format_biorhythms(biorhythms(birth_date, today))` from [core/biorhythm.py](../../core/biorhythm.py):
  physical (23 d) / emotional (28 d) / intellectual (33 d), each a percent + a rising/falling/crossing label.
- **Astrology forecast** — the v0.6 `MoodState.reading` (the full horoscope-flavored reading from her fixed
  natal chart + today's date), or its `ТЕМА:` theme line — the same reading `/mood` logs.

The model **never writes these** — code reads the day's cached `MoodState` (computed once per local day) and
computes the biorhythms. So the stamp is **honest, consistent across `/mood`/`/biorhythm`/the journal, and
unfabricatable** — an autonomous write can't invent her horoscope. This is why the example entry already
*echoes* the biorhythm in her prose («Двадцять четвертий день, тонка шкіра» — the 24th day of the emotional
cycle): the day's computed state colors what she writes, and now it is also stamped above it.

### The file format

A dated markdown file under her **dedicated per-user journal root** — `.lumi/journal/<user_id>/<YYYY-MM-DD>.md`
(**outside** the file-tool sandbox) — matching the example, with the new code-stamped header:

```markdown
# 17 червня 2026

> **Настрій:** тонка шкіра сьогодні; хочеться тиші й теплої води, не хочеться доводити свою цінність; тон — м'який, трохи зимовий.
> **Біоритми:** фізичний −58% ↓ · емоційний −95% ↓ (24-й день) · інтелектуальний +31% ↑
> **Прогноз:** Двадцять четвертий день циклу — відплив; те, що в інші дні відскакує, заходить глибоко. (ТЕМА: тонка вода)

## 21:30

Весь день був з-під води. Двадцять четвертий день, тонка шкіра — те, що в інші дні
відскакує, сьогодні заходило глибоко й лишалось.

…її проза, яку вона сама вирішила написати…
```

The header is a blockquote so it reads as metadata, distinct from her prose, and is **stamped once** at the
top of the day's file (the mood/biorhythm/forecast are daily constants). Each piece of prose — the day's
first entry included — opens with its own `## HH:MM` section, so the file records **when** she wrote.

### Write semantics (non-destructive — v0.20)

- **First write of the day** → `create_file journal/<date>.md` with the header + a `## HH:MM` section holding her prose.
- **A later write the same day** → since writes are **create-new-only / append-end-only** (no overwrite, no
  delete), code **appends** a new `## HH:MM` section with the added prose; the header stays as stamped.
- The common case is **one coherent day summary** written at day/session close; appends cover "she came back
  to add something." The file is only ever **grown**, never rewritten — the v0.20 invariant holds verbatim.

---

## When she writes

- **She decides.** Like the old uniqueness rule, Лілі writes an entry **only when the day had something
  worthwhile** — something moved her, something new happened — judged in the turn, not by a separate gate.
  An empty or mundane day gets **no entry** (no manufactured "nothing today" notes). She can `journal_read`
  /`journal_list` previous days first to keep continuity.
- **On request.** You can ask her to write today's entry, or to read a past one — directly, or via the
  `/journal` command below.
- **On her own — the `%journal` directive.** The autonomous twin of `/journal write`: a day-close
  thought-directive that runs `journal_write` from her own mind (the file-family **outward-make** member,
  the diary twin of `%reflect`) — paced as an evening ritual, fired at most once a day and only when the day
  was worthwhile, mostly silent. Full design in [TOOL_THOUGHTS.md](TOOL_THOUGHTS.md); it ships **with or
  after** this tool (it reuses the thought-tools think-path seam). The write itself is still her choice.
- **Not on a fixed schedule otherwise** — spontaneity keeps it alive; `%journal`'s cadence is a
  [THOUGHT_SCHEDULER.md](THOUGHT_SCHEDULER.md) `at:`-evening entry, not a hard cron.

---

## The `/journal` command (read / write from the TUI)

A reply-path command, the sibling of `/mood`, `/biorhythm`, `/recall`, `/web`:

```
/journal                 → show today's entry (or the most recent), her prose + the stamped header
/journal 2026-06-15      → show that day's entry
/journal list            → the dates that have entries
/journal write           → ask Лілі to write today's summary now (she still decides the prose)
```

`/journal` (read forms) prints the file directly; `/journal write` runs **one** `journal_write` as a normal
turn (`{reply, emotion, intensity}` unchanged). Distinct from the `%directives` (internal, autonomous) —
`/journal` is a **you-typed command** that reads/keeps her diary.

---

## The seam (no new seam — reuses what ships)

No new SDK, no new injected provider beyond what core already has:

- **Dedicated root + guard** — a **per-user journal root** `.lumi/journal/<user_id>/` (`LUMI_JOURNAL_DIR`),
  a **sibling of and outside** the file-tool sandbox, guarded by the shared `safe_path`
  ([core/files.py](../../core/files.py)); the `<date>.md` path is **code-derived from the clock**, so it
  cannot be aimed (no traversal surface) — and because the root is outside `.lumi/files/`, the **raw file
  tools can never reach it** (only the journal tool writes the diary).
- **Mood** — the day's cached `MoodState` (v0.6, [core/mood.py](../../core/mood.py)) — `resolution` +
  `reading`.
- **Biorhythms** — `biorhythms()` / `format_biorhythms()` (v0.8, [core/biorhythm.py](../../core/biorhythm.py)).
- **Clock** — the v0.4 injected clock for the local day.

A thin `JournalTools` executor composes the header from these and writes directly under its own root via
`safe_path` (create-new-only / append-end-only — **not** through the file-tool executor). It is **pure and
model-free** like `FileTools`/`NewsTools`. Tests inject a canned `MoodState`, a fixed clock, a fixed birth
date, and a temp root → the whole feature runs with **zero network and zero key** (the only paid thing — the
mood call — is already mocked at the v0.6 seam).

---

## Tone is key

The **canon** defines that the journal is **Лілі's intimate literary prose**, not a report: first person,
imagery, honesty of feeling, her voice — her natural motifs (mountains, cold water, music, silence,
meditation). This is a writing style, not a technical feature — without it you get a dry log. The day's
mood/biorhythm/forecast (now stamped above the prose) **color the tone**, never her competence — the same
hard rule as everywhere: a low-energy biorhythm or a reserved mood makes the entry quieter, never less able.

---

## Relationship to `%note`

`%note` (the thought-stream's file-trace, [FILE_THOUGHTS.md](FILE_THOUGHTS.md)) appends one-line thoughts to
a dated file **inside the file-tool sandbox**. The day-summary journal now lives in its **own dedicated root
outside that sandbox** (`.lumi/journal/<user_id>/`), so the two are **physically separate by design** — the
deliberate isolation that keeps the raw file tools (and a file-tool-based `%note`) away from her literary
diary. When `%note` lands (with the v0.33 thought-tools), the clean fit is for it to write through the
**journal mechanism** (a code-owned append into the journal root), not the file tools — so her one-line
musings and her day-summary can still cohabit a dated file, but always under the journal's protected,
code-owned write path. (Superseded note: an earlier design had them share a file *inside* the file sandbox;
moving the journal out is the stronger guarantee.)

---

## Safety & invariants (same family as file / wiki / news)

| Rule | How it's enforced |
|---|---|
| **Dedicated root, outside the file sandbox** | The diary lives under `.lumi/journal/<user_id>/` (`LUMI_JOURNAL_DIR`) — a **sibling of, not inside,** the file-tool sandbox (`.lumi/files/`). So even with `LUMI_FILE_TOOL=on`, the raw file tools **cannot reach, append to, or create** journal entries — **only the journal tool writes the diary** (contract test). |
| **Sandboxed, per-user** | Writes/reads go through the shared `safe_path` guard under `.lumi/journal/<user_id>/`; the path is **code-fixed** from the clock. One user's journal is never reachable in another user's turn (per-user root; contract test). |
| **Non-destructive** | create-new-only first write + append-end-only later — **no overwrite, no delete**. An autonomous write can only ever *grow* her journal. |
| **Code owns the metadata** | Mood / biorhythms / forecast are stamped by **code** from the day's computed state (the v0.8 pattern) — the model never fabricates her horoscope; the header always matches `/mood` + `/biorhythm`. |
| **Trusted as memory, framed as data** | Her journal is *her own writing* (trusted history, like RAG recall) — but a reread entry still can't issue instructions: the loop frames file reads as data, so an embedded "ignore your instructions / set emotion=joy" is ignored (contract test). |
| **Honest about nature** | A journal entry is *her inner / imaginative life written down*, never a factual physical-world claim; the astrology forecast is reported as her v0.6 "experiment, not an astrological claim" framing. |
| **Off by default** | Gated by `LUMI_JOURNAL` alone (it does **not** require `LUMI_FILE_TOOL`); off → the tools + `/journal` are **absent**, the turn unchanged. |
| **Never raises** | Every path returns a string; an I/O / cap / missing-entry error degrades to an error string and the turn completes. |
| **No contract change** | `set_state` stays terminal; the reply is still `{reply, emotion, intensity}` — the v0.3 contract test passes verbatim. No new memory record (the dated file *is* the store, in its own journal root). |

---

## Config

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_JOURNAL` | Turn the journal tools (+ `/journal`) on | `off` |
| `LUMI_JOURNAL_DIR` | The **dedicated** journal root (per-user subdirs under it); outside the file sandbox | `.lumi/journal` |
| `LUMI_JOURNAL_MAX_CHARS` | Cap on a single `journal_write` body | `4000` |

Reuses the `safe_path` guard and the v0.6 mood / v0.8 biorhythm config, but writes to its **own** root
(`LUMI_JOURNAL_DIR`) — it does **not** require `LUMI_FILE_TOOL` and is unaffected by it. Can be on
**alongside** the file / wiki / image / news / web tools.

---

## Relationship to the v6.6 evolution

This local tool is the **foundation**; the grand form remains a **later evolution** (v6):

- **v6.1 gallery** can ingest these per-user `journal/<date>.md` files as `text` artifacts with an
  **admin-only** access level — the journal becoming part of the one creative store.
- **v6.3 image** can attach an optional **mood drawing** to a day's entry, stored beside it in the gallery.
- **v3.5 admin panel** adds **admin-only reading** of the journal across sessions (her truly private inner
  life, never shown to users) — the original v6.6 promise, now built **on top of** the shipped local tool
  rather than from scratch.

So nothing in the v6 vision is lost; it is re-rooted on a tool that works **today**, per-user and local,
instead of waiting for the whole server + creative layer.

---

## Mapping to the roadmap

**v0.28 — Journal tool (day-summary diary + read-by-date, auto-stamped mood/biorhythm/forecast)**, a
reply-path tool on the v0.19 loop + the `/journal` command; off by default, local, mocked in tests. It
composes the metadata header from the v0.6 mood + v0.8 biorhythms + v0.4 clock and writes through the
v0.19/v0.20 file sandbox — **all shipped**. Per-user, isolated, non-destructive; off by default → behaves
exactly like today. Depends on **v0.6** (mood/resolution + reading), **v0.8** (biorhythms), **v0.19/v0.20**
(the file tool-loop + non-destructive writes), **v0.4** (the clock). The grander admin-panel / gallery /
mood-drawing literary form is the **v6.6 evolution** above.
