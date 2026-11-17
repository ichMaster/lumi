# LiliWindow — what v4 changed (against v2.12 `design/LiliWindow.dc.html`)

Everything else in the window is unchanged. Template syntax: `{{ hole }}`, `<sc-if value>`, `<sc-for list as>`, `<dc-import name="LiliViews" …>` mounts the views component (`design/LiliViews.dc.html`) with the listed props.

## New props
| Prop | Type | Default | Meaning |
|---|---|---|---|
| `view` | chat · files · reader · gallery · picture · journal · thoughts · state · schedule | chat | open view (reader → Files tab, picture → Gallery tab) |
| `tabs` | 2–7 | 7 | how many tabs exist (phased rollout) |
| `viewState` | ok · empty · loading · error | ok | state of the initially open view |
| `viewVariant` | string | "" | sub-state for the view: `path:notes`, `file:lili.md`, `raw`, `outline`, `pic:rooftop_rain`, `day:2026-07-12`, `list`, `kinds:wonder,learn`, `kindsOpen`, `spoken:spoken`, `cut1000` |
| `chatUnread` | bool | false | unread mark on Chat |
| `lightbox` | bool | false | full picture view open over the chat |
| `side` / `side2` | comma list of tab ids | "" | views pinned to the right / bottom-right panel |
| `sideView` / `side2View` | tab id | first | active tab in each panel |
| `sideVariant` / `side2Variant` | string | "" | variant for the panel's view (e.g. `file:notes/ideas.md`) |
| `sideOpen` | bool | true | right panels unfolded or folded to the rail |

## New state
`view, viewVariant, chatUnread, lightbox, side[], sideActive, sideVariant, side2[], side2Active, side2Variant, sideOpen, sideW (340), sideSplit (0.55)`.

## Root
`<div ref=root lang="uk" tabindex="-1" onKeyDown={onRootKey} …>` —
- ⌘\ (Ctrl+\) with any pins → toggle `sideOpen`;
- ⌘1…⌘7 (≤ `tabs`) → `setView(VIEWS[n-1])`.

`VIEWS = [chat Chat, files Files, gallery Gallery, journal Journal, thoughts Thoughts, state State, schedule Schedule]`.

## Methods
```js
setView(v, variant) {
  // switch tab; close the chat lightbox; clear Chat unread when returning to chat
  // (prototype only: 4 s after first leaving the chat, she writes one line and Chat gets the unread mark)
}
pin(k, slot /* 'r' | 'b' */) {
  // k ≠ chat. Toggle k in that slot; remove it from the other slot; make it active; unfold the panels.
}
startResize(e)  // drag the panel column's left edge: sideW = clamp(280, w0 + (x0 - x), 520)
startSplit(e)   // drag the rule between panels: sideSplit = clamp(.2, (y - top) / height, .8)
```

## Template changes
1. **Top of `<main>`** — the view row:
```html
<nav aria-label="Views" style="flex:none;display:flex;align-items:stretch;box-shadow:inset 0 -2px 0 var(--color-divider);padding:{{ tabPad /* wide '0 16px 0 26px', narrow '0 4px' */ }}">
  <div style="flex:1;min-width:0;display:flex;overflow-x:auto;overflow-y:hidden;scrollbar-width:none">
    <!-- per tab -->
    <button aria-current="page|false" title="Files  ⌘2" style="flex:none;display:inline-flex;align-items:center;gap:7px;height:42px;padding:0 14px;background:none;border:0;border-bottom:2px solid {accent|transparent};font-size:13px;font-weight:600;color:{--color-text|--l-muted};white-space:nowrap">Files<!-- unread: --><span role="img" aria-label="new messages" style="width:6px;height:6px;background:var(--color-accent)"></span></button>
  </div>
  <!-- wide && view ≠ chat -->
  <div role="group" aria-label="Pin this view" style="flex:none;display:flex;align-items:center;gap:2px;padding:0 0 0 10px">
    <span class="kicker" style="font-size:10px">Pin</span>
    <button aria-pressed aria-label="Pin to the right panel" style="width:32px;height:32px;background:{surface|transparent};color:{--l-acc-text|--l-faint}">[panel-right, right part filled]</button>
    <button aria-pressed aria-label="Pin to the bottom-right panel">[panel-right + h-line at 12, bottom-right quarter filled]</button>
  </div>
</nav>
```
2. **Chat body** (message list + input) is wrapped in `isChatView`. Otherwise:
```html
<div style="position:relative;flex:1;min-height:0">
  <LiliViews view={view} variant={viewVariant} viewState={…only for the initial view} layout={wide?'wide':'narrow'} theme appearance tone onTab={(tab, variant) => setView(tab, variant)} style="position:absolute;inset:0"/>
</div>
```
The connection banners stay above both.
3. **Chat picture** — the square becomes `<button aria-label="Open picture rooftop_light.png" style="display:block;position:relative;width:100%;aspect-ratio:1/1;padding:0;border:0;background:var(--color-surface);cursor:zoom-in">` holding the image; hover ring `box-shadow:0 0 0 2px var(--color-accent)`. Caption: `<span mono>art/rooftop_light.png</span> · 768 × 768 · відкрити — пробіл`. Click → `lightbox = true` → over `<main>`:
```html
<div style="position:absolute;inset:0;z-index:20"><LiliViews view="picture" variant="pic:rooftop_light" onClose={() => lightbox=false} onTab={setView} …/></div>
```
4. **After `<main>`** — the right panels (wide only):
```html
<aside ref=side aria-label="Right panels" style="flex:none;position:relative;width:{sideW}px;border-left:2px solid var(--color-divider);display:flex;flex-direction:column;min-width:0;min-height:0">
  <button aria-label="Resize the right panel" style="position:absolute;z-index:3;left:-6px;top:0;bottom:0;width:10px;background:transparent;border:0;cursor:col-resize"></button>  <!-- ← → ±20 -->
  <!-- one <section> per panel that has pins: Right panel, then Bottom-right panel -->
  <section aria-label="Right panel" style="position:relative;flex:{split} 1 0 | {1-split} 1 0 | 1 1 0;min-height:140px;display:flex;flex-direction:column;border-top:{0 | 2px solid var(--color-divider)}">
    <!-- second panel only: --><button aria-label="Resize the two right panels" style="position:absolute;z-index:3;left:0;right:0;top:-7px;height:10px;cursor:row-resize"></button> <!-- ↑ ↓ ±.05 -->
    <div style="flex:none;display:flex;align-items:center;box-shadow:inset 0 -2px 0 var(--color-divider);padding:0 4px 0 8px">
      <div role="group" aria-label="Right panel: pinned views" style="flex:1;min-width:0;display:flex;overflow-x:auto">[tabs: height 40, padding 0 10, 12.5px/600, same active bar]</div>
      <button class="btn btn-icon" aria-label="Unpin Journal" style="width:32px;height:32px;color:var(--l-faint)">[x]</button>
      <!-- top panel only: --><button class="btn btn-icon" aria-label="Collapse the right panels" title="Collapse  ⌘\">[chevrons-right]</button>
    </div>
    <div style="position:relative;flex:1;min-height:0"><LiliViews view={active} variant={…} layout="narrow" onTab={(t,v) => open t in THIS panel} style="position:absolute;inset:0"/></div>
  </section>
</aside>
<!-- folded -->
<div style="flex:none;width:46px;border-left:2px solid var(--color-divider);display:flex;flex-direction:column;align-items:center;gap:14px;padding-top:10px">
  <button class="btn btn-icon" aria-label="Open the right panels">[panel-right]</button>
  <span style="writing-mode:vertical-rl;transform:rotate(180deg)" class="kicker">Journal · Schedule</span>
</div>
```
5. **Thoughts strip** header (portrait panel): after «Усі · N» a text button «Thoughts →» (12px/600 `--l-faint`, hover `--color-text`, `title="Thoughts  ⌘5"`) → `setView('thoughts')` (only when ≥ 5 tabs).
6. **Status details**: after the details grid, the tool list (see README §11), data `[{name, input, result}]`:
```html
<div style="padding:8px 14px;border-bottom:1px solid var(--l-hair)">
  <div><span class="kicker" style="font-size:10px">Tools · last reply</span> <span style="font-size:11px;color:var(--l-faint)">3</span></div>
  <div style="max-height:150px;overflow-y:auto">
    <div style="display:grid;grid-template-columns:104px minmax(0,1fr) auto;gap:2px 14px;padding:5px 0;border-top:1px solid var(--l-hair);font-size:12px;align-items:baseline">
      <span style="font:600 12px/1.4 var(--font-mono);color:var(--l-acc-text)">wiki_search</span>
      <span style="font:12px/1.4 var(--font-mono);color:var(--l-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{query: "пам’ять рослин"}</span>
      <span style="color:var(--l-muted);white-space:nowrap">→ 5 results</span>
    </div>
  </div>
</div>
```
