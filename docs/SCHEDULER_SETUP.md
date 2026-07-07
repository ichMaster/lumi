# Thought scheduler — setup guide (v0.42)

Лілі's `%directives` can fire on a **clock she keeps** — *every 10 min*, *at 08:00 on weekdays*,
*between 07:00–09:00*, a nightly deep-read — via a small **in-TUI scheduler** (no separate process, no
file bus). This is the operator guide; the design is in
[THOUGHT_SCHEDULER.md](../specification/features/THOUGHT_SCHEDULER.md).

> **Restraint is the point.** The scheduler is **off by default**, every schedule row is **off until you
> opt it in**, and quiet hours + per-day caps always apply. A scheduled act is a **gift, never a demand**.

## Turn it on

```ini
LUMI_SCHEDULER=on               # the in-TUI scheduler runs scheduled directives (default: off)
```

Then edit **`core/schedule.toml`** and set `enabled = true` on the rows you want. The scheduler needs the
**TUI running** — it *is* the TUI (the only brain). Nothing fires while it's closed; on restart a
**catch-up pass** fires fixed-time entries you missed within the last few hours.

## The schedule file (`core/schedule.toml`)

Each `[[schedule]]` row binds one **directive** to one **trigger**, plus an optional **raw seed** topic:

```toml
[[schedule]]
directive = "brief"                 # any %directive (think/wonder/catchup/brief/learn/prompt/…)
at    = "08:00"                     # the trigger (pick ONE per row)
days  = ["mon","tue","wed","thu","fri"]
topic = "{interest}"               # a seed — {placeholders} resolve AT FIRE TIME (never stored resolved)
enabled = true
```

### Triggers (pick one per row)

| trigger | meaning | example |
|---|---|---|
| `every = "10m"` | a wall-clock interval | a glance every 10 minutes |
| `idle = "15m"` | idle since your last message (the migrated nudge) | free-muse after a lull |
| `at = "08:00"` + optional `days = [...]` | a fixed daily/weekly minute (fires **once**) | a morning brief |
| `between = "07:00-09:00"` + `every = "20m"` | a windowed periodic | glances only in a window |
| `cron = "*/10 7-9 * * 1-5"` | a raw 5-field cron (minute hour dom month dow) | the power form |

Durations: `s`/`m`/`h`/`d` (e.g. `"90s"`, `"10m"`, `"2h"`). Days: `mon…sun`. A malformed row is skipped,
never fatal.

### Show it in the chat (`show = true`)

By default a scheduled act is **silent** — recorded to her thought stream (`/thoughts`) and fed into her
next reply, but not shown live. Add **`show = true`** to write the result **to the chat** as a `💭` line,
exactly like a typed **`%catchup!`**:

```toml
[[schedule]]
directive = "catchup"
between = "08:00-22:00"
every   = "2h"
topic   = "{ambient_news}"
show    = true                 # write the 💭 result to the chat (silent without it)
enabled = false
```

A `%name!` line in a `seeds` menu shows the same way (the `!` is "open" mode). **A silent row (`show`
unset **and** no `!` seed) never surfaces — it's recorded to `/thoughts` and fed into her next reply, but
never shown and never spoken.** Only a **loud** fire (`show = true` or a `%name!` seed) can surface; among
those, a `%think`/`%wonder` *graduates* to a spoken turn a fraction of the time
(`LUMI_THOUGHTS_SPOKEN_RATIO`, default 0.2 — a genuine per-fire chance) and speaks instead of showing the
`💭` line (no double-surface).

**See *when* each act runs (not just its result).** `show`/`!` write the **thought**; to also mark **the
act itself** in the chat — a dim `✦ Лілі читає новини…` line as it fires, like a typed directive — set
**`LUMI_THOUGHT_SURFACE=on`** in `.env`. Off (default) → acts run quietly (only `show`/`!` results appear);
the status line always shows the running act (`✦ %catchup · news…`) regardless.

### Seeds & placeholders

The `topic` is kept **raw** in the file and expanded **at fire time** against live state (mood, needs,
ambient news…), so the schedule never embeds stale state. Common placeholders: `{ambient_news}`,
`{interest}`, `{hungriest_need}`, `{weather}`. The **`%prompt`** directive makes the topic *itself* the
instruction — "ask Лілі to do X every morning."

### A `seeds` menu (instead of one fixed topic)

A row can carry a **`seeds`** file instead of a `directive`/`topic` — a file of `%directive` lines, one
per line. This is how the shipped idle-muse works, so she doesn't repeat one fixed thought:

```toml
[[schedule]]
seeds = "core/think_seeds.md"      # a menu of %directive lines; one picked per fire
idle  = "15m"
enabled = true
```

`core/think_seeds.md` holds one `%directive` per line (`# …` lines and blanks are ignored). The lines
can be **any** directive — the base `%think`/`%wonder` **or** the tool-thoughts — each with its own topic:

```
%think про що ми говорили сьогодні
%wonder! що б тобі хотілось створити
%learn! про симуляції і керування ними з ШІ
%catchup! технології
```

**How a `seeds` row fires (the logic), step by step:**

1. **The trigger fires** (here `idle = "15m"`) exactly like any other row — quiet hours + caps still apply.
2. **The file is re-read every fire**, so editing `think_seeds.md` while Лілі runs changes the menu **live**
   (no restart) — and `#` comments / blank lines are skipped.
3. **One line is picked at random**, avoiding an **immediate repeat** (never the same line twice in a row);
   with one line it always fires that one.
4. **The picked line runs verbatim through the `%`-router** (`run_directive`) — so `%learn! …` runs the
   learn tool-thought, `%think …` a free-muse, etc. The line's `!` and topic behave exactly as if typed.
5. **A picked line only fires if its family is enabled.** `%think`/`%wonder` need `LUMI_THOUGHTS=on`; a
   tool-thought (`%learn`/`%catchup`/`%search`/`%imagine`/`%share`/…) also needs its family flag
   (`LUMI_THOUGHT_WIKI`/`_NEWS`/`_WEB`/`_IMAGE`/…). If the family is **off**, that pick is a **silent
   no-op** — the scheduler records nothing and moves on (no error, no chat leak).
6. **Graduation** (a fraction spoken aloud) reads the **picked** line's directive: only a `%think`/`%wonder`
   pick can graduate to a spoken turn; a tool-thought stays silent/outward as usual.

> The `seeds` menu is exactly the old in-app "%think A-menu," now driven by the scheduler — one row
> instead of a hard-coded timer. Prefer a plain `directive = "…"` row when you want **one** fixed act on a
> clock; use `seeds` when you want **variety** from a rotating pool.

## Recipes — copy-paste rows

Every row needs a **directive** (or a `seeds` file) + **one trigger**, and starts `enabled = false`.

```toml
# 1) Idle muse — free-thinks after 15 min of silence, a fraction spoken (subsumes the old nudge).
[[schedule]]
seeds = "core/think_seeds.md"
idle  = "15m"
enabled = true

# 2) A world-glance a few times a day, only in waking hours, seeded by the ambient headline.
[[schedule]]
directive = "catchup"
between = "08:00-22:00"
every   = "2h"
topic   = "{ambient_news}"
enabled = false

# 3) A weekday morning news brief, on a topic she follows.
[[schedule]]
directive = "brief"
at    = "08:00"
days  = ["mon", "tue", "wed", "thu", "fri"]
topic = "{interest}"
enabled = false

# 4) A nightly deep-read toward whatever she's hungry to understand.
[[schedule]]
directive = "learn"
at    = "23:00"
topic = "{hungriest_need}"
enabled = false

# 5) A plain wall-clock heartbeat — a %wonder every 30 minutes, no matter what.
[[schedule]]
directive = "wonder"
every = "30m"
enabled = false

# 6) A weekend-only reflection (Sat/Sun 10:00).
[[schedule]]
directive = "reflect"
at    = "10:00"
days  = ["sat", "sun"]
enabled = false

# 7) An open custom task — the topic IS the instruction (%prompt), any clock.
[[schedule]]
directive = "prompt"
at    = "08:00"
topic = "напиши коротке хайку про сьогоднішню погоду: {weather}"
enabled = false

# 8) The power form — raw 5-field cron (minute hour day-of-month month day-of-week):
#    every 10 min, 07:00–09:59, Mon–Fri.
[[schedule]]
directive = "catchup"
cron = "*/10 7-9 * * 1-5"
enabled = false

# 9) A morning "alarm" that fires EVEN in quiet hours (a deliberate `at:` pierces the veto).
[[schedule]]
directive = "brief"
at    = "06:30"
topic = "{interest}"
enabled = false
```

**Notes on the recipes:**
- **`between` needs `every`** (the interval inside the window); `at` optionally takes `days`.
- **`idle` vs `every`:** `idle` counts silence since your last message; `every` is wall-clock regardless.
- **Quiet hours** (`LUMI_THOUGHTS_QUIET_HOURS`) veto `every`/`idle`/`between`/`cron` — but **not** an
  explicit `at:` you set inside the window (recipe 9), so a morning ritual still lands.
- **Sub-minute** isn't scheduled; `at`/`cron` resolve to the minute, `every`/`idle` to the second (but no
  finer than `LUMI_SCHED_TICK_MS`, default 30 s).

## Tuning

| var | default | meaning |
|---|---|---|
| `LUMI_SCHEDULER` | `off` | the whole scheduler |
| `LUMI_SCHEDULE_PATH` | `core/schedule.toml` | the authored schedule |
| `LUMI_SCHED_TICK_MS` | `30000` | how often the schedule is evaluated (keep ≤ 60000 so a minute target isn't skipped) |
| `LUMI_SCHED_TICK_FAST_MS` | `60000` | the fast tick for ephemeral code handlers (v0.42 tick service) |
| `LUMI_SCHED_CATCHUP_H` | `6` | on startup, fire fixed-time entries missed within this window (older → skipped) |
| `LUMI_SCHED_DAY_CAP` | `24` | global max scheduled thoughts per day (restraint) |

Quiet hours ride the existing `LUMI_THOUGHTS_QUIET_HOURS` — a periodic glance is vetoed inside the window,
but a deliberate `at:` you set there still fires (an alarm beats quiet hours). There is **no bus and no
heartbeat file**; the scheduler's only state is a small `.lumi/schedule.state` (last-fired per entry),
read once on startup for the catch-up.

## Notes

- **No core change / single brain.** The scheduler is a TUI module; it runs everything through the
  existing `run_directive`. A scheduled directive that calls a tool still obeys every tool-thought rule
  (de-identified query, untrusted results, per-turn caps, off-by-default tool flags).
- **Single-owner.** Like the Telegram bridge, the scheduler is the owner's — one schedule, one
  relationship. Multi-user / per-user schedules are the v2.3 server era.
- **Outward acts are sharpest.** A scheduled spoken/outward act (a morning `%brief` pushed to Telegram, a
  `%share`) reaches you unprompted — so it's off by default, capped, and quiet-hours-bound.
