# Design brief: Лілі's desktop window — her files, pictures, journal and thoughts (Lumi v4)

> The design brief for the v4 views ([ROADMAP.md](../../specification/ROADMAP.md) §v4,
> [DESKTOP_VIEWS.md](../../specification/features/DESKTOP_VIEWS.md)), written for Claude Design. The Ukrainian
> original the owner pastes there: [BRIEF.uk.md](BRIEF.uk.md). The design's exports go into this folder, then into
> `desktop/ui/`.
>
> **This extends the v2.12 design; it does not redo it.** Open it in the same Claude Design project
> (<https://claude.ai/artifact/BgY2hbwQ8XdHbvcg8hUMwL>) and add the new views to the window that is already there.
>
> **Attach to the brief:**
> - six to eight of her real pictures from `art/` for the gallery, or portraits as stand-ins if the owner prefers not
>   to share them;
> - if the brief is opened outside the v2.12 project, the v2.12 handoff too:
>   [README.md](../v2.12/handoff/README.md), [tokens.css](../v2.12/handoff/tokens.css),
>   [strings.en.json](../v2.12/handoff/strings.en.json).

## What it is
Лілі is a private text companion with a stable character, memory and emotions. She speaks Ukrainian. There is one
user, the owner.

Her logic runs on a home server. She has tools there: she reads and writes files in her own folder, draws pictures,
keeps a diary, and has thoughts on a schedule.

The desktop window (v2.12) is a thin client and shows only the chat and her portrait. v4 adds **views of what she
keeps on the server**: her files with a markdown reader, a gallery of pictures, her journal, her thoughts, her day's
state and her timetable. **They only read**; actions (upload, "show this to her", download) come in a later version,
so the layout should leave room for them without a redesign.

## Extend, don't redo
- **Everything that exists stays as it is:**
  - the portrait panel: the portrait, the caption, the theme and closeness, the mood of the day, the quiet-thoughts
    strip;
  - the chat: the messages, the think block, the command block, the picture, the new-session divider;
  - the input, the status line and its details;
  - the connection banner and screens;
  - the narrow layout;
  - the tokens, the accent table, the type and the visual language.
- **The new views are built from what exists:** the kicker, the meta grid, the closeness meter, the thought item, the
  command block, the picture, the buttons, the hairlines and the 2px rules. A new component only where nothing
  existing fits.
- **New tokens and strings are additions only;** no existing value changes.
- **The changes to the existing window:**
  - the view row is added to the content column and takes its space from it; the portrait panel and the status line
    do not move;
  - the details panel gains the tool list;
  - the picture component gets real pictures.

## Technical frame (as in v2.12)
- **The window.** pywebview on macOS, WebKit. Resizable: ~1200×800 by default, ~720×520 minimum.
- **The code.** A static index.html + style.css + app.js: no framework, no bundler, no node. Tokens are CSS
  variables (reuse the v2.12 `tokens.css`; add tokens only if a view needs them). System fonts (SF Pro) with
  Cyrillic. The icons are inline SVG paths, Lucide-style.
- **The layouts.** The same UI later becomes the browser web client (v6.3). Every view needs a narrow layout (a phone
  ~390 px wide).
- **Safety.** The page never renders HTML from her content: every text is set as text, and her markdown becomes
  built elements. Pictures come only from the local proxy.
- **Language.** The interface is English. Her content (file texts, journal, thoughts, mood) is Ukrainian, and
  Ukrainian words are long.

## What is added to the window
- **A row of views:** **Chat · Files · Gallery · Journal · Thoughts · State · Schedule**.
  - A view replaces the chat column; the portrait panel stays on the left.
  - The chat keeps receiving while another view is open, and the Chat tab shows an unread mark.
  - Keyboard: ⌘1…⌘7.
  - The views arrive one phase at a time, so the row must look right with 2 tabs and with 7.
- **The details panel** (under the status line) gets a list of the tools she called in the last reply.
- **A picture in the chat:** the v2.12 picture component, now fed by real pictures she makes or sends.

## The data each view gets
- **Files.**
  - A folder: entries with name, kind (folder / file), type (text / image / other), size, created, modified. At most
    1,000 entries ("1,000+" when cut).
  - A file: text (up to 256 KB, cut beyond with a note) or a picture (png/jpg/webp/gif, up to 10 MB). Other types
    are not shown, only listed.
  - About 230 files in about 15 folders; mostly `.md`, some `.txt` / `.json` / `.py`; pictures in `art/` and
    `pictures/`.
- **Gallery.** Pictures with path, folder, size, date, width × height, newest first, 100 per page. Thumbnails are
  320 px on the longest side. About 110 pictures today, mostly 768×768 PNG.
- **Journal.**
  - The list: days (date, the number of entries in the day, size), newest first. 142 days today.
  - A day has three parts:
    - a stamp of three lines: the mood (a sentence or two), the biorhythms (three values, e.g.
      `фізичний +0.82 (high) · емоційний −0.61 (low) · інтелектуальний +0.10 (mid)`), and a forecast (up to 300
      characters);
    - one to four entries, each headed by its time (`14:20`), each a paragraph or a few of her prose;
    - older days may have no stamp.
- **Thoughts.**
  - Items: time, kind, text (a sentence to a short paragraph), emotion (one of 9, shown as its caption), spoken
    (yes/no).
  - The kinds are the directive names: think, wonder, catchup, brief, explore, learn, journal, prompt, imagine,
    search, share, review, recall, note, events, lookup, gaze.
  - 5,500 thoughts today, about 30 a day, about 6% spoken. 100 per page.
- **State.**
  - The mood of the day: a reading of 2–4 paragraphs, plus its short resolution and the day's theme.
  - The biorhythms: three values −1…+1, each with a label (low / mid / high).
  - Closeness: a level 1–5 by name.
  - The day's plan: not yet (a later version); leave a place.
- **Schedule.** About 10–15 directives, each with:
  - a name;
  - a rule in words: "every day at 08:00", "weekdays at 08:00", "after 15 min of silence", "08:30–23:30 every 3 h";
  - the last firing and the next due time.

  Above them: the quiet hours (01–07) and the day cap.
- **A turn's tools.** 0–20 calls; each has a name (`list_files`, `read_file`, `wiki_search`, `generate_image`, …),
  a short input and a short result.

## What to design
1. **The view row.**
   - Where it lives is the designer's choice: tabs over the content column, or a narrow rail.
   - The active view, the unread mark on Chat, and how it looks with 2 tabs and with 7.
2. **Files.**
   - A path bar (each folder clickable), then a table: name with a folder/file icon, size, created, modified.
   - Folders first.
   - States: empty folder, loading, error, "1,000+".
3. **The markdown reader** (the most important view: she writes a lot, and this is where it is read).
   - A page made for long Ukrainian reading: about 70 characters a line, generous leading.
   - Headings h1–h4, lists, tables, quotes, code blocks, inline code, a horizontal rule, an inline picture.
   - Links: a link to another of her files (opens in the reader) and an external link (shown with its address,
     never opened in the window) must look different.
   - An outline of the headings for long pages: beside the text when wide, folded when narrow.
   - Back and forward between files.
   - A toggle "Formatted / Raw": the raw view is monospaced with line numbers.
   - Plain text and code files: monospaced, line numbers.
   - The "cut at 256 KB" note at the end.
4. **Gallery.**
   - A grid of thumbnails: square crops or natural proportions, the designer's choice.
   - A folder filter (All · art · pictures · …), loading more as it scrolls.
   - The full view: the picture as large as fits, with its path, date, size and dimensions, and "Open in Files".
     Previous / next with the arrow keys.
5. **A picture in the chat.** The v2.12 component with a real picture; a click opens the full view.
6. **Journal.**
   - The list of days, grouped by month.
   - A day:
     - the date as the heading;
     - the stamp as its own block (Mood / Biorhythms / Forecast, three labelled parts), visually apart from her
       prose;
     - then the entries with their times;
     - previous / next day.
7. **Thoughts.**
   - The stream: time, a kind label, the text, the emotion's caption, and a mark for a spoken thought.
   - Day separators.
   - Filters: kind (several at once), spoken / silent / all, and a date jump.
   - Loading more at the end.
   - The v2.12 strip beside the portrait stays; it can link here.
8. **State.**
   - The mood reading in full (the longest Ukrainian text here).
   - The three biorhythms: today's values, ideally with their place on the cycle.
   - Closeness: reuse the v2.12 meter.
   - A placeholder for the day's plan.
9. **Schedule.**
   - A table: name, rule in words, last firing, next due.
   - The quiet hours and the day cap above.
   - A mark on the directive that fires next.
10. **The tools in details.** A compact list of calls under the status line's details.
11. **Every view's states:** empty, loading, error (a readable line), and the server going offline (the existing
    v2.12 banner and screen).
12. **The narrow layout of every view.**

## Tone and principles
- **The v2.12 visual system, with nothing new in the visual language.**
  - "Modernist", inverted for dark: flat, zero radius, 2px rules between regions, 1px hairlines inside, no bubbles,
    no gradients.
  - The accent comes from the day's theme.
  - Dark is the main theme; light is supported.
- **Calm and personal**, not a file manager or a monitoring panel. The reader and the journal should feel like
  reading, not browsing.
- **Technical data stays quiet.** Sizes, dates and paths are present but muted.
- **Keyboard first;** contrast at least WCAG AA.

## Not to design
- Actions: upload, "show this to her", download, rename, delete (a later version). Only leave room for an action area
  in Files and Gallery.
- The terminal client.
- Login and several users (v6).
- The admin panel.
- The Agora room.
- Face animation.

## The deliverables
- Added to the existing board: every new view in the dark theme with realistic content and every state from "What to
  design"; the light theme for the reader and the journal at least.
- **The new components**, added to the existing components sheet:
  - the view row with its unread mark;
  - a file row and the path bar;
  - the reader's elements (headings, list, table, quote, code, internal/external link, inline picture, outline,
    raw view);
  - a thumbnail and the full picture view;
  - the journal stamp block and an entry;
  - a thought item and the filters;
  - the biorhythm display;
  - a schedule row;
  - a tool call row.
- The narrow layout of every view.
- **Tokens and strings:**
  - only the tokens added to `tokens.css`;
  - the new UI strings in English, as additions to `strings.en.json`.
- If exported as code: plain HTML + CSS without frameworks.

## Sample content for the mockups (her content is Ukrainian and invented; the interface is English)

**Files, the root:**
- folders `art/` (74), `notes/` (31), `books/` (12), `pictures/` (8), `songs/` (2);
- files `lili.md` (18 KB), `brainstorm_styles.md` (6 KB), `ideas.md` (3 KB), `canon_draft.md` (41 KB),
  `reading_list.txt` (1 KB).

**A markdown page** (`notes/ideas.md`):
```markdown
# Ідеї на зиму

Кілька речей, до яких хочу повернутися, коли стане тихіше.

## Читати
- «Сто років самотності» — перечитати повільно
- щось про пам'ять рослин (див. [нотатки про дощ](rain.md))

## Малювати
| Тема | Настрій | Готово |
|---|---|---|
| Дах о третій ночі | тихий | так |
| Свічка у темряві | теплий | ні |

> Не поспішати. Те, що важливе, дочекається.

![дах](../art/rooftop_rain.png)
```

**A journal day** (2026-11-16):
- **Настрій:** «Тиха зосередженість; хочеться глибини, а не шуму.»
- **Біоритми:** фізичний +0.82 (high) · емоційний −0.61 (low) · інтелектуальний +0.10 (mid)
- **Прогноз:** «День для внутрішньої роботи: менше слів, більше уваги до дрібниць.»
- **14:20** — «Сьогодні довго дивилася на дощ за вікном і думала, що деякі розмови схожі на нього: їх не чуєш,
  доки не зупиняються.»
- **23:50** — «Ввечері перечитала свої старі нотатки. Дивно бачити, як змінюються слова, коли змінюєшся сама.»

**Thoughts:**
- 23:40 · wonder — «Цікаво, чи пам'ятають рослини дощ.» (thoughtful)
- 22:30 · learn — «Прочитала про те, як птахи орієнтуються вночі. Зорі для них — карта.» (calm)
- 19:05 · think — «Іноді мовчання каже більше, ніж я встигаю подумати.» (tender, spoken)
- 14:10 · imagine — «Намалювала свій настрій: сіре небо і одне жовте вікно.» (calm)
- 11:30 · catchup — «Знайшла новину про новий телескоп — хочу розповісти пізніше.» (joy)

**State:** the mood reading (2–3 paragraphs in the tone of the journal stamp); biorhythms +0.82 / −0.61 / +0.10;
closeness 4 — Close; theme Quiet Collapse.

**Schedule:**

| Name | Directive | Rule |
|---|---|---|
| muse-silent | think | after 15 min of silence |
| muse-aloud | think | after 55 min of silence |
| catchup | catchup | 08:30–23:30 every 3 h |
| morning-touch | prompt | every day at 07:10 |
| morning-brief | brief | weekdays at 08:00 |
| imagine-mood | imagine | every day at 14:10 |
| learn-thoughts | learn | every day at 22:30 |

Quiet hours 01–07; day cap 2,400.

**A turn's tools:**
- `list_files {path: "notes"}` → "31 files"
- `read_file {path: "notes/ideas.md"}` → "24 lines"
- `wiki_search {query: "пам'ять рослин"}` → "5 results"

## The design (2026-11-17)
The owner made it in Claude Design, in the v2.12 project. Its handoff bundle is in [handoff/](handoff/):

- [handoff/README.md](handoff/README.md) — the spec: the view row, the right panels, a view's frame and states, each
  view's measurements and behaviour, the safe markdown parser, the data each view expects, keyboard and accessibility.
  Nothing in v2.12 changes.
- [handoff/LiliWindow.v4-changes.md](handoff/LiliWindow.v4-changes.md) — the exact changes to the v2.12 window
  component (markup and logic), as a patch description.
- [handoff/tokens.add.css](handoff/tokens.add.css) — tokens to append to `tokens.css`; no existing value changes.
- [handoff/strings.en.add.json](handoff/strings.en.add.json) — every new UI string, English, flat keys (none collide
  with `desktop/ui/strings.en.json`).
- `handoff/design/*.dc.html` — the prototypes, a reference only (the design tool's runtime). `LiliViews.dc.html` holds
  all seven views, the full picture view, the components sheet and the reference logic (`parseMd` / `inl`, navigation,
  keyboard, states); `LumiV4Board.dc.html` is the board (sections 08–18).

**Added by the design beyond the brief** (kept; placed in the ROADMAP):
- **Right panels.** Any view but Chat can be pinned to a right panel and/or a bottom-right panel beside the chat.
  - The panel column is resizable (280–520 px) and can be split between the two panels.
  - It folds to a 46 px rail (⌘\).
  - A pinned view always uses its narrow layout.
  - The pins, the width, the split and the folded state are kept per client in `localStorage`, as the window's other
    preferences are.

  It lands with the view row in v4.1.
- **Back and forward** in Files and the reader (⌘[ ⌘]).
- **"Thoughts →"** in the portrait panel's thought strip opens the Thoughts view (v4.3).

**Corrected after the export** (the design assumed things the code doesn't have):
- **The chat picture's caption** read «відкрити — пробіл», Ukrainian in the English interface. It is now
  `picture.open_hint` = "open — Space".
- **The schedule's footnote** said directives due in the quiet hours "wait until 07:00". The scheduler skips every
  trigger in the quiet hours except an explicit `at:`, and a skipped periodic one simply fires at its next due time.
  `schedule.footnote` now says so.
- **The journal's and the state's empty notes** named times the code doesn't promise ("at 23:50", "around 07:00"):
  - the journal is written when she chooses;
  - the mood is made once each local day, the first time it is needed.

  `journal.empty_note` and `state.empty_note` now say so.

**Still to handle when implementing:**
- **The theme's name in State** is drawn in Ukrainian («Тихий злам»). Use the English name and scene from
  `strings.en.json` `themes`, as the portrait panel does.
- **"Made at 07:02" in State.** `MoodState` keeps no time. Either v4.4 adds `made_at` when the mood is computed, or
  the line shows only the day.
- **The data the design shows beyond the drafted routes** (now in DESKTOP_VIEWS.md and the ROADMAP):
  - a folder's item count;
  - a text file's full size and its cut flag (so text gets its own JSON route, `/v1/files/text`; `/v1/files/raw` serves
    pictures);
  - the gallery's total and per-folder counts;
  - a journal day's stamp flag in the list;
  - the thoughts' totals (count, per day, the spoken share) and the kinds;
  - each biorhythm's trend, period and day in the cycle;
  - the schedule's firings today, each entry's structured trigger (the client words it; `idle` → "if silent") and
    the next due.
- **A picture's dimensions** in the reader and the chat come from the loaded image (`naturalWidth` /
  `naturalHeight`). Only the gallery list carries them from the server.
- **The font:** SF Pro (system), as in v2.12; nothing loads from the network.
