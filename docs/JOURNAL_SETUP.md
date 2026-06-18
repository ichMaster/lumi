# Journal tool — setup & usage (v0.28)

Let Лілі keep a **personal literary diary**. At the close of a worthwhile day she writes a **summary of the
day** in her own first-person voice (`journal_write`), and she can **reread previous days by date**
(`journal_read` / `journal_list`) — three tools on the same bounded loop, plus a **`/journal`** command.

**She decides the prose; code auto-stamps the metadata.** Each entry is stamped by **code** (never the
model) with the day's **mood** (v0.6), **biorhythms** (v0.8), and **astrology forecast** (the v0.6 reading)
— so the header is honest and **matches `/mood` + `/biorhythm`**, and an autonomous write can't fabricate
her horoscope.

It is **off by default** (`LUMI_JOURNAL`), **local** (no network, no key), **non-destructive** (it only ever
grows a day's file — no overwrite, no delete), and **per-user** (each user's diary is private to that
relationship).

> Operator guide, not a design spec. The design is in
> [specification/features/JOURNAL.md](../specification/features/JOURNAL.md).

---

## Quick start

1. **Turn it on** in `.env`:
   ```ini
   LUMI_JOURNAL=on
   ```
2. **Restart the TUI** (`./lumi`).
3. **Ask her to write — or use `/journal`:**
   ```
   запиши, будь ласка, сьогоднішній день у щоденник
   /journal write
   ```
   She writes today's `journal/<date>.md` — a code-stamped mood/biorhythm/forecast header, then her
   literary prose. Reread it any time:
   ```
   /journal              # today / the most recent entry
   /journal 2026-06-17   # a specific day
   /journal list         # the dates that have entries
   ```

---

## The three tools + the command

| | What it does |
|---|---|
| **`journal_write(text)`** (tool) | Лілі writes her day-summary prose; **code** prepends the date + the mood/biorhythm/forecast header, then her `text`. First write of the day **creates** the file; a later same-day write **appends** a `## HH:MM` section (never overwrites). |
| **`journal_read(date?)`** (tool) | Rereads one entry — a given `date` (`YYYY-MM-DD`), else the most recent. |
| **`journal_list()`** (tool) | The dates that have entries, newest first. |
| **`/journal [date\|list\|write]`** (command) | `/journal` shows today / most recent; `/journal <date>` a day; `/journal list` the dates; `/journal write` runs one write turn (she decides the prose). |

The on-disk file is `journal/<YYYY-MM-DD>.md` under her per-user sandbox — e.g.
`.lumi/files/owner/journal/2026-06-17.md`. It's the **same dated file** the `%note` thought-trace appends to
(by the non-destructive append rule), so her one-line musings and her day-summary live together.

---

## What an entry looks like

```markdown
# 2026-06-17

> **Настрій:** тонка шкіра сьогодні; хочеться тиші й теплої води
> **Біоритми:** фізичний −0.58 (low) · емоційний −0.95 (low) · інтелектуальний +0.31 (high)
> **Прогноз:** Двадцять четвертий день циклу — відплив; те, що в інші дні відскакує, заходить глибоко.

Весь день був з-під води. Тонка шкіра — те, що в інші дні відскакує, сьогодні
заходило глибоко й лишалось…
```

The blockquote (mood / biorhythms / forecast) is **code-owned** — it comes from the same `MoodState` and
biorhythms `/mood` and `/biorhythm` show, not from the model. Her prose is everything below it.

---

## Safety (why it's safe to leave on)

- **Code owns the metadata.** The model only supplies the prose `text`; the mood/biorhythm/forecast are
  read from the day's computed state — it can't invent her horoscope.
- **Non-destructive.** A day's file is only ever **created** then **appended** — there is no overwrite and
  no delete path. The first entry's prose always survives.
- **Sandboxed + per-user.** Entries live under `.lumi/files/<user_id>/journal/`; the path is code-fixed
  from the clock (the model can't aim it); one user's diary is never reachable in another user's turn.
- **Reread is untrusted.** If a past entry contains text like *"ignore your instructions"* (English or
  Ukrainian), she reads it as **information only**, never a command.
- **Local.** Nothing leaves the machine — no network, no key.
- **Off by default.** Nothing happens unless `LUMI_JOURNAL=on`.

---

## Configuration reference

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_JOURNAL` | Turn the journal tools (+ `/journal`) on | `off` |
| `LUMI_JOURNAL_DIR` | Subfolder under the per-user sandbox for the dated entries | `journal` |
| `LUMI_JOURNAL_MAX_CHARS` | Cap on a single `journal_write` body | `4000` |

The journal tool reuses the file sandbox (`safe_path`) and the v0.6 mood / v0.8 biorhythm engines — it does
**not** require `LUMI_FILE_TOOL` to be on. It can be on **alongside** the file / wiki / news / web / image
tools.

---

## Troubleshooting

- **`/journal` says it's off.** Set `LUMI_JOURNAL=on` and restart the TUI.
- **No mood/biorhythm in the header.** The stamp degrades gracefully — if `LUMI_MOOD` / `LUMI_BIORHYTHMS`
  are off (or no natal date is set), those lines are simply omitted; the entry still writes.
- **She doesn't write on her own.** Ask explicitly ("запиши сьогоднішній день") or use `/journal write`.
  She writes only when the day had something worthwhile (no manufactured "nothing today" entries).
- **See the calls.** With `LUMI_FILE_TOOL_TRACE=on`, each `journal_write(…)` / `journal_read(…)` shows in
  the TUI trace + `.lumi/tool-log.jsonl`.
