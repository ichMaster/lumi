# Handoff: Лілі's desktop window — views of what she keeps (Lumi v4)

## Overview
v4 extends the v2.12 window (see `design/v2.12/handoff/README.md`). **Nothing in v2.12 changes**: the portrait panel, the chat, the input, the status line, the connection banner and screens, the narrow layout, the tokens and the accent table stay exactly as they are. v4 adds:

1. a **view row** (tabs) over the content column: Chat · Files · Gallery · Journal · Thoughts · State · Schedule;
2. seven **read-only views** of what she keeps on the server, each with empty / loading / error states and a narrow layout;
3. **right panels**: any view except Chat can be pinned to a right panel and/or a bottom-right panel, beside the chat;
4. a **full picture view** (from the gallery, from Files, from the reader and from the chat picture);
5. a **tool list** in the status details;
6. the v2.12 chat picture fed by real pictures.

Target: the same static `desktop/ui/index.html` + `style.css` + `app.js`. No framework, bundler or node. Tokens are CSS variables: v2.12 `tokens.css` plus `tokens.add.css` (additions only). The same UI later becomes the browser client (v6.3), so every view has a phone layout (~390 px).

**Language.** The new interface strings are English (`strings.en.add.json`, keys are stable). Her content — file texts, journal, thoughts, mood — is Ukrainian; keep `lang="uk"`, `hyphens:auto` on content.

**Safety (hard rule).** Never render HTML from her content. Every text is set with `textContent`. Her markdown is parsed into built DOM elements (see Reader). Pictures load only from the local proxy. External links are never opened in the window.

## Files in this package
- `README.md` — this spec. Every value here comes from the design files.
- `tokens.add.css` — tokens to append to v2.12 `tokens.css` (`:root`). No existing value changes.
- `strings.en.add.json` — all new UI strings (English), with `{placeholders}`.
- `LiliWindow.v4-changes.md` — the exact changes made to the v2.12 window component (markup + logic), as a patch description.
- `design/LiliViews.dc.html` — **the reference implementation of all seven views**, the full picture view and the components sheet. Read its logic class: data shapes, the markdown parser, navigation, keyboard, states. Its `<style>` block (`.lv-*` classes) is close to production CSS.
- `design/LumiV4Board.dc.html` — the presentation board (sections 08–18): every view and state side by side, plus the token and string tables.

The `*.dc.html` files are design prototypes written for the design tool's template runtime (`{{ holes }}`, `<sc-if>`, `<sc-for>`, `<dc-import>`, a `DCLogic` class whose `renderVals()` feeds the template; views are built with `React.createElement` as `h(...)`). Recreate them in plain HTML/CSS/JS. Read inline styles as the spec.

## Fidelity
High-fidelity, the v2.12 "Modernist, inverted" language with nothing new: flat, **zero radius**, 2px `--color-divider` rules between regions, 1px `--l-hair` hairlines inside, flush left, no bubbles, no gradients. Accent from the theme of the day. Dark is primary; light is supported (board shows reader, journal, state in light). Contrast ≥ WCAG AA. `prefers-reduced-motion` disables all animation.

Shared atoms reused everywhere (all from v2.12):
- **Kicker**: 10.5px / 600 / uppercase / .1em / `--l-faint`.
- **Divider with label** (the session divider): flex row, gap 12; label is a kicker; rule `flex:1; height:2px; background:var(--color-divider)`. Used for months (gallery) and days (thoughts).
- **Pulse** (loading): three 6×6 accent squares, gap 4, `liliPulse 1.2s` staggered .18s, then 13px `--l-faint` text.
- **Meta grid**: `grid-template-columns:max-content minmax(0,1fr); gap:6px 16px; 13px`; keys `--l-faint`.
- **Closeness meter**: 5 cells, 6px tall, gap 3, filled `--color-accent`, empty `--l-hair`, `role="img"`.
- **Icons**: inline SVG, Lucide paths, `stroke:currentColor; stroke-width:2; stroke-linecap:square; fill:none`, 15–18px.
- **Buttons**: `.btn .btn-secondary` (36px high) and text buttons (12px/600 `--l-acc-text`, hover `--color-text`). Icon buttons 32×32, `--l-muted`, hover bg `color-mix(text 7%)`, disabled opacity .35.
- **Segmented control**: `.seg` / `.seg-opt` with hidden radio inputs (5px 10px, 12px); checked = accent fill, `--color-bg` text.

---

## 1. The view row
Lives at the top of `<main>` (the content column), above the connection banner. The portrait panel and the status line do not move; the row takes its 42 px from the column.

- `<nav aria-label="Views">`: `display:flex; box-shadow: inset 0 -2px 0 var(--color-divider); padding: 0 16px 0 26px` (narrow `0 4px`).
  - Tabs live in an inner scroller `flex:1; min-width:0; overflow-x:auto; scrollbar-width:none` (on a phone the row scrolls sideways).
  - Each tab is a `<button>`: height 42, padding 0 14, 13px/600, no background, `border-bottom:2px solid transparent`, color `--l-muted`, hover `--color-text`, `white-space:nowrap`. **Active**: `aria-current="page"`, color `--color-text`, border-bottom `--color-accent` (it covers the 2px rule).
  - **Unread on Chat**: when another view is open and a new message arrives, a 6×6 `--color-accent` square after the label (`role="img" aria-label="new messages"`). Cleared on opening Chat.
  - `title="Files  ⌘2"` etc.
- **2 to 7 tabs**: the views ship one phase at a time. The row simply ends early; nothing stretches.
- **Keyboard**: ⌘1…⌘7 (Ctrl on non-mac) switch views; ⌘\ folds/unfolds the right panels.
- **The chat keeps receiving** while another view is open (the stream continues off-screen; on return, scroll to bottom).
- A view **replaces the message list + input** (the banner stays above it). Reader is under the Files tab; the full picture view under Gallery.
- **Pin group** (wide only, when the open view isn't Chat): after the tabs, `flex:none; padding-left:10px; gap:2px`: kicker «Pin», then two 32×32 icon buttons with `aria-pressed`:
  - «Pin to the right panel» — icon: Lucide `panel-right` with the right part filled;
  - «Pin to the bottom-right panel» — `panel-right` with a horizontal line at 12 and the bottom-right quarter filled.
  - Pressed: background `--color-surface`, color `--l-acc-text`. Press again to unpin. A view lives in one panel at a time (pinning to the other moves it).

## 2. Right panels
A right column after `<main>` (wide layout only; on a phone, pinned views just stay tabs).

- `<aside aria-label="Right panels">`: `flex:none; width:var(--l-side)` (340, user drag 280–520); `border-left:2px solid var(--color-divider)`; flex column.
- **Width handle**: a transparent `<button aria-label="Resize the right panel">` 10 px wide over the left edge (`left:-6px`), `cursor:col-resize`, hover tint `color-mix(accent 35%)`. Mouse drag; ← → ±20 px.
- **One or two panels**: «Right panel» (top) and «Bottom-right panel». If both have pins, the column splits: `flex: var(--l-side-split) 1 0` / `1 - split` (default .55, drag .2–.8, min height 140). The second panel has a `border-top:2px` rule and a row-resize handle on it (↑ ↓ ±.05). If only one has pins it takes the full height.
- **Panel header**: `box-shadow: inset 0 -2px 0 var(--color-divider)`; pinned view tabs (height 40, 12.5px/600, same active bar as the view row); then × «Unpin {view}» (32×32); the top panel also has » «Collapse the right panels».
- **Body**: the view, always in its **narrow layout** (the panel is ~340 px).
- **Navigation inside a panel** stays in that panel (e.g. a pinned Files opens a file in the reader there; Open in Files from a pinned gallery switches that panel to Files).
- **Collapsed**: a 46 px rail with `border-left:2px`; a `panel-right` icon button «Open the right panels» and the vertical label of what's pinned («Journal · Schedule», kicker, `writing-mode:vertical-rl`).
- Persist pins, active tabs, width, split and open/folded state locally (per client).

## 3. A view's frame
Every view: flex column filling the area. Header `.lv-bar`: `min-height:52px; padding:8px 16px 8px 24px` (narrow 8 12 8 16); `border-bottom:1px solid var(--l-hair)`; flex, wrap, gap 8 12. Title 15/700; count/meta 12px `--l-faint` tabular. Body scrolls (`flex:1; min-height:0; overflow-y:auto`). A view goes narrow below **640 px** of its own width (`--l-view-narrow`), measured with a ResizeObserver, so it also fits the side panels.

### States (all views)
- **Loading**: the pulse + «Reading notes/…» etc., then 5 flat skeleton rows (10 px bars on `--color-surface`, widths 62/44/71/38/55 %, hairline above each). Padding 40 24 (narrow 28 16), max-width 560.
- **Empty**: kicker + one sentence 17/1.5 + a 13px `--l-faint` note (+ an action where useful, e.g. «Clear filters»).
- **Error**: a `role="alert"` row: 18px Lucide `circle-alert` in `--l-acc-text`, a 15/600 title («Couldn’t open notes/») and a 13px `--l-muted` line naming the cause (status + reason); below, `.btn-secondary` «Try again» with the refresh icon.
- **Offline**: the existing v2.12 banner + status line; the view keeps what it already loaded.
Strings for each are in `strings.en.add.json` (`<view>.empty`, `.loading`, `.error_title`, …).

## 4. Files
- **Header**: back / forward icon buttons (⌘[ ⌘]; history covers folders and files), then the **path bar** `<nav aria-label="Path">`: `lili / notes / …`, each segment a `<button>` in mono 13px `--l-muted` (hover underline), separators `/` in `--l-faint`, the current one `aria-current="page"` 600 `--color-text`. Then «N items» (12px faint). Leave the right end of the bar free: the **action area** for upload / show to her / download arrives later.
- **Table** (wide): columns `minmax(0,1fr) 96px 112px 132px` = Name · Size · Created · Modified, gap 16. Sticky header row: kickers over a 2px rule. Rows are `<button>`s: min-height 42, padding 6 24, hairline below, hover `color-mix(text 4%)`.
  - Name: 16px icon + name 14px. **Folders first**, name 600 with a trailing `/`, icon and name in `--l-acc-text` icon (folder), text `--color-text`. Files: icon `--l-faint` (`file-text` text, `image` picture, `file` other).
  - Size / dates: 12px `--l-faint`, tabular, ellipsis. Folder size = «74 items». Dates «16 Nov 2026», today «Today 14:10», «Yesterday».
  - **Other types** (not text, not png/jpg/webp/gif): listed, muted name, an 11px «not shown» tag, `aria-disabled="true"`, no action, `title="This kind of file is listed but not shown"`.
  - Click: folder → open; text → Reader; picture → full picture view (prev/next within that folder).
- **Narrow**: no header row; rows `20px minmax(0,1fr)`, padding 8 16, min-height 52: icon, name, and under it «18 KB · Today 14:10».
- **1,000+**: count reads «1,000+ items»; after the list a 2px rule and «Showing the first 1,000 entries.» (600) + «The folder has more; the server cuts the list at 1,000.» (faint).

## 5. Reader (the most important view)
- **Header**: back/forward, the path bar ending with the file name (non-button, 600), meta «18 KB · Today 14:10», and for `.md` the `.seg` **Formatted / Raw**. Other text files show a kicker instead: «Plain text» / «Code» (.py etc.) / «Data» (.json). Narrow: two header rows — (back/forward + path), then (toggle + meta + «Contents ▾»).
- **Page**: `<article>` max-width **68ch** (≈70 Cyrillic chars), centred, padding 40 40 64 (narrow 24 18 48). Body `--l-read` 17px / 1.7, `text-wrap:pretty`, paragraph margin 0 0 18.
  - h1 32/1.12/800, −.02em, mb 20 · h2 23/1.2/800, −.01em, mt 40 mb 14 · h3 18/1.3/700, mt 30 mb 10 · h4 12px/600 caps .1em `--l-muted`, mt 28 mb 10. `text-wrap:balance`; `scroll-margin-top:16px`.
  - Lists: padding-left 1.4em, gap 4, li padding-left 4.
  - Quote: 2px left `--color-divider`, padding-left 18, `--l-muted`, same size.
  - Code block: `--color-surface`, padding 12 14, mono 13.5/1.6, `white-space:pre`, `overflow-x:auto`.
  - Inline code: mono .84em, padding 1px 5px, `--color-surface`.
  - Table: wrapper `overflow-x:auto`; 15px; head cells kickers over a 2px rule; body cells padding 8 16 8 0, hairline below.
  - Rule: 1px `--l-hair`, margin 36 0.
  - Inline picture: figure `width:min(100%, 420px)`, square thumbnail button (opens the full view), caption mono 11px faint «alt · art/rooftop_rain.png · 768 × 768». Paths resolve relative to the file's folder (`../art/x.png`).
  - **Internal link** (relative href to another of her files): a `<button>` styled as a link — `--l-acc-text`, solid 1px underline, offset 3; opens the file in the reader (pushes history). `title="Open notes/rain.md"`.
  - **External link** (`http(s)://`): **not a link**. The text with a dotted `--l-faint` underline, then `↗ host/path` in mono .72em `--l-faint`; `title="External link — not opened in this window"`.
- **Outline** for long pages (≥ 4 headings): wide → an `<aside>` 220 px, `border-left:1px solid var(--l-hair)`, padding 40 20; kicker «On this page»; h2–h4 as buttons (13px `--l-muted`, h3 indented +12, h4 12px +24), `border-left:2px solid var(--l-hair)`; the **current** heading (follows the scroll) gets `aria-current="true"`, accent left border, 600 `--color-text`. Narrow → folded under «Contents ▾» in the header; opened, it lies on a `--color-surface` band above the text and closes after a jump.
- **Raw** (and all non-md text): mono 13/1.65 grid `max-content minmax(0,1fr)`: line numbers right-aligned, faint, `user-select:none`, 1px hair right border, padding-right 12; lines `pre-wrap`, `overflow-wrap:anywhere`, padding-left 14.
- **Cut at 256 KB**: at the end, a 2px rule, «Cut at 256 KB» 14/600 and «The file is 412 KB. Only the first 256 KB are shown here.» 13px faint.

### Markdown → DOM (safe)
Line-based parser (reference: `parseMd` / `inl` in `LiliViews.dc.html`): `#`–`####` headings, ``` fences, `---`, `![alt](src)` on its own line, `>` quotes, `|` tables (row 2 = separator), `-`/`*`/`1.` lists, paragraphs. Inline: `` `code` ``, `**bold**`, `[text](href)`. Everything else is literal text. Build elements with `createElement` + `textContent`; never `innerHTML`.

## 6. Gallery and the full picture view
- **Header**: «Gallery» 15/700, «112 pictures · newest first», and on the right a `.seg` folder filter `All · art · art/sketches · pictures · notes` with counts (opacity .7). The filter scrolls sideways if needed.
- **Grid**: `repeat(auto-fill, minmax(var(--l-thumb-min) /*150*/, 1fr))`, gap 18 14, padding 20 24 32 (narrow: 3 columns, gap 4, padding 12, **no captions**). Month dividers span the row («November 2026»).
  - Tile = `<button>`: a **square crop** (`aspect-ratio:1; object-fit:cover; background:--color-surface`), then name 12px `--l-muted` (ellipsis) and «Today 14:10» 11px faint. Hover/focus: a 2px inset accent frame (an `::after` overlay, so it sits above the image).
  - Thumbnails 320 px from the server; 100 per page; when the scroller is within 160 px of the end, show the pulse «Loading more · 40 of 112» and fetch the next page. At the end: «That’s all — 112 pictures.»
- **Full view** (`role="dialog" aria-modal="true"`), covers the view area (or the chat column, when opened from the chat picture):
  - Header: × Close (Esc), kicker «Gallery» or the folder «art/», «3 / 112», then ‹ › (disabled at the ends; ← →).
  - Picture area: padding 24 (narrow 12); the picture **as large as fits, natural proportions** — a `container-type:size` box and the image `width:min(100cqw, calc(100cqh * w/h)); aspect-ratio:w/h`.
  - Meta column 280 px (`--l-picmeta`), `border-left:1px solid --l-hair`, padding 20 24: name 15/600; meta grid Path (mono) · Date «9 Nov 2026, 21:40» · Size · Dimensions «768 × 768»; `.btn-secondary` «Open in Files» (folder icon) → Files at the picture's folder; at the bottom the reserved **action area** (kicker «Actions · later»); hint «← → previous / next · Esc close». Narrow: the meta drops under the picture (`border-top`), no action area.
  - Focus moves into the dialog on open; Esc returns.
- **Chat picture**: the v2.12 figure, now a real file: the square is a `<button aria-label="Open picture rooftop_light.png">` (`cursor:zoom-in`, hover 2px accent ring); caption «art/rooftop_light.png · 768 × 768 · відкрити — пробіл» (path in mono). Click / Space opens the full view over the chat column.

## 7. Journal
- **Wide**: a day list `<nav aria-label="Days">` 260 px (`--l-daylist`), `border-right:2px solid var(--color-divider)`, scrolls; the day reads on the right.
  - Sticky month headers: kicker «November 2026» + «16 days», over a 2px rule.
  - Day rows (`<button>`): `34px minmax(0,1fr) auto`, padding 7 16 7 20, min-height 48: date number 20/800; weekday 13px + «2 entries» (or «· no stamp») 11.5 faint; size 11 faint. Current: `aria-current="true"`, `--color-surface` + 2px accent inset on the left.
- **A day** (`<article>`, max 72ch, padding 36 44 56; narrow 20 18 40):
  - kicker «Journal» + «day 142 of 142 · 2 entries · 1.4 KB»;
  - **h1** «Monday, 16 November» 36/1.08/800 (28 narrow) + the year at .6em faint;
  - **Stamp** (`<section aria-label="Stamp of the day">`): `--color-surface`, `border-top:2px`, 14px; three rows `96px minmax(0,1fr)`, padding 11 18, hairlines between: kicker **Mood** — the sentence; **Biorhythms** — three cells (3 columns; stacked narrow): «фізичний» `--l-muted` · «+0.82» 600 tabular · «high» 11 faint; **Forecast** — up to 300 chars. Narrow: label above value.
  - Older days without a stamp: «No stamp — this day was written before she kept one.» 13px faint.
  - **Entries**: grid `64px minmax(0,1fr)` (`--l-time-gutter`), padding 18 0, hairline above; time 13/600 `--l-acc-text` tabular; prose `--l-read` 17/1.7. Narrow: time above.
  - Footer: 2px rule, `.btn-secondary` «‹ Previous · Sun 15 Nov» and «Next · Tue 17 Nov ›» (disabled «First day» / «Latest day»); hint «← → previous / next day». ← → work when focus isn't in a field.
- **Narrow**: the list is its own screen (header «Journal · 142 days · newest first»); a day has a header with «‹ All days» and ‹ › icon buttons.

## 8. Thoughts
- **Header**: «Thoughts» + «5,512 · about 30 a day · 6% spoken»; then filters (wrap):
  - **Kind** chip-button (`.lv-chip`: 30 high, 1px divider border, 12px; `aria-expanded`): «KIND All ▾» / «wonder, learn» / «3 kinds». Opens an in-flow panel under the header with all 17 kinds as mono toggle chips (`aria-pressed`; pressed = accent fill) — several at once — plus «Clear kinds» and the hint «Several at once · the list updates as you pick».
  - **Spoken** `.seg`: All · Spoken · Silent.
  - **Jump to**: kicker + `<input type="date" class="input">` 150×30 (`color-scheme` matches the theme).
- **Stream** (max-width 760, padding 22 40 40; narrow 14 16 32): day dividers «Today · Monday, 16 November · 11»; items `<article>`: grid `56px minmax(0,1fr)`, padding 14 0, hairline above: time 12 faint; kind as a kicker in `--l-muted`; **Spoken** mark: 12px Lucide `message-circle` + «SPOKEN» 11/600 caps `--l-acc-text`; the text 15.5/1.55 in `--color-text`; her caption for the emotion 12px faint (the 9 captions from v2.12). Narrow: time moves into the kind line.
- More loads at the end with the pulse «Loading earlier thoughts…» (100 per page).
- No match: empty state «No thoughts match these filters.» + «Clear filters».
- The v2.12 strip under the portrait stays; its header gains a text button «Thoughts →» (12px/600 `--l-faint`) that opens this view.

## 9. State
- **Header**: «State» + «Monday, 16 November · made at 07:02».
- **Wide**: grid `minmax(0,1.35fr) minmax(300px,1fr)`, the right column with a 2px left rule. Narrow: stacked, 2px rule between.
- **Mood of the day** (padding 36 44 48): kicker; theme row «Тихий злам» 600 + «quiet-collapse · вигорання, мерехтливий неон» 12 faint; the **resolution** 24/1.35/600 (20 narrow), max 30ch, balance; then the reading paragraphs at `--l-read` 17/1.7, max 64ch, gap 16.
- **Biorhythms** (padding 28 28 16): kicker; three rows `120px minmax(0,1fr)`, hairline above: name 13/600, value **24/800** tabular («+0.82»), «high · falling» 11.5 faint; and the **cycle**: an SVG (`role="img"`, aria-label «Physical +0.82, high, falling, on a 23-day cycle»), 56 px high, ±14 days: zero line `--l-hair`, a centre rule for today `--color-divider`, the sine in `--l-muted` 1.5 px (`vector-effect:non-scaling-stroke`), today as an 8×8 accent square; under it «−14 d · today · 23-day cycle · +14 d» 10.5 faint. Cycles 23 / 28 / 33 days; label: ≥ .33 high, ≤ −.33 low, else mid.
- **Closeness** (2px rule above): kicker; «Close» 18/700 + «4 of 5» faint; the v2.12 meter (max-width 160).
- **Plan for the day**: kicker; a 1px `--l-hair` box, padding 14 16: «Her plan for the day will live here.» `--l-muted` + «Arrives in a later version.» faint.

## 10. Schedule
- **Header**: «Schedule» + «what wakes her thoughts · now 23:58».
- **Summary** (2px rule below): 4 cells (2×2 narrow), hairline separators, padding 14 24: kicker + value 16/700 + note 12 faint — Quiet hours «01:00 – 07:00 / nothing fires» · Day cap «2,400 / 31 fired today» · Directives «12 / all enabled» · Next «muse-silent» (mono) «00:13 · if silent».
- **Table** (wide): `minmax(0,1.1fr) 92px minmax(0,1.5fr) 120px 140px` = Name (mono 13/600) · Directive (kicker `--l-muted`) · Rule in words 14px · Last fired (12 faint) · Next due 13px `--l-muted` (+ « · if silent» faint). Sticky kicker header over a 2px rule; rows min-height 44, padding 8 24, hairline below.
  - **Next to fire**: `--color-surface` + 2px accent inset on the left + a «NEXT» label (11/600 caps `--l-acc-text`) after the name; its next time in `--color-text` 600.
- **Narrow**: stacked rows: name + kicker (+ NEXT); rule 14px; «last Today 23:41 · next 00:13».
- Footnote: «Times are the server’s local time. Directives due in the quiet hours wait until 07:00.»

## 11. Tools in the status details
Under the v2.12 details grid, before the think-mode row: padding 8 14, hairline below. Header: kicker «Tools · last reply» + count. List (max-height 150, scrolls; 0–20 rows): grid `104px minmax(0,1fr) auto` (narrow: one column), gap 2 14, padding 5 0, hairline above, 12px: **name** mono 600 `--l-acc-text` · **input** mono `--l-faint`, one line, ellipsis · **result** `--l-muted` «→ 5 results». Empty: «No tools in the last reply.»

## Data each view expects (from the brief)
- **Folder**: entries `{name, kind: folder|file, type: text|image|other, size, created, modified}`, ≤ 1,000 (+ a cut flag).
- **File**: text ≤ 256 KB (+ cut flag and full size) or a picture (png/jpg/webp/gif ≤ 10 MB).
- **Gallery**: `{path, folder, size, date, width, height}`, newest first, 100/page; thumbnails 320 px on the longest side.
- **Journal**: days `{date, entries, size}`; a day `{stamp?: {mood, biorhythms: [{name, value, label}], forecast}, entries: [{time, text}]}`.
- **Thoughts**: `{time, kind, text, emotion, spoken}`, 100/page, filters kind[] / spoken / date.
- **State**: `{mood: {reading, resolution, theme}, biorhythms: [{value, label}]×3, closeness: {level, name}, plan: null}`.
- **Schedule**: `{quiet: [01,07], cap, directives: [{name, directive, rule_text, last, next}]}`; mark the earliest `next`.
- **Turn tools**: `[{name, input, result}]`, 0–20.

## Accessibility & keyboard
- Real `<button>`s for every clickable thing; `aria-current` for the active tab / day / outline item; `aria-pressed` for pins and kind chips; `aria-expanded` for Kind and Contents; dialogs `aria-modal`.
- ⌘1…⌘7 views · ⌘\ right panels · ⌘[ ⌘] back/forward (Files, Reader) · ← → days (Journal), pictures (full view) · Esc closes the full view · arrows on the resize handles.
- Focus ring: 2px `--color-accent`, offset −2 inside views.

## Out of scope (later versions)
Actions (upload, «show this to her», download, rename, delete) — only the reserved action areas exist. The day's plan. Login/multi-user, terminal client, admin, Agora, face animation.
