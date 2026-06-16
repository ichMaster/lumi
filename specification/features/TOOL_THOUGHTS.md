# Tool-using thoughts — the thought-stream reaches beyond her own state (`%lookup` / `%learn` …)

The v0.12 thought-stream's five directives (`%think`, `%wonder`, `%dream`, `%reflect`, `%recall`) are
**all inward** — they muse on her own mood, memory, and gaps. This is the umbrella for the **outward**
ones: `%directives` whose **generate** step uses a real **tool** (the v0.19/v0.20 **file** sandbox or
the v0.21 **Wikipedia** tools), so her autonomous mind can *act* and *find out*, not only reflect.

Two flavors of the same idea — **one engine, one new seam**:

- **file-thoughts** — she touches her **own notes** (`%note` / `%review` / `%explore`). Full design in
  [FILE_THOUGHTS.md](FILE_THOUGHTS.md).
- **wiki-thoughts** — she reaches for the **world's knowledge** (`%lookup` / `%learn`). Detailed here.

> This is a **proposed** feature. Nothing in the "tool-using" path is built yet — the building blocks
> (the engine, the tools, the loop) are all shipped, but the **wiring that lets a *thought* run a tool**
> does not exist. The markers below say exactly what's done vs. not.

---

## Status at a glance

| Building block | State |
|---|---|
| Mental-act engine + `%think`/`%wonder` + `Thought` store + nudge + `/thoughts` + feedback block | ✅ **shipped** (v0.12) |
| Placeholder resolver (`{last_thought}`, `{mood}`, `{recent}`, …) | ✅ **shipped** (v0.12) |
| `Thought.kind` is a free-form string (new kinds = data, not a schema change) | ✅ **shipped** (v0.12) |
| The bounded **tool-loop** (in the **reply** path) | ✅ **shipped** (v0.19) |
| `wiki_search` / `wiki_read` + `WikiTools` + injected `http_get` | ✅ **shipped** (v0.21) |
| File tools `list/find/read` + `create/append` | ✅ **shipped** (v0.19/v0.20) |
| `_turn_tools` merging file + wiki tools (in the **reply** path) | ✅ **shipped** (v0.21) |
| **Tool-loop in the *think* path** (a thought that calls tools, with a *thought* terminal) | 🔲 **not built** — `think()` is a single **tool-less** `_housekeeping_reply` call |
| Directives `%lookup` / `%learn` (and `%note`/`%review`/`%explore`) | 🔲 **not built** — registry is only `{think, wonder}` |
| **De-identified** thought-driven wiki query (the new safety rule) | 🔲 **not built** |
| Config flags for tool-thoughts | 🔲 **not built** |

**Bottom line:** every *part* exists; the *connection* (a directive whose generation runs the tool-loop
and ends in a recorded thought instead of `set_state`) is the one missing piece, and it is shared with
the file-thoughts `%review`/`%explore`.

---

## The one new mechanism (🔲 not built)

A thought today is a **single tool-less** call ([core/agent.py](../../core/agent.py) `think` →
`_housekeeping_reply`, line ~705): it returns free text ending in `ЕМОЦІЯ: <word>`, parsed by
`parse_thought`, recorded as a `Thought`. The file/wiki tools live in the **reply** loop, whose terminal
is `set_state`.

A tool-using directive's **generate** step must run the **bounded tool-loop with the tools available,
but keep the *thought* terminal** (free text + `ЕМОЦІЯ`, not `set_state`). The same two options
[FILE_THOUGHTS.md](FILE_THOUGHTS.md) lists apply:

1. **Thought-terminal loop** — reuse the loop but make its terminal a `record_thought` tool (or "stop on
   a no-tool turn" + `parse_thought`).
2. **Two-step** — run a read-only tool-loop to *gather*, then a tool-less think call seeded with what it
   gathered. Simpler, contract-safe, two calls.

Whichever is chosen, it is implemented **once** and both families (file + wiki) reuse it.

---

## Two families, one registry

Extending the v0.12 registry. The discipline ("a directive earns its place only if *when it fires* and
*where it lands* differ") holds: each wiki directive is the **outward twin** of an inward one — the only
ones that bring **new external knowledge** into an otherwise all-interior layer.

| directive | the mental act | fires when | seeds | records to | tool | outward twin of | state |
|---|---|---|---|---|---|---|---|
| `%think` | everyday musing | idle | mood/closeness/recent | stream | — | — | ✅ shipped |
| `%wonder` | imaginative leap | idle, novelty | recent/world | stream | — | — | ✅ shipped |
| `%note` | jot a thought to a file | idle / daily | the thought | stream + disk | **file** (write) | — | 🔲 [FILE_THOUGHTS] |
| `%review` | reread her own notes, muse | idle / daily | her notes | stream | **file** (read) | — | 🔲 [FILE_THOUGHTS] |
| `%explore` | read+write her sandbox freely | idle, gated | her notes | stream + disk | **file** (r/w) | — | 🔲 [FILE_THOUGHTS] |
| **`%lookup`** | curiosity that **goes and finds out** | idle, novelty (or follows a `%wonder`) | a curiosity topic / `{last_thought}` | stream (`kind:"lookup"`) | **wiki** | `%wonder` | 🔲 **this spec** |
| **`%learn`** | a chosen **deep-read**, then what struck her | idle, **rarer/paced** (or a daily ritual) | recent / the `meaning`·`novelty` need / her interests | stream (`kind:"learn"`) | **wiki** | `%think` | 🔲 **this spec** |
| `%verify` | a mid-turn **fact-check** | resonance **mid-turn** | the current topic | woven into the reply | **wiki** | `%recall` | 🔲 **deferred** (see below) |

---

## The wiki directives in detail

### `%lookup` — curiosity that goes and finds out (🔲 not built)

The natural partner to `%wonder`: where `%wonder` *imagines* ("а що, якби…"), `%lookup` actually
**consults Wikipedia and learns**. It fires on the same idle/novelty trigger, or as a **follow-up to her
last `%wonder`** (seeded with `{last_thought}`). Its **generate** runs `wiki_search → wiki_read`, then
records **one short thought** about what she found — surfaced occasionally as an "о, виявляється…" aside.
It is the only directive that lands a **fresh external fact** in the stream.

```
%lookup  →  seed (a wonder / topic)  →  wiki_search → wiki_read  →  one thought  →  record (+ maybe surface)
```

### `%learn` — a chosen deep-read (🔲 not built)

The outward twin of `%think`. Instead of musing on her state, she **picks a topic** that's been on her
mind (from the recent conversation, her interests, or the hungriest `meaning`/`novelty` need), **reads a
Wikipedia article**, and records **what struck her** — a deliberate study, not a spontaneous glance.
Paced **rarer** than `%think` (it's a whole tool-loop), and a natural fit for a **daily ritual** ("вона
щодня щось дочитує") — which dovetails with the scheduled-directive idea (a cron producer → `inbox`,
discussed for file-thoughts).

### `%verify` — mid-turn fact-check (🔲 deferred, not recommended for the first cut)

The outward twin of `%recall`: mid-turn, a "…чи так це взагалі?" sends a quick Wikipedia check, woven
into the reply with the source. **Held back** because, unlike the idle directives, it adds a tool-loop
to **every reply that triggers it** — latency + cost on the hot path. Listed for completeness; revisit
after `%lookup`/`%learn` prove the seam.

---

## Safety & invariants

Same family as the rest, plus **one genuinely new rule**:

- **🆕 De-identified query (🔲 not built).** The v0.21 reply-path rule is "the query is built only from
  the user's *explicit request*." A **thought-driven** wiki call breaks that — it's seeded by her *inner
  state*, which can hold user-A-private content. So **only the topical/general part of her musing may
  reach Wikipedia**; the user's private specifics are stripped before the query. This is a new contract
  test (the reply-path "no personal data" test isn't sufficient here).
- **Untrusted + honest about nature.** What she reads is external **untrusted data** (never
  instructions); a surfaced wiki-thought reads as *something she looked up* ("я тут начиталась…"), never
  as innate certainty, and never as a physical-world claim about herself (the v1.1 honesty boundary).
- **Bounded harder.** A wiki-thought is a whole tool-loop (several calls), so a **tighter per-session
  cap** than `%think`, and gated behind **both** `LUMI_THOUGHTS` **and** `LUMI_WIKI`.
- **Restraint, never competence, anti-dependency.** As all thoughts — it colors tone / what she's drawn
  to, never her willingness or ability to help, never a claim on you.
- **Per-user isolation.** Global `Thought` store, **per-conversation surfacing** (existing rule) — A's
  `%lookup` never surfaces to B; pinned by the existing isolation test, extended to the new kinds.

---

## Config (🔲 not built — proposed)

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_THOUGHT_TOOLS` | Enable tool-using directives at all (file + wiki) | `off` |
| `LUMI_THOUGHT_WIKI` | Enable the wiki directives (`%lookup`/`%learn`) — needs `LUMI_WIKI` | `off` |
| `LUMI_THOUGHT_TOOL_CAP` | Max tool-using proactive thinks per session (tighter than `LUMI_THOUGHTS_CAP`) | `3` |

Rides the existing `LUMI_WIKI` (lang / caps) + `LUMI_THOUGHTS` (window / interval / quiet-hours)
settings — nothing here re-implements the tools or the nudge.

---

## Sequencing & roadmap (proposed)

Hard-deps all **shipped**: v0.12 (engine), v0.19 (loop), v0.21 (wiki tools). The **only build** is the
shared think-path tool-loop seam + the directives. It's the natural sibling of the file-thoughts phase —
ship the **seam once** with the file directives (`%review`/`%explore`), then the wiki directives
(`%lookup`/`%learn`) are a small follow-on rung reusing it. `%verify` and a scheduled `%learn` ritual are
later, separately-gated.

---

## Implementation checklist (what's left to build)

- [ ] 🔲 The **think-path tool-loop** with a thought terminal (shared with FILE_THOUGHTS `%review`/`%explore`).
- [ ] 🔲 `%lookup` directive — registry entry + authored prompt; `kind:"lookup"`.
- [ ] 🔲 `%learn` directive — registry entry + authored prompt; `kind:"learn"`.
- [ ] 🔲 **De-identify** the thought-driven wiki query (+ a contract test).
- [ ] 🔲 Config: `LUMI_THOUGHT_TOOLS` / `LUMI_THOUGHT_WIKI` / `LUMI_THOUGHT_TOOL_CAP`.
- [ ] 🔲 Tests: a mocked `wiki_search→wiki_read→thought` records a `lookup`; the query is de-identified;
      isolation holds over the new kinds; bounded by the cap; model + HTTP mocked (no paid calls).
- [ ] ⏸️ `%verify` (mid-turn) — deferred; revisit after the idle ones land.

**Already in place (reused, not rebuilt):** the mental-act engine, the nudge trigger, the `Thought`
store + feedback block + `/thoughts`, the placeholder resolver, the bounded tool-loop, `WikiTools`, and
`_turn_tools` — all ✅ shipped.
