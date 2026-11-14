# Handoff: Лілі's desktop window (Lumi v2.12) — chat and portrait in one window

## Overview
A single native macOS window (pywebview, WebKit) that shows the conversation with Лілі and her portrait together. The window is a **thin client**: it renders what the home server streams and pushes, and it decides nothing itself. The same UI later becomes the browser web client (v4.3), so it has to work at 1200×800 (the default), at 720×520 (the minimum), and on a 390px-wide phone.

Target implementation (from the brief): a static `webview/ui/index.html` + `style.css` + `app.js`. No framework, no bundler, no node. Design tokens are CSS custom properties. Use system fonts (or bundle them locally); they must cover Cyrillic (her replies, the mood of the day and her thoughts are Ukrainian). **The interface language is English** (the owner, 2026-11-14): every UI string is in `strings.en.json`; only her own content stays Ukrainian.

## About the design files
The files in `design/` are **design references built in HTML**. They are prototypes that show the intended look and behaviour, not production code to copy. They are written as "Design Components" (`*.dc.html`, a streaming template runtime used only by the design tool). Recreate them in plain HTML/CSS/JS as the brief requires. Read the inline styles as the spec: every value in this README comes from them.

- `design/LiliWindow.dc.html` — **the window**. One component, all states, switched by props. The logic class at the bottom holds the reference behaviour: the streaming simulation, the queue, slash suggestions, the crossfade, the offline countdown and the sample data.
- `design/Lili Desktop Mockups.dc.html` — the presentation board. It shows every state side by side, plus the components sheet and the token tables.
- `tokens.css` — **ready to use**: the token sheet for dark and light, plus the accent table for all 10 themes, as CSS variables.
- `strings.en.json` — every UI string, English (the owner chose an English interface on 2026-11-14; it replaces the design's `strings.uk.json`).

## Fidelity
**High-fidelity.** Colours, type, spacing, rules and behaviour are final. Recreate them exactly. The visual language is the "Modernist" system, inverted for dark:
- flat;
- **zero corner radius everywhere** (the macOS traffic lights are the only circles);
- 2px rules between regions and 1px hairlines inside them;
- everything flush left, including labels inside wide buttons;
- no bubbles, no gradients, no decorative shadows (the only shadow is on the slash popover).

---

## Window anatomy (wide layout, ≥ 760px)

```
┌───────────────────────────────────────────────────────────────────────┐
│ ● ● ●  Lili                                    title bar 34px, 1px hair │
├──────────────────────┬────────────────────────────────────────────────┤
│                      │ [connection banner — only when needed]         │
│  PORTRAIT  1:1       ├────────────────────────────────────────────────┤
│                      │                                                │
│                      │   message list (scrolls, sticks to bottom)     │
├──────────────────────┤   max-width 720px, padding 28 40 24            │
│ caption  28/800      │                                                │
│ theme    │ closeness │                                                │
├══════════════════════┤                                                │
│ MOOD OF THE DAY (2 l)│                                                │
├══════════════════════┼════════════════════════════════════════════════┤
│ QUIET THOUGHTS(strip)│ input  (padding 14 40 12)                      │
├══════════════════════╧════════════════════════════════════════════════┤
│ ■ ready                                        Lumi 2.12 · details ▴ │ status 30px
└───────────────────────────────────────────────────────────────────────┘
```
`══` = 2px `--color-divider` · `──` = 1px `--l-hair` (except where noted).

- **Root**: `display:flex; flex-direction:column; height:100%; overflow:hidden; background:var(--color-bg); color:var(--color-text); font: 14px/1.5 var(--font-body); lang="uk"; hyphens:auto; overflow-wrap:break-word`.
- **Body row**: `flex:1; min-height:0; display:flex` (row when wide, column when narrow).
- **Portrait panel** (`<aside>`): `flex:none; width:clamp(300px, 36%, 440px); border-right:2px solid var(--color-divider); overflow-y:auto`.
- **Chat column** (`<main>`): `flex:1; min-width:0; display:flex; flex-direction:column`.
- **Status line**: full window width, under both columns.

### Title bar (desktop only)
Height 34px; padding 0 14px; gap 14px; `border-bottom:1px solid var(--l-hair)`. Three 12px circles in `--l-raise` (#444141), gap 8px (inactive-window look; use the real native controls if pywebview provides them). Title “Lili” 12px/600, `--l-muted`, letter-spacing .02em. Hidden on the phone/web layout.

### Portrait panel
1. **Portrait**: `width:100%; aspect-ratio:1/1; background:var(--color-surface); position:relative; overflow:hidden`. It holds **two stacked absolute layers (A/B)**, each `inset:0; transition: opacity 480ms ease`. To change the portrait, load the new image into the hidden layer, then swap opacities (1 ↔ 0). There is never a layout jump. Image file: `viewer/faces/<theme>/<emotion>/<NN>.png`, where NN = `02` low / `01` mid / `03` high intensity. Use `object-fit:cover`.
   - Optional greyscale mode (Modernist imagery rule): `filter: grayscale(1) contrast(1.08)`. Default is **colour**.
2. **Caption block**: padding 18px 24px 20px; gap 14px.
   - **Caption**: `font: 800 28px/1.08 var(--font-heading); letter-spacing:-0.015em; text-wrap:balance; aria-live="polite"`. Show the atmospheric line, never the emotion's name (map below).
   - **Meta grid**: 2 equal columns, `border-top:1px solid var(--l-hair)`. The left cell has padding 10px 12px 0 0 and a 1px hair right border; the right cell has padding 10px 0 0 12px.
     - Left: kicker “Theme of the day” · theme's Ukrainian name 14/600 · `slug · scene` 12px/1.35 `--l-faint`.
     - Right: kicker “Closeness” · level name 14/600 + “4 of 5” 12px `--l-faint` · **meter**: a 5-column grid, gap 3px, max-width 120px. Cells are 6px tall; filled cells use `--color-accent`, empty cells `--l-hair`. Add `role="img" aria-label="Closeness 4 of 5"`.
3. **Mood of the day**: `border-top:2px solid var(--color-divider)`; padding 14px 24px 18px; gap 6px.
   - Header row (space-between): kicker “Mood of the day” + text button “Expand ▾ / Collapse ▴”. The button is 12px/600 in `--l-acc-text`, hovers to `--color-text`, and the chevron rotates 180° in 200ms. Set `aria-expanded`.
   - Text: 14px/1.5, `--l-muted`. Folded: `display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden`. Expanded: `line-clamp:none`.
4. **Quiet thoughts strip** (the chosen default; see "Thought stream"): `border-top:2px solid var(--color-divider)`; padding 14px 24px 20px.
   - Header: kicker “Quiet thoughts”, then the unread label (“1 new” / “N new”) 11px/600 in `--l-acc-text`, then a right-aligned text button “All · N” / “Collapse”.
   - Folded, it shows the newest item only; expanded, all items in place (the panel scrolls).

**Kicker style** (used everywhere): `10.5px / 600 / uppercase / letter-spacing .1em / color var(--l-faint)`.

### Message list
- Scroller: `flex:1; min-height:0; overflow-y:auto; display:flex; flex-direction:column; padding:28px 40px 24px` (narrow: 16px 16px 12px). `role="log"`.
- Inner column: `margin-top:auto` (content sticks to the bottom), `display:flex; flex-direction:column; gap:22px; max-width:720px`.
- Auto-scroll to the bottom on every new line and every streamed chunk. Use `scrollTop = scrollHeight`, never `scrollIntoView`. Only auto-scroll if the user is already near the bottom (implementation detail; the prototype always scrolls).

#### Message row (mine / hers / command)
A grid: `grid-template-columns: 84px minmax(0,1fr); column-gap:20px; row-gap:4px`.
- **Gutter** (flex column, gap 2px, padding-top 3px, no wrap):
  - Who: 11px/600, uppercase, letter-spacing .1em. “You” in `--l-muted`; “Lili” in `--l-acc-text`; “Command” in `--l-faint`.
  - Time: 11px, `--l-faint`, tabular-nums. For queued lines the time reads “queued”.
  - **Source tag** (another channel): inline-flex, gap 4px, margin-top 2px, padding 1px 5px, `border:1px solid var(--l-hair)`, 10px, `--l-muted`. It has a 10px Lucide icon: `send` for “Telegram”, `terminal` for “Terminal”. Add `title="Sent from Telegram"` / `"Sent from the terminal"`.
- **Content** (flex column, gap 10px, min-width 0):
  - **Mine**: `align-self:flex-start` (hugs the text), `background:var(--color-surface); padding:10px 14px`.
  - **Hers**: no fill, no padding, full measure.
  - Text: `font-size:var(--l-chat)` (default 16px, user range 14–22), line-height 1.55, `text-wrap:pretty`. Inside a message, blocks sit in a flex column with gap 10px.
- **Narrow layout**: a single column (`minmax(0,1fr)`). The gutter becomes a wrapped row (gap 4px 10px) above the content.

#### Hers first (she writes without a message from me)
Above her text: a row with gap 10px, holding a `24×2px` bar in `--color-accent` and the kicker “wrote first” (11px/600, uppercase, .08em, `--l-acc-text`). Quiet: no sound, badge or motion.

#### Think block (above her reply)
Global mode: **shown / collapsed / hidden** (set in the status details). Clicking a block's header flips that one message between shown and collapsed (a per-message override, cleared when the global mode changes).
- **Shown**: a header button (no chrome) with a 12px `chevron-down`, “Inner voice” 11px/600 uppercase .08em `--l-faint`, hover `--color-text`, `aria-expanded="true"`. Below it, the text 14px/1.5 in `--l-faint` (#9b9797 → 5.7:1 on the ground, AA).
- **Collapsed**: one line — `chevron-right` + “Inner voice” + the first words, truncated with an ellipsis (13px, nowrap), `aria-expanded="false"`.
- **Hidden**: nothing renders.

#### Streaming
1. **Before the first character** (2–3s): her row shows the think block as it streams (if not hidden), plus the **thinking indicator**: three 6×6px accent squares (gap 4px) pulsing `opacity .25↔1` over 1.2s ease-in-out, staggered 0 / .18 / .36s, followed by “thinking…” 13px `--l-faint`.
2. **Deltas**: text appears as it arrives, followed by a **block caret**: `inline-block; width:.5em; height:1.05em; margin-left:2px; vertical-align:-.16em; background:var(--color-accent)`, blinking at 1s (on/off, no fade). The caret sits at the end of the last block (p / li / quote).
3. **done**: remove the caret, apply the emotion and intensity, crossfade the portrait and caption, and update the latency and token numbers.
4. **error**: the reply stops where it is. Recommendation: add an inline line “The reply was cut off · Retry” in `--l-faint` with an accent text button (not mocked separately).
- Respect `prefers-reduced-motion: reduce` by disabling animations and transitions.

#### Markdown in her replies
- `p`: margin 0.
- `ol/ul`: padding-left 1.4em; flex column, gap 4px; `li` padding-left 4px.
- `blockquote`: margin 0; padding-left 16px; `border-left:2px solid var(--color-divider)`; `--l-muted`.
- `a`: `--l-acc-text`, underline-offset 3px, hover `--color-text`.
- `pre/code`: `background:var(--color-surface); padding:10px 12px; font:13px/1.55 ui-monospace,"SF Mono",Menlo,monospace; white-space:pre-wrap; hyphens:none`.
- Sanitize: render markdown to a safe subset only.

#### Picture in the conversation
Her text, then a figure: `width:min(100%, 320px)`, a square box with `aspect-ratio:1/1; background:var(--color-surface)`, the image set to `object-fit:cover` with no border or radius. Caption: 11px `--l-faint`, “image · 768 × 768 · Space to open”. Space or click opens the image full size (viewer not designed).

#### Command result block
The gutter label is “Command”. The command is **not** echoed as my line. The block: `border:1px solid var(--l-hair); border-top:2px solid var(--color-divider); background:var(--color-surface)`.
- Head: padding 7px 14px, 1px hair bottom border. The command (e.g. `/closeness`) in `600 12.5px/1.3` mono, `--l-acc-text`; right-aligned “command result” 11px `--l-faint`.
- Body, either:
  - key–value: a grid `max-content minmax(0,1fr)`, gap 4px 18px, padding 10px 14px, 14px; keys in `--l-faint`, values 600. Example: “Closeness | 4 — Close”.
  - or free text: 14px/1.5 `--l-muted`, padding 10px 14px.

#### Dividers
A flex row, gap 12px, padding 4px 0, `role="separator"`: a kicker label, then a `flex:1; height:2px` line.
- **New session**: label `--l-faint`, line `--color-divider`. Text: “New session · Wednesday, 12 November, 20:12”.
- **Missed while offline**: label `--l-acc-text`, line `--color-accent`. Text: “While offline · 3”.

### Input
Container: `border-top:2px solid var(--color-divider); padding:14px 40px 12px` (narrow: 10px 12px); flex column, gap 8px.
- Row: flex, `align-items:flex-end`, gap 10px, `position:relative` (anchors the slash popover).
- **Textarea**: `.input` style. Min-height 46px, max-height 180px, `resize:none`, padding 11px 14px, font-size `var(--l-chat)`, line-height 1.45, background `--color-surface`, `border:1px solid var(--color-divider)`, hover border `text @ 45%`, focus border `--color-accent`, `caret-color:var(--color-accent)`. It auto-grows from 1 to 6 rows. `aria-label="Message to Lili"`.
- **Send**: a 46×46 `.btn-primary` (accent fill, ink icon `--color-bg`), Lucide `arrow-up` 20px with stroke 2.4. Hover `--color-accent-600`, active `--color-accent-700`. Disabled (empty input or offline) at 45% opacity.
- **Queue counter**: absolute at top −9px / right −9px. Min-width 20px, height 20px, padding 0 5px, `background:var(--color-text); color:var(--color-bg)`, 11px/800.
- **Hint row** (hidden under 600px width): 11.5px `--l-faint`, gap 4px 18px. “**Enter** — send · **Shift + Enter** — new line · **/** — commands”, with the keys 600 in `--l-muted`. When the queue is non-empty, a right-aligned “Queued: N” 600 in `--l-acc-text`.
- Placeholders: “Write to Lili…” / “Lili is answering — a new line will be queued” / “No connection to the server”.

#### Slash suggestions
Open when the input starts with `/` and contains no whitespace. Filter by prefix.
- Popover: absolute, `left:0; right:56px; bottom:calc(100% + 10px)`, `background:var(--color-surface); border-top:2px solid var(--color-divider); box-shadow:var(--shadow-lg)`, max-height 300px with scroll, `role="listbox"`.
- Header: padding 8px 14px, 1px hair bottom border. Kicker “Commands” on the left; on the right, “↑ ↓ select · Tab insert · Esc close” (normal case, 10.5px).
- Row: a grid `112px minmax(0,1fr) auto`, gap 12px, padding 7px 14px, `role="option"`. The command in `600 13px` mono, the description 13px, and the hint “Tab” 11px/600 on the selected row only.
- **Selected row**: `background:var(--color-accent); color:var(--color-bg)`. Mouse hover moves the selection.
- Keys: ↑/↓ move (wrapping); Tab, or Enter when the text isn't an exact command, inserts `"/cmd "`; Esc closes until the next keystroke; Enter on an exact command sends it.

### Status line
Height 30px; padding 0 8px 0 14px; gap 12px; `border-top:2px solid var(--color-divider)`; 12px `--l-muted`; nowrap.
- **State**: an 8×8 square, then a label. Ready: solid `--l-muted`, “ready”. Answering: `--color-accent` pulsing at 1.2s, “answering”. Connecting: `--l-faint` pulsing, “connecting…”. Offline: a hollow square with a 1.5px `--l-faint` border, “offline”, plus the note “retry in 12 s” in `--l-faint`.
- **DEV badge** (when not prod): “DEV” 10px/800, letter-spacing .1em, padding 1px 6px, `background:var(--color-accent); color:var(--color-bg)`, `title="Not prod: connected to the dev server"`.
- **Right**: a text button “Lumi 2.12 · details” plus a 12px `chevron-up` that rotates when open; `aria-expanded`, `aria-controls`. Narrow: chevron only.
- **Details panel** (sits above the status line and pushes the content up): `border-top:2px solid var(--color-divider); background:var(--color-surface)`.
  - A grid `repeat(auto-fill, minmax(140px,1fr))` of cells, each with padding 9px 14px and 1px hair right and bottom borders. Cell kicker 10px, value 13px/600 tabular-nums. Cells: Environment · Version · Model · Profile · Thinking · Style · First character · Whole reply · Tokens · Exchanges.
  - Controls row (padding 10px 14px, gap 10px 28px):
    - “Inner voice”: a `.seg` with three radio options “show / collapse / hide”. The checked option is accent-filled with ink text.
    - “Chat text”: a `.seg` reading “A− | 16 px | A+” (range 14–22), plus the hint “⌘ − / ⌘ +” 11px `--l-faint`. Bind ⌘−/⌘+ to it.

### Connection banners (top of the chat column; the portrait never moves)
- **Connecting**: padding 12px 24px; 2px divider bottom. “Connecting to the server…” 14/600, plus the host in 12px mono `--l-faint`. An indeterminate bar sits on the bottom rule: a 2px-tall, 30%-wide accent segment sliding from −100% to 340% over 1.4s ease-in-out, infinite. When retrying the text reads “Trying again…”.
- **Unreachable**: `role="alert"`, padding 12px 16px 12px 24px, `background:var(--color-surface); border-bottom:2px solid var(--color-accent)`. It holds:
  - Lucide `wifi-off` 18px in `--l-acc-text`;
  - “The server isn't answering” 14/600;
  - “Next try in {n} s · what you missed will load by itself” 13px `--l-muted`, tabular-nums, with a live countdown;
  - a `.btn-secondary` “Retry now”, 36px tall.
  - The input is disabled. Suggested backoff: 5 → 10 → 15 → 30s, capped.
- **Restored**: padding 10px 12px 10px 24px; 2px divider bottom; 13px. An 8px accent square, “Connection restored” 600, “N missed lines loaded” `--l-muted`, and a dismiss ✕ icon button (32px). The missed lines are inserted under the accent “While offline · N” divider.

### Auth / config screen (token refused · no server configured)
This replaces the body, keeping the title bar and status line. Wide: two columns (`minmax(0,1.1fr) minmax(0,1fr)`), padding 56px 48px, with a 2px divider between them. Narrow: one column, padding 28px 20px, with a 2px divider below the first block.
- **Left column**:
  - kicker in `--l-acc-text` (“Connection · 401”);
  - title `800 48px/1.04`, letter-spacing −0.02em (narrow 32px);
  - body 16px/1.55 `--l-muted`, max-width 44ch;
  - actions: a `.btn-primary` with `refresh-cw` and a flush-left label (min-width 210px, 44px tall), and a `.btn-secondary` “Open secrets.yaml”.
- **Right column**:
  - kicker “What to do”;
  - three steps, each a grid `40px 1fr` with padding 16px 0 and a 1px hair top border. The number is `800 22px` in `--l-acc-text`; the text is 15px; an optional code block is 13px mono on surface, padding 8px 10px, `pre-wrap`.
  - Facts: a 2-column grid of 12px text, with a 2px divider above it.
- Copy for both screens is in `strings.en.json` (rewritten for the real client config: `settings.yaml` / `secrets.yaml`).

### Empty state (new session)
Show the portrait and the caption only: no meta, no mood block in the panel, no thoughts. The chat column shows only the mood of the day, set large at the bottom: kicker “Mood of the day · Wednesday, 12 November”, then the text 22px/1.45 (narrow 18px), max-width 600px. The input stays.

### Narrow layout (< 760px: future web client, phone)
The body becomes a column. The portrait panel is replaced by a **top strip** (`border-bottom:2px solid var(--color-divider); max-height:72%; overflow-y:auto`):
- Row (padding 10px 12px 10px 16px, gap 12px):
  - a **52×52 portrait thumb** (the same A/B crossfade); tap to expand;
  - the caption 800 18px/1.15;
  - the meta line “Close · 4/5 · Quiet Collapse” 12px `--l-faint`, ellipsis;
  - a 44×44 thoughts icon button (Lucide `cloud`) with an unread badge: 16px, accent fill, ink 10px/800.
- Expanded portrait: a full-width square (max-height 420px) with a 44px collapse button (`chevron-up`) at the top right on `bg @ 80%`.
- Mood row: a full-width button with a 1px hair top border, padding 10px 16px, 13px/1.45 `--l-muted`. “Mood of the day ·” is in 600 `--color-text`. Folded to 1 line, it expands on tap.
- The thoughts list opens inside the strip, below the mood.
- Messages use the single-column row. Input padding is 10px 12px. Hints are hidden under 600px. The title bar is hidden on the phone and in the browser.
- All hit targets are ≥ 44px on touch.

---

## Thought stream (decision)
**Default: the strip under the portrait (board option 1b).** Thoughts are a separate stream, never chat lines. Two alternatives were mocked:
- **1a, drawer**: a 46px right rail (cloud icon + unread badge + a vertical label “Quiet thoughts”) toggles a 300px column between the chat and the rail.
- **1c, on the portrait**: a 32px pill at the portrait's top right, “Thoughts · N”, on `bg @ 78%` with an 8px backdrop blur. Opened, the list overlays the portrait on `bg @ 88%` with a 6px blur.

**Thought item**: a flex column, gap 3px, padding 10px 0, 1px hair top border.
- Meta row (11px `--l-faint`, gap 8px): a 6×6 accent square if unread · the kind, 600 uppercase .08em · the time.
- Text: 14px/1.45 `--l-muted`.
- Emotion caption: 12px `--l-faint` (the caption line, not the emotion's name).
- Unread is cleared when the list is closed after opening.

Kind labels: the real `Thought.kind` values are the directive names (`think`, `wonder`, `recall`, `note`, `review`, `explore`, `journal`, `lookup`, `learn`, `catchup`, `brief`, `search`, `events`, `prompt`, `gaze`, `imagine`, `share`); their English labels are `thoughtKinds` in `strings.en.json` (an unknown kind shows as is).

---

## Interactions & behaviour summary
| Trigger | Result |
|---|---|
| Enter (not Shift, not IME composing) | Send. If she is answering, append the line to the **queue** (shown dimmed at 55% opacity, time “queued”; the counter goes up). |
| Shift+Enter | New line (the textarea grows, up to 6 rows). |
| `/` at the start | Slash popover (see above). Commands go to the server; the result renders as a command block. |
| `done` received | Crossfade the portrait (480ms) and caption; update latency and tokens; then send the next queued line, if any. |
| New thought push | Prepend to the thoughts with unread=true; the unread counter goes up. Never touch the chat. |
| Hers-first push | A normal her-row with the “wrote first” marker. |
| Line from another channel | A normal row with the source tag. |
| Server lost | The unreachable banner with its countdown; input disabled; status “offline”. |
| Server back | Fetch the missed lines; insert the accent divider and the lines; show the restored banner. |
| 401 / no config | The auth/config screen. |
| ⌘− / ⌘+ | Chat text size 14–22px (persist locally). |
| Window < 760px | Narrow layout (measure the window or `matchMedia`). |

Every interactive element gets a themed hover and a `:focus-visible { outline:2px solid var(--color-accent); outline-offset:2px }`. Never show the browser's blue ring. Tab order: input → send → panel controls → status. Esc closes popovers.

## State (client)
`messages[]` (kind: divider | missed | mine | hers | cmd; time; source?: tg | term; first?; think?; blocks (markdown); image?; streaming?; thinkOverride?) · `queue[]` · `input` · `slash {open, index, dismissed}` · `answering` · `thinkMode` (shown | collapsed | hidden; persisted) · `chatSize` (persisted) · `moodOpen` · `statusOpen` · `thoughtsOpen` · `thoughts[] {kind, text, emotion, time, unread}` · `face {front: A | B, A: {theme, emotion, level}, B}` · `conn` (connecting | online | offline | retrying) · `countdown` · `reconnectedBanner` · `serverState {moodText, theme, closeness {level, name}, style, env, version, model, profile, thinking, latency {first, total}, tokens, turns}`.

Server events (from the brief): `think` → `delta` → `done {emotion, intensity}` | `error`; pushes for state, hers-first, other-channel lines, thoughts, pictures and command results.

Intensity → level: low < 0.34 ≤ mid < 0.67 ≤ high (adjust to the server's actual buckets). Level → file: low `02`, mid `01`, high `03`.

Emotion → caption: joy “lit up, openly glad” · calm “quietly here” · playful “a teasing little smile” · tender “soft and warm” · thoughtful “somewhere in thought” · serious “level and present” · surprise “caught off guard” · doubt “not quite sure” · sad “a quiet heaviness”.

Closeness names: 1 Polite · 2 Friendly · 3 Warm · 4 Close · 5 Dearest (shown by level; the server sends the Ukrainian name, the client maps the level).

## Design tokens
The full sheet is in **`tokens.css`**. Summary:

**Dark (default)** — `--color-bg #201e1d` · `--color-surface #2d2b2b` · `--color-text #f3f2f2` (15.6:1) · `--l-muted #bab6b6` (8.3:1) · `--l-faint #9b9797` (5.7:1) · `--l-raise #444141` · `--color-divider` = text @ 30% · `--l-hair` = text @ 13% · `--shadow-lg: 0 0 0 1px text@10%, 0 16px 40px #000@55%`.

**Light** — bg `#f3f2f2` · surface `#eae9e9` · text `#201e1d` · muted `#444141` · faint `#605d5d` · raise `#d7d3d3` · divider = text @ 40% · hair = text @ 14% · shadow-lg `0 12px 32px #2d2b2b@22%`.

**Accent per theme**, applied as `[data-theme="…"]` on the root. Dark: base `oklch(0.68 C H)`, text `oklch(0.82 C×0.75 H)`, hover `0.74`, pressed `0.62`. Light: base `0.58`, text `0.46`, hover `0.52`, pressed `0.46`.

| theme | Name | H | C |
|---|---|---|---|
| 3am | 3 AM | 245 | .12 |
| day-after | The Day After | 255 | .035 |
| furious | Furious | system red: `#ec3013` / text `#ff9783` / light text `#ae1800` / hover `#ff563c` / pressed `#dd2b0f` | — |
| dissociation | Dissociation | 305 | .06 |
| last-memory | The Last Memory | 70 | .14 |
| quiet-collapse | Quiet Collapse | 345 | .19 |
| im-fine | I'm Fine | 135 | .19 |
| drowning | Drowning | 200 | .10 |
| vigil | Vigil | 55 | .16 |
| calm-before | The Calm Before | 95 | .11 |

Contrast on dark: base ≈ 5.8:1, text ≈ 9.6:1. Light text ≈ 6.4:1. Furious base is 4:1, so use it for fills and icons only. Ink (`--color-bg`) on any accent fill is ≥ 4:1. WebKit (Safari 15.4+) supports `oklch()`.

**Type**:
- Family: `'Archivo', -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Helvetica Neue', Arial, sans-serif`. Archivo's web build has no Cyrillic, so Ukrainian falls through to SF Pro. Either bundle Archivo locally for Latin or simply use SF Pro throughout.
- Weights 400 / 600 / 800.
- Scale:
  - caption 28/1.08/800 (−0.015em);
  - auth title 48/1.04/800 (−0.02em);
  - empty mood 22/1.45;
  - chat 16/1.55 (14–22);
  - think, thoughts and mood 14/1.5;
  - meta 11–12;
  - kicker 10.5/600/caps/+0.1em;
  - mono `ui-monospace, 'SF Mono', Menlo, monospace` 12.5–13.
- Set `lang="uk"` and `hyphens:auto` on the root; use `overflow-wrap:break-word`, and `hyphens:none` on code.

**Space**: 4 · 8 · 12 · 16 · 24 · 32. Layout constants: gutter 84px, gutter gap 20px, message gap 22px, panel `clamp(300px, 36%, 440px)`, list max-width 720px, title bar 34px, status 30px, input control 46px.
**Radius**: 0 everywhere. **Rules**: 2px between regions, 1px inside.
**Motion**:
- portrait crossfade: opacity 480ms ease;
- chevrons: 200ms;
- pulse: 1.2s ease-in-out;
- caret: 1s blink;
- connecting bar: 1.4s ease-in-out;
- all disabled under reduced motion.

## Assets
- **Portraits**: local `viewer/faces/<theme>/<emotion>/<NN>.png`, 768×768. The mockups use placeholders (none were attached).
- **Icons**: Lucide (https://lucide.dev), inline SVG on `currentColor`, stroke 2–2.4, `stroke-linecap: square` (architectural). Used: `arrow-up`, `chevron-down/up/right`, `send` (Telegram mark), `terminal`, `wifi-off`, `refresh-cw`, `cloud` (thoughts), `x`. Inline these SVGs so the client stays offline-capable.
- No other imagery.

## Files in this bundle
- `README.md` — this document.
- `tokens.css` — CSS variables: dark and light sets plus the 10 theme accents (drop into `webview/ui/`).
- `strings.en.json` — every UI string, English (replaces the design's `strings.uk.json`).
- `design/LiliWindow.dc.html` — the window prototype (all states; the logic class holds the reference behaviour and sample data).
- `design/Lili Desktop Mockups.dc.html` — the presentation board (states, components sheet, token tables).
- `design/styles.css` — the Modernist system stylesheet the prototypes link (`.btn`, `.input`, `.seg`, `.table`, base tokens).
