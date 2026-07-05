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

### Seeds & placeholders

The `topic` is kept **raw** in the file and expanded **at fire time** against live state (mood, needs,
ambient news…), so the schedule never embeds stale state. Common placeholders: `{ambient_news}`,
`{interest}`, `{hungriest_need}`, `{weather}`. The **`%prompt`** directive makes the topic *itself* the
instruction — "ask Лілі to do X every morning."

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
