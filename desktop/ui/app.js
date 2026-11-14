/* Lili's desktop window — the chat (v2.12.1, LUMI-267).
   A thin client: everything goes to the server through the local proxy's relative /v1 URLs (desktop/proxy.py adds
   the token). It mirrors tui/remote.py — a streamed turn by turn_id (a broken stream asks the same id the blocking
   way, so she never answers twice), the push channel with Last-Event-ID resume, the command layer, %directives.
   No HTML strings anywhere: every node is built, every text is textContent; her markdown becomes DOM nodes. */
"use strict";

const KEY = (document.querySelector('meta[name="lumi-desktop-key"]') || {}).content || "";
const CLIENT_ID = "desktop-" + Math.random().toString(16).slice(2, 10);
const BACKOFF_S = [5, 10, 15, 30];
const DEAD_STREAM_MS = 45000; // ~3 missed heartbeats (the server sends one per 15 s)
const REPLAY_QUIET_MS = 700; // the replay after a (re)connect is over once nothing arrives for this long
const CHAT_SIZE = { min: 14, max: 22, default: 16, store: "lumi.desktop.chatSize" };
const THEMES = ["3am", "day-after", "furious", "dissociation", "last-memory", "quiet-collapse", "im-fine", "drowning",
  "vigil", "calm-before"];

let S = {}; // strings.en.json
const st = {
  server: {}, conn: "connecting", answering: false, queue: [], lastEventId: null, everOnline: false,
  retryIdx: 0, countdown: 0, wake: null, replaying: false, replayTimer: null, missed: [], restored: null,
  slash: { open: false, items: [], index: 0, dismissed: false }, detailsOpen: false, rows: 0, chatSize: 16,
  thinkMode: null, thinkBlocks: [], thoughts: [], thoughtsOpen: false,  // v2.12.2
  emoji: {},  // LUMI-276: the TUI's emotion → emoji map, from the shell
};
const THINK_STORE = "lumi.desktop.thinkMode";
const THINK_MODES = ["shown", "collapsed", "hidden"];
const THINK_FROM_SERVER = { open: "shown", debug: "collapsed", off: "hidden" };  // the state's think_show → a first default

/* ---------- small helpers ---------- */
const $ = (id) => document.getElementById(id);

function t(key, vars) {
  let v = key.split(".").reduce((o, k) => (o && typeof o === "object" && k in o ? o[k] : undefined), S);
  if (v === undefined) v = S[key];
  if (typeof v !== "string") return key;
  return v.replace(/\{(\w+)\}/g, (m, name) => (vars && name in vars ? String(vars[name]) : m));
}

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) {
    node.append(typeof kid === "string" ? document.createTextNode(kid) : kid);
  }
  return node;
}

const ICONS = {
  "arrow-up": [["path", { d: "m5 12 7-7 7 7" }], ["path", { d: "M12 19V5" }]],
  "chevron-up": [["path", { d: "m18 15-6-6-6 6" }]],
  "chevron-down": [["path", { d: "m6 9 6 6 6-6" }]],
  "chevron-right": [["path", { d: "m9 18 6-6-6-6" }]],
  cloud: [["path", { d: "M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" }]],
  send: [["path", { d: "M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" }], ["path", { d: "m21.854 2.147-10.94 10.939" }]],
  terminal: [["polyline", { points: "4 17 10 11 4 5" }], ["line", { x1: "12", x2: "20", y1: "19", y2: "19" }]],
  "wifi-off": [["path", { d: "M12 20h.01" }], ["path", { d: "M8.5 16.429a5 5 0 0 1 7 0" }], ["path", { d: "M5 12.859a10 10 0 0 1 5.17-2.69" }], ["path", { d: "M19 12.859a10 10 0 0 0-2.007-1.523" }], ["path", { d: "M2 8.82a15 15 0 0 1 4.177-2.643" }], ["path", { d: "M22 8.82a15 15 0 0 0-11.288-3.764" }], ["path", { d: "m2 2 20 20" }]],
  "refresh-cw": [["path", { d: "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" }], ["path", { d: "M21 3v5h-5" }], ["path", { d: "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" }], ["path", { d: "M8 16H3v5" }]],
  x: [["path", { d: "M18 6 6 18" }], ["path", { d: "m6 6 12 12" }]],
};

function icon(name, size) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  if (size) { svg.setAttribute("width", size); svg.setAttribute("height", size); }
  for (const [tag, attrs] of ICONS[name]) {
    const part = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) part.setAttribute(k, v);
    svg.append(part);
  }
  return svg;
}

const pad = (n) => String(n).padStart(2, "0");
const clock = (d = new Date()) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const weekday = (d = new Date()) => d.toLocaleDateString("en-GB", { weekday: "long" });
const dayMonth = (d = new Date()) => d.toLocaleDateString("en-GB", { day: "numeric", month: "long" });
const uid = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : String(Date.now()) + Math.random().toString(16).slice(2));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------- the API through the local proxy ---------- */
function headers(extra) {
  return Object.assign({ "X-Lumi-Desktop-Key": KEY, "X-Lumi-Client": CLIENT_ID }, extra || {});
}

class ApiError extends Error {
  constructor(status, kind, detail) { super(detail || kind || `HTTP ${status}`); this.status = status; this.kind = kind; }
}

async function api(path, body) {
  const opts = { method: body === undefined ? "GET" : "POST", headers: headers(body === undefined ? {} : { "Content-Type": "application/json" }) };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.kind, typeof data.detail === "string" ? data.detail : "");
  return data;
}

/* Server-Sent Events from a fetch body — the frames tui.remote.parse_sse_frames reads: event / id / data, ":" heartbeats,
   multi-line data joined, a blank line ends a frame. onByte resets the caller's dead-stream watchdog. */
async function* sse(res, onByte) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "", ev = null, id = null, data = [];
  const frame = () => {
    let payload = {};
    if (data.length) payload = JSON.parse(data.join("\n"));
    const out = { event: ev || "message", data: payload && typeof payload === "object" ? payload : {}, id };
    ev = null; id = null; data = [];
    return out;
  };
  const take = (line) => { // one line → a finished frame, or null
    if (line.endsWith("\r")) line = line.slice(0, -1);
    if (line === "") return ev !== null || data.length ? frame() : null;
    if (line.startsWith(":")) return null;
    const at = line.indexOf(":");
    const field = at < 0 ? line : line.slice(0, at);
    let val = at < 0 ? "" : line.slice(at + 1);
    if (val.startsWith(" ")) val = val.slice(1);
    if (field === "event") ev = val;
    else if (field === "id") id = val || null;
    else if (field === "data") data.push(val);
    return null;
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    if (onByte) onByte();
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const got = take(buf.slice(0, nl));
      buf = buf.slice(nl + 1);
      if (got) yield got;
    }
  }
  buf += dec.decode();
  if (buf) { const got = take(buf); if (got) yield got; } // a last line without its newline
  if (ev !== null || data.length) yield frame(); // a last frame without its blank line
}

/* ---------- her markdown → DOM (a small safe subset; links http/https only) ---------- */
const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*|_[^_\n]+_)|(\[[^\]\n]+\]\((https?:\/\/[^\s)]+)\))/i;

function inline(text) {
  const out = [];
  let rest = text;
  while (rest) {
    const m = INLINE.exec(rest);
    if (!m) { out.push(rest); break; }
    if (m.index) out.push(rest.slice(0, m.index));
    const tok = m[0];
    if (m[1]) out.push(el("code", { text: tok.slice(1, -1) }));
    else if (m[2]) out.push(el("strong", {}, inline(tok.slice(2, -2))));
    else if (m[3]) out.push(el("em", {}, inline(tok.slice(1, -1))));
    else if (m[4]) {
      const label = tok.slice(1, tok.indexOf("]("));
      out.push(el("a", { href: m[5], target: "_blank", rel: "noopener noreferrer" }, label));
    }
    rest = rest.slice(m.index + tok.length);
  }
  return out;
}

function withBreaks(lines) {
  const out = [];
  lines.forEach((line, i) => { if (i) out.push(el("br")); out.push(...inline(line)); });
  return out;
}

function markdown(src) {
  const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.trimStart().startsWith("```")) {
      const code = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) code.push(lines[i++]);
      i++;
      blocks.push(el("pre", {}, el("code", { text: code.join("\n") })));
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ""));
      blocks.push(el("blockquote", {}, withBreaks(quote)));
      continue;
    }
    const bullet = /^\s*[-*•]\s+/, numbered = /^\s*\d+[.)]\s+/;
    if (bullet.test(line) || numbered.test(line)) {
      const kind = bullet.test(line) ? bullet : numbered;
      const list = el(kind === bullet ? "ul" : "ol");
      while (i < lines.length && kind.test(lines[i])) list.append(el("li", {}, inline(lines[i++].replace(kind, ""))));
      blocks.push(list);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(>|```|[-*•]\s|\d+[.)]\s)/.test(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    blocks.push(el("p", {}, withBreaks(para)));
  }
  return blocks;
}

/* ---------- the list ---------- */
function nearBottom() {
  const list = $("list");
  return list.scrollHeight - list.scrollTop - list.clientHeight < 80;
}

function stick(force) {
  const list = $("list");
  if (force || nearBottom()) list.scrollTop = list.scrollHeight;
}

function append(node) {
  const follow = nearBottom();
  const wasEmpty = st.rows === 0;
  removeEmpty();
  $("col").append(node);
  st.rows++;
  if (wasEmpty) renderPanel(); // the empty state is over: the panel shows its meta and the mood again
  stick(follow);
  return node;
}

function sourceTag(source, origin) {
  if (source === "telegram") return el("span", { class: "source", title: t("source.tg.title") }, icon("send", 10), t("source.tg"));
  if ((source === "client" || source === "voice") && origin && !String(origin).startsWith("desktop-")) {
    return el("span", { class: "source", title: t("source.term.title") }, icon("terminal", 10), t("source.term"));
  }
  return null;
}

function row(kind, { time, source, origin, queued } = {}) {
  const whoKey = kind === "mine" ? "who.me" : kind === "hers" ? "who.her" : "who.command";
  const whoClass = kind === "mine" ? "me" : kind === "hers" ? "her" : "cmd";
  const content = el("div", { class: "content" });
  const node = el("div", { class: `row ${kind}${queued ? " queued" : ""}` },
    el("div", { class: "gutter" }, el("span", { class: `who ${whoClass}`, text: t(whoKey) }),
      time === undefined ? null : el("span", { class: "time", text: queued ? t("queued.time") : time }),
      sourceTag(source, origin)),
    content);
  return { node, content };
}

function addMine(text, opts = {}) {
  const r = row("mine", opts);
  r.content.append(el("div", { class: "text", lang: "uk", text }));
  append(r.node);
  return r;
}

function band(intensity) {  // the TUI's bands: low < 0.34, mid, high >= 0.67
  const x = Number(intensity);
  return !Number.isFinite(x) ? 1 : x < 0.34 ? 0 : x < 0.67 ? 1 : 2;
}

function setEmotion(rowNode, emotion, intensity) {  // her emotion under "LILI": the glyph(s), the caption on hover
  if (!rowNode || !emotion) return;
  const map = st.emoji || {};
  const glyphs = (map[emotion] || map.calm || [])[band(intensity)];
  if (!glyphs) return;
  const gutter = rowNode.querySelector(".gutter");
  const old = gutter.querySelector(".emo");
  if (old) old.remove();
  const level = Number.isFinite(Number(intensity)) ? ` \u00b7 ${Number(intensity).toFixed(1)}` : "";
  gutter.append(el("span", { class: "emo", title: `${captionOf(emotion)}${level}`, text: glyphs }));
}

function addHers(text, opts = {}) {
  const r = row("hers", opts);
  if (opts.first) r.content.append(el("div", { class: "first" }, el("span", { class: "bar" }), el("span", { class: "label", text: t("her.first") })));
  const body = el("div", { class: "text", lang: "uk" });  // her words are Ukrainian: hyphenation
  if (text !== null) body.append(...markdown(text));
  r.content.append(body);
  append(r.node);
  if (opts.emotion) setEmotion(r.node, opts.emotion, opts.intensity);
  return { ...r, body };
}

function addCommand(line, result) {
  const r = row("cmd", { time: clock() });
  const block = el("div", { class: "cmdblock" },
    el("div", { class: "cmdhead" }, el("span", { class: "cmd", text: line }), el("span", { class: "what", text: t("command.result") })));
  const text = String((result && result.text) || "").trim();
  const lines = text.split("\n").filter((l) => l.trim());
  const kv = lines.length > 0 && lines.every((l) => /^[^:]{1,32}:\s+\S/.test(l));
  if (kv) {
    const dl = el("dl", { class: "kv" });
    for (const l of lines) {
      const at = l.indexOf(":");
      dl.append(el("dt", { text: l.slice(0, at).trim() }), el("dd", { text: l.slice(at + 1).trim() }));
    }
    block.append(dl);
  } else {
    block.append(el("div", { class: "free", text }));
  }
  r.content.append(block);
  append(r.node);
}

function addNote(text) {
  append(el("div", { class: "note", text }));
}

function addDivider(label, missed) {
  const node = el("div", { class: `divider${missed ? " missed" : ""}`, role: "separator" },
    el("span", { class: "kicker", text: label }), el("span", { class: "line" }));
  append(node);
  return node;
}

function removeEmpty() {
  const empty = $("empty");
  if (empty) empty.remove();
}

function renderEmpty() {
  if (st.rows || $("empty") || !st.server.mood) return;
  $("col").append(el("div", { id: "empty", class: "empty" },
    el("span", { class: "kicker", text: t("empty.mood.kicker", { weekday: weekday(), date: dayMonth() }) }),
    el("p", { class: "empty-mood", lang: "uk", text: st.server.mood })));
}

/* ---------- the portrait panel (v2.12.2) ---------- */
const NARROW = window.matchMedia("(max-width: 759px)");
const face = { box: null, layers: [], front: 0, src: null, emotion: "calm", intensity: 0.5, theme: null, seq: 0, asked: false };
const panel = { moodOpen: false, expanded: false };

function captionOf(emotion) {  // the atmospheric line — never the emotion's own name
  const caps = S.captions || {};
  return caps[emotion] || caps.calm || "";
}

function portraitBox() {  // one box, two stacked layers: the new face loads hidden, then the opacities swap
  if (!face.box) {
    face.layers = [el("img", { class: "layer on", alt: "" }), el("img", { class: "layer", alt: "" })];
    face.box = el("div", { class: "portrait" }, ...face.layers);
  }
  return face.box;
}

async function showFace(theme, emotion, intensity) {
  face.asked = true;
  face.theme = theme || null;
  face.emotion = (S.captions && S.captions[emotion]) ? emotion : "calm";
  face.intensity = intensity === null || intensity === undefined ? 0.5 : Number(intensity);
  const seq = ++face.seq;
  const q = new URLSearchParams({ theme: face.theme || "", emotion: face.emotion, intensity: String(face.intensity) });
  let data = null;
  try {
    const res = await fetch(`/face?${q}`, { headers: headers() }); // the shell picks it from the local pack
    if (res.ok) data = await res.json();
  } catch (e) { data = null; }
  if (seq !== face.seq) return; // a newer state asked meanwhile
  for (const node of document.querySelectorAll(".face-caption")) node.textContent = captionOf(face.emotion);
  if (!data || !data.src || data.src === face.src) return;
  face.src = data.src;
  const back = face.layers[1 - face.front];
  back.onload = () => {
    if (back.getAttribute("src") !== face.src) return;
    back.classList.add("on");
    face.layers[face.front].classList.remove("on");
    face.front = 1 - face.front;
  };
  back.src = data.src;
}

function themeInfo(slug) {
  const th = slug && S.themes ? S.themes[slug] : null;
  return th ? { name: th.name, sub: `${slug} \u00b7 ${th.scene}` } : { name: t("panel.theme.everyday"), sub: "" };
}

function closenessLevel(s) {
  const level = s.closeness ? Number(s.closeness.level) : NaN;
  return level >= 1 && level <= 5 ? level : null;
}

function closenessName(level) {
  return level && Array.isArray(S.closeness) ? S.closeness[level - 1] : "\u2014";
}

function metaGrid(s) {
  const th = themeInfo(s.theme);
  const level = closenessLevel(s);
  const of = level ? t("panel.closeness.of", { n: level }) : "";
  const meter = el("div", { class: "meter", role: "img", "aria-label": `${t("panel.closeness")} ${of}`.trim() },
    ...[1, 2, 3, 4, 5].map((n) => el("span", { class: level && n <= level ? "on" : null })));
  return el("div", { class: "meta" },
    el("div", { class: "meta-cell" }, el("span", { class: "kicker", text: t("panel.theme") }), el("span", { class: "name", text: th.name }),
      th.sub ? el("span", { class: "sub", text: th.sub }) : null),
    el("div", { class: "meta-cell" }, el("span", { class: "kicker", text: t("panel.closeness") }),
      el("span", { class: "name" }, closenessName(level), level ? el("span", { class: "of", text: ` ${of}` }) : null), meter));
}

function metaLine(s) {
  const level = closenessLevel(s);
  return [level ? `${closenessName(level)} \u00b7 ${level}/5` : null, themeInfo(s.theme).name].filter(Boolean).join(" \u00b7 ");
}

function toggleMood() { panel.moodOpen = !panel.moodOpen; renderPanel(); }

function moodBlock(mood) {
  return el("section", { class: `mood${panel.moodOpen ? " open" : ""}` },
    el("div", { class: "mood-head" }, el("span", { class: "kicker", text: t("panel.mood") }),
      el("button", { type: "button", class: "toggle", "aria-expanded": String(panel.moodOpen), onclick: toggleMood },
        t(panel.moodOpen ? "panel.mood.collapse" : "panel.mood.expand"), icon("chevron-down"))),
    el("p", { class: "mood-text", lang: "uk", text: mood }));
}

function moodRow(mood) {
  return el("button", { type: "button", class: `mood-row${panel.moodOpen ? " open" : ""}`, "aria-expanded": String(panel.moodOpen),
    onclick: toggleMood }, el("b", { text: `${t("panel.mood")} \u00b7 ` }), el("span", { lang: "uk", text: mood }));
}

function renderPanel() {
  const aside = $("panel");
  const narrow = NARROW.matches;
  aside.classList.toggle("narrow", narrow);
  document.querySelector(".body").classList.toggle("narrow", narrow);
  const s = st.server || {};
  const empty = st.rows === 0; // a new session: the portrait + caption only (the mood is large in the chat)
  const caption = el("p", { class: `caption face-caption`, "aria-live": "polite", text: captionOf(face.emotion) });
  const kids = [];
  if (!narrow) {
    kids.push(portraitBox(), el("div", { class: "caption-block" }, caption, empty ? null : metaGrid(s)));
    if (!empty && s.mood) kids.push(moodBlock(s.mood));
  } else {
    const thumb = el("button", { type: "button", class: "thumb", "aria-expanded": String(panel.expanded),
      "aria-label": t(panel.expanded ? "narrow.portrait.hide" : "narrow.portrait.show"),
      onclick: () => { panel.expanded = !panel.expanded; renderPanel(); } });
    if (!panel.expanded) thumb.append(portraitBox());
    kids.push(el("div", { class: "strip-row" }, thumb,
      el("div", { class: "strip-words" }, caption, empty ? null : el("span", { class: "strip-meta", text: metaLine(s) })),
      el("div", { id: "thoughts-button" })));
    if (panel.expanded) {
      kids.push(el("div", { class: "expanded" }, portraitBox(),
        el("button", { type: "button", class: "collapse", "aria-label": t("narrow.portrait.hide"),
          onclick: () => { panel.expanded = false; renderPanel(); } }, icon("chevron-up"))));
    }
    if (!empty && s.mood) kids.push(moodRow(s.mood));
  }
  kids.push(el("div", { id: "thoughts" }));
  aside.replaceChildren(...kids);
  renderThoughts();
}

/* ---------- the think block (v2.12.2): her inner voice above her reply ---------- */
function thinkBlock(her) {
  const block = { text: "", override: null, node: el("div", { class: "think", hidden: true }) };
  her.content.insertBefore(block.node, her.body);  // above the text; empty and hidden until a think frame comes
  st.thinkBlocks.push(block);
  return block;
}

function setThink(block, text) {
  block.text = text;
  renderThink(block);
}

function renderThink(block) {
  const mode = block.override || st.thinkMode || "collapsed";
  block.node.hidden = !block.text || mode === "hidden";
  if (block.node.hidden) { block.node.replaceChildren(); return; }
  const open = mode === "shown";
  const flip = () => { block.override = open ? "collapsed" : "shown"; renderThink(block); };
  const head = el("button", { type: "button", class: "think-head", "aria-expanded": String(open), onclick: flip },
    icon(open ? "chevron-down" : "chevron-right"), el("span", { class: "label", text: t("think.label") }),
    open ? null : el("span", { class: "first", lang: "uk", text: block.text.replace(/\s+/g, " ").trim() }));
  const parts = [head];  // never a null into replaceChildren — it would print as text
  if (open) parts.push(el("div", { class: "think-text", lang: "uk", text: block.text }));
  block.node.replaceChildren(...parts);
  block.node.classList.toggle("open", open);
}

function setThinkMode(mode) {
  st.thinkMode = THINK_MODES.includes(mode) ? mode : "collapsed";
  try { localStorage.setItem(THINK_STORE, st.thinkMode); } catch (e) { /* the mode lasts this window */ }
  for (const block of st.thinkBlocks) { block.override = null; renderThink(block); }  // a new global mode clears overrides
  if (st.detailsOpen) renderDetails();
}

/* ---------- the quiet-thoughts strip (v2.12.2): her thoughts beside the portrait, never in the chat ---------- */
function onThought(data) {
  if (data.origin && data.origin === CLIENT_ID) return;  // this window's own %directive — shown from its response
  st.thoughts.unshift({ kind: String(data.kind || ""), text: String(data.text || ""), emotion: String(data.emotion || ""),
    when: data.when, unread: !data.replay });  // a replayed thought is history, never "new"
  if (st.thoughts.length > 100) st.thoughts.length = 100;
  renderThoughts();
}

function thoughtTime(when) {
  const d = when ? new Date(when) : null;
  return d && !Number.isNaN(d.getTime()) ? clock(d) : "";
}

function thoughtItem(th) {
  const kind = (S.thoughtKinds && S.thoughtKinds[th.kind]) || th.kind;
  return el("li", { class: "thought" },
    el("div", { class: "thought-meta" }, th.unread ? el("span", { class: "sq", "aria-hidden": "true" }) : null,
      el("span", { class: "kind", text: kind }), el("span", { text: thoughtTime(th.when) })),
    el("p", { class: "thought-text", lang: "uk", text: th.text }),
    S.captions && S.captions[th.emotion] ? el("span", { class: "thought-cap", text: S.captions[th.emotion] }) : null);
}

function toggleThoughts() {
  if (st.thoughtsOpen) for (const th of st.thoughts) th.unread = false;  // read once the list is closed after opening
  st.thoughtsOpen = !st.thoughtsOpen;
  renderThoughts();
}

function renderThoughts() {
  const slot = $("thoughts");
  const button = $("thoughts-button");
  const unread = st.thoughts.filter((th) => th.unread).length;
  const narrow = NARROW.matches;
  if (button) {  // the narrow strip's cloud button + its unread badge
    button.replaceChildren(st.thoughts.length ? el("button", { type: "button", class: "cloud",
      "aria-expanded": String(st.thoughtsOpen), "aria-label": t("panel.thoughts.badge", { n: st.thoughts.length }), onclick: toggleThoughts },
    icon("cloud"), unread ? el("span", { class: "badge", text: String(unread) }) : null) : "");
  }
  if (!slot) return;
  if (!st.thoughts.length || st.rows === 0 || (narrow && !st.thoughtsOpen)) { slot.replaceChildren(); slot.className = ""; return; }
  const shown = st.thoughtsOpen ? st.thoughts : st.thoughts.slice(0, 1);
  slot.className = "thoughts";
  slot.replaceChildren(
    el("div", { class: "thoughts-head" }, el("span", { class: "kicker", text: t("panel.thoughts") }),
      unread ? el("span", { class: "new", text: unread === 1 ? t("panel.thoughts.new.one") : t("panel.thoughts.new.many", { n: unread }) }) : null,
      el("button", { type: "button", class: "toggle", "aria-expanded": String(st.thoughtsOpen), onclick: toggleThoughts,
        text: st.thoughtsOpen ? t("panel.thoughts.collapse") : t("panel.thoughts.all", { n: st.thoughts.length }) })),
    el("ul", { class: "thought-list" }, shown.map(thoughtItem)));
}

/* ---------- state from the server ---------- */
function absorb(state) {
  if (!state || typeof state !== "object") return;
  const before = st.server || {};
  st.server = state;
  if (!st.thinkMode) st.thinkMode = THINK_FROM_SERVER[state.think_show] || "collapsed";  // only when none is kept
  if (!face.asked) {  // the first state: her last emotion in today's theme
    const last = state.last_emotion || {};
    showFace(state.theme, last.emotion || "calm", last.intensity);
  } else if (state.theme !== before.theme) {
    showFace(state.theme, face.emotion, face.intensity); // a new day's wardrobe
  }
  renderPanel();
  const theme = THEMES.includes(state.theme) ? state.theme : null;
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  const empty = $("empty");
  if (empty && state.mood) empty.querySelector(".empty-mood").textContent = state.mood;
  renderStatus();
}

/* ---------- the status line + details ---------- */
function renderStatus() {
  const s = st.server;
  const bar = $("status");
  const state = st.conn === "offline" ? "offline" : st.conn === "connecting" || st.conn === "retrying" ? "connecting"
    : st.answering ? "answering" : "ready";
  const label = { offline: t("status.offline"), connecting: t("status.connecting"), answering: t("status.answering"), ready: t("status.ready") }[state];
  const kids = [el("span", { class: `dot ${state}` }), el("span", { text: label })];
  if (state === "offline" && st.countdown > 0) kids.push(el("span", { class: "retry", text: t("status.retryIn", { n: st.countdown }) }));
  if (s.env && s.env !== "prod") kids.push(el("span", { class: "dev", title: t("status.dev.title"), text: "DEV" }));
  const open = el("button", { type: "button", class: "open-details", "aria-expanded": String(st.detailsOpen), "aria-controls": "details",
    onclick: () => { st.detailsOpen = !st.detailsOpen; renderStatus(); } },
  el("span", { class: "label", text: t("status.details", { version: s.version || "—" }) }), icon("chevron-up"));
  kids.push(open);
  bar.replaceChildren(...kids);
  renderDetails();
}

function fmtMs(ms) { return ms === null || ms === undefined ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`; }
function fmtTokens(n) { return n === null || n === undefined ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n); }

function renderDetails() {
  const box = $("details");
  box.hidden = !st.detailsOpen;
  if (!st.detailsOpen) return;
  const s = st.server, totals = s.totals || {}, last = s.last_stats || {};
  const tokens = ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"].reduce((a, k) => a + (totals[k] || 0), 0);
  const style = s.style === "normal" ? t("details.style.normal") : (S.styles && S.styles[s.style]) || s.style || "—";
  const cells = [
    ["details.env", s.env || "—"], ["details.version", s.version || "—"], ["details.model", s.model || "—"],
    ["details.profile", s.profile || "—"], ["details.thinking", s.thinking ? t("details.thinking.on") : t("details.thinking.off")],
    ["details.style", style], ["details.firstChar", fmtMs(s.last_ttft_ms)], ["details.total", fmtMs(last.latency_ms)],
    ["details.tokens", fmtTokens(tokens)], ["details.turns", totals.turns === undefined ? "—" : String(totals.turns)],
  ];
  const grid = el("div", { class: "cells" }, cells.map(([k, v]) => el("div", { class: "cell" },
    el("div", { class: "kicker", text: t(k) }), el("div", { class: "value", text: v }))));
  const size = el("div", { class: "control" }, el("span", { class: "kicker", text: t("details.textSize") }),
    el("div", { class: "seg" },
      el("button", { type: "button", text: "A−", "aria-label": "smaller", onclick: () => setChatSize(st.chatSize - 1) }),
      el("span", { text: `${st.chatSize} px` }),
      el("button", { type: "button", text: "A+", "aria-label": "larger", onclick: () => setChatSize(st.chatSize + 1) })),
    el("span", { class: "hint-keys", text: "⌘ − / ⌘ +" }));
  const voice = el("div", { class: "control" }, el("span", { class: "kicker", text: t("details.thinkMode") }),
    el("div", { class: "seg", role: "group", "aria-label": t("details.thinkMode") }, THINK_MODES.map((m) =>
      el("button", { type: "button", class: st.thinkMode === m ? "on" : null, "aria-pressed": String(st.thinkMode === m),
        text: t(`think.modes.${m}`), onclick: () => setThinkMode(m) }))));
  box.replaceChildren(grid, el("div", { class: "controls" }, voice, size));
}

function setChatSize(px) {
  st.chatSize = Math.max(CHAT_SIZE.min, Math.min(CHAT_SIZE.max, px));
  document.documentElement.style.setProperty("--l-chat", `${st.chatSize}px`);
  try { localStorage.setItem(CHAT_SIZE.store, String(st.chatSize)); } catch (e) { /* no storage — the size lasts this window */ }
  if (st.detailsOpen) renderDetails();
}

/* ---------- connection banners + screens ---------- */
function renderBanner() {
  const box = $("banner");
  if (st.conn === "connecting" || st.conn === "retrying") {
    box.replaceChildren(el("div", { class: "banner connecting", role: "status" },
      el("span", { class: "title", text: st.conn === "retrying" ? t("banner.retrying") : t("banner.connecting") }),
      el("span", { class: "bar" })));
  } else if (st.conn === "offline") {
    box.replaceChildren(el("div", { class: "banner offline", role: "alert" }, icon("wifi-off"),
      el("div", { class: "words" }, el("span", { class: "title", text: t("banner.offline.title") }),
        el("span", { class: "body", text: t("banner.offline.body", { n: st.countdown }) })),
      el("button", { type: "button", class: "btn btn-secondary", text: t("banner.offline.retry"), onclick: () => st.wake && st.wake() })));
  } else if (st.restored !== null) {
    const kids = [el("span", { class: "sq" }), el("span", { class: "title", text: t("banner.restored.title") })];
    kids.push(el("span", { class: "body", text: st.restored ? t("banner.restored.body", { n: st.restored }) : "" }));
    kids.push(el("button", { type: "button", class: "close", "aria-label": t("banner.close"), onclick: () => { st.restored = null; renderBanner(); } }, icon("x")));
    box.replaceChildren(el("div", { class: "banner restored", role: "status" }, ...kids));
  } else {
    box.replaceChildren();
  }
  renderInput();
  renderStatus();
}

function showScreen(kind, detail) {
  const which = kind === "no_server" ? "auth.noServer" : "auth.refused";
  const block = S[which] || {};
  const steps = (block.steps || []).map((s, i) => el("div", { class: "step" }, el("span", { class: "n", text: String(i + 1) }),
    el("div", {}, el("p", { text: s.text }), s.code ? el("pre", { text: s.code }) : null)));
  const facts = el("dl", { class: "facts" });
  if (kind !== "no_server") {
    facts.append(el("dt", { text: block.facts ? block.facts.response : "" }), el("dd", { text: detail || "401" }),
      el("dt", { text: block.facts ? block.facts.lastTry : "" }), el("dd", { text: clock() }));
  }
  const screen = $("screen");
  screen.replaceChildren(
    el("div", { class: "left" }, el("span", { class: "kicker acc", text: block.kicker || "" }), el("h1", { text: block.title || "" }),
      el("p", { class: "lead", text: block.body || "" }),
      el("div", { class: "actions" }, el("button", { type: "button", class: "btn btn-primary", onclick: () => { hideScreen(); connect(); } },
        icon("refresh-cw"), block.primary || ""))),
    el("div", { class: "right" }, el("span", { class: "kicker", text: block.stepsTitle || "" }), ...steps,
      kind !== "no_server" ? facts : null));
  screen.hidden = false;
  $("list").hidden = true;
  $("composer").hidden = true;
  st.conn = "stopped";
  $("banner").replaceChildren();
  renderStatus();
}

function hideScreen() {
  $("screen").hidden = true;
  $("list").hidden = false;
  $("composer").hidden = false;
}

/* ---------- the push channel ---------- */
let listening = false;

async function connect() {
  if (listening) return;
  listening = true;
  st.conn = st.everOnline ? "retrying" : "connecting";
  renderBanner();
  try {
    absorb(await api("/v1/state"));
  } catch (e) {
    if (e.kind === "token_refused" || e.kind === "no_server") { listening = false; showScreen(e.kind, e.message); return; }
  }
  for (;;) {
    let opened = false;
    const ctrl = new AbortController();
    let watchdog = null;
    const pet = () => { clearTimeout(watchdog); watchdog = setTimeout(() => ctrl.abort(), DEAD_STREAM_MS); };
    try {
      const h = headers();
      if (st.lastEventId) h["Last-Event-ID"] = st.lastEventId;
      pet();
      const res = await fetch("/v1/events", { headers: h, signal: ctrl.signal });
      if (res.status === 401 || res.status === 503) {
        const data = await res.json().catch(() => ({}));
        if (data.kind === "token_refused" || data.kind === "no_server") { clearTimeout(watchdog); listening = false; showScreen(data.kind, data.detail); return; }
      }
      if (!res.ok) throw new ApiError(res.status, "", "");
      for await (const frame of sse(res, pet)) {
        if (!opened) { opened = true; online(); }
        onPush(frame.event, frame.data);
        if (frame.id) st.lastEventId = frame.id;
      }
    } catch (e) { /* a drop, a dead stream, the server down — the loop reconnects */ }
    clearTimeout(watchdog);
    offline();
    await backoff();
  }
}

function online() {
  const wasOffline = st.conn === "offline" || st.conn === "retrying";
  st.retryIdx = 0;
  st.countdown = 0;
  st.conn = "online";
  st.replaying = true;
  st.missed = [];
  st.missedDivider = null;
  st.collectMissed = wasOffline && st.everOnline;
  armReplay(1500);
  renderBanner();
}

function armReplay(ms) {
  clearTimeout(st.replayTimer);
  st.replayTimer = setTimeout(endReplay, ms);
}

function endReplay() {
  st.replaying = false;
  if (st.collectMissed) {
    st.restored = st.missed.length;
    st.collectMissed = false;
    renderBanner();
  }
  st.everOnline = true;
  renderEmpty();
  drainQueue();
}

function offline() {
  if (st.conn === "stopped") return;
  st.conn = "offline";
  renderBanner();
}

function backoff() {
  const secs = BACKOFF_S[Math.min(st.retryIdx, BACKOFF_S.length - 1)];
  st.retryIdx++;
  st.countdown = secs;
  renderBanner();
  return new Promise((resolve) => {
    const tick = setInterval(() => {
      st.countdown--;
      if (st.countdown <= 0) done();
      else renderBanner();
    }, 1000);
    function done() { clearInterval(tick); st.wake = null; st.countdown = 0; st.conn = "retrying"; renderBanner(); resolve(); }
    st.wake = done;
  });
}

function onPush(event, data) {
  if (event === "state") { absorb(data.state); return; }
  if (event === "turn") { showFace(data.theme ?? st.server.theme, data.emotion, data.intensity); return; } // any client's turn
  if (event === "thought") { if (st.replaying) armReplay(REPLAY_QUIET_MS); onThought(data); return; }
  if (event !== "message") return;
  if (data.origin && data.origin === CLIENT_ID) return; // our own line — shown from the response
  if (st.replaying) armReplay(REPLAY_QUIET_MS);
  if (st.collectMissed && data.replay) {  // only what the server replays counts as missed
    if (!st.missedDivider) st.missedDivider = addDivider(t("divider.missed", { n: 0 }), true);
    st.missed.push(data);
    st.missedDivider.querySelector(".kicker").textContent = t("divider.missed", { n: st.missed.length });
  }
  const time = data.replay ? undefined : clock();  // a replayed line's moment isn't known
  if (data.role === "user") addMine(String(data.text || ""), { time, source: data.source, origin: data.origin });
  else addHers(String(data.text || ""), { time, source: data.source, origin: data.origin, first: data.source === "thought" || data.source === "nudge",
    emotion: data.emotion, intensity: data.intensity });
}

/* ---------- turns, commands, directives ---------- */
function setAnswering(on) {
  st.answering = on;
  renderInput();
  renderStatus();
}

async function sendLine(text, queuedRow) {
  setAnswering(true);
  try {
    if (text.startsWith("/")) await slashCommand(text, queuedRow);
    else if (text.startsWith("%")) await directive(text, queuedRow);
    else await turn(text, queuedRow);
  } finally {
    setAnswering(false);
    drainQueue();
  }
}

function settleMine(text, queuedRow) {
  if (queuedRow) {
    queuedRow.node.classList.remove("queued");
    const time = queuedRow.node.querySelector(".time");
    if (time) time.textContent = clock();
    return queuedRow;
  }
  return addMine(text, { time: clock() });
}

async function turn(text, queuedRow, waited = false) {
  const mine = settleMine(text, queuedRow);
  const busy = async () => { // another client holds her: both rows go, the line is asked again in a moment —
    mine.node.remove(); her.node.remove(); // the note once per line, however long she stays busy (review #2)
    if (!waited) addNote(t("note.busy"));
    await sleep(3000);
    return turn(text, null, true);
  };
  const her = addHers(null, { time: clock() });
  her.think = thinkBlock(her);  // v2.12.2: her inner voice fills the wait
  const waiting = el("div", { class: "thinking" }, el("i"), el("i"), el("i"), el("span", { text: t("thinking") }));
  her.body.append(waiting);
  stick(true);
  const body = { text, turn_id: uid() };
  let result = null;
  try {
    if (st.server.stream_enabled) result = await streamTurn(body, her, waiting);
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) return busy();
    if (e instanceof ApiError && (e.kind === "token_refused" || e.kind === "no_server")) { showScreen(e.kind, e.message); return; }
    if (e instanceof ApiError && e.kind === "turn_error") { brokenReply(her, text, e.message); return; }
    /* a broken stream — the same turn_id the blocking way below */
  }
  if (!result) {
    try {
      result = await api("/v1/turn", body);
    } catch (e) {
      if (e.status === 409) return busy();
      if (e.kind === "token_refused" || e.kind === "no_server") { showScreen(e.kind, e.message); return; }
      brokenReply(her, text, e.message);
      return;
    }
  }
  her.body.replaceChildren(...markdown(result.reply));
  if (!her.think.text && result.thinking) setThink(her.think, String(result.thinking));  // nothing streamed: the whole of it
  if (result.state) absorb(result.state);
  showFace((result.state && result.state.theme) ?? st.server.theme, result.emotion, result.intensity);
  setEmotion(her.node, result.emotion, result.intensity);
  stick();
}

async function streamTurn(body, her, waiting) {
  const res = await fetch("/v1/turn/stream", { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify(body) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.kind, typeof data.detail === "string" ? data.detail : "");
  }
  let text = "", streamed = null;
  const caret = el("span", { class: "caret", "aria-hidden": "true" });
  for await (const frame of sse(res)) {
    if (frame.event === "delta") {
      if (!streamed) { streamed = el("div", { class: "streaming" }); waiting.replaceWith(streamed); }
      text += String(frame.data.text || "");
      streamed.textContent = text;
      streamed.append(caret);
      stick();
    } else if (frame.event === "think") {
      setThink(her.think, her.think.text + String(frame.data.text || ""));  // v2.12.2: her inner voice as it comes
      stick();
    } else if (frame.event === "done") {
      return frame.data;
    } else if (frame.event === "error") {
      throw new ApiError(500, "turn_error", String(frame.data.detail || ""));
    }
  }
  return null; // the stream ended without an outcome — the caller asks the same turn_id the blocking way
}

function brokenReply(her, text, detail) {
  her.body.replaceChildren(el("div", { class: "broken", title: detail || "" }, el("span", { text: t("reply.broken") }),
    el("button", { type: "button", text: t("reply.retry"), onclick: () => submit(text) })));
}

async function slashCommand(line, queuedRow) {
  if (queuedRow) queuedRow.node.remove();
  const name = line.split(/\s+/, 1)[0];
  const arg = line.slice(name.length).trim();
  if (name === "/new") {
    try {
      const res = await api("/v1/session/new", {});
      if (res.state) absorb(res.state);
      addDivider(t("divider.session", { weekday: weekday(), date: dayMonth(), time: clock() }));
    } catch (e) { addNote(t("note.failed", { detail: e.message })); }
    return;
  }
  if (name === "/web" || name === "/search" || name === "/w") {
    if (!arg) { addNote(t("note.webEmpty")); return; }
    await turn(arg, null);
    return;
  }
  if (name === "/image") { addNote(t("note.image")); return; }
  await command(line, false);
}

async function command(line, confirmed) {
  let res;
  try {
    res = await api("/v1/command", { line, confirmed });
  } catch (e) {
    if (e.kind === "token_refused" || e.kind === "no_server") { showScreen(e.kind, e.message); return; }
    addNote(t("note.failed", { detail: e.message }));
    return;
  }
  if (res.state) absorb(res.state);
  if (!res.handled) { addNote(t("note.unknownCommand", { cmd: line.split(/\s+/, 1)[0] })); return; }
  const result = res.result || {};
  if (result.confirm) {
    if (window.confirm(result.confirm)) await command(line, true);
    else addNote(t("note.cancelled"));
    return;
  }
  if (result.turn) { await turn(result.turn, null); return; }
  addCommand(line, result);
}

async function directive(line, queuedRow) {
  if (queuedRow) queuedRow.node.remove();
  try {
    const res = await api("/v1/directive", { line });
    if (res.state) absorb(res.state);
    const thought = res.thought;
    addCommand(line, { text: thought ? thought.text : t("note.directive", { kind: line.split(/\s+/, 1)[0], mode: res.mode || "" }) });
  } catch (e) {
    if (e.kind === "token_refused" || e.kind === "no_server") { showScreen(e.kind, e.message); return; }
    addNote(t("note.failed", { detail: e.message }));
  }
}

/* ---------- the input ---------- */
function submit(text) {
  const line = String(text || "").trim();
  if (!line) return;
  if (st.answering || st.replaying || st.conn !== "online") {
    const r = line.startsWith("/") || line.startsWith("%") ? { node: el("div"), content: null } : addMine(line, { time: "", queued: true });
    st.queue.push({ text: line, row: r });
    renderInput();
    return;
  }
  sendLine(line, null);
}

function drainQueue() {
  if (st.answering || st.replaying || st.conn !== "online" || !st.queue.length) { renderInput(); return; }
  const next = st.queue.shift();
  renderInput();
  sendLine(next.text, next.row.content ? next.row : null);
}

function renderInput() {
  const input = $("input");
  const offline = st.conn !== "online";
  input.disabled = offline;
  input.placeholder = offline ? t("input.placeholder.offline") : st.answering ? t("input.placeholder.answering") : t("input.placeholder");
  $("send").disabled = offline || !input.value.trim();
  const count = $("queue-count");
  count.hidden = !st.queue.length;
  count.textContent = String(st.queue.length);
  const hints = $("hints");
  const keys = (k, rest) => [el("b", { text: k }), rest];
  const parts = [
    el("span", {}, ...keys("Enter", t("input.hint.enter").replace(/^Enter/, ""))),
    el("span", {}, ...keys("Shift + Enter", t("input.hint.shift").replace(/^Shift \+ Enter/, ""))),
    el("span", {}, ...keys("/", t("input.hint.slash").replace(/^\//, ""))),
  ];
  if (st.queue.length) parts.push(el("span", { class: "queued", text: t("queue.count", { n: st.queue.length }) }));
  hints.replaceChildren(...parts); // a null passed to replaceChildren would print as text
}

function grow() {
  const input = $("input");
  input.style.height = "auto";
  const line = parseFloat(getComputedStyle(input).lineHeight) || 22;
  input.style.height = `${Math.min(input.scrollHeight + 2, line * 6 + 24)}px`;
}

/* the slash popover */
function slashItems(text) {
  if (!text.startsWith("/") || /\s/.test(text)) return [];
  return Object.entries(S.commands || {}).filter(([name]) => name.startsWith(text));
}

function renderSlash() {
  const box = $("slash");
  const sl = st.slash;
  if (!sl.open || !sl.items.length) { box.hidden = true; box.replaceChildren(); return; }
  box.hidden = false;
  box.replaceChildren(el("div", { class: "slash-head" }, el("span", { class: "kicker", text: t("slash.title") }),
    el("span", { class: "help", text: t("slash.help") })),
  ...sl.items.map(([name, desc], i) => el("div", { class: "slash-row", role: "option", "aria-selected": String(i === sl.index),
    onmouseenter: () => { sl.index = i; renderSlash(); }, onmousedown: (ev) => { ev.preventDefault(); insertCommand(name); } },
  el("span", { class: "name", text: name }), el("span", { text: desc }), el("span", { class: "tab", text: "Tab" }))));
}

function updateSlash() {
  const text = $("input").value;
  const items = slashItems(text);
  const sl = st.slash;
  if (!text.startsWith("/")) sl.dismissed = false;
  sl.items = items;
  sl.open = items.length > 0 && !sl.dismissed;
  if (sl.index >= items.length) sl.index = 0;
  renderSlash();
}

function insertCommand(name) {
  const input = $("input");
  input.value = `${name} `;
  st.slash.open = false;
  st.slash.dismissed = true;
  renderSlash();
  renderInput();
  input.focus();
}

function onKey(ev) {
  const sl = st.slash;
  const input = $("input");
  if (sl.open && sl.items.length) {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      const n = sl.items.length;
      sl.index = (sl.index + (ev.key === "ArrowDown" ? 1 : -1) + n) % n;
      renderSlash();
      return;
    }
    if (ev.key === "Escape") { ev.preventDefault(); sl.open = false; sl.dismissed = true; renderSlash(); return; }
    if (ev.key === "Tab") { ev.preventDefault(); insertCommand(sl.items[sl.index][0]); return; }
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
      const exact = sl.items.some(([name]) => name === input.value.trim());
      if (!exact) { ev.preventDefault(); insertCommand(sl.items[sl.index][0]); return; }
    }
  }
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    const text = input.value;
    input.value = "";
    grow();
    updateSlash();
    submit(text);
  }
}

/* ---------- start ---------- */
async function start() {
  try { S = await (await fetch("strings.en.json")).json(); } catch (e) { S = {}; }
  try { st.emoji = await (await fetch("/emoji", { headers: headers() })).json(); } catch (e) { st.emoji = {}; }
  document.title = t("window.title");
  try {
    const saved = parseInt(localStorage.getItem(CHAT_SIZE.store), 10);
    st.chatSize = Number.isFinite(saved) ? saved : CHAT_SIZE.default;
  } catch (e) { st.chatSize = CHAT_SIZE.default; }
  setChatSize(st.chatSize);
  try {
    const kept = localStorage.getItem(THINK_STORE);
    if (THINK_MODES.includes(kept)) st.thinkMode = kept;
  } catch (e) { /* none kept — the server's think_show gives the first default */ }
  const input = $("input");
  $("input-label").textContent = t("input.label");
  input.setAttribute("aria-label", t("input.label"));
  const send = $("send");
  send.setAttribute("aria-label", t("input.send"));
  send.append(icon("arrow-up"));
  $("composer").addEventListener("submit", (ev) => { ev.preventDefault(); const text = input.value; input.value = ""; grow(); updateSlash(); submit(text); });
  input.addEventListener("keydown", onKey);
  input.addEventListener("input", () => { grow(); updateSlash(); renderInput(); });
  input.addEventListener("blur", () => { st.slash.open = false; renderSlash(); });
  document.addEventListener("keydown", (ev) => {
    if (!ev.metaKey) return;
    if (ev.key === "-") { ev.preventDefault(); setChatSize(st.chatSize - 1); }
    else if (ev.key === "=" || ev.key === "+") { ev.preventDefault(); setChatSize(st.chatSize + 1); }
  });
  NARROW.addEventListener("change", () => { renderPanel(); stick(true); });  // a new width: back to the newest line
  renderPanel();
  renderInput();
  renderStatus();
  input.focus();
  connect();
}

start();
