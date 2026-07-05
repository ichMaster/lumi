# Model roles — register-routed replies (talking / thinking / emotional)

Today one model answers **every** turn, whatever the turn is: "дякую, добраніч" pays the same frontier
price as "поясни, чому кеш інвалідовується", and an emotionally loaded confession gets the same engine
as a shopping list. This document specifies **model roles**: the reply is answered in one of three
**registers**, each backed by its own model, picked **automatically per turn** by a staged classifier —
with **stickiness**, so a register holds through its moment instead of flapping.

- **`talking`** — the everyday voice: small talk, logistics, short exchanges. Fast and cheap
  (e.g. Sonnet 5); this is where most turns land and where the hot prompt cache lives.
- **`thinking`** — difficult analytical tasks: architecture questions, debugging, planning, long
  multi-part problems. The frontier model with extended thinking (e.g. Opus 4.8 + thinking).
- **`emotional`** — loaded moments: vulnerability, grief, joy, conflict, closeness. The fullest
  persona (e.g. Opus 4.8) — nuance matters more than speed here.
- **`classifier`** — not a register but the router's LLM tier: a tiny, fast model (e.g. Haiku 4.5)
  consulted **only when the lexical stage can't decide**.

> **Distinct from the v0.40 op-tiers and the v0.41 profiles.** v0.40 routes the *internal operations*
> (think/mood/housekeeping); v0.41 groups whole stacks per provider. Roles route **the visible reply
> itself, per turn, by what the user's message is**. All three compose: the roles are fields *of* the
> active profile, and the op-tiers are untouched.

## The principle: registers, not tiers

The register is about **what this turn needs**, not about cost alone. Three rules keep it honest:

1. **Routing reads the user's message — never her state.** Her mood/needs color *how* she speaks
   (v0.6/v1.1); they must never decide *which brain* answers — that would make her competence
   mood-dependent (the hard never-competence rule).
2. **Escalation is invisible help, not a mode.** She doesn't announce "switching to thinking mode";
   the register shows only in the status bar and `/roles`. One Лілі, three depths.
3. **When unsure — talk.** The default register is `talking`; the classifier needs confidence to
   escalate, and any failure anywhere degrades to `talking`. Routing can never block a turn.

## Config — roles live in the profiles

Each `[profiles.*]` block in `core/models.toml` (v0.41) gains four fields — provider-homogeneous like
everything else in a profile:

```toml
[profiles.anthropic]
provider = "anthropic"
reply = "claude-opus-4-8"            # the single-model fallback (roles off)
think = "claude-sonnet-5"            # v0.40 op-tiers, unchanged
mood = "claude-sonnet-5"
housekeeping = "claude-haiku-4-5-20251001"
talking = "claude-sonnet-5"          # v0.43 roles
thinking = "claude-opus-4-8"         #   (thinking runs with extended thinking on)
emotional = "claude-opus-4-8"
classifier = "claude-haiku-4-5-20251001"
```

All four are **additive and default to `reply`** — a profile without them behaves exactly as today.
`LUMI_MODEL_ROLES` (off by default) gates the whole feature; off → the single reply model,
**byte-identical**.

## The flow — hybrid firing (waste-free by default)

```
message → LEXICAL STAGE (code, ~0 ms)
   ├─ clear talk        → fire talking only                     (short messages always land here)
   ├─ clear think/emo   → fire that model only                  (strong markers)
   └─ unsure            → fire talking ∥ classifier (parallel)
                             ├─ verdict talk / low confidence / failure → use the talking reply
                             └─ verdict think/emo → re-send to that model, discard the draft
```

**1. The lexical stage** (`core/roles.py`, driven by an authored `core/roles.md`): no LLM, table-driven —

| signal | verdict |
|---|---|
| short message (≤ ~40 chars) / a closer or greeting from the authored list («дякую», «добраніч», «привіт») | **talk** — and always classified fresh, even mid-hold |
| distress markers («мені важко», «я не можу більше», tears/crisis vocabulary — authored list) | **emotional** (fast-path, no LLM) |
| code blocks, stack traces, multi-part questions, "поясни/спроєктуй/порахуй" verbs, length ≫ | **think** |
| an explicit override (`/role think`, or authored phrases like «подумай як слід») | as asked |
| an active hold (see stickiness) and the message is substantive | the held register |
| anything else | **unsure** → the classifier |

**2. The classifier call** — one structured request to the `classifier` role returning
`{label: talk | think | emotional, confidence: 0..1}`. One call, not separate yes/no questions. Its
prompt is **minimal by design**: the user's message + the last few register labels — **no memory, no
facts, no persona state** (the no-personal-data rule; pinned by a contract test). Below
`LUMI_ROLE_CONFIDENCE` (~0.6) → talk. Malformed/failed → talk.

**3. Firing.** A lexical-clear turn costs exactly one reply request. An *unsure* turn fires `talking`
and the classifier **in parallel**: no escalation → the talking reply is already in hand (zero added
latency, the classifier cost is a tiny model); escalation → the same message goes to the chosen
register's model and the talking draft is **discarded** (the accepted waste case — rare by design,
because the lexical stage catches the clear ones). The register's model rides the normal reply call, so
the **tool-loop follows it** (v0.40-style) and the `{reply, emotion, intensity}` contract is untouched.

## Stickiness — the hold

A real moment spans turns: one deep question begets follow-ups; a hard conversation doesn't end in one
message. After an escalation the register **holds** for `LUMI_ROLE_COOLDOWN` turns (default ~3):

- A **substantive** message during the hold goes straight to the held register (lexical short-circuit —
  no classifier call, no talking draft).
- A **short** message («дякую», «ок») **bypasses** the hold — classified fresh, usually answered by
  `talking` — but the countdown **keeps running**: the next substantive message still lands on the held
  register. (A thank-you inside a heavy conversation doesn't drop her out of it.)
- A **new escalation** (emotional → thinking, or vice versa) re-targets the hold and resets the counter.
- After N substantive turns the hold expires → back to `talking`.

The hold is **per conversation, in-memory** (like the v0.10 relation read) — it does not persist across
restarts and never crosses users.

## Composition & manual control

- **`/model <x>`** (v0.37/v0.41) pins the reply model and **suspends role-routing for the session** —
  an explicit user choice always wins over automation. `/model-set <profile>` re-enables it (the new
  profile brings its own roles).
- **`/roles`** — the read-state command: the three registers with their models, the active hold and its
  remaining turns, whether routing is suspended.
- **Status bar:** the answering register when it isn't `talking` (e.g. `✦ emotional`) — the same quiet
  surfacing style as the v0.33 directive states.
- **The v0.40 op-tiers** (think/mood/housekeeping) are untouched — `%think` directives, the mood call,
  and summaries keep their own models regardless of the reply register.
- **Caching:** each register has its own prompt cache. `talking` becomes the hot one; `thinking`/
  `emotional` warm up on escalation (an accepted one-off per moment). The op-tier caches (v0.40) are
  unaffected.
- **Interfaces:** routing lives in `core.reply` — the TUI, the Telegram bridge, and the voicer all get
  it for free (core stays interface-independent).

## Bounds & invariants

- **Never competence.** The register changes *which engine* answers, never *whether* she helps; and her
  own mood/needs are **not** routing inputs — only the user's message (+ recent labels) is.
- **No personal data in the classifier prompt.** The message + the last few labels only — no memory,
  facts, closeness, or mood. Pinned by a contract test (the wiki/news de-id family).
- **Never blocks a turn.** Lexical failure → unsure; classifier failure/timeout → talk; an escalation
  failure (missing model) → the talking draft (or a plain retry on `reply`).
- **The emotion contract is untouched.** Every register answers through the same
  `{reply, emotion, intensity}` gate; the classifier's label is internal (like the raw closeness score).
- **Provider-homogeneous.** Roles are profile fields — one provider per profile (the v0.41 rule); no
  cross-engine flapping inside one conversation.
- **Off by default.** `LUMI_MODEL_ROLES=off` → single reply model, byte-identical; each role field
  defaults to `reply`.
- **Costs are bounded by design:** clear turns = 1 request; unsure-but-talk turns = 1 reply + 1 tiny
  classify; escalated turns = 1 discarded draft + 1 reply (the only double-payment, rare by
  construction). No paid calls in CI — the classifier and all registers are mocked.

## Config reference

| var | default | meaning |
|---|---|---|
| `LUMI_MODEL_ROLES` | `off` | the whole feature gate |
| `LUMI_ROLE_COOLDOWN` | `3` | substantive turns a register holds after an escalation |
| `LUMI_ROLE_CONFIDENCE` | `0.6` | the classifier floor — below it, talk |
| `LUMI_ROLES_FILE` | `core/roles.md` | the authored lexical lists (closers, distress, thinking markers, overrides) |
| (profiles) | — | `talking`/`thinking`/`emotional`/`classifier` per `[profiles.*]` block in `core/models.toml` |

## Mapping to the roadmap

**v0.43 — Model roles: register-routed replies**, after the v0.41 profiles it extends and the v0.42
scheduler. Depends on **v0.41** (profiles + `switch_profile`), **v0.40** (`_model_for` — the
op-tiers it leaves untouched), **v0.3** (the emotion gate every register answers through). The tests
mirror the phase: the lexical table, the one-call classifier (mocked, floor, failure→talk), the firing
matrix, the hold (countdown / short-message bypass / re-target / revert), byte-identical off.

**Status: proposed** (nothing built). The natural follow-ons once it ships: register-aware `%`-thought
seeds (a `{register}` placeholder) and a per-register line in the cost report (`.lumi/cache-report.md`
already tags the model per call, so the split is visible on day one).
