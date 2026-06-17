# Tool-using thoughts — the thought-stream reaches beyond her own state (`%lookup` / `%learn` / `%imagine` / `%gaze` / `%share` …)

The v0.12 thought-stream's five directives (`%think`, `%wonder`, `%dream`, `%reflect`, `%recall`) are
**all inward** — they muse on her own mood, memory, and gaps. This is the umbrella for the **outward**
ones: `%directives` whose **generate** step uses a real **tool** (the v0.19/v0.20 **file** sandbox, the
v0.21 **Wikipedia** tools, or the v0.22–v0.24 **image** tools), so her autonomous mind can *act*, *find
out*, and *make*, not only reflect.

Three flavors of the same idea — **one engine, one new seam**:

- **file-thoughts** — she touches her **own notes** (`%note` / `%review` / `%explore`). Full design in
  [FILE_THOUGHTS.md](FILE_THOUGHTS.md).
- **wiki-thoughts** — she reaches for the **world's knowledge** (`%lookup` / `%learn`). Detailed here.
- **image-thoughts** — she **sees, makes, and shares pictures** (`%imagine` / `%gaze` / `%share`), on the
  v0.22 (`view_image`) / v0.23 (`generate_image`) / v0.24 (`send_image`) tools. Detailed here.

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
| Image tools `view_image` / `generate_image` / `send_image` (+ the `ImageGen` seam + the `telegram_sink`) | ✅ **shipped** (v0.22/v0.23/v0.24) |
| `_turn_tools` merging file + wiki + image tools (in the **reply** path) | ✅ **shipped** (v0.21/v0.24) |
| **Tool-loop in the *think* path** (a thought that calls tools, with a *thought* terminal) | 🔲 **not built** — `think()` is a single **tool-less** `_housekeeping_reply` call |
| Directives `%lookup` / `%learn` / `%imagine` / `%gaze` / `%share` (and `%note`/`%review`/`%explore`) | 🔲 **not built** — registry is only `{think, wonder}` |
| **De-identified** thought-driven external query/prompt (wiki **and** image-gen) | 🔲 **not built** |
| Config flags for tool-thoughts | 🔲 **not built** |

**Bottom line:** every *part* exists; the *connection* (a directive whose generation runs the tool-loop
and ends in a recorded thought instead of `set_state`) is the one missing piece, and it is shared across
**all three families** — file (`%review`/`%explore`), wiki (`%lookup`/`%learn`), and image
(`%imagine`/`%gaze`/`%share`). Build the seam once; the directives are thin registry entries on top.

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

## Three families, one registry

Extending the v0.12 registry. The discipline ("a directive earns its place only if *when it fires* and
*where it lands* differ") holds: each tool directive is the **outward twin** of an inward one — the only
ones that bring something **new from outside** into an otherwise all-interior layer (wiki brings external
*knowledge*; image brings a *made/seen/given picture*; `%share` alone brings a reach **toward you**).

| directive | the mental act | fires when | seeds | records to | tool | outward twin of | state |
|---|---|---|---|---|---|---|---|
| `%think` | everyday musing | idle | mood/closeness/recent | stream | — | — | ✅ shipped |
| `%wonder` | imaginative leap | idle, novelty | recent/world | stream | — | — | ✅ shipped |
| `%note` | jot a thought to a file | idle / daily | the thought | stream + disk | **file** (write) | — | 🔲 [FILE_THOUGHTS] |
| `%review` | reread her own notes, muse | idle / daily | her notes | stream | **file** (read) | — | 🔲 [FILE_THOUGHTS] |
| `%explore` | read+write her sandbox freely | idle, gated | her notes | stream + disk | **file** (r/w) | — | 🔲 [FILE_THOUGHTS] |
| **`%lookup`** | curiosity that **goes and finds out** | idle, novelty (or follows a `%wonder`) | a curiosity topic / `{last_thought}` | stream (`kind:"lookup"`) | **wiki** | `%wonder` | 🔲 **this spec** |
| **`%learn`** | a chosen **deep-read**, then what struck her | idle, **rarer/paced** (or a daily ritual) | recent / the `meaning`·`novelty` need / her interests | stream (`kind:"learn"`) | **wiki** | `%think` | 🔲 **this spec** |
| **`%imagine`** | **render** an inner image she's been picturing | idle, creative impulse (or follows a `%dream`/`%wonder`) | a dream/mood/`{last_thought}` | stream (`kind:"imagine"`) **+ a PNG** in her sandbox | **image** (`generate_image`, v0.23) | `%dream` | 🔲 **this spec** |
| **`%gaze`** | **look again** at a picture she has, and muse | idle, drawn back to it | a sandbox image | stream (`kind:"gaze"`) | **image** (`view_image`, v0.22) | `%review` | 🔲 **this spec** |
| **`%share`** | **choose to send you** a picture, unprompted | rare, warmth (a gift, not a demand) | a picture she made/kept | a **spoken turn** + the **photo** to your Telegram | **image** (`send_image`, v0.24) | — (the reaching-out one) | 🔲 **this spec** |
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

## The image directives in detail (🔲 not built)

Where wiki-thoughts bring in **knowledge**, image-thoughts let her autonomous mind **see, make, and
give** pictures — the inner life made visible. They reuse the **same think-path tool-loop seam**; the only
difference is *which* tool the generate step runs and where the artifact lands. All three are off unless
`LUMI_THOUGHTS` **and** `LUMI_IMAGE` are on.

### `%imagine` — render an inner image (🔲 not built)

The **visual twin of `%dream`** (and the maker-cousin of `%wonder`): instead of picturing something only
in words, she **turns it into an actual PNG**. Its generate step runs **`generate_image`** with a prompt
distilled from what she's been holding — a dream, a mood, her last `%wonder`/`%dream` (`{last_thought}`) —
saving the picture **create-only** into her `art/` sandbox and recording **one short thought** about it
("намалювала те, що наснилось… дивно і тепло"). Surfaced occasionally as a quiet "я тут дещо намалювала".
It is the only thought that lands a **made artifact** in the stream — and those PNGs seed the **v5.1
gallery**.

```
%imagine  →  seed (a dream / mood / wonder)  →  generate_image  →  one thought  →  record (+ the PNG; maybe surface)
```

**Paid** (it's a real generation), so it's the **rarest, hardest-capped** image-thought, and its prompt
is **de-identified** (see Safety — the same new rule as the wiki query).

### `%gaze` — look again at a picture she has (🔲 not built)

The **image twin of `%review`** (file): she's **drawn back** to a picture already in her sandbox — one she
made with `%imagine`/`generate_image`, or one you shared that landed there — **views it** (`view_image`)
and muses on what it stirs now. **Read-only**: it makes nothing, it only *re-sees*. The lightweight,
free, everyday image-thought (no paid call), so it can fire more often than `%imagine`.

```
%gaze  →  seed (a picture she keeps)  →  view_image  →  one thought  →  record (+ maybe surface)
```

### `%share` — choose to send you a picture (🔲 not built)

The one image-thought that **reaches out**. Unprompted, she decides a picture is **worth giving** — one
she made or kept — and sends it to your **Telegram** via **`send_image`**; it **graduates to a spoken
turn** ("глянь, що я зробила сьогодні 🌸") + the photo push. There is no inward twin: this is the
*expressive*, relational act, the natural successor to the v0.24 send tool reaching the autonomous layer.

```
%share  →  pick a picture worth giving  →  send_image  →  a spoken turn + the photo to Telegram
```

Because it **initiates contact with an artifact**, it is the **most restrained** of all directives —
rarest cap, anti-dependency front and center (a gift, never a guilt-trip or a claim on your attention),
**owner-only**, and it needs the **Telegram bridge** connected (else it's a no-op, never an error). It
follows a `%imagine`/`%gaze` naturally (share the thing she just made or revisited).

Same family as the rest, plus **one genuinely new rule**:

- **🆕 De-identified query/prompt (🔲 not built).** The v0.21/v0.23 reply-path rule is "the query (wiki)
  / prompt (image-gen) is built only from the user's *explicit request*." A **thought-driven** call breaks
  that — it's seeded by her *inner state*, which can hold user-A-private content. So **only the
  topical/creative part of her musing may reach Wikipedia or the image model** (`%lookup`/`%learn` and
  `%imagine`); the user's private specifics are stripped before the call. One new contract test covers
  both the wiki query and the gen prompt (the reply-path "no personal data" test isn't sufficient here).
- **Untrusted + honest about nature.** What she reads is external **untrusted data** (never
  instructions) — a viewed image (`%gaze`) is read the same way (embedded text is information, never a
  command). A surfaced wiki-thought reads as *something she looked up* ("я тут начиталась…"); a surfaced
  image-thought reads as *something she made/saw* ("я тут дещо намалювала…"), never as innate certainty
  and never as a physical-world claim about herself (the v1.1 honesty boundary).
- **Bounded harder; paid hardest.** A tool-thought is a whole tool-loop, so a **tighter per-session cap**
  than `%think`. `%imagine` is **paid** (a real generation), so it gets the **tightest sub-cap** of all;
  `%gaze` is free (read-only) and may fire more freely. All gated behind `LUMI_THOUGHTS` **and**
  `LUMI_IMAGE` (image) / `LUMI_WIKI` (wiki).
- **Restraint, never competence, anti-dependency — hardest for `%share`.** As all thoughts, these color
  tone / what she's drawn to, never her willingness or ability to help, never a claim on you. **`%share`
  reaches out**, so it is held to the strictest restraint: a **gift, never a guilt-trip or a demand on
  your attention**, owner-only, and a no-op (never an error) when the bridge is off.
- **Per-user isolation.** Global `Thought` store, **per-conversation surfacing** (existing rule) — A's
  `%lookup`/`%imagine` never surfaces to B; the **PNG** that `%imagine` makes lands in the **owner's**
  per-user sandbox (isolation inherited), and `%share` only ever reaches the **owner**. Pinned by the
  existing isolation test, extended to the new kinds.

---

## Config (🔲 not built — proposed)

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_THOUGHT_TOOLS` | Enable tool-using directives at all (file + wiki + image) | `off` |
| `LUMI_THOUGHT_WIKI` | Enable the wiki directives (`%lookup`/`%learn`) — needs `LUMI_WIKI` | `off` |
| `LUMI_THOUGHT_IMAGE` | Enable the image directives (`%imagine`/`%gaze`/`%share`) — needs `LUMI_IMAGE`; `%share` also needs the bridge | `off` |
| `LUMI_THOUGHT_TOOL_CAP` | Max tool-using proactive thinks per session (tighter than `LUMI_THOUGHTS_CAP`) | `3` |
| `LUMI_THOUGHT_IMAGINE_CAP` | Max **paid** `%imagine` generations per session (tightest — it costs) | `1` |

Rides the existing `LUMI_WIKI` (lang / caps) + `LUMI_IMAGE` (gen model / size / max-gen) +
`LUMI_TELEGRAM_*` (the bridge `%share` rides) + `LUMI_THOUGHTS` (window / interval / quiet-hours)
settings — nothing here re-implements the tools or the nudge.

---

## Sequencing & roadmap (proposed)

Hard-deps all **shipped**: v0.12 (engine), v0.19 (loop), v0.21 (wiki tools), v0.22–v0.24 (image tools).
The **only build** is the shared think-path tool-loop seam + the directives. It's the natural sibling of
the file-thoughts phase — ship the **seam once** with the file directives (`%review`/`%explore`), then each
family is a small follow-on rung reusing it:

1. **wiki** (`%lookup`/`%learn`) — first; the de-identified-query rule is introduced here.
2. **image** (`%gaze` → `%imagine` → `%share`) — next, in **ascending risk/cost**: `%gaze` (free,
   read-only) → `%imagine` (paid, makes an artifact — reuses the wiki de-identification rule for the gen
   prompt) → `%share` (reaches out — the strictest restraint, needs the bridge). The `%imagine` PNGs are
   an early feeder for the **v5.1 gallery**.
3. **later / separately-gated** — `%verify` (mid-turn wiki) and, once **v0.25 news** ships, a `%catchup`
   news twin (the outward "what's happening" sibling of `%lookup`).

---

## Implementation checklist (what's left to build)

- [ ] 🔲 The **think-path tool-loop** with a thought terminal (shared by all three families).
- [ ] 🔲 `%lookup` directive — registry entry + authored prompt; `kind:"lookup"`.
- [ ] 🔲 `%learn` directive — registry entry + authored prompt; `kind:"learn"`.
- [ ] 🔲 `%gaze` directive — registry entry + authored prompt; `kind:"gaze"`; runs `view_image` (read-only).
- [ ] 🔲 `%imagine` directive — registry entry + authored prompt; `kind:"imagine"`; runs `generate_image`
      (create-only PNG in the owner's sandbox; **paid → own sub-cap**).
- [ ] 🔲 `%share` directive — registry entry + authored prompt; runs `send_image` and **graduates to a
      spoken turn**; owner-only; a no-op when the bridge is off.
- [ ] 🔲 **De-identify** the thought-driven wiki query **and** the `%imagine` gen prompt (+ a contract test).
- [ ] 🔲 Config: `LUMI_THOUGHT_TOOLS` / `LUMI_THOUGHT_WIKI` / `LUMI_THOUGHT_IMAGE` / `LUMI_THOUGHT_TOOL_CAP`
      / `LUMI_THOUGHT_IMAGINE_CAP`.
- [ ] 🔲 Tests: a mocked `wiki_search→wiki_read→thought` records a `lookup`; a mocked
      `generate_image→thought` records an `imagine` (stub `ImageGen`, **no paid calls**) with a
      de-identified prompt; `%gaze` views a sandbox image; `%share` calls a **fake `telegram_sink`** and
      graduates; isolation holds over the new kinds; bounded by the caps; model + HTTP + image-gen + TTS
      all mocked.
- [ ] ⏸️ `%verify` (mid-turn) + a `%catchup` news twin (after v0.25) — deferred; revisit after the idle ones land.

**Already in place (reused, not rebuilt):** the mental-act engine, the nudge trigger, the `Thought`
store + feedback block + `/thoughts`, the placeholder resolver, the bounded tool-loop, `WikiTools`,
`ImageTools` / `ImageMaker` / `SendImageTools` (v0.22–v0.24), and `_turn_tools` — all ✅ shipped.
