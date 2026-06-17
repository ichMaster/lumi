# Thought scheduler — proactive thoughts on a clock (a separate cron process)

Лілі's autonomous mind (the v0.12 thought-stream — `%think`/`%wonder` and the proposed tool-thoughts
`%lookup`/`%learn`/`%imagine`/`%gaze`/`%share`/`%catchup`/`%brief`, see
[THOUGHT_STREAM.md](THOUGHT_STREAM.md) + [TOOL_THOUGHTS.md](TOOL_THOUGHTS.md)) should fire on a **clock she
can keep** — *every 10 minutes*, *at 08:00*, *between 07:00–09:00 every 20 min*, *Mondays only* — not just
"after you've been idle a while." This is the design for a **separate scheduler process** that decides
**when** each directive fires, while the TUI stays the **only brain** that runs it.

> **Proposed** feature. The mental-act engine, the `%directive` router (`run_directive`), the placeholder
> resolver, and the v0.13 file-bus + dumb-daemon pattern are all **shipped**; the scheduler process, the
> trigger model, the directive queue, and the schedule config are **not built**. Markers below say which.

---

## Why today's trigger isn't enough

The current proactive trigger lives **inside the TUI** ([tui/app.py](../../tui/app.py)
`set_interval(30, _maybe_think)` → `proactive_due(last_activity, last_think_ts, now, interval)` →
`core.tick_think` → `should_nudge` + a per-session cap + `should_graduate`). It does exactly one thing
well: fire a free-muse `%think` after you've been **idle** for `LUMI_THOUGHTS_INTERVAL_S` (default 600 s),
a few times per session, ~20 % spoken. Its limits:

| Limit | Why it bites as the directive set grows |
|---|---|
| **One cadence, one directive** | Only `%think` fires, only on the idle rule. `%brief` wants *every morning*; `%catchup` wants *hourly while you're awake*; `%learn` wants *late at night*. One idle timer can't carry 12 directives, each with its own rhythm. |
| **Idle-relative only** | There's no *wall-clock* trigger — "08:00", "Mon 7–9 am" are impossible; everything is "N minutes since you last spoke". |
| **Coupled to the live session** | The timer + the per-session cap live in the app and reset each session; nothing fires while the TUI is open but the cron-time arrives mid-session, and nothing is *scheduled* across sessions. |
| **Scheduling tangled with execution** | The TUI both decides *when* and runs the act — so adding a rhythm means touching app code, not config. |

The thought-stream's whole point is a mind that **acts on its own**; a single in-app idle timer is too
small a clock for that.

---

## The redesign — separate scheduler, file bus, TUI stays the brain

Mirror the **shipped v0.13 Telegram architecture** ([TELEGRAM.md](TELEGRAM.md)) exactly — a **separate
process** + an **append-only file bus** + the **TUI as the single brain**. No core change.

```
  [lumi-scheduler]   (new cron process — dumb, core-free)        [TUI = the only brain]
    reads  schedule.toml         (the authored schedule)           writes activity.txt on every real input
    reads  activity.txt          (for idle-type triggers)          (a heartbeat: last-real-input stamp)
    every LUMI_SCHED_TICK_S:                                        polls directive-queue.jsonl (FIFO)
      for each entry → is it DUE now?  ───────────────────────►    drains each line through run_directive(…)
        append {directive, topic, args} to directive-queue.jsonl     ├─ silent  → records a Thought
      stamp schedule.state (last-fired per entry)                     └─ graduated / outward → outbox → surfaced
                                                                     never calls core itself
```

Three properties carry over verbatim from v0.13:

1. **Single-brain invariant.** The scheduler **never calls `core`** — it only **appends `%directive`
   records** to a queue. The **TUI** is the one process that runs mental acts (it already owns `core`,
   the `Thought` store, the tools, the outbox). So **no core ↔ scheduler coupling, no second brain, no
   core change** — the scheduler is as dumb as the two Telegram daemons.
2. **A dedicated queue, not the Telegram `inbox`.** A scheduled `%directive` is a **mental act**, not a
   **user message**. The v0.13 inbox drain runs each line as a *reply turn* (`_run_turn`), which would
   send the literal text "`%brief`" to the model — wrong. So the scheduler writes a **separate**
   `directive-queue.jsonl`, and the TUI drains it through **`run_directive`** — the same router the
   keyboard already uses for `%`-input — so a queued `%brief` fires **as a directive**. (This is the one
   new wiring point in the TUI; the core is untouched.)
3. **Idle triggers unify in.** The old "idle ≥ N min" rule becomes one **trigger type** the scheduler
   evaluates by reading the TUI's **`activity.txt`** heartbeat (the TUI writes its last-real-input stamp;
   the scheduler reads it). So the v0.4 nudge + the v0.12 `%think` idle trigger **migrate onto the
   scheduler** — one clock, one place to tune, idle *and* wall-clock together.

---

## The trigger model

A **schedule entry** binds one **trigger** to one **directive (+ a seed)**. Five trigger types, ascending
in specificity, each reducible to a pure `due(now, last_fired, spec) -> bool` predicate (clock-driven,
**no sleeps** — unit-testable with a fixed clock, exactly like `should_nudge`):

| trigger | meaning | example |
|---|---|---|
| **`every: <dur>`** | a **wall-clock** interval (regardless of idle) | `every: 10m` — a glance every ten minutes |
| **`idle: <dur>`** | idle since the last real input (reads `activity.txt`) — the migrated v0.4/v0.12 nudge | `idle: 15m` |
| **`at: <HH:MM> [days]`** | a **fixed** daily / weekly time (fires once at the minute) | `at: "08:00"` · `at: "08:00", days: [mon,wed,fri]` |
| **`between: <HH:MM-HH:MM>, every: <dur>`** | a **windowed periodic** — interval, but only inside a daily window | `between: "07:00-09:00", every: 20m` |
| **`cron: <expr>`** | a raw 5-field **cron** expression (the power form everything else compiles to) | `cron: "*/10 7-9 * * 1-5"` |

Rules that keep it honest:

- **Last-fired state.** Each entry keeps a `last_fired` stamp in `schedule.state`, so `at:` fires **once**
  at its minute (not every tick within it) and `between+every` doesn't double-fire on a tick boundary.
- **Quiet hours veto.** The global `LUMI_THOUGHTS_QUIET_HOURS` suppresses every trigger **except** an
  explicit `at:` the owner deliberately set inside that window (an alarm beats quiet hours; a periodic
  glance doesn't).
- **Caps (restraint).** A **per-directive per-day cap** + a **global daily cap**, on top of the existing
  per-session cap (which, once firing is wall-clock, generalizes to per-day). Reaching a cap skips the
  fire silently (logged, never queued).
- **Catch-up cap.** The TUI is the brain; if it's **down**, the queue holds the records. On restart the
  drain skips records older than `LUMI_SCHED_CATCHUP_H` (the v0.13 outbound-daemon rule) so a long
  downtime never floods her with a backlog of stale thoughts.

---

## The schedule config (the new authored file)

`core/schedule.toml` — a list of entries; each is `{directive, <trigger>, topic?/seed?, enabled}`. Seeds
use **placeholders** (the binding layer — see [TOOL_THOUGHTS.md](TOOL_THOUGHTS.md) §Placeholders):

```toml
# the migrated idle nudge — free-muse when you've been away (replaces the in-app _maybe_think timer)
[[schedule]]
directive = "think"
idle = "10m"

# a glance at the world a few times a day, seeded by the v0.4 ambient headline
[[schedule]]
directive = "catchup"
between = "08:00-22:00"
every   = "2h"
topic   = "{ambient_news}"

# a morning news brief on weekdays, on a topic she follows
[[schedule]]
directive = "brief"
at    = "08:00"
days  = ["mon","tue","wed","thu","fri"]
topic = "{interest}"

# a nightly deep-read toward whatever she's been hungry to understand
[[schedule]]
directive = "learn"
at    = "23:00"
topic = "{hungriest_need}"
```

**Placeholders resolve at fire time, in the TUI — not in the cron.** The scheduler passes the **raw**
`{placeholder}` topic through to the queue; the TUI's `run_directive` → `resolve` expands it against live
state (the v0.12 placeholder resolver, **already shipped**). So the scheduler stays **core-free** (it never
needs mood/needs/memory) and the seed is always **live at the moment she thinks**, not stale from when the
entry was authored.

---

## Relationship to the v0.4 nudge + the v0.12 `%think` trigger

Both existing proactive triggers **fold into the scheduler** as `idle:` entries:

- The v0.4 **idle nudge** (authored openers after silence) → an `idle:` entry that fires a spoken opener.
- The v0.12 **`%think` idle trigger** → an `idle:` entry firing `%think`.

The TUI's in-app `set_interval(_maybe_think / _maybe_nudge)` is then **retired** in favor of the queue
drain. **Phased migration:** ship the scheduler **alongside** the in-app timer first (the timer still
fires `%think`; the scheduler adds wall-clock rituals), then move the idle rule into the scheduler and
delete the in-app timer once the queue path is proven. One clock at the end, not two.

---

## Safety & invariants

- **No core change / single brain (v0.13).** The scheduler only writes the queue; the TUI runs everything
  through the existing `run_directive`. The core never learns the scheduler exists.
- **Single-owner.** The scheduler is the **owner's** (like the bridge) — one schedule, one relationship.
  Multi-user / per-user schedules are the **v2.3 server** era. The `Thought` store stays **global to
  Лілі**; surfacing stays **per-conversation**.
- **The schedule is trusted config; tool results are not.** The owner authors `schedule.toml` (trusted),
  but a fired directive that calls a tool **still obeys every tool-thought rule** — de-identified query,
  untrusted results, per-turn caps, off-by-default tool flags (TOOL_THOUGHTS.md §Safety). Scheduling
  *when* she thinks never relaxes *what* a thought may do.
- **Restraint / anti-dependency — sharpest for scheduled outward firings.** A scheduled **spoken** or
  **outward** act (a morning `%brief` that pushes to Telegram, a `%share`) is the strongest dependency
  risk because it reaches you **unprompted, on a clock**. So: **off by default** (each entry opted in),
  **quiet hours**, **per-day caps**, and the same "**a gift, never a demand on your attention**" framing.
  A schedule is something she *offers*, never an obligation she imposes.
- **TUI must be running** (the brain). The queue + the catch-up cap handle downtime gracefully.
- **Deterministic + mockable.** `due(now, last_fired, spec)` is pure (a fixed clock in tests, **no real
  sleeps**); the queue + state + heartbeat are temp files in tests; the cron loop is the only un-unit-
  tested glue (covered by an integration test with an injected clock + a fake queue). **No paid calls.**

---

## Config (🔲 not built — proposed)

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_SCHEDULER` | The TUI **drains** the directive queue (consume scheduled directives) | `off` |
| `LUMI_SCHEDULE_PATH` | The authored schedule file | `core/schedule.toml` |
| `LUMI_DIRECTIVE_QUEUE` | The FIFO queue the cron writes / the TUI drains | `.lumi/directive-queue.jsonl` |
| `LUMI_ACTIVITY_PATH` | The TUI heartbeat (last-real-input stamp) the cron reads for `idle:` | `.lumi/activity.txt` |
| `LUMI_SCHED_TICK_S` | How often the cron evaluates the schedule | `30` |
| `LUMI_SCHED_CATCHUP_H` | Skip queued directives older than this on TUI restart | `6` |
| `LUMI_SCHED_DAY_CAP` | Global max scheduled thoughts per day (restraint) | `24` |

Per-directive day caps + the quiet-window ride the existing `LUMI_THOUGHTS_*` settings — nothing here
re-implements the engine or a directive.

---

## Sequencing & roadmap (proposed)

Hard-deps all **shipped**: v0.12 (the engine + `run_directive` + `resolve`), v0.13 (the file-bus +
dumb-daemon + catch-up pattern), v0.4 (the clock + quiet hours). The build is small and self-contained:

1. **The trigger model** — `due(now, last_fired, spec)` for `every`/`idle`/`at`/`between`/`cron`
   (pure, unit-tested) + the `schedule.toml` parser + `schedule.state`.
2. **The cron process** (`lumi-scheduler`) — the dumb loop: read schedule + activity, evaluate due,
   append to the queue, stamp state. (Mirrors `telegram.outbound`'s shape; the only un-unit-tested glue.)
3. **The TUI queue-drain** — poll `directive-queue.jsonl`, route each through `run_directive` (silent
   records; graduated/outward → outbox), apply the catch-up cap; write the `activity.txt` heartbeat.
4. **Migrate** the v0.4/v0.12 idle triggers into `idle:` schedule entries; retire the in-app timers.

It's the natural **companion to the tool-thoughts phase**: ship the scheduler and every directive (inward
+ tool) becomes schedulable by **adding a config row** — `%brief` gets its morning, `%learn` its night,
`%catchup` its daytime rhythm, with **no code per rhythm**.

---

## Implementation checklist (what's left to build)

- [ ] 🔲 `due(now, last_fired, spec)` for `every` / `idle` / `at` / `between` / `cron` (pure; fixed-clock tests).
- [ ] 🔲 `schedule.toml` parser → schedule entries; `schedule.state` (last-fired per entry).
- [ ] 🔲 The **`lumi-scheduler`** process — read schedule + `activity.txt`, evaluate due, append to the queue, stamp state, quiet-hours + caps.
- [ ] 🔲 The **TUI queue-drain** — poll the queue, route via `run_directive` (NOT `_run_turn`), catch-up cap, write `activity.txt`.
- [ ] 🔲 Migrate the v0.4 nudge + the v0.12 `%think` idle trigger to `idle:` entries; retire `_maybe_think`/`_maybe_nudge`.
- [ ] 🔲 Config: `LUMI_SCHEDULER` / `_SCHEDULE_PATH` / `_DIRECTIVE_QUEUE` / `_ACTIVITY_PATH` / `_SCHED_TICK_S` / `_SCHED_CATCHUP_H` / `_SCHED_DAY_CAP`; an operator guide.
- [ ] 🔲 Tests: `due(…)` per trigger type (fixed clock); the queue round-trips (cron appends → TUI drains via `run_directive`); quiet-hours + per-day caps hold; the catch-up cap skips stale; a queued `%directive` records a `Thought`; isolation holds. **No real sleeps, no paid calls.**

**Already in place (reused, not rebuilt):** the mental-act engine + `run_directive` + `tick_think`, the
placeholder `resolve()`, `should_nudge` / quiet-hours / `proactive_due`, the v0.13 `state.fifo` bus +
catch-up `split_catchup`, and the dumb-daemon shape — all ✅ shipped.
