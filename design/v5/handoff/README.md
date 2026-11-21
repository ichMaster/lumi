# Handoff: the Lumi admin panel v5 (operations, and Лілі in the panel)

## Overview
The Lumi admin panel is a web page served by the panel's FastAPI on the home box (`slate-server`). The owner opens it in a browser on their Mac. It watches and configures two **instances** of Лілі, **prod** and **dev**, plus the box itself. v5 replaces the ten flat tabs with a real structure:

- an **Overview**;
- a **Server** area: Operations (Health, Incidents, Containers, Logs, Alerts), Configuration (Settings, Secrets, Config history, Promote) and Audit;
- an **Agent** area that repeats per agent. Today there is only Лілі. Her group "Her" holds State, History, Memory → Summaries / Facts, Thoughts, Gallery, Journal and Files. Her group "How she runs" holds Usage, Tools, Schedule, Models, Texts and Prompt.

[`../BRIEF.md`](../BRIEF.md) is the product brief; its last section, "The design (2026-11-21)", lists what was changed after the export. Read it first: it defines every view, the states each one must show, the sample content, and the safety rules.

## About the design files
The files in `prototype/` are **design references built in HTML**. They are clickable prototypes that show the intended look and behaviour. They are **not production code to copy**.

They use a small streaming-template runtime (`support.js`, the `.dc.html` files, `{{ }}` holes, `<sc-for>` / `<sc-if>`). The target stack does **not** have that runtime. Per the brief, the target is:

- a static `index.html` + CSS + JS in `admin/static/`, served by the panel's FastAPI;
- **no framework, no bundler, no node**;
- charts drawn as inline SVG by hand, tokens as CSS variables, icons as inline SVG.

**Task:** recreate these designs in that vanilla stack. Read each `.dc.html` the same way you'd read a mockup:

- the template section is the markup, with inline styles;
- the `class Component` section holds the sample data and the small amount of behaviour.

Port the styles into a real stylesheet built on the tokens in `tokens/`. Render every data string with `textContent`, never `innerHTML` (see Safety).

**To view the prototype:** serve the `prototype/` folder over HTTP (e.g. `python3 -m http.server`) and open `Lumi Admin.dc.html`. Opening it as `file://` won't work. `Components.dc.html` is the components sheet.

## Fidelity
**High fidelity.** Colors, type sizes, spacing, density, states and copy are final. Recreate the look faithfully. Exceptions:

- Gallery thumbnails are hatched placeholders; use the real images.
- Chart data in the prototype is generated; use real data.
- The dashed **"Mockup state"** button at the bottom of the rail is a demo control for switching between states. **Do not ship it.**

### Deviations from the brief (decided during design)
- **Visual system:** the brief asked for the v2.12 window style: flat, zero radius, 2px rules. The project instead uses the **Nocturne** design system, made denser:
  - soft radii: 4px for chips and rows, 6px for controls, 8px for panels;
  - Inter for text and a system monospace for numbers, logs and keys;
  - rules that fade over 48px at each end;
  - outlined primary buttons.

  If the owner wants zero radius and system fonts after all, that is a token-level change (`--radius-*`, `--font-*`).
- **Icons:** the brief says Lucide-style; Nocturne specifies **Phosphor** (regular weight; fill weight only for the toast check and the pinned-fact pin). The prototype loads the Phosphor web font for convenience. In production, inline the SVG paths from phosphoricons.com, as the brief requires, with `fill="currentColor"`.
- **Sample content:** State, Thoughts, Gallery and Journal use invented Ukrainian sample content, because the v4 brief's samples weren't available.

## Safety (from the brief, non-negotiable)
- **Never render HTML from data.** Every log line, file text, thought, message, fact and summary is set with `textContent`.
- Her markdown (Files, Texts) is parsed into built elements: `h1` / `h2` / `p` / list item / blockquote. Never injected.
- The page never holds Лілі's token. The panel's own token is entered on the sign-in screen and kept in `sessionStorage` only.
- Prod actions that change something need a **typed confirmation**: the user types `prod`, and the confirm button stays disabled until they have.

## Global layout (the shell, `Lumi Admin.dc.html`)
Target screen: 1440×900. It must work in a narrower window too; a ~390px phone layout is a nice-to-have and not designed.

```
┌────────────────────────── attention strip · 32px ──────────────────────────┐
├── rail 236px ──┬── main ───────────────────────────────────────────────────┤
│ lumi admin  v5 │ 2px top edge: red when prod is active and the view follows │
│ host · IP      │ header 52px: crumb + title │ updated · switcher · theme    │
│ Overview       ├────────────────────────────────────────────────────────────┤
│ SERVER         │ view content (scrolls on its own)                          │
│  Operations…   │ padding 16–20px 24px 40–48px                               │
│ AGENT  [L Lili]│                                                            │
│  Her…          │                                                            │
│ footer: token, │                                                            │
│ sign out, keys │                                                            │
└────────────────┴────────────────────────────────────────────────────────────┘
```

- **Page:** `height:100vh`, a column flex. The rail and the main column scroll independently. Full-height views (Logs, History, Texts) are `calc(100vh - 86px)` tall: strip 32 + header 52 + top edge 2.
- **Base type:** Inter 13px, line-height 1.45. Body text in tables is 12.5–13px. Meta is 11.5px. Labels are 10.5px, uppercase, letter-spacing .08em, `--color-neutral-500`. Monospace (`--mono`) is used for every number, time, version, key, path, model name and log line.

### Attention strip (32px, full width, top)
- **Something needs attention:**
  - Background: `color-mix(in srgb, var(--warn|--bad) 10%, var(--color-bg))`, using the worst state present.
  - Left label: "NEEDS ATTENTION" with a bell icon, in the state color.
  - Then one chip per item: height 22px, 1px divider border, radius 4px, background `--color-bg`. The chip holds a 7px state dot, an instance tag, the kind ("alert" / "incident" / "panel") in neutral-400, the text, the age in mono 11px neutral-500, and an arrow. Clicking a chip opens that view.
  - Right side: "1 alert firing · 0 open incidents" in mono 11px.
- **Nothing needs attention:** "ALL QUIET" in `--ok` with a check-circle icon, on a plain background.

### Rail (236px)
- **Background:** `color-mix(in srgb, var(--color-surface) 40%, var(--color-bg))`, with a 1px divider on the right.
- **Brand block:** "lumi" 16px/500, "admin" 12.5px neutral-400, "v5" mono 10.5px on the right. Below it, "slate-server · 192.168.1.197" in mono 11px neutral-500.
- **Item rows:** height 27px (Overview 28px), padding `0 8px 0 10px`, radius 4px, gap 9px. Icon 15px, label 13px.
  - Inactive: text `--color-neutral-300`, icon `--color-neutral-500`. Hover: `color-mix(text 6%)`.
  - Active: background `color-mix(tone 15%, transparent)`, text `--color-text`, icon in the tone, and a 2px × (row−14px) mark at `left:-6px` in the tone. The **tone** is `--color-accent` in the Server area and `--agent` in the Agent area.
  - Badges: a 17px-tall pill, mono 10.5px/500, background `color-mix(state 22%)`, text in the state color. Examples: Health 1 amber, Alerts 1 amber.
- **Area headers:** "SERVER" in neutral-400 and "AGENT" in `--agent`. 10.5px, uppercase, .1em letter-spacing, with an icon.
- **Group labels:** 11px neutral-500 ("Operations", "Configuration", "Audit", "Her", "How she runs").
- **The Agent area** is wrapped with `box-shadow: inset 2px 0 0 color-mix(var(--agent) 45%)`, a faint orchid line down its left side.
- **"Memory"** is a non-clickable sub-heading with a brain icon. Summaries and Facts are indented (left padding 30px) and use the dot-outline icon.
- **Agent picker:** a button with a 1px border `color-mix(agent 30%)` and background `color-mix(agent 8%)`, radius 6px. It holds a 26px initial tile "L" (background `color-mix(agent 24%)`, text `--agent`, 13px/600), "Lili" 13.5px/500, "1 agent · companion" 11px, and a caret-up-down icon. It opens a popover (`--color-surface`, `--shadow-md`, radius 8px) with:
  - **Lili** + a check;
  - a disabled **"Second agent — appears here in the next version, with its own color"** row with a dashed `--agent-2` (cyan) "+" tile. This is the reserved slot the brief asks for.
- **Footer:**
  - "token ···a91f" + Sign out;
  - key hints "P / D instance · J / K view";
  - a link to the components sheet.

### Header (52px)
- **Left:** the crumb (10.5px uppercase) and the title (17px/500).
  - Crumb text: "Server · Operations" in neutral-500 for server views, "Lili · Her · Memory" in `--agent` for agent views, "slate-server · everything at a glance" on Overview.
- **Right:**
  - **Updated time:** mono 11px with a refresh icon, e.g. "21:40:12 · 1 min" on Health and Overview, "live" on Logs. Its tooltip says whether the view follows the switcher or shows both instances.
  - **Instance switcher:** a 30px segmented control, radius 6px.
    - `prod` option: red 7px dot, "prod", "v4.7.0" in mono 11px. When active: background `color-mix(var(--bad) 15%)` and an inset 1px `--bad` ring.
    - `dev` option: hollow neutral dot, "dev", "sha-6b80895". When active: background `--color-neutral-800` and an inset 1px neutral-500 ring.
  - **Theme toggle:** a 30px icon button (sun / moon).
- **Prod is red everywhere:** when prod is active **and** the view follows the switcher, `main` gets `border-top: 2px solid var(--bad)`.
  - Views that show both instances side by side and ignore the switcher: Overview, Health, Containers, Usage.

### Shared states (rendered by the shell for any view)
- **Loading:** "Loading {view} from {instance}…" with a circle-notch icon, three 86px surface blocks, then 8 skeleton bars (9px tall, `--color-neutral-900`). No shimmer.
- **Can't reach the server:**
  - A 40px icon tile (plugs icon) in `color-mix(bad 14%)`.
  - Title "Can't reach {instance}'s server" at 22px.
  - Body: "{view} needs an answer from {instance}'s server, and it isn't answering. The rest of the panel still works."
  - A mono block: "GET http://192.168.1.197:8766/v1/state — connection refused / last answer 21:34:02 (6 min ago) · retrying in 24 s".
  - Buttons: Retry now (primary), Containers, Logs.
- **Empty:** a 28px icon in neutral-500, a title (17px/500), a sub line, and one secondary button. The texts per view are in the shell's `EMPTY` map, e.g. "No incidents in 30 days".
- **Confirmation dialog:** a Nocturne `.dialog`, width `min(500px,100%)`, with an inset 3px top edge (`--bad` for prod, neutral-600 for dev).
  - Instance tag + title (17px).
  - Body text.
  - Optional diff block: mono 12px rows `key  ~~old~~ → new`, old in `--bad` struck through, new in `--ok`.
  - For prod: a field "Type **prod** to confirm" with a red-tinted border.
  - Actions: Cancel (with an "Esc" hint) and a confirm button. The confirm button is outlined red for prod and accent for dev, and stays disabled until the user types `prod`.
  - Reversible actions (move to trash, schedule save) skip the typed step: `typed:false` in the prototype.
- **Toast:** fixed bottom-right, `--color-surface`, `--shadow-md`, radius 8px. A filled check-circle in `--ok`, the message, and an × button. Auto-hides after 4.2 s.
- **Sign-in screen:**
  - A form centred at 380px wide over a faint accent radial glow. The h1 is "Sign in to the panel" at 24px.
  - Under the title: "slate-server · 192.168.1.197:8767 · home network" in mono.
  - A password input in mono, labelled "Panel token".
  - On error: a red line "The token was refused. It lives in ~/lumi/admin/secrets.yaml on the box (admin.token)."
  - A full-width outlined "Sign in" button, then the footnote "The token stays in this tab's session. The panel never holds Lili's own token."

## Screens / views
Each file below is a self-contained view; its sample data is at the top of its `renderVals()`. The common table pattern is a CSS grid, not `<table>`:

- Header row: 10.5px uppercase, neutral-500.
- Rows: min-height 32–44px, padding `3–5px 10px`, radius 4px.
- Row rule: a 1px fading line at the bottom (`linear-gradient(to right, transparent, color-mix(text 9%) 48px, … calc(100% - 48px), transparent)` sized `100% 1px`).
- Hover: `background-color: color-mix(text 4–5%)`.
- Selected row: `color-mix(accent 10%)` plus an inset 2px accent left edge. In agent views, use `--agent` instead of the accent.
- Wide tables sit in an `overflow-x:auto` wrapper with a `min-width` (900–960px), so text columns never collapse.

### Overview — `Overview.dc.html`
- **The box:** a strip of cells (auto-fit, min 150px, 1px divider gaps, radius 8px):
  - "slate-server / Linux 6.8 · 8 cores · up 41 d";
  - Load "0.42" plus "0.38 · 0.35" and an SVG sparkline;
  - Memory "5.1 / 16 GB" with a 5px bar;
  - Disk "61 % free of 500 GB" with a bar;
  - Docker "7 / 7 running".

  Values are mono 19px/500.
- **Instances:** two cards, auto-fit min 400px.
  - The card edge is `inset 3px 0 0` — `--bad` for prod, neutral-600 for dev.
  - Header row: a 9px state dot with a 3px halo, the name in mono 15px (prod in red), the state word, a version chip, and the uptime.
  - Four metrics (Incidents / Alerts / Checks / Containers), each a 10.5px label + mono 18px value + an 11.5px sub line. Clicking a metric opens its view.
  - A 13-cell check strip: 6px tall, gap 3px, one cell per check.
  - An optional banner (alert / incident / unreachable): 6px radius, state-tinted 11%, icon + title + mono meta + one action.
  - Ghost links: Health · Containers · Logs.
- **Agents:**
  - Лілі's card (`inset 3px` `--agent` edge), split into two columns:
    - **Left:** the initial tile, "Lili / companion · Ukrainian", and a prod/dev cost grid with mono 20px cost, turns and last reply.
    - **Right:** "MOOD OF THE DAY · PROD" with the quote at **17px** (Ukrainian, line-height 1.45), and "LAST REPLY" with 🥰 tender, intensity, time and channel.
  - Beside it, a **dashed `--agent-2` placeholder card**: "A second agent appears here".
- **Last 24 hours:** event rows `time | instance tag | icon | text | result`, mono 11.5px times. Results: "firing" in amber, "error" in red, "recovered 7 min" in green.
- **Month card:** "NOVEMBER · ALL AGENTS", "$41.30 to date" in mono 24px, 20 day bars (the current day in accent, the rest neutral-700), "forecast ≈ $62.10", "October $55.70".
- **States** (change the cards, the strip and the rail badges):
  - sample — dev alert firing;
  - both green;
  - dev down — incident, server exited 137, 2/3 containers;
  - prod alert firing;
  - can't reach dev's server — values "—", dot off, neutral banner.

### Health — `Health.dc.html`
- A range segmented control (24 h / 7 d / 30 d), a legend, and "next refresh in 48 s".
- Two panels (prod, dev), auto-fit min 560px, with the same edge rule as the Overview cards.
  - Panel header: instance tag, version, a summary ("12 green · 1 amber · 1 off"), and "UPTIME 30 d **99.93 %**".
  - **Probe bar:** a status line ("Last probe 18:02 — answered in 7.4 s · claude-opus-4-8") and the button **"Probe the model · costs 1 call"**. While probing it shows a spinner and "Probing the model — one real call…", then a toast.
  - **Check rows:** columns `10px | 1fr | minmax(150px,200px) | 58px`.
    - A 9px state dot.
    - The name in mono 12.5px/500 over the detail line (12px; amber or red when not green).
    - A **48-cell timeline strip**, 13px tall with 1px gaps. Cell colors: green `color-mix(--ok 45%)`, amber `--warn`, red `--bad`, off neutral-700, no data = transparent with an inset 1px neutral-700 outline. The threshold text sits underneath in mono 10.5px.
    - Uptime %.
- **Checks:** server, state, mood, model, telegram, scheduler, disk, marker, deploy agent, archive, image drift, room, voice.
- Sample: prod `scheduler` amber, "the newest firing 6 h 20 min ago".

### Incidents — `Incidents.dc.html`
- Filters: instance (Both / prod / dev) and range (30 d / 90 d), plus a summary line.
- **Row columns:** instance · started · duration · red checks (red mono chips) · first detail · alerts. Click a row to open it.
- **Opened incident:** a surface panel.
  - Title "server red — connection refused", status "✓ recovered", and the meta "2026-11-19 21:52:08 → 21:59:14 · 7 min 6 s · 1 restart (exit 137 · OOM killed)".
  - Buttons: "Logs around 21:52 · ±5 min" (primary, opens Logs), the container, and close.
  - **Timeline:** check lanes on a green track with red and amber spans positioned by time, then an **events** lane with markers: died exit 137, alert sent, loading, healthy, recovered.
  - Two lists below: **Container events** (die / restart / start / healthy) and **Alerts it sent** (sent, or held and why).

### Containers — `Containers.dc.html`
- Groups prod / dev / box, 7 containers in all.
- **Columns:** container · state (dot) · health · uptime · restarts (amber if >0) · last exit ("137 · OOM killed" in red) · image chip · CPU sparkline 80×20 + % · memory sparkline with a dashed limit line + "used / limit".
- **Opened container:**
  - 24 h / 7 d toggle.
  - Start (disabled while running), Stop, and Restart. Restart is outlined red for prod and opens the typed confirmation.
  - Four charts in a 2×2 auto-fit grid, each 96px SVG on `--color-bg`:
    - CPU: a median line plus a p5–p95 band;
    - Memory: used, plus a dashed limit;
    - Network: rx, plus dashed tx;
    - Disk I/O: write, plus dashed read.
  - A facts grid: image, created, restart policy, OOM kills · 30 d, ports.

### Logs — `Logs.dc.html`
- **Toolbar, row 1:** source chips grouped by instance tag (prod: lumi.log, mood.log, telegram-in, telegram-out, containers; dev: the same; box: panel.log, deploy-agent.log). Chips are 24px mono; when on: an accent border plus `color-mix(accent 14%)`.
- **Toolbar, row 2:**
  - range segmented control;
  - level segmented control (DEBUG / INFO / WARN / ERROR, each with a 3px color mark);
  - logger input;
  - text/regex input with a `.*` toggle;
  - a count ("2 matches · ±2 lines");
  - Live / Paused toggle and Download.
- **Lines:** mono 12px/20px, grid `22px link | 62px time | 3px level bar | 62px level | 84px source | 108px logger | 1fr message`.
  - Level bar colors: DEBUG neutral-700, INFO neutral-500, WARNING `--warn`, ERROR `--bad`. ERROR rows get a 5% red background.
  - **Traceback:** a red outlined chip "▸ traceback · 6 lines" opens a block under the line, indented to the message column, with a 2px red left border and a 6% red background, `white-space: pre`.
  - **Search:** matches are highlighted `color-mix(accent 38%)`. Non-matching lines are hidden except ±2 lines of context. Hidden runs show as "⋯ N lines hidden" with a dashed rule.
- **Live tail:** "following N sources — new lines appear here". When the user scrolls up, a floating pill appears: **"Paused — 23 new lines · Jump to live ↓"**.
- Each line has a link icon that copies `/logs?src=prod-lumi&at=…`.

### Alerts — `Alerts.dc.html`
- **Firing:** an amber-tinted card with an amber 3px edge.
  - Title, and meta "warning · firing since 21:02 (38 min) · sent to Telegram · rule …".
  - Actions: **Acknowledge** (it then reads "Acknowledged 21:41", disabled), **Snooze ▾** (a popover with "1 hour · until 22:41" and "Until the morning · 08:00"), and **Mute rule**.
- **Rules:** columns on/off switch · kind (icon + check/log/usage/container/schedule) · condition in words (click to edit) · scope chips (prod red, dev neutral, Lili agent-tinted, box accent) · severity dot (critical red, warning amber, info neutral) · quiet hours · last fired.
- **Rule editor:** a right drawer, 480px wide.
  - **Kind** segmented control (5 kinds); each kind has its own fields:
    - check: check, turns, for (min), failures in 1 h;
    - log: logger, level, text/regex, times, within;
    - usage: measure, over, agent;
    - container: event, threshold, containers;
    - schedule: row, no firing in (h), between.
  - Common fields: instance toggles, severity, quiet from / until, and the note "Held during quiet hours and sent at 08:00 as one message. Critical rules ignore quiet hours."
  - A back-test line ("In the last 7 days this would have fired 2 times…").
  - Footer: Delete (red ghost), Cancel, Save rule.
- **History:** when · instance · rule · sent/held · why ("quiet hours — sent with the 08:00 digest", "grouped with … — same incident", "snoozed until 15:00", "rule muted") · channel.

### Configuration — `Configuration.dc.html` (the `sub` prop picks the view)
- **Settings:** restyle today's schema-driven forms.
  - Left: a group list (150–200px) with a search box; group names in mono with counts.
  - Rows: key (mono 12.5px/500) + badges ("↻ needs a restart" neutral, "changed outside · 11-19 22:14" accent) + description (12.5px neutral-400) · the value control (toggle or mono input) · "default X" plus a "Reset to default" ghost button when the value differs from the default.
  - An edited row gets a 2px accent left edge.
  - **Sticky bottom bar:** "N changes not applied · 1 needs a restart of lumi-prod-server", Discard, and **"Review the diff and apply"** (red for prod). It opens the confirmation with the diff.
- **Secrets:**
  - Info line: "Values are written to secrets.yaml on {instance} and never sent back to this page — only whether a secret is set and its last four characters."
  - Columns: name · set/unset · last 4 ("···f3Qa") · used by · changed · Rotate/Set + Clear.
  - Rotate expands an inline password field with Save / Cancel and a note.
  - OPENAI_API_KEY is unset ("nothing — OpenAI can't be chosen in Models").
- **Config history:** a version list (#id, summary, when, source: deploy agent / outside change / "panel · Safari, Mac") beside a diff panel with **"Roll back to #N"**. Rollback is recorded as a new entry.
- **Promote:** "dev → prod · 6 settings differ."
  - Checkbox rows: key · dev value (green) · prod value · note.
  - **"Review and promote to prod"** (red), with the typed confirmation and the diff.

### Audit — `Audit.dc.html`
- **Chain banner:** "The chain is intact · verified 21:40 · 4 812 entries since 2026-07-30 · last hash 9f3c…a1e0" with a seal-check icon in green. Broken state: red tint, link-break icon, "The chain is broken at line 4 790".
- Buttons: Verify the chain, Export · JSONL.
- **Filters:** source chips (panel, deploy agent, outside change, window, docker), result segmented control, search.
- **Columns:** when · source (icon) · instance · agent (in `--agent`) · action (mono) · target (mono) · result (done neutral, refused amber, failed red).
- **Opened entry:** the diff on the left; on the right, meta such as client ("Safari 18 · Mac · 192.168.1.14"), line and hash.

### State — `State.dc.html`
- **Hero card** (agent edge):
  - "MOOD OF THE DAY · RESOLVED 07:12 ON PROD" and the quote at **22px**.
  - "The full reading" folds open three paragraphs at 14.5px/1.65.
  - Right: "LAST REPLY · 21:16", 🥰 at 40px, "tender", "intensity 0.6" with a 4px agent-colored bar.
- **Biorhythms:** three rows (physical 23 d, emotional 28 d, intellectual 33 d). Each has a 200×34 SVG sine with today marked by an agent-colored dot at the center line, the value in mono, and a direction arrow.
- **Closeness:** "4 Close" (mono 30px in agent + 18px name) and "level 4 of 5". Five segments with the levels Polite / Friendly / Warm / Close / Dearest, the English names the window and `/closeness` use since v4.8.
- **Needs:** six 8px bars (connection, novelty, rest, creativity, meaning, care), each with a 0..1 value.
- **Today's plans:** check-circle / circle rows at 14.5px; done items are struck through.
- No portrait (the portrait stays in the window).

### History — `History.dc.html`
- **Toolbar:**
  - search box (with a match counter and up/down buttons);
  - channel toggles: window (app-window icon), Telegram, TUI (terminal icon);
  - "Jump to" date picker.
- The list is newest at the bottom (`flex-direction: column-reverse` scroll), with "Load earlier · 2026-11-18" at the top.
- Day dividers: 10.5px uppercase + a fading rule.
- **Message grid:** `44px time | 74px who + channel | 1fr`.
  - Her lines: name in `--agent`, background `color-mix(agent 5%)`, inset 2px agent edge, the emotion line above the text ("🤔 thoughtful").
  - Text is 14.5px/1.6, max 72ch.
  - Attachments: a 40px thumbnail with the file name and size.
  - **Folded tools:** a chip "▸ 🔧 5 tools · 4 kinds · 1 failed" (the failed count in red). It opens a mono table: ✓/✗ · name · one input line · result · ms. Failed rows are red.
- **Search:** matches highlighted `color-mix(warn 40%)`.
- **No results:** "Nothing matches «кит»", the line "Searched 30 days, 1 214 messages, all channels…", and the buttons Clear search and Search all time.
- **States to cover:** a long 3-paragraph reply, a reply with 5 tools, a failed tool, no results.

### Memory — `Memory.dc.html` (`sub` = summaries | facts)
- **Summaries:**
  - Tier chips: running digest, session, day, week, facts digest. Plus a **dashed** "the room's digests" chip.
  - Date range and search.
  - One timeline by day. Each entry: a 28px icon tile (sessions agent-tinted, other tiers neutral), a title ("session 20:58 – 21:40"), "covers 62 messages", "written 21:52", and the gist quote at 15px/1.6.
  - Sessions fold out "The detailed summary · 3 paragraphs" with an agent-colored left rule.
  - Room entries carry a dashed "the room" tag. Read-only.
- **Facts:**
  - Filters: All / Core / Obsolete, a "Show obsolete" checkbox, search, and "Add a fact".
  - Rows: pin button (filled agent pin = in the identity core) · fact text 14.5px + source line 12px · confidence bar + value · when · edit and Obsolete/Restore buttons.
  - Obsolete facts are struck through in neutral-400, with "corrected 2026-11-18 → «Виріс біля моря, в Одесі.»".
  - **Edit dialog:** a textarea, a confidence slider, the core checkbox, and the note "The current version is kept as obsolete and shown on request. Nothing is deleted."
  - **Refused state:** an amber alert "Refused — she is in the middle of a turn. Try again in a moment.", and the button becomes "Try again".

### Thoughts — `Thoughts.dc.html`
- Kind chips with counts (wonder, learn, think, imagine, catchup), a Spoken filter (All / Spoken / Silent), and "412 in 30 days · 23 % spoken".
- **Item:** time + a 20px emoji on the left; on the right, kind (icon), emotion, spoken (agent color, speaker icon) or silent (eye-slash), the origin row in mono, and the text at 15px/1.6.
- Spoken items get `color-mix(agent 6%)`.

### Gallery — `Gallery.dc.html`
- Tabs Pictures · 12 / Trash · 2. A grid `auto-fill minmax(160px,1fr)` of square thumbnails, radius 6px, with the name and date in mono underneath.
- **Opened picture:** a lightbox (backdrop `color-mix(bg 92%)` + blur) with the image on the left and a 320px side panel:
  - "N of M", the name, a Ukrainian caption, size / pixels / made / model / shown;
  - Download, Rename (inline input), Move to trash (red text, simple confirmation);
  - in the Trash tab: "Restore from trash";
  - keys ← → and Esc.

### Journal — `Journal.dc.html`
- **Left column (260px):** day list — date + entry count in mono, the mood line truncated. The selected day gets an agent tint and edge.
- **Right column:** reading layout, max 820px.
  - "LILI'S JOURNAL" kicker in agent, h1 "Thursday, 2026-11-20" at 28px, meta line.
  - **Stamp block:** Mood (16px quote) | Biorhythms (three rows), with Forecast spanning underneath.
  - **Entries:** grid `56px time (mono, in agent) | text 16px/1.7, max 62ch`.

### Files — `Files.dc.html`
- **Path bar:** `lili / notes /` as clickable segments, an item count, **Trash · 1**, and **Upload** (primary).
- **List:** icon · name (mono) · size · modified · rename/trash on hover. Rename is an inline input (Enter / Esc). Folders have a trailing `/`. In the trash view the action is Restore.
- **Reader:** the file header + Download. Her markdown is rendered as built elements: h1 24px, h2 17px, paragraphs 15px/1.7, list items with an agent-colored bullet, and blockquotes with a 2px agent-tinted left border.
- The two panes wrap (flex-wrap; list `flex:1 1 420px`, reader `flex:1.2 1 460px`).

### Usage — `Usage.dc.html` (both instances summed)
- Range segmented control (7 days / 30 days / 90 days / November), a pricing note, and Export CSV.
- **KPI strip:**
  - To date $41.30 (prod $39.60 · dev $1.70);
  - Forecast ≈ $62.10;
  - October $55.70;
  - Turns 1 046;
  - Per turn ≈ $0.028.
- **Cost per day:** stacked bars 150px tall with a gridline every $2, a y-axis, and the x-axis labels. Future days are an outlined forecast bar.
  - Providers in **muted categorical colors**: Anthropic accent-500, Google neutral-400, ElevenLabs accent-700, Deepgram neutral-600, OpenAI (no key) shown outlined.
- **Breakdowns** (bar lists: label · mono value · %): by model (embeddings "not priced"), by kind, by origin, by channel.
- **Prompt cache:** a 96px donut at 71 %, and "saved ≈ $38.40 this month".
- **Turn latency:** a median line plus a p25–p95 band. **The data gap is drawn as a gap**, labelled "no data". Below it, a PRE / MODEL / POST bar (0.4 / 8.9 / 0.5 s).
- **The slowest calls:** a list.

### Tools — `Tools.dc.html`
- **Columns:** tool · calls · failed · rate (amber if any failures, red if > 5 %) · median · p95 · last error · used-by chips.
- **Selected tool:** a calls-per-day chart (ok bars neutral-500 with red failed segments on top) and "Slowest and failed calls".

### Schedule — `Schedule.dc.html`
- **Table:** on switch · name (mono) + directive (Ukrainian, truncated) · trigger in words · shown/silent · origin chip (authored neutral, changed here accent, added here agent) · last firing + outcome ("then missed 2" in amber).
- **Row form:**
  - directive textarea;
  - trigger kind segmented control (every N h / after you go quiet / at a time), each with its own fields;
  - topic, Shown, On.
  - Right: **"Next five firings · live"**, recomputed as the fields change, with the trigger in words.
  - Buttons: Reset to authored, **Advanced · YAML** (reveals a mono textarea), Save row.
- **Side column:**
  - **The scheduler:** a switch and an amber warning ("catchup didn't fire at 17:30 and 20:30"), then quiet hours, thoughts per day 9 / 14, shown per day 2 / 6, and min gap 45 min.
  - **Next up:** a list.

### Models — `Models.dc.html`
- **Profile cards:** anthropic (active, accent ring), gemini, budget. Each has **Save** and **Now, until restart**.
- **Role rows:** role · provider · running now (mono) + the badge "⚡ switched until the restart" when it differs from settings · settings say · Change.
  - Change expands an option list: radio rows of provider · model · price.
  - A provider with no key shows a prohibit icon, "no key — set OPENAI_API_KEY in Secrets", and can't be chosen.
  - Below the list: "Now, until restart" and **Save** (red for prod; persists and restarts).
- Sample: dev `reply` is running claude-sonnet-5 while settings say claude-opus-4-8.

### Texts — `Texts.dc.html`
- **Left column (270px):** the file list — path in mono, "release" (neutral) or "changed here" (accent) mark, "⑂ drifted" in amber, size.
- **Header:** path, mark, "21.4 KB · ≈ 6 900 tokens in the prompt", and the live delta chip "+120 since the release". Buttons: Reset to the release, Copy to prod/dev, Download as a patch.
- **Drifted file:** an amber-tinted panel with **both differences** side by side ("The release changed (v4.7.0)" | "Your change here (11-02)"), and the buttons Take the release, Keep mine, Merge by hand.
- **Editor:** a full-height mono 13px/1.7 textarea on `--color-bg`.
- **Footer:**
  - check line, e.g. "check passed — 6 sections, no unknown placeholders, valid UTF-8", or an amber warning;
  - "Diff before saving" (shows the diff strip);
  - Save (red for prod).

### Prompt — `Prompt.dc.html`
- **Count strip:** input 1 240 · cache read 18 900 · cache write 0 · output 410 · system 29 KB. Next to it: "the last turn · 21:16 · Telegram · claude-opus-4-8 · $0.041".
- **Copy buttons:** "Copy the system part" and "Copy the whole prompt". Each confirms as "✓ Copied — 29 KB" for 2.2 s.
- **Sticky outline (230px):** sections with their sizes, and a dashed accent **cache split** marker between the facts digest and the recent days.
- **Body:**
  - each section as a long mono `pre` (12.5px/1.65, pre-wrap) with its name, size and ≈ tokens; long sections get "Show the remaining X";
  - the cache split as a visible accent band ("✂ cache split — everything above is read from the cache (18 900 tokens) · below is new each turn");
  - then the messages (role · text).
- **Empty state:** "No turn yet since the restart".

### Components sheet — `Components.dc.html`
Every component listed in the brief's deliverables, on one page, with a dark/light prop. Use it as the visual spec for shared pieces.

## Interactions & behaviour
- **Keyboard first:**
  - `P` / `D` switch the instance;
  - `J` / `K` move to the next / previous view;
  - `Esc` closes dialogs and popovers;
  - `← →` move between pictures in the Gallery lightbox;
  - `Enter` / `Esc` in inline rename.
  - Shortcuts are ignored while focus is in an input.
- **Focus:** `:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px }` on everything interactive.
- **Hover:** neutral tints `color-mix(text 4–7%)`; accent controls use `color-mix(accent 12% / 22%)` for hover / pressed (Nocturne `.btn`). Disabled controls are at 45 % opacity.
- **Live data:** Health refreshes every minute, the log tail streams, everything else loads on open. Overview and the attention strip should refresh with Health.
- **Persist** the current view and instance in `localStorage`, and use the URL hash for deep links (e.g. `#/server/logs?src=…&at=…`).
- **Animations:** none needed beyond instant state changes. The toast and popovers can appear without transitions; the "probing" state uses a spinning circle-notch.
- **Narrow windows:** grids use `auto-fit/minmax`; wide tables scroll horizontally inside their section; two-pane views (Files, Config history) wrap.

## State to implement (client)
```
session:   token (sessionStorage), signedIn
ui:        view, instance ('prod'|'dev'), theme ('dark'|'light'), pickerOpen
global:    attention { alerts[], incidents[] }   ← from health/alerts endpoints
dialog:    { title, body, instance, diff[], typed (bool), action, done, onConfirm } + typedText
toast:     message (auto-clear 4.2 s)
per view:  filters, selection, open/folded rows, form drafts (Settings diff, Schedule row, Texts buffer)
```
Each view shows **loading**, **empty**, and **"can't reach {instance}'s server"** states (an error from the instance's server, shown per view, with retry).

## Design tokens
- `tokens/nocturne.css` is the Nocturne token sheet and component classes, exactly as bound in the design. Key values:
  - Ground `--color-bg #161826`, surface `--color-surface #232532`, text `--color-text #e9e9ed`, accent `--color-accent #9184d9`, divider `color-mix(#e9e9ed 16%, transparent)`.
  - Ramps `--color-neutral-100…900` (#f3f5fe → #292b31) and `--color-accent-100…900` (#f5f4ff → #2b2741).
  - Spacing `--space-1…8` = 2.8, 5.6, 8.4, 11.2, 16.8, 22.4px.
  - Radii 4 / 8 / 14px.
  - Shadows: `--shadow-sm 0 0 0 1px #3f424d`, `--shadow-md 0 0 0 1px #595d6c, 0 6px 18px rgba(0,0,0,.55)`, `--shadow-lg 0 0 0 1px #9397ab, 0 16px 40px rgba(0,0,0,.65)`.
  - Font: Inter 400/500 (headings 500, never bolder).
- `tokens/tokens.css` holds the panel's additions. State colors in dark:
  - `--ok oklch(0.76 0.14 152)`, `--warn oklch(0.82 0.13 80)`, `--bad oklch(0.70 0.17 25)`, `--off #75798c`;
  - `--prod = --bad`;
  - agent colors `--agent oklch(0.78 0.11 330)` (Лілі, orchid) and `--agent-2 oklch(0.80 0.08 210)` (the second agent's slot, cyan);
  - provider colors, chart colors, diff colors, type sizes, layout sizes (rail 236, strip 32, header 52);
  - the **light theme** (`[data-theme="light"]`): bg #f3f5fe, surface #e8ebf8, text #292b31, accent #6658b5. Ramps are flipped, so "dark steps for fills, light steps for text" keeps working. State colors are darker for AA contrast.
- **Brief requirement:** dark and light themes, dark first. Light was designed for at least Overview, History and Journal; it applies to the whole shell.
- `tokens/strings.json` holds every UI string in English, grouped by view, with `{placeholders}`. Her content (Ukrainian) comes from data and is never in strings.

## Assets
- **Icons:** Phosphor, regular weight. Inline the SVG paths you need from phosphoricons.com, sized 13–16px in UI and 18–28px in empty states. Names used (Phosphor ids): squares-four, hard-drives, heartbeat, warning-diamond, cube, scroll, bell-simple-ringing, sliders-horizontal, key, clock-counter-clockwise, arrow-fat-lines-up, fingerprint, user-circle, gauge, chats-teardrop, brain, dot-outline, cloud, images-square, book-open-text, folder-simple, chart-bar, wrench, calendar-blank, cpu, text-aa, terminal-window, caret-up-down, check, plus, flask, sign-out, arrows-clockwise, columns, sun, moon, circle-notch, plugs, arrow-clockwise, arrow-right, check-circle, x, link-simple, magnifying-glass, download-simple, pause, arrow-down, lightning, x-circle, skull, paper-plane-tilt, play, stop, moon, bell-simple-slash, pencil-simple, trash, coins, square, check-square, lock-key, circle-dashed, git-diff, arrow-counter-clockwise, seal-check, link-break, link, browser, robot, file-dashed, app-window, telegram-logo, terminal, push-pin, hourglass-medium, sparkle, book-open, paint-brush, newspaper, speaker-high, eye-slash, stack, sun-horizon, list-bullets, users-three, chats-circle, file-text, upload-simple, file-csv, git-fork, copy, file-arrow-down, scissors, prohibit, radio-button, code, warning, warning-circle.
- **Emoji:** an emotion is one of the nine of the enum (joy, calm, playful, tender, thoughtful, serious, surprise, doubt, sad), shown with her own emoji from `core/emoji.md` (😄 joy, 🙂 calm, 😏 playful, 🥰 tender, 🤔 thoughtful, 😐 serious, 😮 surprise, 😕 doubt, 😢 sad; the glyph grows with the intensity). They come from data.
- **Fonts:** the brief asks for "system fonts with Cyrillic"; Nocturne uses Inter, which has Cyrillic. If loading Inter isn't wanted on the box, fall back to `system-ui` without changing sizes. Mono: `ui-monospace, "SF Mono", Menlo, Consolas, monospace`.
- No images ship with the design. Gallery thumbnails come from her `art/` folder.

## Files
- [`../BRIEF.md`](../BRIEF.md) — the product brief: views, states, sample content, safety; and the design's adaptation at its end.
- `prototype/Lumi Admin.dc.html` — the shell (entry point): rail, strip, header, switcher, sign-in, dialogs, toast, shared states, view routing.
- `prototype/Overview.dc.html` … `prototype/Prompt.dc.html` — one file per view (23 in all, including `Configuration` for Settings/Secrets/Config history/Promote and `Memory` for Summaries/Facts).
- `prototype/Components.dc.html` — the components sheet.
- `prototype/support.js`, `prototype/design-system/` — the prototype runtime and the Nocturne stylesheet, needed only to view the prototype.
- `tokens/nocturne.css`, `tokens/tokens.css`, `tokens/strings.json` — tokens and strings to carry into `admin/static/`.
