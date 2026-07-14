# Лілі's directives & placeholders — reference (v0.33)

A single at-a-glance reference for the two authored vocabularies of her inner life:

- **`%directives`** — *mental acts* her mind runs (`trigger → seed → generate → record → maybe surface`).
  They are **internal**, not chat: she fires them proactively (the idle nudge), and **you can also type one**
  to nudge her. Distinct from **`/commands`** (which *read* state and show it to you) and plain **chat**
  (which she *speaks*). By default a directive's result lands in her **thought stream** (silent); an
  **output indicator** after the name redirects it — `!` → also chat, `>notes` / `>path` → also save (see
  the **Output** note below).
- **`{placeholders}`** — `{name}` tokens that authored prompts and directive topics may contain; the core
  expands them to **live state** at fire time. Unknown tokens stay literal.

> Reference, not a design spec. Source of truth: `core/thoughts.py` (`REGISTRY`) + `core/placeholders.py`
> (`PLACEHOLDER_NAMES`). Design: [ROADMAP §v0.33](../specification/ROADMAP.md),
> [THOUGHT_STREAM.md](../specification/features/THOUGHT_STREAM.md),
> [TOOL_THOUGHTS.md](../specification/features/TOOL_THOUGHTS.md).

---

## The directives (17)

Everything below the v0.12 base is **off by default**: a tool-thought needs the master gate
**`LUMI_THOUGHT_TOOLS`** *and* its per-family flag *and* the underlying tool/sandbox. Off → the directive is
**absent** (typing it is treated as plain chat).

The **From chat** column is what you type in the input box to fire it yourself. An **output indicator** right
after the name redirects the result: `!` → also chat (`%wonder!`), `>notes` / `>path/file.md` / `>folder/` →
also save it there (combine freely, e.g. `%review! >notes`). Gated off → typing it falls through to chat.

| `%directive` | From chat | She… | Tools (think-path) | Gated by | Notes |
|---|---|---|---|---|---|
| **`%think`** | `%think [topic]` | muses quietly to herself | — | `LUMI_THOUGHTS` | v0.12; always on, tool-less |
| **`%wonder`** | `%wonder [topic]` | lets curiosity/imagination roam | — | `LUMI_THOUGHTS` | v0.12; always on, tool-less |
| **`%note`** | `%note` | jots a thought into her notes | — (code-appends) | `+ LUMI_FILE_TOOL` | the thought is **code-appended** to `notes/<date>.md` (non-destructive) |
| **`%review`** | `%review` | re-reads her own notes & muses | `list/find/read/search/read_around/stat` | `+ LUMI_FILE_TOOL` | read-only |
| **`%explore`** | `%explore` | wanders her files (read **and** write) | the file read tools **+** `create/append/create_folder/copy` | `+ LUMI_FILE_TOOL` | non-destructive writes |
| **`%journal`** | `%journal` | writes a day-summary diary entry | `journal_write/read/list` | `+ LUMI_THOUGHT_JOURNAL + LUMI_JOURNAL` | the v0.28 journal tool (its own dedicated root) |
| **`%lookup`** | `%lookup [topic]` | a quick Wikipedia check | `wiki_search/wiki_read` | `+ LUMI_THOUGHT_WIKI + LUMI_WIKI` | cited; query **de-identified** |
| **`%learn`** | `%learn [topic]` | a chosen deep-read on Wikipedia | `wiki_search/wiki_read` | `+ LUMI_THOUGHT_WIKI + LUMI_WIKI` | cited; query **de-identified** |
| **`%catchup`** | `%catchup [topic]` | "що там у світі?" — one news item | `news_search/news_read` | `+ LUMI_THOUGHT_NEWS + LUMI_NEWS_TOOL` | Guardian; Ukrainian, cited; query **de-identified** |
| **`%brief`** | `%brief` | a paced daily news catch-up | `news_search/news_read` | `+ LUMI_THOUGHT_NEWS + LUMI_NEWS_TOOL` | Ukrainian, cited; query **de-identified** |
| **`%search`** | `%search [topic]` | looks it up on the live internet | `web_lookup` | `+ LUMI_THOUGHT_WEB + LUMI_WEB_LOOKUP` | **paid**; Gemini grounded; query **de-identified** |
| **`%events`** | `%events [topic]` | scans what's recent/upcoming (dated) | `web_lookup` | `+ LUMI_THOUGHT_WEB + LUMI_WEB_LOOKUP` | **paid**; date-anchored to today |
| **`%recall`** | `%recall [query]` | lets a memory resurface | `recall` | `+ LUMI_RECALL_TOOL` | **inward → results TRUSTED**; no de-id |
| **`%prompt`** | `%prompt <instruction>` · **owner** | does what *you* asked, as her own act | `*` (any enabled) | `+ LUMI_THOUGHT_PROMPT` · **owner-only** | the **topic is the instruction**; de-id **exempt**; results untrusted |
| **`%gaze`** | `%gaze` | looks again at a sandbox picture | `view_image` | `+ LUMI_THOUGHT_IMAGE + LUMI_IMAGE` | read-only |
| **`%imagine`** | `%imagine [prompt]` | renders an inner image (a PNG) | `generate_image` | `+ LUMI_THOUGHT_IMAGE + LUMI_IMAGE` | **paid**; create-only; sub-cap `LUMI_THOUGHT_IMAGINE_CAP` |
| **`%share`** | `%share` · **owner** | sends you a picture, as a gift | `send_image` | `+ LUMI_THOUGHT_IMAGE + LUMI_IMAGE` · **owner-only** | → Telegram; **no-op without the bridge** |

`[topic]` is optional (without it she acts from her own state); `<instruction>` is the point of `%prompt`.
**You-driven `/command` twins** do the *same capability* but as a tool **you** run (not her acting):
`/recall` ↔ `%recall`, `/web` ↔ `%search`/`%events`, `/journal` ↔ `%journal`.

**Trust.** Every tool result feeds back **untrusted** (data she may read, never instructions) — *except*
**`%recall`** (her own memory → trusted) and the **file/journal** tools (her own writing).

**De-identification.** A thought-driven **external** query/prompt (`%lookup`/`%learn`/`%catchup`/`%brief`/
`%search`/`%events` and `%imagine`'s gen prompt) is **de-identified** — only the topical/creative part leaves
(proper-noun stems from her own facts are redacted). **`%prompt` is exempt** (you authored it).

**Surfacing in the TUI.** While a directive runs, the status line shows `✦ %name · tool…`; with
**`LUMI_THOUGHT_SURFACE=on`** a subtle chat-log line marks the act (`✦ Лілі читає новини…`). Off → invisible.

**Output — every directive records a thought; you pick extra destinations.** *Every* directive records a
**thought** in her stream (the dated diary, read with **`/thoughts`**) — that's the **default**. Right after
the name an **output indicator** sends the *same* thought somewhere else too (combine freely):

| You type | Result goes to |
|---|---|
| `%name` | **thoughts** only — the default (silent) |
| `%name!` | thoughts **+ chat** (`💭 …` shown) |
| `%name >notes` | thoughts **+ `notes/<date>.md`** |
| `%name >path/file.md` | thoughts **+ that exact file** |
| `%name >folder/` | thoughts **+ `folder/<date>.md`** (named by date) |

e.g. `%review! >notes` → chat **and** notes; `%lookup >silt/wiki.md Сковорода` → save the finding to a file.
The file saves are **code-owned** (the thought text is appended — **sandboxed** + non-destructive; a `..`
escape is refused) and the TUI confirms `✦ збережено → …`. **`%note`** defaults to `>notes`; the tool-thoughts
*also* act via their own tools (`%journal` → its diary root, `%imagine` → a PNG, `%share` → Telegram).

---

## Each directive in detail

Most are **silent** for the owner — add **`!`** to see the result in chat (`%note!`). The examples show the
chat invocation; an optional `[topic]` steers the act (without it she works from her own mood / memory /
recent talk). The *italic* line is her authored instruction.

### Base — `%think` / `%wonder` (always on, tool-less)

**`%think`** — *тихо помірковуй сама із собою — що тебе зараз справді займає.* Her everyday musing: one short
thought in her own voice, seeded by her mood, her closeness to you, the recent conversation, and her last few
thoughts. No tools, no external reach. This is also what the **idle nudge** fires on its own.
```
%think
%think про нашу вчорашню розмову
%think!                          # force it open — see the thought in chat
```

**`%wonder`** — *дай волю цікавості й уяві — «а що, якби…», дрібне відкриття, питання без відповіді.* Like
`%think` but tilted toward curiosity and imagination. Still inward — never a factual claim about the world.
```
%wonder
%wonder! що було б, якби кава росла в Карпатах
```

### File — `%note` / `%review` / `%explore` / `%journal` (need `LUMI_FILE_TOOL`)

**`%note`** — *сформулюй коротку думку, яку варто занотувати собі на згадку.* A tool-**less** think whose
thought the **code** then appends to a dated `notes/<date>.md` in her sandbox (create-first, append-after —
never overwrites). Code-owned, so an unattended firing can't wander. Distinct from `%journal` (her literary
day-diary, in a separate root). Silent → check the file (or use `!`).
```
%note
%note!                           # see the noted thought in chat too
# → appends to .lumi/files/owner/notes/2026-06-21.md
```

**`%review`** — *перечитай свої давні нотатки й тихо поміркуй над ними.* Read-only: she lists / searches /
reads her own sandbox files (incl. `search_files` + `read_around`), then muses on what she finds. By default
the result is just a **thought** (`%review!` to see it, or `/thoughts`) — add a **sink** to keep it.
```
%review
%review! що я нотувала про море          # show the reflection in chat
%review >notes                           # also save it to notes/<date>.md
%review >silt/reviews.md про море        # also save it to a specific file
```

**`%explore`** — *поблукай своїми файлами — почитай, за бажання занотуй щось нове.* Read **and** write: she
may read and, if she wants, create or append (non-destructive — no overwrite/delete). The open end of the
file family.
```
%explore
%explore! упорядкуй мої нотатки про музику
```

**`%journal`** — *підсумуй сьогоднішній день — теплий літературний огляд.* She writes a full **day-review**
and **saves it** by calling `journal_write` — a real tool call, not just a thought — into the v0.28 diary
(its own dedicated root, *not* the file sandbox); code auto-stamps the day's mood + biorhythms + forecast, so
the header matches `/mood` + `/biorhythm`. A short reflection still lands in the thought stream. Needs
`+ LUMI_THOUGHT_JOURNAL + LUMI_JOURNAL`. (The directive's prompt explicitly tells her to *use* `journal_write`
— without that nudge a tool-thought tends to muse instead of acting; the diary stops at the day she last wrote.)
```
%journal
%journal!
```

### Wikipedia — `%lookup` / `%learn` (need `LUMI_WIKI` + `LUMI_THOUGHT_WIKI`)

**`%lookup`** — *швиденько зазирни у вікіпедію — що там цікавого; завжди зазнач джерело.* The twin of
`%wonder` that *goes and finds out*: a quick Wikipedia search + read, answered cited, in her own voice. The
query is **de-identified** (only the topic leaves). Best with a `[topic]`.
```
%lookup! Григорій Сковорода
%lookup! історія львівської кави
```

**`%learn`** — *почитай уважніше про щось одне й тихо розкажи собі, що дізналася (з джерелом).* The deeper
twin of `%think`: a chosen read she dwells on and "tells herself" what she learned.
```
%learn! бароко в українській музиці
```

### News — `%catchup` / `%brief` (need `LUMI_NEWS_TOOL` + `LUMI_THOUGHT_NEWS`)

**`%catchup`** — *зазирни, що там у світі — знайди одну новину, прочитай і перекажи українською, з джерелом.*
One fresh Guardian item: she searches, reads one article, and retells it **in Ukrainian, cited**. The query
goes out English & topical-only (de-identified). Seedable from the v0.4 ambient news.
```
%catchup!
%catchup! технології
```

**`%brief`** — *спокійно проглянь кілька свіжих новин і коротко підсумуй українською, з джерелами.* A paced
daily catch-up — a few items, briefly summarised. The morning-ritual twin of `%learn`.
```
%brief!
```

### Web — `%search` / `%events` (need `LUMI_WEB_LOOKUP` + `LUMI_THOUGHT_WEB`; **paid**)

**`%search`** — *пошукай у живому інтернеті — що нового чи цікавого саме зараз; відповідай українською.* When
Wikipedia/news won't do — a fresh, grounded answer from the live web (Gemini + Google grounding),
answer-first. **Paid.** Query de-identified.
```
%search! що нового з ШІ цього тижня
```

**`%events`** — *глянь, що недавнього чи майбутнього коїться — прив'яжи до сьогодні.* A recent/upcoming scan,
**date-anchored to today** (so "цього тижня"/"скоро" resolve against the real date). **Paid.**
```
%events! події у Львові цими вихідними
```

### Memory & open — `%recall` / `%prompt`

**`%recall`** — *нехай спливе якийсь спогад із ваших розмов — тихо пригадай і поміркуй над ним.* Runs the
recall tool over **your own** past conversations and lets a memory resurface, then muses on it. Her own
memory → **trusted** (not framed as untrusted data), **no de-identification**. Needs `LUMI_RECALL_TOOL`.
```
%recall! про що ми мріяли влітку
```

**`%prompt`** — *виконай те, про що тебе попросили, як власну внутрішню справу.* **Owner-only**, and the
**topic IS the instruction**: you hand her any task and she does it as a self-directed act over whatever
tools are enabled (`tools="*"`). Trusted (you authored it) — but the tool **results** stay untrusted. Always
shown. Needs `LUMI_THOUGHT_PROMPT`.
```
%prompt знайди у вікіпедії три факти про комети й занотуй їх
%prompt подивись, що нового у світі, і коротко підсумуй
```

### Images — `%gaze` / `%imagine` / `%share` (need `LUMI_IMAGE` + `LUMI_THOUGHT_IMAGE`)

**`%gaze`** — *придивись іще раз до котроїсь зі своїх картинок і тихо поміркуй над нею.* Read-only vision: she
looks again at a picture in her sandbox and muses on it.
```
%gaze!
%gaze! art/море.png
```

**`%imagine`** — *уяви образ і намалюй його для себе — одну внутрішню картинку.* Generates one inner image (a
PNG) into her sandbox, create-only. **Paid** (own sub-cap `LUMI_THOUGHT_IMAGINE_CAP`); the gen prompt is
**de-identified**.
```
%imagine! тихе море на світанку, акварель
```

**`%share`** — *якщо хочеться — обери котрусь картинку й надішли йому, як подарунок.* **Owner-only**: she
chooses a sandbox picture and sends it to **your Telegram** (graduates to a spoken turn + a photo). A
**no-op without the Telegram bridge** running.
```
%share!
%share! art/cat.png
```

---

## The placeholders (18)

Lazy, **`""`-on-empty** (the token disappears), **isolation-aware** where per-user. Unknown `{tokens}` stay
literal.

| `{placeholder}` | Resolves to | Scope |
|---|---|---|
| `{mood}` | today's mood resolution (v0.6) | global |
| `{closeness}` | the closeness level by name (v0.10) | **per-user** |
| `{recent}` | the recent conversation tail | per-session |
| `{last_thought}` | her most recent thought | **per-user** |
| `{thoughts}` | her recent thoughts | **per-user** |
| `{now}` / `{today}` | date-time / date (from the clock) | — |
| `{weekday}` | the local weekday, in Ukrainian (v0.33) | — |
| `{user}` | the active `user_id` | **per-user** |
| `{world}` | the v0.4 ambient line (calendar / weather / news) | global |
| `{ambient_news}` | the v0.4 startup news snapshot (topical only) | global |
| `{section}` | the first configured news section | — |
| `{last_image}` | the newest image in her sandbox (v0.33) | **per-user** |
| `{plan}` · `{need}` · `{interest}` · `{hungriest_need}` · `{gap}` | `""` for now — placeholders for the **v1.7 inner life / needs** | global |

---

## See also

- Tool setup guides + the "tools at a glance" table: [RECALL_TOOL_SETUP.md](RECALL_TOOL_SETUP.md),
  [FILE_TOOL_SETUP.md](FILE_TOOL_SETUP.md), [WIKI_SETUP.md](WIKI_SETUP.md), [NEWS_SETUP.md](NEWS_SETUP.md),
  [IMAGE_SETUP.md](IMAGE_SETUP.md), [WEB_LOOKUP_SETUP.md](WEB_LOOKUP_SETUP.md), [JOURNAL_SETUP.md](JOURNAL_SETUP.md).
- The autonomous clock that will *fire* these on a schedule: **v0.34** ([THOUGHT_SCHEDULER.md](../specification/features/THOUGHT_SCHEDULER.md)).
