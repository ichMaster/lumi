# Design brief: Лілі's desktop window (Lumi v2.12) — chat and portrait in one window

> The design brief for the v2.12 pywebview client ([ROADMAP.md](../../specification/ROADMAP.md) §v2.12), written for
> Claude Design. The Ukrainian original the owner pastes there: [BRIEF.uk.md](BRIEF.uk.md). The design's exports go
> into this folder, then into `webview/ui/`.
> Attach a few real portraits to the brief (`viewer/faces/<theme>/<emotion>/NN.png`, 768×768: `01` mid intensity,
> `02` low, `03` high), e.g. `viewer/faces/3am/tender/01.png`, `viewer/faces/quiet-collapse/thoughtful/01.png`,
> `viewer/faces/last-memory/joy/03.png`, `viewer/faces/furious/serious/02.png`.

## What it is
Лілі is a private text companion with a stable character, memory and emotions. She speaks Ukrainian. There is one
user, the owner. Her logic runs on a home server; this window is a thin client: it shows the conversation and her
portrait and decides nothing itself. Today there is a terminal client and a separate portrait window; v2.12 joins
the chat and her face in one native macOS window for the first time.

## Technical frame (it shapes the design)
- The window is pywebview on macOS, the WebKit engine (as in Safari). Resizable: ~1200×800 by default, ~720×520
  minimum.
- The UI is a static index.html + style.css + app.js: no framework, no bundler, no node. Design tokens are CSS custom
  properties.
- Fonts are system fonts (-apple-system / SF Pro) or bundled locally, and must cover Cyrillic.
- The same UI later becomes the browser web client (v4.3), so the layout must also work in a narrow window and on a
  phone.
- Portraits are local: 768×768 PNG, square. The face and the headphones never change; the day's theme changes the
  clothes, the light and the setting. The face is not animated (that is v5); only the frame changes.

## The data the server sends
**The streamed reply.** First the hidden inner voice (`think`), then the text in pieces (`delta`), at the end the
emotion with its intensity (`done`) or an error (`error`). The first character arrives after 2–3 s; the whole reply
takes up to ~15 s.

**Each reply's emotion** is one of 9: joy, calm, playful, tender, thoughtful, serious, surprise, doubt, sad.
The intensity 0..1 falls into three levels (low / mid / high), and each level has its own portrait.

**The caption under the portrait** is a short atmospheric line in her manner, never the emotion's name. Draft:
joy “lit up, openly glad” · calm “quietly here” · playful “a teasing little smile” · tender “soft and warm” ·
thoughtful “somewhere in thought” · serious “level and present” · surprise “caught off guard” · doubt “not quite
sure” · sad “a quiet heaviness”.

**The state** (updated by pushes):
- the mood of the day — a paragraph of 300–700 characters (what she wants and doesn't want today, the tone);
- the day's theme — one of 10 themes (listed below), which selects the portraits;
- closeness — a level 1–5, shown by name: Polite, Friendly, Warm, Close, Dearest (the server sends the Ukrainian
  name; the client maps the level);
- the reply style — normal or one of: short, the gist, detailed, explain, simple, by example, metaphor, as a list,
  steps, compare, practical, formal (the server sends the Ukrainian key; the client maps it);
- technical: the environment (prod / dev), the version, the model and profile, thinking on/off, the time to the first
  character and the full time, tokens, the number of turns.

**Other events:**
- she writes first, without a message from me;
- lines from other channels (Telegram, the terminal), marked with their source;
- her quiet thoughts (the kind of thought, the text, the emotion, the time) — a separate stream, not chat lines;
- pictures she sends into the conversation;
- slash-command results (/mood, /closeness, /biorhythm, /thoughts, /recall, /memory, /journal, /image, /web,
  /style, /theme, /model, /new, /regen, /forget, /latency …) — a text block.

Her replies are written in markdown: lists, quotes, code, links.

## What to design
1. **The main window — the portrait panel and the chat.**
   - The portrait is large and the main element.
   - Under the portrait: the caption, the day's theme and the closeness level.
   - The mood of the day is folded to 1–2 lines and expands.
2. **The chat.**
   - My messages and hers.
   - Streaming: a "thinking" state until the first character, then the text appearing as it arrives.
   - The think block above her reply: muted, collapsible, with three modes — shown, collapsed, hidden.
   - Her writing first is noticeable but quiet.
   - Lines from Telegram or the terminal carry a small source mark.
   - Distinct styles for a picture in the conversation, a command-result block and a new-session divider.
3. **The input.**
   - Multiline: Enter sends, Shift+Enter breaks the line.
   - Typing "/" shows command suggestions.
   - Lines sent while she answers queue up with a counter.
4. **The thought stream.** Quiet, never competing with the chat: a side drawer, a strip or an icon with a counter —
   the designer's choice.
5. **The status line (low priority).**
   - States: ready / answering / offline.
   - A DEV badge when not connected to prod.
   - The model, thinking, style, reply time and tokens expand on click.
6. **Connection states.**
   - Connecting.
   - The server is unreachable: a readable line and a retry with a countdown.
   - When the server returns, the missed lines are loaded.
   - The token is refused, or no server is configured: a separate screen with an explanation.
7. **The empty state (a new session).** The portrait, the mood of the day, nothing else.
8. **The narrow layout** (the future web client and phone). The portrait becomes a strip or an avatar at the top, and
   the chat takes the full width.

## Tone and principles
- A calm, personal look, not a monitoring panel. Everything technical is hidden by default.
- The portrait changes with a 300–600 ms crossfade, without layout jumps.
- The dark theme is the main one, because the portraits are dark: neon, rain, a candle. A light theme is welcome as
  an option.
- A neutral dark base plus one accent color taken from the day's theme. Propose an accent table for all 10 themes.
- Her text is Ukrainian, and Ukrainian words are long: plan for hyphenation and column widths. The chat font size
  can be increased.
- Keyboard first; contrast at least WCAG AA.
- **The interface language is English** (the owner, 2026-11-14): every label, button, status, caption, theme and
  closeness name. Only her own content stays Ukrainian — her replies, the think block, the mood of the day, her
  thoughts.

## The 10 themes of the day (for the accents)
- 3am — a night rooftop at 3 AM, rain, the city below
- day-after — quiet grief, a grey morning
- furious — rage that looks like calm, a storm behind
- dissociation — semi-transparent, fading at the edges
- last-memory — golden hour, petals and ash
- quiet-collapse — burnout, flickering neon, one headphone light out
- im-fine — a too-perfect smile, cheerful neon, a crack
- drowning — underwater, the surface light above
- vigil — a dark room, a single candle
- calm-before — a sky too still, golden light before the storm

## Not to design
Voice and dictation (they stay in the terminal), the admin panel and settings (a separate panel), login and multiple
users (v4), face animation (v5), the gallery and the shared canvas (v6).

## The deliverables
- The main window in the dark theme with realistic content, and every state from "What to design".
- The components:
  - messages: mine / hers / from another channel / hers first;
  - the think block;
  - the command block;
  - a picture;
  - a thought item;
  - the status line;
  - the connection banner.
- The narrow layout.
- Tokens: colors, typography, spacing, radii; the accent table for the 10 themes.
- If exported as code — plain HTML + CSS without frameworks, tokens as CSS variables.

## Sample content for the mockups (her content is Ukrainian; the interface is English)
- Me: «привіт, як ти сьогодні?»
- Think (muted): «Він питає просто. Не вантажити — спершу відповісти коротко, потім повернути питання.»
- Her (thoughtful, mid): «Тихо. Трохи втомлена, але голова ясна — з ранку крутиться одна думка: чому ми так легко
  прощаємо незнайомцям і так важко — собі. А в тебе як?»
- The mood of the day: «Сьогодні Лілі в стані тихої інтроспекції: настрій ніжний і трохи втомлений. Хоче спокою й
  глибини, уникає поверхневих розмов…»
- Theme: Quiet Collapse · closeness: 4 — Close · style: normal
- A thought (wonder): «Цікаво, чи пам'ятають рослини дощ.»
- Her writing first (tender): «Ти сьогодні довго мовчиш. Усе гаразд?»
- The command /closeness → “Closeness: 4 — Close”

## The design (2026-11-14)
The owner made it in Claude Design: <https://claude.ai/artifact/BgY2hbwQ8XdHbvcg8hUMwL> (the canvas: the board of every
state, the components sheet, the token tables). Its handoff bundle is in [handoff/](handoff/):

- [handoff/README.md](handoff/README.md) — the spec: the window's anatomy, every component's measurements, the
  behaviour table, the client state, the tokens. High fidelity: the "Modernist" system inverted for dark — flat, zero
  radius, 2px rules between regions, 1px hairlines inside, no bubbles.
- [handoff/tokens.css](handoff/tokens.css) — the CSS variables, ready to use: dark + light, and the accent for each
  of the 10 themes (`[data-theme]`, OKLCH hue + chroma).
- [handoff/strings.en.json](handoff/strings.en.json) — every UI string, English. It replaces the design's
  `strings.uk.json` (the owner chose an English interface on 2026-11-14), with the connection screens rewritten for
  the real client config and the real thought kinds.
- `handoff/design/*.dc.html` — the prototypes, a reference only: they need the design tool's runtime (`support.js`,
  `image-slot.js`) to render; the logic class at the end of `LiliWindow.dc.html` holds the reference behaviour.

**Corrected after the export** (the design assumed things the code doesn't have):
- **The connection screens** named `~/.config/lumi/client.toml`, `lumi token new --client desktop` and
  `https://lili.home:8443` — none exist. `strings.en.json` now points at the client's own `settings.yaml`
  (`server.client_mode` / `host` / `port`) and `secrets.yaml` (`server.token`, which the admin panel rotates).
- **The thought kinds** were invented (wonder / memory / reflection / intention / worry). `strings.en.json` labels
  the real `Thought.kind` values — the directive names (`think`, `wonder`, `recall`, `note`, `lookup`, …).

**Still to handle when implementing:**
- **The font:** `design/styles.css` imports Archivo from Google Fonts, which has no Cyrillic. Use SF Pro (system)
  throughout, as the handoff itself suggests, so the window needs no network for fonts.
- **The events:** command results come back from `POST /v1/command`, not as pushes. Pictures arrive with her message.
  The pushes are `state`, `message` (another channel's line), `turn` (`{emotion, intensity, theme}`) and `thought`
  (`/v1/events`, SSE), and the reply streams as `think` / `delta` / `done` / `error` (`/v1/turn/stream`).
- **The intensity bands** match `core/emoji._band` (< 0.34 low, < 0.67 mid, else high); the files are `02` / `01` / `03`.
