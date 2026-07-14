# Thought scheduler — proactive thoughts on a clock (an in-TUI module)

Лілі's autonomous mind (the v0.12 thought-stream — `%think`/`%wonder` and the proposed tool-thoughts
`%lookup`/`%learn`/`%imagine`/`%gaze`/`%share`/`%catchup`/`%brief`, see
[THOUGHT_STREAM.md](THOUGHT_STREAM.md) + [TOOL_THOUGHTS.md](TOOL_THOUGHTS.md)) should fire on a **clock she
can keep** — *every 10 minutes*, *at 08:00*, *between 07:00–09:00 every 20 min*, *Mondays only* — not just
"after you've been idle a while." This is the design for an **in-TUI scheduler module** that decides
**when** each directive fires and runs it **in-process** — the TUI is already the **only brain**, so the
clock lives in it, not a separate daemon.

> **Proposed** feature. The mental-act engine, the `%directive` router (`run_directive`), and the
> placeholder resolver are all **shipped**; the **in-TUI scheduler module**, the trigger model, the **tick
> service**, and the schedule config are **not built**. Markers below say which.

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

## The design — an in-TUI scheduler module, no separate process

The TUI is already the **only brain** — it owns `core`, the `Thought` store, the tools, the outbox, and the
`run_directive` router. The clock that decides *when* a directive fires belongs **in that same process**,
not in a separate daemon. (v0.13's Telegram daemons earn their separateness — messages arrive when the TUI
is down, so an always-on receiver is needed. **Scheduling has no such property**: the TUI is the only thing
that can *run* a directive, and almost nothing must *fire* during downtime — so a separate scheduler would
buy only IPC, an `activity.txt` heartbeat, a `directive-queue.jsonl`, and a flood/liveness problem, for a
thing the brain can do in-process.) **No core change.**

```
  [TUI = the only brain]   — owns core · Thought store · tools · outbox · run_directive
    knows its OWN last-input (in memory)                         — no activity.txt
    on a timer (LUMI_SCHED_TICK_MS):
      for each schedule.toml entry → due(now, last_fired, spec)?
        run_directive(directive, args)  ──►  ├─ silent             → records a Thought
                                             └─ graduated/outward  → outbox → surfaced
        stamp last_fired  (in memory + schedule.state)
    on startup: catch-up pass — fire wall-clock entries missed while closed (≤ LUMI_SCHED_CATCHUP_H)
    on a FAST timer (LUMI_SCHED_TICK_FAST_MS):
      run EPHEMERAL code handlers (e.g. %update_state — a callback, not a model directive) — fire-and-forget, not persisted, no-op if missed
```

Three properties, simpler than v0.13's:

1. **One process, one brain.** No daemon, no IPC. The scheduler is an **in-TUI module** that calls
   `run_directive` **directly** — the same router the keyboard uses, so a scheduled `%brief` fires **as a
   directive**, not as the literal text "`%brief`" through the reply path.
2. **No bus files.** There is **no `directive-queue.jsonl`** (nothing to hand to another process) and **no
   `activity.txt`** (the TUI reads its **own** last-input from memory — `idle:` triggers evaluate against
   it directly). The only persisted file is a small **`schedule.state`** (last-fired per entry) — *not* a
   bus, just the module's own state, read once on startup for the **catch-up pass**.
3. **Two cadences.** A normal tick (`LUMI_SCHED_TICK_MS`) evaluates `due()` for the authored schedule
   (durable acts — a miss is recovered by the startup catch-up). A **fast tick**
   (`LUMI_SCHED_TICK_FAST_MS`) runs **ephemeral code handlers** like **`%update_state`** (a registered
   callback, **not** a model directive — silent: no `Thought`, no model call) — fire-and-forget,
   never persisted, **a no-op if missed** (the work is a split-invariant advance-to-`now`; her time
   flows only while the TUI runs — state saved on close, resumed on start, v1.7). So the v0.4 nudge + the v0.12 `%think` idle trigger **fold in** as
   `idle:` entries — one clock, one place to tune, idle *and* wall-clock together.

**Why not a separate always-on scheduler?** It would have to either (a) run while the TUI is down — but it
can't *execute* anything then (the brain is the TUI), so it would only pile stale entries on disk — or (b)
own state itself, which rebuilds the heavy thing (process lifecycle, cron↔core consistency, crash
recovery). A genuinely always-on scheduler belongs at **v2 (the server)**, where the brain *is* always-on;
there the same `due()` + `run_directive` + the v1.7/v1.9 `update(state, now)` simply move into the server loop,
unchanged.

---

## The trigger model

A **schedule entry** binds one **trigger** to one **directive (+ a seed)**. Five trigger types, ascending
in specificity, each reducible to a pure `due(now, last_fired, spec) -> bool` predicate (clock-driven,
**no sleeps** — unit-testable with a fixed clock, exactly like `should_nudge`):

| trigger | meaning | example |
|---|---|---|
| **`every: <dur>`** | a **wall-clock** interval (regardless of idle) | `every: 10m` — a glance every ten minutes |
| **`idle: <dur>`** | idle since the last real input (the TUI's **in-memory** last-input) — the migrated v0.4/v0.12 nudge | `idle: 15m` |
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

# the OPEN directive — any custom task you author, on any clock (see TOOL_THOUGHTS §%prompt)
[[schedule]]
directive = "prompt"
at    = "08:00"
topic = "напиши коротке хайку про сьогоднішню погоду: {weather}"
```

The **`%prompt`** directive ([TOOL_THOUGHTS.md](TOOL_THOUGHTS.md) §The open directive) is what makes the
scheduler open-ended: instead of only the authored directives, you can schedule **any instruction** — the
topic *is* the instruction. A scheduled `%prompt` is "ask Лілі to do *X* every morning," with placeholders
filling in the live seed. (Trusted because the owner authored it; tool results it pulls stay untrusted.)

**Placeholders resolve at fire time, in-process.** The schedule entry keeps the `{placeholder}` topic
**raw**; only at fire time does `run_directive` → `resolve` expand it against live state (the v0.12
placeholder resolver, **already shipped**). So the schedule stays a **static seed** (it never embeds
mood/needs/memory) and the resolved seed is always **live at the moment she thinks**, not stale from when
the entry was authored.

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
- **TUI must be running** (the scheduler *is* the TUI). Downtime is handled by the **startup catch-up**
  (durable acts) + the idempotent `update` (ephemeral ticks just don't fire while it's down — a no-op).
- **Deterministic + mockable.** `due(now, last_fired, spec)` is pure (a fixed clock in tests, **no real
  sleeps**); `schedule.state` is a temp file in tests; the in-TUI timer loop is the only un-unit-tested
  glue (covered by an integration test with an injected clock). **No paid calls.**

---

## Config (🔲 not built — proposed)

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_SCHEDULER` | The in-TUI scheduler runs scheduled directives | `off` |
| `LUMI_SCHEDULE_PATH` | The authored schedule file | `core/schedule.toml` |
| `LUMI_SCHED_TICK_MS` | How often the in-TUI scheduler evaluates the schedule, in **milliseconds** | `30000` |
| `LUMI_SCHED_TICK_FAST_MS` | The **fast** tick for ephemeral code handlers (e.g. `%update_state`), in **milliseconds** | `60000` |
| `LUMI_SCHED_CATCHUP_H` | On startup, fire wall-clock entries missed within this window (older → skipped) | `6` |
| `LUMI_SCHED_DAY_CAP` | Global max scheduled thoughts per day (restraint) | `24` |

(No `LUMI_DIRECTIVE_QUEUE` / `LUMI_ACTIVITY_PATH` — there is no bus and no heartbeat file. The scheduler's
only state is a small `schedule.state` (last-fired per entry), read once on startup for the catch-up pass.)

Per-directive day caps + the quiet-window ride the existing `LUMI_THOUGHTS_*` settings — nothing here
re-implements the engine or a directive.

**On the tick granularity.** The tick is configured in **milliseconds** for fine control (a tight tick in
tests, future sub-minute triggers, or just tuning), but the practical **scheduling floor is the minute** —
the finest a trigger resolves (`at: "08:00"`, a 5-field `cron`, `every/idle` durations). So keep it
**≤ 60 000 ms** or an `at:`/cron-minute target can be skipped; **~30 000 ms (two ticks per minute)** is the
recommended default — it leaves a 2× margin against drift/missed ticks without re-reading the schedule for
no gain (a tick is cheap: small file reads + the pure `due()` predicate, no core, no network). Going below
~15 000 ms buys no precision since nothing schedules sub-minute. The `last_fired` state keeps repeated ticks
**within the same minute idempotent**, so a sub-minute tick can never double-fire an `at:` entry.

---

## Sequencing & roadmap (proposed)

Hard-deps all **shipped**: v0.12 (the engine + `run_directive` + `resolve`), v0.4 (the clock + quiet
hours). The build is small and self-contained:

1. **The trigger model** — `due(now, last_fired, spec)` for `every`/`idle`/`at`/`between`/`cron`
   (pure, unit-tested) + the `schedule.toml` parser + `schedule.state`.
2. **The in-TUI scheduler loop** — a timer evaluates `due()` each tick and calls `run_directive`
   **directly** (silent → a `Thought`; graduated/outward → outbox); a **startup catch-up pass**; reads the
   TUI's own in-memory last-input for `idle:`. The only un-unit-tested glue (one integration test).
3. **The tick service** — a fast in-TUI timer for **ephemeral code handlers** (`%update_state` —
   registered callbacks, not model directives): fire-and-forget, not persisted, a no-op if missed.
4. **Migrate** the v0.4/v0.12 idle triggers into `idle:` schedule entries; retire the in-app timers.

It's the natural **companion to the tool-thoughts phase**: ship the scheduler and every directive (inward
+ tool) becomes schedulable by **adding a config row** — `%brief` gets its morning, `%learn` its night,
`%catchup` its daytime rhythm, with **no code per rhythm**.

---

## Implementation checklist (what's left to build)

- [ ] 🔲 `due(now, last_fired, spec)` for `every` / `idle` / `at` / `between` / `cron` (pure; fixed-clock tests).
- [ ] 🔲 `schedule.toml` parser → schedule entries; `schedule.state` (last-fired per entry).
- [ ] 🔲 The **in-TUI scheduler loop** — a timer evaluates `due()` and calls `run_directive` directly (NOT `_run_turn`); a **startup catch-up pass**; quiet-hours + caps; reads the TUI's in-memory last-input for `idle:`.
- [ ] 🔲 The **tick service** — a fast in-TUI timer for ephemeral **code handlers** (`%update_state` — callbacks, not model directives): fire-and-forget, not persisted, collapse a backlog.
- [ ] 🔲 Migrate the v0.4 nudge + the v0.12 `%think` idle trigger to `idle:` entries; retire `_maybe_think`/`_maybe_nudge`.
- [ ] 🔲 Config: `LUMI_SCHEDULER` / `_SCHEDULE_PATH` / `_SCHED_TICK_MS` / `_SCHED_TICK_FAST_MS` / `_SCHED_CATCHUP_H` / `_SCHED_DAY_CAP`; an operator guide. (No `_DIRECTIVE_QUEUE` / `_ACTIVITY_PATH` — no bus.)
- [ ] 🔲 Tests: `due(…)` per trigger type (fixed clock); a due entry runs through `run_directive` → records a `Thought`; quiet-hours + per-day caps hold; the **startup catch-up** skips stale + fires the most-recent due; the **tick service** is fire-and-forget (a missed ephemeral tick is a no-op; idempotent `update` advances once); isolation holds. **No real sleeps, no paid calls.**

**Already in place (reused, not rebuilt):** the mental-act engine + `run_directive` + `tick_think`, the
placeholder `resolve()`, `should_nudge` / quiet-hours / `proactive_due`, and the `set_interval` timer
pattern the in-TUI scheduler + tick service ride — all ✅ shipped. (No file bus / daemon is needed.)
