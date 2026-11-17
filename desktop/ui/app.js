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
  portraitShown: true,  // LUMI-277: the owner can hide her portrait (kept locally)
};
const THINK_STORE = "lumi.desktop.thinkMode";
const PORTRAIT_STORE = "lumi.desktop.portrait";
const THINK_MODES = ["shown", "collapsed", "hidden"];
const THINK_FROM_SERVER = { open: "shown", debug: "collapsed", off: "hidden" };  // the state's think_show → a first default

/* ---------- small helpers ---------- */
const $ = (id) => document.getElementById(id);

function lookup(key) {  // "a.b.c": the longest flat prefix the strings have ("think.modes"), then walk the rest
  const parts = key.split(".");
  for (let i = parts.length; i >= 1; i--) {
    const head = parts.slice(0, i).join(".");
    if (!(head in S)) continue;
    let v = S[head];
    for (const k of parts.slice(i)) v = v && typeof v === "object" && k in v ? v[k] : undefined;
    if (typeof v === "string") return v;
  }
  return undefined;
}

function t(key, vars) {
  const v = lookup(key);
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
  // v4.1: the views
  "panel-right": [["rect", { x: "3", y: "3", width: "18", height: "18" }], ["path", { d: "M15 3v18" }]],
  "pin-right": [["rect", { x: "3", y: "3", width: "18", height: "18" }], ["rect", { x: "15", y: "3", width: "6", height: "18", fill: "currentColor", stroke: "none" }]],
  "pin-bottom": [["rect", { x: "3", y: "3", width: "18", height: "18" }], ["path", { d: "M15 3v18" }], ["path", { d: "M15 12h6" }],
    ["rect", { x: "15", y: "12", width: "6", height: "9", fill: "currentColor", stroke: "none" }]],
  "chevrons-right": [["path", { d: "m6 17 5-5-5-5" }], ["path", { d: "m13 17 5-5-5-5" }]],
  "circle-alert": [["circle", { cx: "12", cy: "12", r: "10" }], ["path", { d: "M12 8v4" }], ["path", { d: "M12 16h.01" }]],
  folder: [["path", { d: "M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" }]],
  "file-text": [["path", { d: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" }], ["path", { d: "M14 2v4a2 2 0 0 0 2 2h4" }],
    ["path", { d: "M10 9H8" }], ["path", { d: "M16 13H8" }], ["path", { d: "M16 17H8" }]],
  file: [["path", { d: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" }], ["path", { d: "M14 2v4a2 2 0 0 0 2 2h4" }]],
  image: [["rect", { x: "3", y: "3", width: "18", height: "18" }], ["circle", { cx: "9", cy: "9", r: "2" }], ["path", { d: "m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" }]],
  "arrow-left": [["path", { d: "m12 19-7-7 7-7" }], ["path", { d: "M19 12H5" }]],
  "arrow-right": [["path", { d: "M5 12h14" }], ["path", { d: "m12 5 7 7-7 7" }]],
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
  if (vw.view !== "chat" && !vw.chatUnread) { vw.chatUnread = true; renderViews(); }  // v4.1: seen on return
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
  const pic = st.portraitShown;  // LUMI-277: hidden → the panel without the picture
  if (!narrow) {
    if (pic) kids.push(portraitBox());
    kids.push(el("div", { class: "caption-block" }, caption, empty ? null : metaGrid(s)));
    if (!empty && s.mood) kids.push(moodBlock(s.mood));
  } else {
    const thumb = el("button", { type: "button", class: "thumb", "aria-expanded": String(panel.expanded),
      "aria-label": t(panel.expanded ? "narrow.portrait.hide" : "narrow.portrait.show"),
      onclick: () => { panel.expanded = !panel.expanded; renderPanel(); } });
    if (!panel.expanded) thumb.append(portraitBox());
    kids.push(el("div", { class: "strip-row" }, pic ? thumb : null,
      el("div", { class: "strip-words" }, caption, empty ? null : el("span", { class: "strip-meta", text: metaLine(s) })),
      el("div", { id: "thoughts-button" })));
    if (pic && panel.expanded) {
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
    when: data.when, unread: !data.replay || st.collectMissed });  // history on the first connect; missed after a reconnect (review #2)
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
  syncServed();  // code review #3: the views the server serves (features) — re-rendered only when that set changes
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
  const portrait = el("div", { class: "control" }, el("span", { class: "kicker", text: t("details.portrait") }),
    el("div", { class: "seg", role: "group", "aria-label": t("details.portrait") }, ["shown", "hidden"].map((m) => {
      const on = (m === "shown") === st.portraitShown;
      return el("button", { type: "button", class: on ? "on" : null, "aria-pressed": String(on), text: t(`portrait.modes.${m}`),
        onclick: () => setPortraitShown(m === "shown") });
    })));
  box.replaceChildren(grid, el("div", { class: "controls" }, portrait, voice, size));
}

function setPortraitShown(shown) {  // LUMI-277: her picture on or off; everything else in the panel stays
  st.portraitShown = Boolean(shown);
  try { localStorage.setItem(PORTRAIT_STORE, st.portraitShown ? "shown" : "hidden"); } catch (e) { /* lasts this window */ }
  renderPanel();
  if (st.detailsOpen) renderDetails();
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
  $("views").hidden = true;
  $("viewhost").hidden = true;
  $("side").hidden = true;
  $("rail").hidden = true;
  st.conn = "stopped";
  $("banner").replaceChildren();
  renderStatus();
}

function hideScreen() {
  $("screen").hidden = true;
  $("views").hidden = false;
  applyView();
  renderSide();
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

/* ---------- the views (v4.1, design/v4/handoff §1–§3): a row of views over the content column, right panels ----------
   A view replaces the message list and the input (the banner stays); the push channel keeps running underneath, and a
   line that arrives meanwhile marks Chat. Any view but Chat can be pinned to a right panel and/or a bottom-right one
   beside the chat (wide layout only); a pinned view always takes its narrow layout. The pins, the active tabs, the
   width, the split and the folded state are kept per client in localStorage. Each place keeps its own view state. */
const VIEWS = ["chat", "files"];  // v4.1 — later phases add gallery, journal, thoughts, state, schedule
const VIEW_LABEL = { chat: "views.chat", files: "views.files" };
const VIEW_KINDS = {};  // kind → render(ctx): each view registers itself below
const PANELS_STORE = "lumi.desktop.panels";
// the owner (2026-11-17): the panel widens as far as the window allows — the chat keeps at least chatMin
const SIDE = { min: 280, default: 340, step: 20, chatMin: 360 };
const SPLIT = { min: 0.2, max: 0.8, default: 0.55, step: 0.05 };
const VIEW_NARROW_PX = 640;
const vw = { view: "chat", chatUnread: false, r: [], b: [], rActive: null, bActive: null, open: true,
  w: SIDE.default, split: SPLIT.default };
const places = { main: { states: {} }, r: { states: {} }, b: { states: {} } };  // per place: each kind's own state
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

function loadPanels() {
  try {
    const kept = JSON.parse(localStorage.getItem(PANELS_STORE) || "null");
    if (!kept || typeof kept !== "object") return;
    const pinnable = (list) => (Array.isArray(list) ? list.filter((k) => VIEWS.includes(k) && k !== "chat") : []);
    vw.r = pinnable(kept.r);
    vw.b = pinnable(kept.b).filter((k) => !vw.r.includes(k));
    vw.rActive = vw.r.includes(kept.rActive) ? kept.rActive : vw.r[0] || null;
    vw.bActive = vw.b.includes(kept.bActive) ? kept.bActive : vw.b[0] || null;
    vw.open = kept.open !== false;
    if (Number.isFinite(kept.w)) vw.w = Math.max(SIDE.min, Math.round(kept.w));  // the window caps it when drawn
    if (Number.isFinite(kept.split)) vw.split = clamp(kept.split, SPLIT.min, SPLIT.max);
  } catch (e) { /* none kept — no pins, the defaults */ }
}

function savePanels() {
  const { r, b, rActive, bActive, open, w, split } = vw;
  try { localStorage.setItem(PANELS_STORE, JSON.stringify({ r, b, rActive, bActive, open, w, split })); } catch (e) { /* lasts this window */ }
}

function served(kind) {  // code review #3: a view shows only when the server says it serves it (state.features)
  if (kind === "chat") return true;
  const features = (st.server && st.server.features) || {};
  return kind === "files" ? features.files === true : false;
}

function shownViews() { return VIEWS.filter(served); }

let servedKey = null;
function syncServed() {  // a state push follows every turn: re-render the row and the panels only on a change
  const key = shownViews().join(",");
  if (key === servedKey) return;
  servedKey = key;
  if (!served(vw.view)) setView("chat"); else renderViews();
  renderSide();
}

function viewName(kind) { return t(VIEW_LABEL[kind] || "views.chat"); }

function setView(kind) {
  if (!VIEWS.includes(kind) || !served(kind)) return;
  vw.view = kind;
  if (kind === "chat") vw.chatUnread = false;
  renderViews();
  applyView();
}

function applyView() {  // which of the chat or the open view fills the content column
  if (!$("screen").hidden) return;  // the auth screen owns the column
  const chat = vw.view === "chat";
  $("list").hidden = !chat;
  $("composer").hidden = !chat;
  const host = $("viewhost");
  host.hidden = chat;
  if (chat) {
    const watch = widthWatch.get(host);
    if (watch) { watch.disconnect(); widthWatch.delete(host); }
    releaseBlobs(host);
    places.main.ctx = null;
    host.replaceChildren();
    stick(true);
    $("input").focus();
    return;
  }
  mountView("main", host, vw.view);
}

function pin(kind, slot) {  // a view lives in one panel at a time; pressing again unpins it
  const mine = slot === "r" ? vw.r : vw.b;
  const other = slot === "r" ? vw.b : vw.r;
  if (mine.includes(kind)) {
    mine.splice(mine.indexOf(kind), 1);
  } else {
    if (other.includes(kind)) other.splice(other.indexOf(kind), 1);
    mine.push(kind);
    vw.open = true;
  }
  if (slot === "r") vw.rActive = mine.includes(kind) ? kind : vw.r[0] || null;
  else vw.bActive = mine.includes(kind) ? kind : vw.b[0] || null;
  if (!vw.r.includes(vw.rActive)) vw.rActive = vw.r[0] || null;
  if (!vw.b.includes(vw.bActive)) vw.bActive = vw.b[0] || null;
  savePanels();
  renderViews();
  renderSide();
}

function tab(kind, current, onclick, n) {
  const kids = [viewName(kind)];
  if (kind === "chat" && vw.chatUnread && vw.view !== "chat") {
    kids.push(el("span", { class: "unread", role: "img", "aria-label": t("views.chat_unread") }));
  }
  return el("button", { type: "button", class: "vtab", "aria-current": current ? "page" : "false",
    title: n ? `${viewName(kind)}  ${t("views.shortcut", { n })}` : null, onclick }, ...kids);
}

function renderViews() {
  const nav = $("views");
  nav.setAttribute("aria-label", t("views.label"));
  const tabs = el("div", { class: "views-tabs" },
    shownViews().map((kind, i) => tab(kind, kind === vw.view, () => setView(kind), i + 1)));
  const kids = [tabs];
  if (vw.view !== "chat" && !NARROW.matches) {
    const name = viewName(vw.view);
    kids.push(el("div", { class: "pins", role: "group", "aria-label": t("panels.pin_this") },
      el("span", { class: "kicker", text: t("panels.pin") }),
      el("button", { type: "button", class: "ib", "aria-pressed": String(vw.r.includes(vw.view)),
        "aria-label": vw.r.includes(vw.view) ? t("panels.unpin_right") : t("panels.pin_right", { view: name }),
        title: t("panels.right"), onclick: () => pin(vw.view, "r") }, icon("pin-right")),
      el("button", { type: "button", class: "ib", "aria-pressed": String(vw.b.includes(vw.view)),
        "aria-label": vw.b.includes(vw.view) ? t("panels.unpin_bottom") : t("panels.pin_bottom", { view: name }),
        title: t("panels.bottom_right"), onclick: () => pin(vw.view, "b") }, icon("pin-bottom"))));
  }
  nav.replaceChildren(...kids);
}

function dragOn(handle, onMove, onEnd) {  // a pointer drag on a handle (mouse or pen)
  handle.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    handle.setPointerCapture(ev.pointerId);
    const move = (e) => onMove(e);
    const up = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); onEnd(); };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  });
}

function sideMax() {  // the window's width less the portrait panel and the chat's minimum
  const body = document.querySelector(".body");
  const portrait = $("panel");
  const total = body ? body.clientWidth : window.innerWidth;
  return Math.max(SIDE.min, total - (portrait ? portrait.offsetWidth : 0) - SIDE.chatMin);
}

function applySideWidth() { $("side").style.width = `${clamp(vw.w, SIDE.min, sideMax())}px`; }

function setSideWidth(w) { vw.w = clamp(Math.round(w), SIDE.min, sideMax()); applySideWidth(); }

function sidePanel(slot, both, list) {
  const kept = slot === "r" ? vw.rActive : vw.bActive;
  const active = list.includes(kept) ? kept : list[0];
  const label = slot === "r" ? t("panels.right") : t("panels.bottom_right");
  const head = el("div", { class: "shead" },
    el("div", { class: "stabs", role: "group", "aria-label": t("panels.pinned", { panel: label }) },
      list.map((kind) => tab(kind, kind === active, () => {
        if (slot === "r") vw.rActive = kind; else vw.bActive = kind;
        savePanels();
        renderSide();
      }))),
    el("button", { type: "button", class: "ib", "aria-label": t("panels.unpin", { view: viewName(active) }),
      onclick: () => pin(active, slot) }, icon("x")),
    slot === "r" ? el("button", { type: "button", class: "ib", "aria-label": t("panels.collapse"), title: t("panels.collapse"),
      onclick: () => { vw.open = false; savePanels(); renderSide(); } }, icon("chevrons-right")) : null);
  const body = el("div", { class: "sbody" });
  const section = el("section", { class: "spanel", "aria-label": label, "data-slot": slot }, head, body);
  section.style.flex = both ? `${slot === "r" ? vw.split : 1 - vw.split} 1 0` : "1 1 0";
  if (both && slot === "b") {
    // code review #1: the split moves in place — rebuilding the column would re-mount (and refetch) both views on
    // every move and drop the handle that holds the pointer capture
    const split = el("button", { type: "button", class: "split", "aria-label": t("panels.resize_split") });
    const side = $("side");
    dragOn(split, (e) => {
      const box = side.getBoundingClientRect();
      vw.split = clamp((e.clientY - box.top) / box.height, SPLIT.min, SPLIT.max);
      applySplit();
    }, savePanels);
    split.addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown") return;
      ev.preventDefault();
      vw.split = clamp(vw.split + (ev.key === "ArrowUp" ? -SPLIT.step : SPLIT.step), SPLIT.min, SPLIT.max);
      applySplit();
      savePanels();
    });
    section.prepend(split);
  }
  mountView(slot, body, active, true);
  return section;
}

function applySplit() {  // the two panels' shares from vw.split, on the sections already there
  const top = $("side").querySelector('.spanel[data-slot="r"]');
  const bottom = $("side").querySelector('.spanel[data-slot="b"]');
  if (top && bottom) { top.style.flex = `${vw.split} 1 0`; bottom.style.flex = `${1 - vw.split} 1 0`; }
}

function renderSide() {
  const side = $("side");
  const rail = $("rail");
  const rs = vw.r.filter(served);  // a pinned view the server no longer serves stays pinned but unshown
  const bs = vw.b.filter(served);
  const pinned = rs.length + bs.length > 0;
  if (!pinned || NARROW.matches) { side.hidden = true; rail.hidden = true; side.replaceChildren(); return; }
  if (!vw.open) {
    side.hidden = true;
    side.replaceChildren();
    rail.hidden = false;
    rail.replaceChildren(
      el("button", { type: "button", class: "ib", "aria-label": t("panels.open"), title: t("panels.open"),
        onclick: () => { vw.open = true; savePanels(); renderSide(); } }, icon("panel-right")),
      el("span", { class: "kicker", text: [...rs, ...bs].map(viewName).join(" · ") }));
    return;
  }
  rail.hidden = true;
  side.hidden = false;
  side.querySelectorAll(".sbody").forEach(releaseBlobs);  // the old panel bodies' pictures go with them
  side.setAttribute("aria-label", t("panels.label"));
  applySideWidth();
  const grip = el("button", { type: "button", class: "grip", "aria-label": t("panels.resize_width") });
  let x0 = 0, w0 = 0;
  grip.addEventListener("pointerdown", (ev) => { x0 = ev.clientX; w0 = vw.w; });
  dragOn(grip, (e) => setSideWidth(w0 + (x0 - e.clientX)), savePanels);
  grip.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    ev.preventDefault();
    setSideWidth(vw.w + (ev.key === "ArrowLeft" ? SIDE.step : -SIDE.step));
    savePanels();
  });
  const both = rs.length > 0 && bs.length > 0;
  side.replaceChildren(grip, ...(rs.length ? [sidePanel("r", both, rs)] : []), ...(bs.length ? [sidePanel("b", both, bs)] : []));
}

/* A view mounts into a host with its context: the place, its own state, the narrow test, and a way to open another
   view in the same place (navigation inside a panel stays in that panel). */
function mountView(place, host, kind, forceNarrow) {
  const render = VIEW_KINDS[kind];
  if (!render) { host.replaceChildren(); return; }
  const states = places[place].states;
  if (!states[kind]) states[kind] = {};
  const ctx = {
    place, host, kind, state: states[kind],
    narrow: () => forceNarrow || host.clientWidth < VIEW_NARROW_PX,
    rerender: () => render(ctx),
    open: (other, init) => {
      if (init) states[other] = Object.assign(states[other] || {}, init);
      if (place === "main") { setView(other); return; }
      const list = place === "r" ? vw.r : vw.b;
      if (!list.includes(other)) { pin(other, place); return; }
      if (place === "r") vw.rActive = other; else vw.bActive = other;
      savePanels();
      renderSide();
    },
  };
  places[place].ctx = ctx;
  host.classList.toggle("vnarrow", ctx.narrow());
  if (!forceNarrow) watchWidth(host, ctx);
  render(ctx);
}

const widthWatch = new WeakMap();
function watchWidth(host, ctx) {  // a view goes narrow below 640 px of its own width
  if (typeof ResizeObserver === "undefined") return;
  const old = widthWatch.get(host);
  if (old) old.disconnect();
  let narrow = ctx.narrow();
  const ro = new ResizeObserver(() => {
    const now = host.clientWidth < VIEW_NARROW_PX;
    if (now === narrow) return;
    narrow = now;
    host.classList.toggle("vnarrow", now);
    ctx.rerender();
  });
  ro.observe(host);
  widthWatch.set(host, ro);
}

/* the frame every view shares (§3): a header bar, a scrolling body, and the loading / empty / error states */
function viewFrame(bar, body) {
  return el("div", { class: "view" }, el("div", { class: "vbar" }, ...bar), el("div", { class: "vscroll" }, body));
}

function viewLoading(label) {
  return el("div", { class: "vstate", role: "status" },
    el("div", { class: "pulse" }, el("i"), el("i"), el("i"), el("span", { text: label })),
    el("div", { class: "skel", "aria-hidden": "true" },
      [62, 44, 71, 38, 55].map((w) => el("span", { style: `width:${w}%` }))));
}

function viewEmpty(kicker, sentence, note, action) {
  return el("div", { class: "vstate" },
    kicker ? el("span", { class: "kicker", text: kicker }) : null,
    el("p", { class: "sentence", text: sentence }),
    note ? el("p", { class: "note", text: note }) : null,
    action || null);
}

function viewError(title, err, retry) {
  const cause = err instanceof ApiError ? `${err.status} · ${err.message}` : String((err && err.message) || err || "");
  return el("div", { class: "vstate" },
    el("div", { class: "verror", role: "alert" }, icon("circle-alert"),
      el("div", {}, el("div", { class: "title", text: title }), el("div", { class: "cause", text: cause }))),
    retry ? el("button", { type: "button", class: "btn btn-secondary", onclick: retry }, icon("refresh-cw"), t("state.try_again")) : null);
}

/* ---------- Files and the reader (v4.1 LUMI-304, design/v4/handoff §4–§5) ----------
   Her sandbox, read-only: /v1/files (a folder), /v1/files/text (a text file), /v1/files/raw (a picture). One history per
   place covers folders, files and pictures (back / forward, ⌘[ ⌘]). Her markdown becomes built DOM nodes, never HTML:
   a relative link opens her other file here, an external link is shown and never followed, a link out of her sandbox
   stays text. A picture is fetched with the window's key into a blob: URL (an <img> can't send the key), released when
   the place shows something else. */
const PICTURE_EXT = /\.(png|jpe?g|gif|webp)$/i;
const MONTHS3 = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const READER_MODE = { plain: "reader.plain", code: "reader.code", data: "reader.data" };
const READER_INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(\[([^\]\n]+)\]\(([^)\s]+)\))/;
const blobsByHost = new WeakMap();

function qpath(path) { return "?path=" + encodeURIComponent(path || ""); }

function fmtSize(n) {
  if (n === null || n === undefined) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function ymdOf(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }

function fmtDate(stamp) {  // "YYYY-MM-DD HH:MM" (the server's local time) → "16 Nov 2026"
  const [y, m, d] = String(stamp || "").slice(0, 10).split("-").map(Number);
  return y && m && d ? `${d} ${MONTHS3[m - 1]} ${y}` : "";
}

function fmtWhen(stamp) {  // → "Today 14:10" / "Yesterday 09:02" / "16 Nov 2026"
  if (!stamp) return "";
  const [day, time] = String(stamp).split(" ");
  const now = new Date();
  const before = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (day === ymdOf(now)) return `${t("date.today")} ${time || ""}`.trim();
  if (day === ymdOf(before)) return `${t("date.yesterday")} ${time || ""}`.trim();
  return fmtDate(stamp);
}

function releaseBlobs(host) {
  for (const url of blobsByHost.get(host) || []) URL.revokeObjectURL(url);
  blobsByHost.set(host, []);
}

async function pictureURL(host, path) {  // the only way a picture reaches the page: fetch with the key → a blob: URL
  const res = await fetch("/v1/files/raw" + qpath(path), { headers: headers() });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.kind, typeof data.detail === "string" ? data.detail : "");
  }
  const url = URL.createObjectURL(await res.blob());
  if (!blobsByHost.has(host)) blobsByHost.set(host, []);
  blobsByHost.get(host).push(url);
  return url;
}

function resolveIn(base, href) {  // a relative href against her file's folder → a sandbox path, or null if it leaves it
  if (!href || /^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("/")) return null;
  let rel = href;
  try { rel = decodeURIComponent(href); } catch (e) { /* keep it as written */ }
  const parts = base ? base.split("/") : [];
  for (const seg of rel.split("/")) {
    if (seg === "..") { if (!parts.length) return null; parts.pop(); }
    else if (seg && seg !== ".") parts.push(seg);
  }
  return parts.join("/");
}

function slugOf(text) {
  return String(text).toLowerCase().replace(/[`*_[\]()]/g, "").trim().replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s+/g, "-");
}

function filesGo(ctx, entry) {
  const s = ctx.state;
  s.hist = s.hist.slice(0, s.idx + 1);
  s.hist.push(entry);
  s.idx = s.hist.length - 1;
  s.raw = false;
  s.outlineOpen = false;
  s.cache.clear();
  renderFilesPlace(ctx);
}

function filesStep(ctx, delta) {
  const s = ctx.state;
  const i = s.idx + delta;
  if (!s.hist || i < 0 || i >= s.hist.length) return;
  s.idx = i;
  s.outlineOpen = false;
  s.cache.clear();
  renderFilesPlace(ctx);
}

async function filesFetch(ctx, key, load) {  // a re-render (a new width, Raw ↔ Formatted) reads from here
  const cache = ctx.state.cache;
  if (cache.has(key)) return cache.get(key);
  const value = await load();
  cache.set(key, value);
  return value;
}

function openTarget(ctx, path, isDir, anchor) {
  if (isDir) filesGo(ctx, { kind: "dir", path });
  else if (PICTURE_EXT.test(path)) filesGo(ctx, { kind: "picture", path });
  else filesGo(ctx, { kind: "file", path, anchor: anchor || null });
}

function renderFilesPlace(ctx) {
  releaseBlobs(ctx.host);
  const cur = ctx.state.hist[ctx.state.idx];
  if (cur.kind === "dir") renderFolder(ctx, cur);
  else if (cur.kind === "picture") renderPicturePage(ctx, cur);
  else renderReader(ctx, cur);
}

VIEW_KINDS.files = (ctx) => {
  const s = ctx.state;
  if (!s.hist) Object.assign(s, { hist: [{ kind: "dir", path: "" }], idx: 0, raw: false, outlineOpen: false, ticket: 0,
    cache: new Map() });
  if (s.goto) { const to = s.goto; s.goto = null; filesGo(ctx, to); return; }  // opened from another view
  renderFilesPlace(ctx);
};

function navButtons(ctx) {
  const s = ctx.state;
  return [
    el("button", { type: "button", class: "ib", "aria-label": t("nav.back"), title: `${t("nav.back")}  ⌘[`,
      disabled: s.idx <= 0, onclick: () => filesStep(ctx, -1) }, icon("arrow-left")),
    el("button", { type: "button", class: "ib", "aria-label": t("nav.forward"), title: `${t("nav.forward")}  ⌘]`,
      disabled: s.idx >= s.hist.length - 1, onclick: () => filesStep(ctx, 1) }, icon("arrow-right")),
  ];
}

function crumbs(ctx, dir, fileName) {
  const parts = dir ? dir.split("/") : [];
  const nav = el("nav", { class: "crumbs", "aria-label": t("nav.path") });
  const atRoot = !fileName && !parts.length;
  nav.append(el("button", { type: "button", class: "crumb", "aria-current": atRoot ? "page" : null,
    onclick: atRoot ? null : () => filesGo(ctx, { kind: "dir", path: "" }) }, t("nav.root")));
  parts.forEach((part, i) => {
    const path = parts.slice(0, i + 1).join("/");
    const last = !fileName && i === parts.length - 1;
    nav.append(el("span", { class: "sep", text: "/" }), el("button", { type: "button", class: "crumb",
      "aria-current": last ? "page" : null, onclick: last ? null : () => filesGo(ctx, { kind: "dir", path }) }, part));
  });
  if (fileName) nav.append(el("span", { class: "sep", text: "/" }), el("span", { class: "crumb current", text: fileName }));
  return nav;
}

function itemsLabel(n) {
  return n === 1 ? t("files.item") : t("files.items", { n: Number(n || 0).toLocaleString("en-US") });
}

function fileRow(ctx, dir, e, narrow) {
  const full = dir ? `${dir}/${e.name}` : e.name;
  const isDir = e.kind === "dir";
  const shown = isDir || e.type === "text" || e.type === "image";
  const glyph = isDir ? "folder" : e.type === "image" ? "image" : e.type === "text" ? "file-text" : "file";
  const size = isDir ? itemsLabel(e.count) : fmtSize(e.size);
  const open = !shown ? null : isDir ? () => filesGo(ctx, { kind: "dir", path: full })
    : e.type === "image" ? () => filesGo(ctx, { kind: "picture", path: full, meta: e })
      : () => filesGo(ctx, { kind: "file", path: full, meta: e });
  const name = el("span", { class: "fname" + (isDir ? " dir" : "") + (shown ? "" : " muted") },
    el("span", { class: "fnm", text: isDir ? `${e.name}/` : e.name }),
    shown ? null : el("span", { class: "tag", text: t("files.not_shown") }));
  const attrs = { type: "button", class: "frow", onclick: open, "aria-disabled": shown ? null : "true",
    title: shown ? null : t("files.not_shown_hint") };
  const glyphEl = el("span", { class: "ficon" + (isDir ? " dir" : "") }, icon(glyph));
  if (narrow) {
    return el("button", attrs, glyphEl, el("span", { class: "fstack" }, name,
      el("span", { class: "fcell", text: [size, fmtWhen(e.modified)].filter(Boolean).join(" · ") })));
  }
  return el("button", attrs, el("span", { class: "fnamecell" }, glyphEl, name), el("span", { class: "fcell", text: size }),
    el("span", { class: "fcell", text: fmtDate(e.created) }), el("span", { class: "fcell", text: fmtWhen(e.modified) }));
}

async function renderFolder(ctx, cur) {
  const s = ctx.state;
  const ticket = ++s.ticket;
  const label = cur.path || t("nav.root");
  const bar = (count) => [...navButtons(ctx), crumbs(ctx, cur.path), count ? el("span", { class: "vmeta", text: count }) : null,
    el("span", { class: "vactions", "aria-hidden": "true" })];  // the right end stays free for v4.5's actions
  ctx.host.replaceChildren(viewFrame(bar(), viewLoading(t("files.loading", { path: label }))));
  let data;
  try {
    data = await filesFetch(ctx, "dir:" + cur.path, () => api("/v1/files" + qpath(cur.path)));
  } catch (err) {
    if (ticket === s.ticket) ctx.host.replaceChildren(viewFrame(bar(), viewError(t("files.error_title", { path: label }), err,
      () => renderFilesPlace(ctx))));
    return;
  }
  if (ticket !== s.ticket) return;
  const entries = data.entries || [];
  const count = data.truncated ? t("files.items_cut") : itemsLabel(entries.length);
  if (!entries.length) {
    ctx.host.replaceChildren(viewFrame(bar(count), viewEmpty(`${label}/`, t("files.empty"), t("files.empty_note"))));
    return;
  }
  const narrow = ctx.narrow();
  const table = el("div", { class: "ftable" + (narrow ? " narrow" : "") },
    narrow ? null : el("div", { class: "fhead" },
      ["files.col_name", "files.col_size", "files.col_created", "files.col_modified"].map((k) => el("span", { class: "kicker", text: t(k) }))),
    entries.map((e) => fileRow(ctx, cur.path, e, narrow)),
    data.truncated ? el("div", { class: "fcut" }, el("span", { class: "title", text: t("files.cut_title") }),
      el("span", { class: "cut-note", text: t("files.cut_note") })) : null);
  ctx.host.replaceChildren(viewFrame(bar(count), table));
}

/* her markdown for the reader: headings h1–h4, fences, rules, a picture on its own line, quotes, pipe tables, lists,
   paragraphs; inline code, bold, italic and links — everything else is literal text */
function readerLink(ctx, label, href, base) {
  if (/^https?:\/\//i.test(href)) {  // external: shown with its address, never followed in this window
    const addr = href.replace(/^https?:\/\//i, "").replace(/\/$/, "");
    return el("span", { class: "xlink", title: t("reader.external") }, el("span", { class: "xtext", text: label }), " ",
      el("span", { class: "xaddr", text: `↗ ${addr}` }));
  }
  if (href.startsWith("#")) {
    return el("button", { type: "button", class: "ilink", onclick: () => scrollToSlug(ctx, href.slice(1)) }, label);
  }
  const hash = href.indexOf("#");
  const pathPart = hash < 0 ? href : href.slice(0, hash);
  const anchor = hash < 0 ? null : href.slice(hash + 1);
  const target = resolveIn(base, pathPart);
  if (target === null || (!target && !pathPart.endsWith("/"))) return document.createTextNode(label);  // out of her sandbox
  return el("button", { type: "button", class: "ilink", title: t("reader.open_file", { path: target }),
    onclick: () => openTarget(ctx, target, pathPart.endsWith("/"), anchor) }, label);
}

function readerInline(ctx, text, base) {
  const out = [];
  let rest = text;
  while (rest) {
    const m = READER_INLINE.exec(rest);
    if (!m) { out.push(rest); break; }
    if (m.index) out.push(rest.slice(0, m.index));
    const tok = m[0];
    if (m[1]) out.push(el("code", { class: "icode", text: tok.slice(1, -1) }));
    else if (m[2]) out.push(el("strong", {}, readerInline(ctx, tok.slice(2, -2), base)));
    else if (m[3]) out.push(el("em", {}, readerInline(ctx, tok.slice(1, -1), base)));
    else out.push(readerLink(ctx, m[5], m[6], base));
    rest = rest.slice(m.index + tok.length);
  }
  return out;
}

function readerPicture(ctx, alt, src, base) {
  if (/^https?:\/\//i.test(src)) return el("p", {}, readerLink(ctx, alt || src, src, base));
  const path = resolveIn(base, src);
  if (!path || !PICTURE_EXT.test(path)) return el("p", { text: alt || src });
  const img = el("img", { class: "rimg", alt: alt || path });
  const cap = el("figcaption", { class: "rcap", text: [alt, path].filter(Boolean).join(" · ") });
  const fig = el("figure", { class: "rfig" }, el("button", { type: "button", class: "rthumb",
    "aria-label": t("reader.open_file", { path }), onclick: () => openTarget(ctx, path, false) }, img), cap);
  img.addEventListener("load", () => {
    cap.textContent = [alt, path, `${img.naturalWidth} × ${img.naturalHeight}`].filter(Boolean).join(" · ");
  }, { once: true });
  pictureURL(ctx.host, path).then((url) => { img.src = url; }).catch(() => fig.classList.add("broken"));
  return fig;
}

const MD_STARTS = /^(#{1,4}\s|```|>|\||!\[|\s*[-*]\s|\s*\d+\.\s|---\s*$)/;

function readerBlocks(ctx, src, base) {
  const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  const heads = [];
  const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  let i = 0;
  let m;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) {
      const node = el(`h${m[1].length}`, { class: "rh" }, readerInline(ctx, m[2], base));
      heads.push({ n: m[1].length, text: m[2].replace(/[`*]/g, ""), slug: slugOf(m[2]), node });
      blocks.push(node);
      i++;
      continue;
    }
    if (/^```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      blocks.push(el("pre", { class: "rcode", text: buf.join("\n") }));
      continue;
    }
    if (/^---\s*$/.test(line)) { blocks.push(el("hr", { class: "rrule" })); i++; continue; }
    if ((m = /^!\[([^\]]*)\]\(([^)\s]+)\)\s*$/.exec(line))) { blocks.push(readerPicture(ctx, m[1], m[2], base)); i++; continue; }
    if (/^>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(el("blockquote", { class: "rquote" }, readerInline(ctx, buf.join(" "), base)));
      continue;
    }
    if (/^\|/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) rows.push(lines[i++]);
      const sep = rows.length > 1 && /^\|?\s*:?-{2,}/.test(rows[1].trim());
      const head = sep ? cells(rows[0]) : null;
      const body = (sep ? rows.slice(2) : rows).map(cells);
      blocks.push(el("div", { class: "rtable" }, el("table", {},
        head ? el("thead", {}, el("tr", {}, head.map((c) => el("th", { class: "kicker" }, readerInline(ctx, c, base))))) : null,
        el("tbody", {}, body.map((r) => el("tr", {}, r.map((c) => el("td", {}, readerInline(ctx, c, base)))))))));
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      blocks.push(el(ordered ? "ol" : "ul", { class: "rlist" }, items.map((it) => el("li", {}, readerInline(ctx, it, base)))));
      continue;
    }
    const buf = [];
    do { buf.push(lines[i++]); } while (i < lines.length && lines[i].trim() && !MD_STARTS.test(lines[i]));
    blocks.push(el("p", { class: "rp" }, readerInline(ctx, buf.join(" "), base)));
  }
  return { blocks, heads };
}

function scrollToSlug(ctx, slug) {
  const hit = (ctx.state.heads || []).find((h) => h.slug === slugOf(slug) || h.slug === slug);
  if (hit) hit.node.scrollIntoView({ block: "start" });
}

function rawLines(text) {
  const box = el("div", { class: "rlines" });
  String(text).split("\n").forEach((line, i) => box.append(el("span", { class: "ln", "aria-hidden": "true", text: String(i + 1) }),
    el("span", { class: "lt", text: line })));
  return box;
}

async function renderReader(ctx, cur) {
  const s = ctx.state;
  const ticket = ++s.ticket;
  const cut = cur.path.lastIndexOf("/");
  const dir = cut < 0 ? "" : cur.path.slice(0, cut);
  const name = cur.path.slice(cut + 1);
  const head = [...navButtons(ctx), crumbs(ctx, dir, name)];
  ctx.host.replaceChildren(viewFrame(head, viewLoading(t("reader.loading", { path: cur.path }))));
  let doc;
  try {
    doc = await filesFetch(ctx, "file:" + cur.path, () => api("/v1/files/text" + qpath(cur.path)));
  } catch (err) {
    if (ticket === s.ticket) ctx.host.replaceChildren(viewFrame(head, viewError(t("reader.error_title", { name }), err,
      () => renderFilesPlace(ctx))));
    return;
  }
  if (ticket !== s.ticket) return;
  const narrow = ctx.narrow();
  const md = doc.type === "markdown";
  const raw = !md || s.raw;
  const meta = [fmtSize(doc.size), cur.meta && cur.meta.modified ? fmtWhen(cur.meta.modified) : null].filter(Boolean).join(" · ");
  const mode = md
    ? el("div", { class: "seg rseg", role: "group", "aria-label": t("reader.mode") },
      el("button", { type: "button", class: raw ? "" : "on", "aria-pressed": String(!raw),
        onclick: () => { s.raw = false; renderFilesPlace(ctx); } }, t("reader.formatted")),
      el("button", { type: "button", class: raw ? "on" : "", "aria-pressed": String(raw),
        onclick: () => { s.raw = true; renderFilesPlace(ctx); } }, t("reader.raw")))
    : el("span", { class: "kicker", text: t(READER_MODE[doc.type] || "reader.plain") });
  const cutNote = doc.cut ? el("div", { class: "rcut" }, el("span", { class: "title", text: t("reader.cut_title") }),
    el("span", { class: "cut-note", text: t("reader.cut_note", { size: fmtSize(doc.size) }) })) : null;
  const scroller = el("div", { class: "vscroll rscroll" });
  let heads = [];
  if (raw) {
    scroller.append(el("div", { class: "rraw" }, rawLines(doc.text), cutNote));
  } else {
    const built = readerBlocks(ctx, doc.text, dir);
    heads = built.heads;
    scroller.append(el("article", { class: "rpage", lang: "uk" }, built.blocks, cutNote));
  }
  s.heads = heads;
  const long = heads.length >= 4;
  const outline = long && !raw ? el("nav", { class: "routline-list", "aria-label": t("reader.outline") },
    heads.filter((h) => h.n > 1).map((h) => {
      const btn = el("button", { type: "button", class: `lvl${h.n}`, onclick: () => {
        h.node.scrollIntoView({ block: "start" });
        if (narrow) { s.outlineOpen = false; renderFilesPlace(ctx); }
      } }, h.text);
      h.button = btn;
      return btn;
    })) : null;
  if (outline) {
    const follow = () => {  // the current heading follows the scroll
      let at = heads.find((h) => h.button) || null;
      for (const h of heads) if (h.button && h.node.offsetTop <= scroller.scrollTop + 40) at = h;
      for (const h of heads) if (h.button) h.button.setAttribute("aria-current", String(h === at));
    };
    scroller.addEventListener("scroll", follow, { passive: true });
    requestAnimationFrame(follow);
  }
  const contents = narrow && outline ? el("button", { type: "button", class: "contents", "aria-expanded": String(!!s.outlineOpen),
    onclick: () => { s.outlineOpen = !s.outlineOpen; renderFilesPlace(ctx); } }, t("reader.contents"), icon(s.outlineOpen ? "chevron-up" : "chevron-down")) : null;
  if (narrow && outline && s.outlineOpen) scroller.prepend(el("div", { class: "routline-band" },
    el("span", { class: "kicker", text: t("reader.outline") }), outline));
  const bar = [...head, mode, el("span", { class: "vmeta", text: meta }), contents];
  const body = !narrow && outline
    ? el("div", { class: "rwrap" }, scroller, el("aside", { class: "routline" }, el("span", { class: "kicker", text: t("reader.outline") }), outline))
    : scroller;
  ctx.host.replaceChildren(el("div", { class: "view" }, el("div", { class: "vbar" }, ...bar), body));
  if (cur.anchor) requestAnimationFrame(() => scrollToSlug(ctx, cur.anchor));
}

async function renderPicturePage(ctx, cur) {  // v4.1: a picture at full size (v4.2's full picture view replaces it)
  const s = ctx.state;
  const ticket = ++s.ticket;
  const cut = cur.path.lastIndexOf("/");
  const dir = cut < 0 ? "" : cur.path.slice(0, cut);
  const name = cur.path.slice(cut + 1);
  const meta = cur.meta ? [fmtSize(cur.meta.size), fmtWhen(cur.meta.modified)].filter(Boolean).join(" · ") : "";
  const bar = [...navButtons(ctx), crumbs(ctx, dir, name), meta ? el("span", { class: "vmeta", text: meta }) : null];
  ctx.host.replaceChildren(viewFrame(bar, viewLoading(t("reader.loading", { path: cur.path }))));
  let url;
  try {
    url = await pictureURL(ctx.host, cur.path);
  } catch (err) {
    if (ticket === s.ticket) ctx.host.replaceChildren(viewFrame(bar, viewError(t("reader.error_title", { name }), err,
      () => renderFilesPlace(ctx))));
    return;
  }
  if (ticket !== s.ticket) return;
  const img = el("img", { class: "pfull", alt: name });
  const cap = el("div", { class: "pcap" }, el("span", { class: "mono", text: cur.path }));
  img.addEventListener("load", () => cap.append(` · ${img.naturalWidth} × ${img.naturalHeight}`), { once: true });
  img.src = url;
  ctx.host.replaceChildren(el("div", { class: "view" }, el("div", { class: "vbar" }, ...bar), el("div", { class: "pstage" }, img, cap)));
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
    st.portraitShown = localStorage.getItem(PORTRAIT_STORE) !== "hidden";
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
    if ((ev.metaKey || ev.ctrlKey) && /^[1-9]$/.test(ev.key)) {  // v4.1: ⌘1…⌘n (Ctrl elsewhere) switch views
      const kind = shownViews()[Number(ev.key) - 1];
      if (kind) { ev.preventDefault(); setView(kind); }
      return;
    }
    if ((ev.metaKey || ev.ctrlKey) && (ev.key === "[" || ev.key === "]") && vw.view === "files" && places.main.ctx) {
      ev.preventDefault();  // ⌘[ ⌘] back / forward in Files and the reader
      filesStep(places.main.ctx, ev.key === "[" ? -1 : 1);
      return;
    }
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "\\") {  // ⌘\ folds or unfolds the right panels
      if (vw.r.length + vw.b.length) { ev.preventDefault(); vw.open = !vw.open; savePanels(); renderSide(); }
      return;
    }
    if (!ev.metaKey) return;
    if (ev.key === "-") { ev.preventDefault(); setChatSize(st.chatSize - 1); }
    else if (ev.key === "=" || ev.key === "+") { ev.preventDefault(); setChatSize(st.chatSize + 1); }
  });
  NARROW.addEventListener("change", () => { renderPanel(); renderViews(); renderSide(); stick(true); });  // a new width
  window.addEventListener("resize", () => { if (!$("side").hidden) applySideWidth(); });  // a narrower window caps it
  loadPanels();
  renderViews();
  renderSide();
  renderPanel();
  renderInput();
  renderStatus();
  input.focus();
  connect();
}

start();
