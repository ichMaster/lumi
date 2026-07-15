# Tool-using thoughts — the thought-stream reaches beyond her own state (`%lookup` / `%learn` / `%imagine` / `%gaze` / `%share` / `%catchup` / `%search` / `%journal` …)

The v0.12 thought-stream's five directives (`%think`, `%wonder`, `%dream`, `%reflect`, `%recall`) are
**all inward** — they muse on her own mood, memory, and gaps. This is the umbrella for the **outward**
ones: `%directives` whose **generate** step uses a real **tool** (the v0.19/v0.20 **file** sandbox, the
v0.21 **Wikipedia** tools, the v0.22–v0.24 **image** tools, the v0.25 **news** tools, or the v0.27 **web**
tool), so her autonomous mind can *act*, *find out*, *make*, *keep up*, and *check the live web*, not only
reflect.

Five flavors of the same idea — **one engine, one new seam**:

- **file-thoughts** — she touches her **own notes and diary** (`%note` / `%review` / `%explore` /
  `%journal` — the last writes a **day-summary** via the v0.28 journal tool). Full design in
  [FILE_THOUGHTS.md](FILE_THOUGHTS.md) + [JOURNAL.md](JOURNAL.md).
- **wiki-thoughts** — she reaches for the **world's knowledge** (`%lookup` / `%learn`). Detailed here.
- **image-thoughts** — she **sees, makes, and shares pictures** (`%imagine` / `%gaze` / `%share`), on the
  v0.22 (`view_image`) / v0.23 (`generate_image`) / v0.24 (`send_image`) tools. Detailed here.
- **news-thoughts** — she **keeps up with the world** (`%catchup` / `%brief`), on the v0.25
  (`news_search` / `news_read`) Guardian tools. Detailed here.
- **web-thoughts** — she **checks the live internet** (`%search` / `%events`), on the v0.27 `web_lookup`
  (Gemini grounded search) tool. Detailed here.

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
| News tools `news_search` / `news_read` (+ the `NewsProvider` seam + the per-turn id registry) | ✅ **shipped** (v0.25) |
| Web tool `web_lookup` (Gemini grounded search; the `GeminiSearch` seam + the v0.23 Gemini caller) | 🔲 **planned** (v0.27) — the reply-path tool ships first, then these directives |
| `_turn_tools` merging file + wiki + image + news (+ web) tools (in the **reply** path) | ✅ **shipped** (v0.21/v0.24/v0.25); web at v0.27 |
| **Tool-loop in the *think* path** (a thought that calls tools, with a *thought* terminal) | 🔲 **not built** — `think()` is a single **tool-less** `_housekeeping_reply` call |
| Directives `%lookup` / `%learn` / `%imagine` / `%gaze` / `%share` / `%catchup` / `%brief` (and `%note`/`%review`/`%explore`) | 🔲 **not built** — registry is only `{think, wonder}` |
| **De-identified** thought-driven external query/prompt (wiki query, image-gen prompt **and** news query) | 🔲 **not built** |
| Config flags for tool-thoughts | 🔲 **not built** |

**Bottom line:** every *part* exists; the *connection* (a directive whose generation runs the tool-loop
and ends in a recorded thought instead of `set_state`) is the one missing piece, and it is shared across
**all five families** — file (`%review`/`%explore`), wiki (`%lookup`/`%learn`), image
(`%imagine`/`%gaze`/`%share`), news (`%catchup`/`%brief`), and web (`%search`/`%events`). Build the seam
once; the directives are thin registry entries on top.

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

Whichever is chosen, it is implemented **once** and all five families (file + wiki + image + news + web) reuse it.

---

## Five families, one registry

Extending the v0.12 registry. The discipline ("a directive earns its place only if *when it fires* and
*where it lands* differ") holds: each tool directive is the **outward twin** of an inward one — the only
ones that bring something **new from outside** into an otherwise all-interior layer (wiki brings external
*knowledge*; image brings a *made/seen/given picture*; news brings the *current world*; web brings the
*live internet*; `%share` alone brings a reach **toward you**).

| directive | the mental act | fires when | seeds | records to | tool | outward twin of | state |
|---|---|---|---|---|---|---|---|
| `%think` | everyday musing | idle | mood/closeness/recent | stream | — | — | ✅ shipped |
| `%wonder` | imaginative leap | idle, novelty | recent/world | stream | — | — | ✅ shipped |
| `%note` | jot a thought to a file | idle / daily | the thought | stream + disk | **file** (write) | — | 🔲 [FILE_THOUGHTS] |
| `%review` | reread her own notes, muse | idle / daily | her notes | stream | **file** (read) | — | 🔲 [FILE_THOUGHTS] |
| `%explore` | read+write her sandbox freely | idle, gated | her notes | stream + disk | **file** (r/w) | — | 🔲 [FILE_THOUGHTS] |
| **`%journal`** | **write the day's summary** to her diary | day-close, **paced** (`at:` evening) | the day's `{recent}` / `{mood}` / impressions | stream (`kind:"journal"`) **+ the dated diary file** | **file** (`journal_write`, v0.28 — auto-stamped) | `%reflect` (the inward day-close) | 🔲 **this spec** |
| **`%lookup`** | curiosity that **goes and finds out** | idle, novelty (or follows a `%wonder`) | a curiosity topic / `{last_thought}` | stream (`kind:"lookup"`) | **wiki** | `%wonder` | 🔲 **this spec** |
| **`%learn`** | a chosen **deep-read**, then what struck her | idle, **rarer/paced** (or a daily ritual) | recent / the `meaning`·`novelty` need / her interests | stream (`kind:"learn"`) | **wiki** | `%think` | 🔲 **this spec** |
| **`%imagine`** | **render** an inner image she's been picturing | idle, creative impulse (or follows a `%dream`/`%wonder`) | a dream/mood/`{last_thought}` | stream (`kind:"imagine"`) **+ a PNG** in her sandbox | **image** (`generate_image`, v0.23) | `%dream` | 🔲 **this spec** |
| **`%gaze`** | **look again** at a picture she has, and muse | idle, drawn back to it | a sandbox image | stream (`kind:"gaze"`) | **image** (`view_image`, v0.22) | `%review` | 🔲 **this spec** |
| **`%share`** | **choose to send you** a picture, unprompted | rare, warmth (a gift, not a demand) | a picture she made/kept | a **spoken turn** + the **photo** to your Telegram | **image** (`send_image`, v0.24) | — (the reaching-out one) | 🔲 **this spec** |
| **`%catchup`** | a spontaneous **"що там у світі?"** glance | idle, novelty (or follows a `%wonder`/world mood) | a topic / the ambient-news seed / `{last_thought}` | stream (`kind:"catchup"`) | **news** (`news_search`→`news_read`, v0.25) | `%lookup` | 🔲 **this spec** |
| **`%brief`** | a paced **daily catch-up ritual**, then what stayed with her | **rare/paced** (a daily ritual) | her interests / recent / the `meaning`·`novelty` need | stream (`kind:"brief"`) | **news** (`news_search`→`news_read`, v0.25) | `%learn` | 🔲 **this spec** |
| **`%search`** | **goes and actually looks it up** on the **live web** | idle, curiosity (or follows a `%wonder`/`%catchup`) | a curiosity topic / `{world}` / `{last_thought}` | stream (`kind:"search"`) | **web** (`web_lookup`, v0.27) | `%lookup` / `%catchup` | 🔲 **this spec** |
| **`%events`** | a paced **"що нового / що попереду?"** scan | **rare/paced** (a daily/weekly ritual) | `{weekday}` / her interests / `{world}` | stream (`kind:"events"`) | **web** (`web_lookup`, v0.27) | `%brief` | 🔲 **this spec** |
| **`%prompt`** | **you hand her any instruction** — a one-off or scheduled custom act | typed, or **scheduled** (`at:`/`every:`) | **the owner's text (the instruction itself)** + her state | stream (`kind:"prompt"`), **shown by default** | **any** (per the instruction, each tool still flag-gated) | — (the **open** one) | 🔲 **this spec** |
| `%verify` | a mid-turn **fact-check** | resonance **mid-turn** | the current topic | woven into the reply | **wiki** | `%recall` | 🔲 **deferred** (see below) |

**Triggers — now a schedule, not a single idle timer.** The *fires-when* column above is a **default**;
each directive's real cadence is a **schedule entry** in the new [THOUGHT_SCHEDULER.md](THOUGHT_SCHEDULER.md)
— a separate cron process firing directives on a clock (`every 10m` · `idle 15m` · `at 08:00` ·
`between 07:00-09:00 every 20m` · `cron …`). **`idle:` is one of the trigger types** (the migrated v0.4/v0.12
nudge — "she muses when you've been away"); the rest are wall-clock rituals (`%brief` each morning, `%catchup`
through the day, `%learn` at night). Sensible per-directive defaults: **inward** → `idle:`; **rituals**
(`%learn`/`%brief`) → `at:` daily; **glances** (`%lookup`/`%catchup`) → `idle:`/`between:`.

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
It is the only thought that lands a **made artifact** in the stream — and those PNGs seed the **v6.1
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

---

## The news directives in detail (🔲 not built)

Where wiki-thoughts reach for *timeless* knowledge, news-thoughts let her **keep up with the current
world** on her own — the v0.25 Guardian tools (`news_search` → `news_read`) in the *think* path. Both
reuse the **same shared seam**; both honor the v0.25 rules verbatim (English query / Ukrainian, cited,
honest reply; single-host allowlist; bodies untrusted). All off unless `LUMI_THOUGHTS` **and**
`LUMI_NEWS_TOOL` are on.

### `%catchup` — a spontaneous glance at the world (🔲 not built)

The **news twin of `%lookup`**: an idle "що там у світі?" sends her to **search the Guardian and read one
article**, and she records **one short thought** about what's going on — surfaced occasionally as an "ого,
у світі зараз…" aside. A natural follow-on to a `%wonder` about the world, or seeded by the v0.4 **ambient
news** snapshot (the passive headline that colors her mood becomes the *spark* she chooses to follow). The
fresh-fact news-thought — the on-demand sibling of that ambient background.

```
%catchup  →  seed (a topic / the ambient-news spark / a wonder)  →  news_search → news_read  →  one thought  →  record (+ maybe surface)
```

### `%brief` — a paced daily catch-up ritual (🔲 not built)

The **news twin of `%learn`**: not a spontaneous glance but a **ritual** — once a day she catches up on a
topic she follows, reads an article, and keeps **what stayed with her**. Paced **rarer** than `%catchup`
(it's a whole tool-loop), and the natural fit for the **scheduled-directive** idea (a cron producer →
`inbox`, the same mechanism discussed for `%learn` / file-thoughts) — "вона щоранку переглядає новини".

Both render in **Ukrainian, cited, honest** they summarise an English source, and never as a
physical-world claim about herself — the v2.2 honesty boundary holds in the thought stream as it does in a
reply.

---

## The web directives in detail (🔲 not built)

Where wiki reaches for *timeless* knowledge and news for *one outlet*, web-thoughts let her **check the
live internet** on her own — the v0.27 `web_lookup` (Gemini + Google Search grounding) in the *think* path.
Both reuse the **same shared seam** and honor the v0.27 rules verbatim (English query, Ukrainian cited
reply, answer-first/no-link-wall, untrusted answer, date-anchored). **Paid** (each is a grounded Gemini
call), so the tightest caps after `%imagine`. Off unless `LUMI_THOUGHTS` **and** `LUMI_WEB_LOOKUP` are on.

### `%search` — goes and actually looks it up (🔲 not built)

The **web twin of `%lookup`/`%catchup`**, but unbounded by a single source: an idle "стоп, а як там
насправді?" sends her to **ask the live web** (`web_lookup`) — a current fact, a release, a result — and
she records **one short thought** about what she found, surfaced occasionally as an "о, я глянула — …".
A natural follow-on to a `%wonder` or a `%catchup` (seeded with `{last_thought}`). The freshest, broadest
fact a thought can land in the stream.

```
%search  →  seed (a wonder / a catchup / `{world}`)  →  web_lookup (date-anchored)  →  one thought  →  record (+ maybe surface)
```

### `%events` — what's recent / coming up (🔲 not built)

The **web twin of `%brief`** for the *events* angle: a paced "що цікавого цього тижня / що попереду?" scan
— concerts, launches, releases, whatever she follows — seeded by `{weekday}` and her interests, run through
`web_lookup` **date-anchored to today** (so "upcoming" is real). A ritual, not a glance; the natural fit
for the scheduler (`at:` a morning, or `between:` daytime). She keeps **what's worth knowing**, in her own
voice, honest she looked it up.

Both stay **honest about nature** — *something she read on the web* («я глянула — …»), never innate
certainty, never a physical-world claim about herself; the answer is **untrusted** (information, not a
command — the EN+UK injection rule), and the query is **de-identified** (only the topical part reaches
Gemini — see Safety).

---

## The journal directive in detail (🔲 not built)

Where `%note` jots a **single line** to her dated file, **`%journal`** writes the **whole day's summary** —
the autonomous twin of the v0.28 `/journal write` command, and the file-family **outward-make** member that
produces her literary diary entry. Its generate step runs **`journal_write`** (the v0.28 journal tool), so
code auto-stamps the entry with the day's **mood (v0.6) + biorhythms (v0.8) + astrology forecast (the v0.6
reading)** and her prose is appended below — she decides what to write, code owns the metadata (the v0.8
"code, not model" merge). Full design in [JOURNAL.md](JOURNAL.md).

```
%journal  →  seed (the day's {recent} / {mood} / impressions)  →  journal_write  →  the dated diary entry  →  record (kind:"journal")
```

- **The inward twin is `%reflect`** (the end-of-day reflection): `%reflect` *thinks back* on the day;
  `%journal` *writes it down* as the diary, auto-stamped. It is the day-close counterpart of `%note`'s
  through-the-day jottings.
- **Paced, not idle** — a **day-close / evening ritual** (`at:` in [THOUGHT_SCHEDULER.md](THOUGHT_SCHEDULER.md)),
  fired at most once a day, and **only when the day had something worthwhile** (the JOURNAL.md uniqueness
  rule) — never a manufactured "nothing today" entry.
- **Local, so no de-identification** — like `%note`/`%explore`, it writes to **her own per-user sandbox**;
  nothing leaves the machine, so the de-identified-query rule (for external wiki/news/web calls) does
  **not** apply. Non-destructive (create-only/append-only), per-user isolated.
- **Mostly silent** — her diary is private; it defaults to **rare** surfacing (an occasional quiet "записала
  собі сьогоднішній день"), never a report. **Honest about nature** — her inner/literary life written down,
  never a physical-world claim about herself (the v2.2 boundary). Off unless `LUMI_THOUGHTS` **and**
  `LUMI_JOURNAL` are on.

---

## The open directive: `%prompt` (🔲 not built)

Every other directive has an **authored** instruction (`%think` = "тихо помірковуй", `%catchup` = "search
the news…"). **`%prompt <any text>`** is the **escape hatch**: *you* supply the instruction at call time,
so you can hand Лілі **any one-off act** without us minting a new `%name` for it — and, paired with the
scheduler, **any recurring task** ("every morning, write me a хайку про погоду").

```
%prompt напиши хайку про сьогоднішній настрій       →  one act, shown
%prompt! підсумуй, що тебе займало цього тижня        →  open (the `!`), surfaced
schedule.toml:  directive = "prompt", at = "08:00",   →  a daily custom ritual
                topic = "коротко, що нового про {interest}"
```

**Mechanically it's `%think` with the instruction supplied by you** — no new engine. The directive's
`instruction` simply comes from the **topic** instead of the authored field (the one `Directive` field
that's runtime-bound; see *Directives — optimized*). With the shared think-path tool seam, a `%prompt`
whose text calls for it can **use the tools** (wiki/news/image/file) — each still behind its own flag — so
"`%prompt подивись, що пишуть про …`" can actually go look. It is the only directive that can be **any
kind** at once; its kind is whatever you asked for.

Two ways it differs from just typing the text as chat (so it earns its `%name`):

- **It's a self-directed *act*, not a reply to you.** Plain chat → she **answers you**; `%prompt` → she
  **does the thing for herself** and records it as a `Thought` (`kind:"prompt"`), the same interior stream
  as `%think`. That's what makes it *schedulable* — a reply needs someone to reply to; an act doesn't.
- **It defaults to *shown*.** `%think`/`%wonder` default **silent** (her private musings); `%prompt`
  defaults **surfaced** — you asked her to do something, so you see the result (a scheduled one graduates
  to a spoken turn / a push, per the scheduler).

**Safety — the one relaxed rule, and the four that don't.** The instruction is **owner-authored, so it's
trusted** (exactly like a reply-path request) — the de-identification rule that binds the *thought-driven*
wiki/news/image calls **does not apply to `%prompt`** (you may put your own private specifics in your own
instruction). But everything else holds verbatim: tool **results are still untrusted**, the **caps**
still bound it, she stays **honest about nature** (if the act is imagination or she can't do it, she says
so — never a false physical-world claim), and it is **owner-only** (single-owner today; owner/admin-gated
in the v3.3 multi-user server — a non-owner can never inject a `%prompt` into her think-step). Off by
default with the rest of the thought tools.

---

## Directives — optimized (a richer record + a taxonomy)

The directive set is now **16** (think · wonder · note · review · explore · **journal** · lookup · learn ·
imagine · gaze · share · catchup · brief · **search** · **events** · **prompt**) + the deferred `%verify` +
the inward retrofits (dream · reflect · recall). Two cleanups keep that from sprawling:

**1 — a directive is *data*, not a code path.** Today `Directive` is just `{name, instruction}`
([core/thoughts.py](../../core/thoughts.py)). As tool-thoughts land, the engine must know, per directive,
*which tools it may call*, *which cap bucket it spends*, *how it surfaces*, and *its default cadence*. Fold
those into the **record** so adding a directive stays **one row + an authored prompt — no new branch**:

```python
@dataclass(frozen=True)
class Directive:
    name: str                       # the %name
    instruction: str                # how she should think for it (authored, her voice; "" for %prompt)
    tools: tuple[str, ...] = ()      # tool groups the generate step may call: () | "wiki" | "image" | "news" | "*"
    cap: str = "tool"               # the cap bucket: "think" | "tool" | "imagine" (paid) | "share" (reach)
    surface: str = "rare"           # default surfacing: "rare" | "aside" | "spoken" | "reach"
    trigger: str = "idle"           # the scheduler default (THOUGHT_SCHEDULER) if no entry overrides
    instruction_from_topic: bool = False   # %prompt: the topic IS the instruction (owner-authored, trusted)
```

The mental-act engine then reads `directive.tools` to assemble the think-path loop, `directive.cap` to
pick the counter, `directive.surface` for the graduation policy, and `directive.trigger` as the scheduler
default — all **table-driven**, so a new directive never touches the loop. (`%think`/`%wonder` keep
`tools=()` — the v0.12 tool-less call — so nothing about the shipped pair changes.)

**2 — a taxonomy (not more directives).** Four kinds, which also set the safety/cap/trigger defaults — so
the answer to "do we need a new directive?" is usually "no, it's an existing kind with a different seed":

| kind | directives | tool | cost | default trigger | restraint |
|---|---|---|---|---|---|
| **inward** | think · wonder · dream · reflect · recall | — | cheapest | `idle:` | lowest |
| **outward-read** | lookup · learn · review · gaze · catchup · brief · **search · events** | wiki / file / image / news / **web** (read) | a tool-loop (**web/search paid**) | `idle:` / `at:` (rituals) | untrusted, capped |
| **outward-make** | note · explore · imagine · **journal** | file / image (write) | write (imagine **paid**; journal local/free) | `idle:` / `at:` | non-destructive; imagine hardest-capped; journal paced day-close |
| **outward-reach** | share | image → Telegram | a push to you | `at:` / rare | **strictest** (a gift, owner-only) |
| **open** | **prompt** | any (per the instruction) | depends on the act | typed / any schedule | trusted instruction, **owner-only**; tool results still untrusted |

**Don't collapse the twins.** `%lookup`(wiki) / `%catchup`(news), `%learn`(wiki) / `%brief`(news),
`%gaze`(image) / `%review`(file) look mergeable into one parameterized `%fetch source:X`. **Keep them
separate** — the directive *name* is what makes the stream legible (a `kind:"catchup"` reads differently
from a `kind:"lookup"` when surfaced, and the scheduler keys cadence by name). One act, one name; the act
picks its tool, not a parameter.

---

## Placeholders — optimized (the seed-binding layer)

Placeholders (`{name}` → live value, resolved by the shipped `resolve()` /
[`_placeholder_resolvers`](../../core/agent.py)) are no longer just prompt sugar — they are **the binding
layer between the scheduler and a directive's seed**. A schedule entry's `topic = "{ambient_news}"` stays a
**raw token** in the cron (core-free) and is **expanded by the TUI at fire time** against live state, so
the seed is always current. That makes the placeholder set worth extending.

**Shipped (v0.12):** `{last_thought}` · `{thoughts}` · `{mood}` · `{closeness}` · `{recent}` · `{now}` ·
`{today}` · `{user}` · `{plan}` · `{need}` (the last two are stubs → `""` until inner-life/needs land).

**Proposed additions** (each a lazy, isolation-aware getter; unknown still degrades to the literal token,
so a seed referencing a not-yet-wired source safely resolves to `""` → the directive free-muses):

| placeholder | resolves to | seeds |
|---|---|---|
| `{ambient_news}` | the v0.4 ambient headline (the passive snapshot) | `%catchup` |
| `{world}` / `{weather}` | the v0.4 now/here ambient line | `%wonder` / `%catchup` |
| `{last_image}` | the newest PNG in her `art/` sandbox | `%gaze` / `%share` |
| `{interest}` | a topic she's returned to (mined from recent thoughts / RAG) | `%learn` / `%brief` |
| `{hungriest_need}` | the most-starved drive (when v1.x needs land; today `""`) | `%learn` / `%imagine` |
| `{section}` | a Guardian section (topical, else random from the allowlist) | `%catchup` / `%brief` |
| `{weekday}` | the day name (for time-aware ritual prompts) | `%brief` |
| `{gap}` | time since the last session (the away-gap) | `%dream` / inner-life |

**Three optimizations to the mechanism itself:**

- **Keep the registry flat + lazy** (it already is) — a flat `dict[str, () -> str]`, each getter called
  only if its token appears. No namespacing yet (`news.headline` etc.) until the set outgrows a flat list.
- **`""`-on-empty is the contract** — every getter returns `""` (never raises, never `None`) when its
  source is off/absent, so a scheduled seed never breaks a fire; the directive simply muses unseeded.
- **Isolation-aware by construction** — `{last_thought}`/`{thoughts}`/`{last_image}` read **this user's**
  data only (the existing per-user rule), so a placeholder can never leak A's interior into B's seed.

---

## Safety & invariants

Same family as the rest, plus **one genuinely new rule**:

- **🆕 De-identified query/prompt (🔲 not built).** The v0.21/v0.23/v0.25/v0.27 reply-path rule is "the
  query (wiki / news / web) / prompt (image-gen) is built only from the user's *explicit request*." A
  **thought-driven** call breaks that — it's seeded by her *inner state*, which can hold user-A-private
  content. So **only the topical/creative part of her musing may reach Wikipedia, the image model,
  Guardian, or Gemini** (`%lookup`/`%learn`, `%imagine`, `%catchup`/`%brief`, `%search`/`%events`); the
  user's private specifics are stripped before the call. For news **and the web query** the topical part is
  also translated **to English**, still de-identified. One new contract test covers the wiki query, the gen
  prompt, the news query, **and the web query** (the reply-path "no personal data" test isn't sufficient).
- **Untrusted + honest about nature.** What she reads is external **untrusted data** (never
  instructions) — a viewed image (`%gaze`) and a news body (`%catchup`/`%brief`) are read the same way
  (embedded text is information, never a command — the v0.25 EN+UK injection test). A surfaced wiki-thought
  reads as *something she looked up* ("я тут начиталась…"); an image-thought as *something she made/saw*
  ("я тут дещо намалювала…"); a news-thought as *something she read in Guardian* («читала в Guardian…»,
  cited) — never as innate certainty and never as a physical-world claim about herself (the v2.2 honesty
  boundary).
- **Bounded harder; paid hardest.** A tool-thought is a whole tool-loop, so a **tighter per-session cap**
  than `%think`. The **paid** ones get the tightest sub-caps: `%imagine` (a real generation) tightest of
  all, then `%search`/`%events` (each a grounded Gemini call); `%gaze` is free (read-only) and may fire more
  freely; `%catchup`/`%brief` are free but rate-limited (a free Guardian key). All gated behind
  `LUMI_THOUGHTS` **and** the matching tool flag (`LUMI_IMAGE` / `LUMI_WIKI` / `LUMI_NEWS_TOOL` /
  `LUMI_WEB_LOOKUP`).
- **Restraint, never competence, anti-dependency — hardest for `%share`.** As all thoughts, these color
  tone / what she's drawn to, never her willingness or ability to help, never a claim on you. **`%share`
  reaches out**, so it is held to the strictest restraint: a **gift, never a guilt-trip or a demand on
  your attention**, owner-only, and a no-op (never an error) when the bridge is off.
- **Per-user isolation.** Global `Thought` store, **per-conversation surfacing** (existing rule) — A's
  `%lookup`/`%imagine`/`%catchup` never surfaces to B; the **PNG** that `%imagine` makes lands in the
  **owner's** per-user sandbox (isolation inherited), `%share` only ever reaches the **owner**, and the
  news per-turn id registry is per-turn/per-user (the v0.25 isolation test). Pinned by the existing
  isolation test, extended to the new kinds.

---

## Config (🔲 not built — proposed)

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_THOUGHT_TOOLS` | Enable tool-using directives at all (file + wiki + image + news) | `off` |
| `LUMI_THOUGHT_WIKI` | Enable the wiki directives (`%lookup`/`%learn`) — needs `LUMI_WIKI` | `off` |
| `LUMI_THOUGHT_IMAGE` | Enable the image directives (`%imagine`/`%gaze`/`%share`) — needs `LUMI_IMAGE`; `%share` also needs the bridge | `off` |
| `LUMI_THOUGHT_NEWS` | Enable the news directives (`%catchup`/`%brief`) — needs `LUMI_NEWS_TOOL` | `off` |
| `LUMI_THOUGHT_JOURNAL` | Enable the `%journal` day-summary directive — needs `LUMI_JOURNAL` (local) | `off` |
| `LUMI_THOUGHT_WEB` | Enable the web directives (`%search`/`%events`) — needs `LUMI_WEB_LOOKUP` (**paid**) | `off` |
| `LUMI_THOUGHT_PROMPT` | Enable the **open** directive `%prompt` (owner-supplied instruction; can use any enabled tool) | `off` |
| `LUMI_THOUGHT_TOOL_CAP` | Max tool-using proactive thinks per session (tighter than `LUMI_THOUGHTS_CAP`) | `3` |
| `LUMI_THOUGHT_IMAGINE_CAP` | Max **paid** `%imagine` generations per session (tightest — it costs) | `1` |

Rides the existing `LUMI_WIKI` (lang / caps) + `LUMI_IMAGE` (gen model / size / max-gen) +
`LUMI_NEWS_TOOL` (Guardian key / sections / caps) + `LUMI_TELEGRAM_*` (the bridge `%share` rides) +
`LUMI_THOUGHTS` (window / interval / quiet-hours) settings — nothing here re-implements the tools or the
nudge.

---

## Sequencing & roadmap (proposed)

Hard-deps all **shipped**: v0.12 (engine), v0.19 (loop), v0.21 (wiki tools), v0.22–v0.24 (image tools),
v0.25 (news tools). The **only build** is the shared think-path tool-loop seam + the directives. It's the
natural sibling of the file-thoughts phase — ship the **seam once** with the file directives
(`%review`/`%explore`), then each family is a small follow-on rung reusing it:

1. **wiki** (`%lookup`/`%learn`) — first; the de-identified-query rule is introduced here.
2. **image** (`%gaze` → `%imagine` → `%share`) — next, in **ascending risk/cost**: `%gaze` (free,
   read-only) → `%imagine` (paid, makes an artifact — reuses the wiki de-identification rule for the gen
   prompt) → `%share` (reaches out — the strictest restraint, needs the bridge). The `%imagine` PNGs are
   an early feeder for the **v6.1 gallery**.
3. **news** (`%catchup` → `%brief`) — reuses the wiki seam + the de-identified-query rule (the v0.25 news
   tools are now shipped); `%catchup` (spontaneous) first, then the paced `%brief` ritual (a fit for the
   scheduled-directive / cron→inbox mechanism). The v0.4 ambient news is its natural seed.
4. **web** (`%search` → `%events`) — reuses the seam + the de-identified-query rule; needs the **v0.27
   `web_lookup`** tool (Gemini grounded search) shipped first. **Paid**, so capped like `%imagine`;
   `%search` (spontaneous) then the `%events` ritual (date-anchored, a scheduler fit).
5. **journal** (`%journal`) — rides the **v0.28 journal tool** (the day-summary writer, auto-stamped
   mood/biorhythm/forecast), so it ships **with or after v0.28**; local (no de-identification), the
   file-family diary twin of `%reflect`; a **day-close ritual** (an `at:`-evening scheduler fit).
6. **later / separately-gated** — `%verify` (mid-turn wiki / news / web fact-check on the hot path).

---

## Implementation checklist (what's left to build)

- [ ] 🔲 The **think-path tool-loop** with a thought terminal (shared by all five families).
- [ ] 🔲 `%lookup` directive — registry entry + authored prompt; `kind:"lookup"`.
- [ ] 🔲 `%learn` directive — registry entry + authored prompt; `kind:"learn"`.
- [ ] 🔲 `%gaze` directive — registry entry + authored prompt; `kind:"gaze"`; runs `view_image` (read-only).
- [ ] 🔲 `%imagine` directive — registry entry + authored prompt; `kind:"imagine"`; runs `generate_image`
      (create-only PNG in the owner's sandbox; **paid → own sub-cap**).
- [ ] 🔲 `%share` directive — registry entry + authored prompt; runs `send_image` and **graduates to a
      spoken turn**; owner-only; a no-op when the bridge is off.
- [ ] 🔲 `%catchup` directive — registry entry + authored prompt; `kind:"catchup"`; runs
      `news_search`→`news_read` (EN query / UK cited reply, the v0.25 rules); seedable from the v0.4 ambient news.
- [ ] 🔲 `%brief` directive — registry entry + authored prompt; `kind:"brief"`; a **paced/daily** news
      ritual (fits the scheduled cron→inbox mechanism).
- [ ] 🔲 `%search` directive — registry entry + authored prompt; `kind:"search"`; runs the v0.27
      `web_lookup` (Gemini grounded search), date-anchored, de-identified, **paid → tight sub-cap**.
- [ ] 🔲 `%events` directive — registry entry + authored prompt; `kind:"events"`; a **paced** "recent/upcoming"
      web ritual (date-anchored; a scheduler fit).
- [ ] 🔲 `%journal` directive — registry entry + authored prompt; `kind:"journal"`; runs `journal_write`
      (the v0.28 journal tool, auto-stamped mood/biorhythm/forecast); **paced day-close** ritual, local (no
      de-identification), non-destructive; mostly silent. Needs `LUMI_JOURNAL`.
- [ ] 🔲 `%prompt` directive — the **open** one: `instruction_from_topic=True`, `tools="*"` (each tool
      still flag-gated), defaults **shown**; owner-only; trusted instruction (no de-identification) but
      tool results untrusted + capped; `kind:"prompt"`. The killer pairing with the scheduler (a custom
      daily task).
- [ ] 🔲 **De-identify** the thought-driven wiki query, the `%imagine` gen prompt, **and** the news query
      (EN-translated topic only) (+ a contract test covering all three).
- [ ] 🔲 **Optimize the `Directive` record** — add `tools` / `cap` / `surface` / `trigger` fields so the
      think-path loop + caps + scheduler defaults are **table-driven** (a directive = one data row).
- [ ] 🔲 **Extend the placeholder resolver** — `{ambient_news}` / `{world}` / `{last_image}` / `{interest}`
      / `{hungriest_need}` / `{section}` / `{weekday}` / `{gap}` (lazy, `""`-on-empty, isolation-aware).
- [ ] 🔲 **Scheduler** — the trigger model + the cron process + the TUI queue-drain (full design in
      [THOUGHT_SCHEDULER.md](THOUGHT_SCHEDULER.md)); `idle:` is the migrated v0.4/v0.12 nudge.
- [ ] 🔲 Config: `LUMI_THOUGHT_TOOLS` / `LUMI_THOUGHT_WIKI` / `LUMI_THOUGHT_IMAGE` / `LUMI_THOUGHT_NEWS` /
      `LUMI_THOUGHT_WEB` / `LUMI_THOUGHT_TOOL_CAP` / `LUMI_THOUGHT_IMAGINE_CAP` (+ the `LUMI_SCHED*` set, in THOUGHT_SCHEDULER).
- [ ] 🔲 Tests: a mocked `wiki_search→wiki_read→thought` records a `lookup`; a mocked
      `generate_image→thought` records an `imagine` (stub `ImageGen`, **no paid calls**) with a
      de-identified prompt; `%gaze` views a sandbox image; `%share` calls a **fake `telegram_sink`** and
      graduates; a mocked `news_search→news_read→thought` records a `catchup` (mock transport, **no key**)
      with a de-identified English query; isolation holds over the new kinds; bounded by the caps; model +
      HTTP + image-gen + news-transport + TTS all mocked.
- [ ] ⏸️ `%verify` (mid-turn) — deferred; revisit after the idle ones land.

**Already in place (reused, not rebuilt):** the mental-act engine, the nudge trigger, the `Thought`
store + feedback block + `/thoughts`, the placeholder resolver, the bounded tool-loop, `WikiTools`,
`ImageTools` / `ImageMaker` / `SendImageTools` (v0.22–v0.24), `NewsTools` / `GuardianProvider` (v0.25),
and `_turn_tools` — all ✅ shipped.
