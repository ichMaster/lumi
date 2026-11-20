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
  lastTools: null,  // v4.4: the tools of this window's last reply (the server's tool_calls), or null
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
  "chevron-left": [["path", { d: "m15 18-6-6 6-6" }]],
  "message-circle": [["path", { d: "M7.9 20A9 9 0 1 0 4 16.1L2 22Z" }]],  // v4.3: a spoken thought
  "arrow-down": [["path", { d: "M12 5v14" }], ["path", { d: "m19 12-7 7-7-7" }]],  // v4.5: download
  play: [["polygon", { points: "6 3 20 12 6 21 6 3" }]],  // v4.6: her voice on a line
  pause: [["rect", { x: "14", y: "4", width: "4", height: "16" }], ["rect", { x: "6", y: "4", width: "4", height: "16" }]],
  volume: [["path", { d: "M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z" }],
    ["path", { d: "M16 9a5 5 0 0 1 0 6" }], ["path", { d: "M19.364 18.364a9 9 0 0 0 0-12.728" }]],  // v4.6: the switch, on
  "volume-off": [["path", { d: "M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z" }],
    ["line", { x1: "22", x2: "16", y1: "9", y2: "15" }], ["line", { x1: "16", x2: "22", y1: "9", y2: "15" }]],  // off
  mic: [["path", { d: "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" }], ["path", { d: "M19 10v2a7 7 0 0 1-14 0v-2" }],
    ["line", { x1: "12", x2: "12", y1: "19", y2: "22" }]],  // v4.7: dictation
  more: [["circle", { cx: "12", cy: "12", r: "1" }], ["circle", { cx: "19", cy: "12", r: "1" }],
    ["circle", { cx: "5", cy: "12", r: "1" }]],  // v4.5: a row's actions  // v4.2: the picture view's previous
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
const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*|_[^_\n]+_)|(\[[^\]\n]+\]\((https?:\/\/[^\s)]+)\))|(https?:\/\/[^\s<>"'«»`]+)/i;

/* v4.5 LUMI-326: a bare http / https address ends at a space, a quote, «» or <>; trailing punctuation and a bracket it
   didn't open stay outside it. → [the address or "" when nothing is left of it, the text after it] */
function bareUrl(raw) {
  let url = raw;
  for (;;) {
    const last = url.slice(-1);
    const count = (ch) => url.split(ch).length - 1;
    if (/[.,;:!?…*_]/.test(last)) url = url.slice(0, -1);
    else if (last === ")" && count("(") < count(")")) url = url.slice(0, -1);
    else if (last === "]" && count("[") < count("]")) url = url.slice(0, -1);
    else break;
  }
  return /^https?:\/\/[^/?#\s]+/i.test(url) ? [url, raw.slice(url.length)] : ["", raw];
}

function chatLink(href, label) {  // v4.5 LUMI-326: the proxy opens it in the default browser; this window never navigates
  return el("a", { href, title: href, target: "_blank", rel: "noopener noreferrer",
    onclick: (ev) => { ev.preventDefault(); openExternal(href); } }, label);
}

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
    else if (m[4]) out.push(chatLink(m[5], tok.slice(1, tok.indexOf("]("))));
    else {
      const [url, after] = bareUrl(tok);
      if (url) out.push(chatLink(url, url));
      if (after) out.push(after);
    }
    rest = rest.slice(m.index + tok.length);
  }
  return out;
}

function withLinks(text) {  // v4.5 LUMI-326: your line stays as you typed it; only its http / https addresses are links
  const out = [];
  let at = 0;
  for (const m of text.matchAll(/https?:\/\/[^\s<>"'«»`]+/gi)) {
    const [url] = bareUrl(m[0]);
    if (!url) continue;
    if (m.index > at) out.push(text.slice(at, m.index));
    out.push(chatLink(url, url));
    at = m.index + url.length;
  }
  if (at < text.length) out.push(text.slice(at));
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
  r.content.append(el("div", { class: "text", lang: "uk" }, withLinks(String(text))));
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
  if (opts.pictures) chatPictures(r, opts.pictures);  // v4.2: what she made or sent in that turn
  return { ...r, body };
}

/* her pictures in the chat (v4.2 LUMI-308; the v2.12 figure, design/v4/handoff §6): the sandbox paths a turn carries
   (`pictures` on the outcome and on her message), each a square that opens the full picture view over the chat column.
   Fetched once per window with the key into a blob: URL (/v1/files/raw) — the conversation keeps its lines, so its
   pictures stay with them. */
const chatPlace = { state: {} };

function chatCtx() {
  const host = $("chat");
  return { host, state: chatPlace.state, narrow: () => NARROW.matches || host.clientWidth < VIEW_NARROW_PX };
}

function chatPictures(r, pictures) {
  const paths = (Array.isArray(pictures) ? pictures : []).filter((p) => typeof p === "string" && p);
  if (!paths.length) return;
  const follow = nearBottom();
  paths.forEach((_, i) => r.content.append(chatPicture(paths, i)));
  stick(follow);
}

function chatPicture(paths, index) {
  const path = paths[index];
  const name = path.slice(path.lastIndexOf("/") + 1);
  const img = el("img", { alt: name });
  const dims = el("span", { class: "num" });
  const hint = el("span", { text: ` · ${t("picture.open_hint")}` });
  const cap = el("figcaption", { class: "mcap" }, el("span", { class: "mono", text: path }), dims, hint);
  const box = el("button", { type: "button", class: "mthumb", "aria-label": t("reader.open_file", { path }),
    onclick: () => openChatPicture(paths, index) }, img);
  const fig = el("figure", { class: "mfig" }, box, cap);
  img.addEventListener("load", () => {
    dims.textContent = ` · ${img.naturalWidth} × ${img.naturalHeight}`;  // the dimensions of the picture itself
    stick();  // a line that grew below the reader's eye keeps the newest line in view
  }, { once: true });
  pictureURL($("col"), path).then((url) => { img.src = url; }).catch(() => {
    fig.classList.add("broken");  // its path stays; never a broken <img>
    box.disabled = true;
    box.replaceChildren(el("span", { class: "mbroken" }, icon("image")));
    hint.remove();  // nothing to open
  });
  return fig;
}

function openChatPicture(paths, index) {
  const ctx = chatCtx();
  openPicture(ctx, { items: paths.map((path) => ({ path })), index, kicker: `${folderOf(paths[index]) || t("nav.root")}/`,
    toFiles: served("files") ? (item) => {
      closePicture(ctx, false);
      places.main.states.files = Object.assign(places.main.states.files || {}, { goto: { kind: "dir", path: folderOf(item.path) } });
      setView("files");
    } : null });
  const view = ctx.state.picture;
  if (view && ctx.narrow()) view.node.classList.add("vnarrow");  // the chat column has no view host to say so
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
      el("span", { class: "name" }, closenessName(level), level ? el("span", { class: "of", text: ` ${of}` }) : null), meter),
    cycleCell(s.cycle));
}

function phaseName(key) { return t(`phase.${key}`); }  // v4.4: the hormonal phase in the window's words

function cycleCell(c) {  // v4.4 LUMI-319: today's hormonal phase, a row of its own under the theme and the closeness
  if (!c || !c.phase) return null;
  return el("div", { class: "meta-cell meta-cycle" }, el("span", { class: "kicker", text: t("panel.cycle") }),
    el("span", { class: "name" }, phaseName(c.phase).replace(/^./, (ch) => ch.toUpperCase()),  // a name here: "Luteal"
      el("span", { class: "of", text: ` ${t("panel.cycle.day", { d: c.day, n: c.length })}` })));
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
        text: st.thoughtsOpen ? t("panel.thoughts.collapse") : t("panel.thoughts.all", { n: st.thoughts.length }) }),
      served("thoughts") ? el("button", { type: "button", class: "open-view", onclick: () => setView("thoughts"),  // v4.3
        title: `${viewName("thoughts")}  ${t("views.shortcut", { n: shownViews().indexOf("thoughts") + 1 })}`,
        text: t("strip.open_thoughts") }) : null),
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
  kids.push(voiceSwitch());  // v4.6: her voice on or off, for the window and Telegram both
  const open = el("button", { type: "button", class: "open-details", "aria-expanded": String(st.detailsOpen), "aria-controls": "details",
    onclick: () => { st.detailsOpen = !st.detailsOpen; renderStatus(); } },
  el("span", { class: "label", text: t("status.details", { version: s.version || "—" }) }), icon("chevron-up"));
  kids.push(open);
  bar.replaceChildren(...kids.filter(Boolean));
  renderDetails();
  if (dict.checked) renderInput();  // v4.7: the server's features decide whether the mic shows
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
  box.replaceChildren(grid, toolsBlock(), el("div", { class: "controls" }, portrait, voice, size));
}

/* v4.4 LUMI-318 (design/v4/handoff §11): the tools of this window's last reply, between the cells and the controls —
   name, short input, short result; none → one line; no list from the server (the trace off) → no block */
function toolsBlock() {
  const calls = st.lastTools;
  if (!Array.isArray(calls)) return null;
  return el("div", { class: "tools" },
    el("div", { class: "tools-head" }, el("span", { class: "kicker", text: t("tools.title") }),
      el("span", { class: "tools-n", text: String(calls.length) })),
    calls.length ? el("div", { class: "tools-list" }, calls.slice(0, 20).map((c) => el("div", { class: "tool" },
      el("span", { class: "tool-name", text: String(c.name || "") }),
      el("span", { class: "tool-in", text: String(c.input || ""), title: String(c.input || "") }),
      el("span", { class: "tool-out", text: `\u2192 ${String(c.result || "")}` }))))
      : el("div", { class: "tools-none", text: t("tools.none") }));
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
  if (event === "voice") { if (st.replaying) armReplay(REPLAY_QUIET_MS); onVoice(data); return; }  // v4.6: any line's
  if (event !== "message") return;
  if (data.origin && data.origin === CLIENT_ID) return; // our own line — shown from the response
  if (st.replaying) armReplay(REPLAY_QUIET_MS);
  if (st.collectMissed && data.replay) {  // only what the server replays counts as missed
    if (!st.missedDivider) st.missedDivider = addDivider(t("divider.missed", { n: 0 }), true);
    st.missed.push(data);
    st.missedDivider.querySelector(".kicker").textContent = t("divider.missed", { n: st.missed.length });
  }
  const time = data.replay ? undefined : clock();  // a replayed line's moment isn't known
  if (data.role === "user") {
    const mine = addMine(String(data.text || ""), { time, source: data.source, origin: data.origin });
    chatPictures(mine, data.pictures);  // v4.5: a picture another window showed her
  }
  else {
    const hers = addHers(String(data.text || ""), { time, source: data.source, origin: data.origin, first: data.source === "thought" || data.source === "nudge",
      emotion: data.emotion, intensity: data.intensity, pictures: data.pictures });
    voiceLine(hers, data.voice);  // v4.6: "voicing…" until its voice push
  }
}

/* her voice (v4.6 LUMI-330): a line that names a voice id shows "voicing…" until the server's `voice` push — then a
   ▶ (ready) or nothing (failed). ▶ fetches the audio with the window's key into a blob: URL and plays it; one line plays
   at a time, ❚❚ pauses, Esc stops. Nothing ever plays without a click. */
const VOICE_ID = /^[0-9a-f]{32}$/;
const VOICE_KEEP = 200;  // the lines, readiness and audio the window remembers — the oldest go first
const voice = { lines: new Map(), ready: new Map(), urls: new Map(), now: null, switching: false };

function voiceKeep(map, key, value, drop) {
  map.delete(key);
  map.set(key, value);
  while (map.size > VOICE_KEEP) {
    const [oldest, gone] = map.entries().next().value;
    map.delete(oldest);
    if (drop) drop(gone);
  }
}

function voiceLine(r, vid) {
  if (typeof vid !== "string" || !VOICE_ID.test(vid)) return;
  const holder = el("div", { class: "voice" }, el("span", { class: "voicing", text: t("voice.voicing") }));
  r.content.append(holder);
  voiceKeep(voice.lines, vid, holder);
  if (voice.ready.has(vid)) voiceReady(vid, voice.ready.get(vid));  // its push came first
}

function onVoice(data) {
  const vid = String(data.message || "");
  if (!VOICE_ID.test(vid)) return;
  voiceKeep(voice.ready, vid, data.ready === true);
  voiceReady(vid, data.ready === true);
}

function voiceReady(vid, ok) {
  const holder = voice.lines.get(vid);
  if (!holder) return;
  if (!ok) { holder.remove(); voice.lines.delete(vid); return; }  // the generation failed: the text stands alone
  if (holder.querySelector(".vplay")) return;
  const btn = el("button", { type: "button", class: "vplay", "aria-pressed": "false",
    onclick: () => voiceToggle(vid, btn) });
  voiceDraw(btn, null);
  holder.replaceChildren(btn);
}

const mmss = (sec) => { const n = Math.floor(Number(sec) || 0); return `${Math.floor(n / 60)}:${pad(n % 60)}`; };

function voiceDraw(btn, audio) {
  const playing = !!audio && !audio.paused && !audio.ended;
  btn.replaceChildren(icon(playing ? "pause" : "play"), el("span", { class: "vtime num", text: audio ? mmss(audio.currentTime) : "" }));
  btn.setAttribute("aria-pressed", String(playing));
  btn.setAttribute("aria-label", t(playing ? "voice.pause" : "voice.play"));
  btn.title = t(playing ? "voice.pause" : "voice.play");
}

async function voiceFetch(vid) {
  const res = await fetch("/v1/voice/" + vid, { headers: headers() });
  if (!res.ok) throw new ApiError(res.status, "", "");
  const url = URL.createObjectURL(await res.blob());
  voiceKeep(voice.urls, vid, url, (gone) => URL.revokeObjectURL(gone));
  return url;
}

async function voiceToggle(vid, btn) {  // a click: the only way her voice ever plays
  const now = voice.now;
  if (now && now.vid === vid) {
    if (now.audio.paused) now.audio.play().catch(() => voiceStop());
    else now.audio.pause();
    return;
  }
  voiceStop();
  let url = voice.urls.get(vid);
  if (!url) {
    btn.disabled = true;
    try { url = await voiceFetch(vid); }
    catch (e) { btn.disabled = false; btn.title = t("voice.failed"); return; }
    btn.disabled = false;
    if (voice.now) return;  // another line started meanwhile: this one waits for its next click
  }
  const audio = new Audio(url);
  voice.now = { vid, audio, btn };
  const draw = () => { if (voice.now && voice.now.audio === audio) voiceDraw(btn, audio); };
  for (const ev of ["play", "pause", "timeupdate"]) audio.addEventListener(ev, draw);
  audio.addEventListener("ended", () => { if (voice.now && voice.now.audio === audio) voiceStop(); });
  audio.addEventListener("error", () => {  // an audio the window can't decode: stop, and say so on the button
    if (voice.now && voice.now.audio === audio) voiceStop();
    btn.title = t("voice.failed");
  });
  audio.play().catch(() => { voiceStop(); btn.title = t("voice.again"); });  // the click's moment passed: once more
}

function voiceStop() {
  const now = voice.now;
  if (!now) return;
  voice.now = null;
  now.audio.pause();
  voiceDraw(now.btn, null);
}

function voiceSwitch() {  // the «Voice» toggle in the status line — only on a server that can voice her
  const v = st.server.voice;
  if (!v || typeof v !== "object") return null;
  const on = v.enabled === true, can = v.available === true;
  const title = !can ? t("voice.switch.unavailable") : t(on ? "voice.switch.on" : "voice.switch.off");
  return el("button", { type: "button", class: `voice-switch${on ? " on" : ""}`, "aria-pressed": String(on),
    "aria-label": title, title, disabled: !can || voice.switching, onclick: () => setVoice(!on) },
  icon(on ? "volume" : "volume-off"), el("span", { text: t("voice.switch.label") }));
}

async function setVoice(on) {
  if (voice.switching) return;
  voice.switching = true;
  renderStatus();
  try {
    const res = await api("/v1/voice", { enabled: on });
    st.server = { ...st.server, voice: { enabled: res.enabled === true, available: res.available === true } };
  } catch (e) {
    if (e.kind === "token_refused" || e.kind === "no_server") { showScreen(e.kind, e.message); return; }
    addNote(t("voice.switch.failed", { detail: e.message }));
  } finally {
    voice.switching = false;
    renderStatus();
  }
}

/* dictation (v4.7 LUMI-334): the mic button in the composer. The shell records the Mac's mic and the server recognizes
   it; the page drives it through the proxy's /local/dictation routes and reads one event stream per recording. A click
   starts and a second click stops; holding the button (or ⌥Space) talks while held. The phrase being spoken shows in a
   muted line under the input; each settled phrase is appended to the input, after what is typed, to edit and send.
   Esc cancels, and an error leaves the input as it was. «Send when I stop» sends the line when the recording ends. */
const DICT_STORE = "lumi.desktop.sendWhenStop";
const HOLD_MS = 350;  // a press longer than this is "hold to talk": the release stops
const dict = { available: false, reason: "", checked: false, state: "idle", base: "", interim: "", level: 0,
  started: 0, timer: null, ctrl: null, press: null, error: "", errorTimer: null, sendWhenStop: false, said: false };

function micShown() {
  return dict.available && !!(st.server && st.server.features && st.server.features.dictation === true);
}

async function dictCheck() {
  try {
    const res = await fetch("/local/dictation", { headers: headers() });
    const data = await res.json();
    dict.available = res.ok && data.available === true;
    dict.reason = typeof data.reason === "string" ? data.reason : "";
  } catch (e) { dict.available = false; dict.reason = ""; }
  dict.checked = true;
  renderInput();
}

function setSendWhenStop(on) {
  dict.sendWhenStop = !!on;
  try { localStorage.setItem(DICT_STORE, dict.sendWhenStop ? "on" : "off"); } catch (e) { /* lasts this window */ }
}

function renderMic() {
  const mic = $("mic");
  if (!mic) return;
  const shown = micShown();
  mic.hidden = !shown;
  const live = dict.state !== "idle";
  mic.disabled = shown && !live && st.conn !== "online";
  mic.classList.toggle("on", live);
  mic.setAttribute("aria-pressed", String(live));
  const label = live ? t("dictation.stop") : t("dictation.mic");
  mic.setAttribute("aria-label", label);
  mic.title = live ? label : t("dictation.mic_hint");
  if (!mic.firstChild) mic.append(icon("mic"));
  renderDictation();
}

function renderDictation() {  // the live line under the input: the level, the timer, the phrase being spoken
  const box = $("dictation");
  if (!box) return;
  const live = dict.state !== "idle";
  box.hidden = !live && !dict.error;
  if (box.hidden) { box.replaceChildren(); return; }
  if (dict.error && !live) {
    box.replaceChildren(el("span", { class: "dict-error", text: dict.error }));
    return;
  }
  const status = dict.state === "recognizing" ? t("dictation.recognizing")
    : dict.state === "starting" ? t("dictation.starting") : t("dictation.listening");
  const secs = dict.started ? (Date.now() - dict.started) / 1000 : 0;
  const bar = el("span", { class: "dict-bar" });
  bar.style.width = `${Math.round(Math.min(1, dict.level * 3) * 100)}%`;  // speech peaks low: the meter is scaled
  box.replaceChildren(
    el("span", { class: "dict-level", "aria-hidden": "true" }, bar),
    el("span", { class: "dict-status", text: status }),
    el("span", { class: "dict-time num", text: mmss(secs) }),
    el("span", { class: "dict-interim", text: dict.interim }),  // text only — never HTML
    el("span", { class: "dict-esc", text: t("dictation.esc") }));
}

function dictFail(detail) {
  dict.error = t("dictation.failed", { detail });
  clearTimeout(dict.errorTimer);
  dict.errorTimer = setTimeout(() => { dict.error = ""; renderDictation(); }, 8000);
}

function dictEnd() {
  dict.state = "idle";
  dict.interim = "";
  dict.level = 0;
  dict.started = 0;
  clearInterval(dict.timer);
  dict.timer = null;
  dict.ctrl = null;
  dict.press = null;
  renderMic();
}

function dictAppend(text) {  // a settled phrase, after whatever is in the input now (typed meanwhile too)
  const input = $("input");
  const now = input.value;
  input.value = now + (now && !/\s$/.test(now) ? " " : "") + text;
  dict.said = true;
  grow();
  renderInput();
}

function onDictEvent(kind, data) {
  if (kind === "ready") {
    if (dict.state === "starting") dict.state = "listening";
    dict.started = Date.now();
    dict.timer = setInterval(renderDictation, 250);
  } else if (kind === "level") {
    dict.level = Number(data.value) || 0;
  } else if (kind === "interim") {
    dict.interim = String(data.text || "");
  } else if (kind === "final") {
    dict.interim = "";
    dictAppend(String(data.text || ""));
  } else if (kind === "error") {
    $("input").value = dict.base;  // the input as it was before the recording
    grow();
    dictFail(String(data.detail || ""));
    dictEnd();
    renderInput();
    return "end";
  } else if (kind === "done") {
    const send = dict.sendWhenStop && dict.said && $("input").value.trim();
    dictEnd();
    if (send) {
      const input = $("input");
      const text = input.value;
      input.value = "";
      grow();
      updateSlash();
      submit(text);
    } else {
      $("input").focus();
    }
    return "end";
  }
  renderDictation();
  return null;
}

async function dictStart() {
  if (dict.state !== "idle" || !micShown() || st.conn !== "online") return;
  dict.state = "starting";
  dict.base = $("input").value;
  dict.interim = "";
  dict.said = false;
  dict.error = "";
  const ctrl = new AbortController();
  dict.ctrl = ctrl;
  renderMic();
  try {
    const res = await fetch("/local/dictation/start", { method: "POST", body: "{}", signal: ctrl.signal,
      headers: headers({ "Content-Type": "application/json" }) });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      dictFail(res.status === 409 ? t("dictation.busy") : String(data.detail || `HTTP ${res.status}`));
      dictEnd();
      return;
    }
    for await (const frame of sse(res)) {
      if (dict.ctrl !== ctrl) return;  // cancelled meanwhile
      if (onDictEvent(frame.event, frame.data) === "end") return;
    }
    if (dict.ctrl === ctrl) {  // the stream ended without a word of why
      $("input").value = dict.base;
      grow();
      dictFail(t("dictation.broken"));
      dictEnd();
      renderInput();
    }
  } catch (e) {
    if (dict.ctrl !== ctrl) return;  // aborted by a cancel
    $("input").value = dict.base;
    grow();
    dictFail(e.message || "network");
    dictEnd();
    renderInput();
  }
}

async function dictStop() {
  if (dict.state !== "listening" && dict.state !== "starting") return;
  dict.state = "recognizing";
  dict.level = 0;
  renderDictation();
  renderMic();
  try { await api("/local/dictation/stop", {}); } catch (e) { /* the stream reports how it ended */ }
}

async function dictCancel() {
  if (dict.state === "idle") return;
  const ctrl = dict.ctrl;
  $("input").value = dict.base;  // Esc: the input as it was
  grow();
  dictEnd();
  renderInput();
  try { await api("/local/dictation/cancel", {}); } catch (e) { /* the shell closes the mic when the stream goes */ }
  if (ctrl) ctrl.abort();
}

function micDown(ev) {  // a press: start now; a long press is "hold to talk"
  if (ev.button !== undefined && ev.button !== 0) return;
  ev.preventDefault();
  if (dict.state === "idle") { dict.press = { at: Date.now() }; dictStart(); }
  else dict.press = { stop: true };
}

function micUp() {
  const press = dict.press;
  dict.press = null;
  if (!press) return;
  if (press.stop) { dictStop(); return; }  // the second click of a toggle
  if (Date.now() - press.at >= HOLD_MS) dictStop();  // held: talk while held, stop on release
}

function micKey(ev) {  // ⌥Space: the same as the button — a tap toggles, a hold talks while held
  if (ev.type === "keydown") {
    if (!(ev.altKey && ev.code === "Space") || !micShown()) return false;
    ev.preventDefault();
    if (ev.repeat) return true;  // a held ⌥Space repeats: swallowed, or macOS types non-breaking spaces
    if (dict.state === "idle") { dict.press = { at: Date.now(), key: true }; dictStart(); }
    else if (dict.state === "listening" || dict.state === "starting") { dict.press = null; dictStop(); }
    return true;
  }
  if (ev.code === "Space" && dict.press && dict.press.key) {
    const press = dict.press;
    dict.press = null;
    if (Date.now() - press.at >= HOLD_MS) dictStop();
    return true;
  }
  return false;
}

/* ---------- turns, commands, directives ---------- */
function setAnswering(on) {
  st.answering = on;
  renderInput();
  renderStatus();
}

async function sendLine(text, queuedRow, pictures = null) {
  setAnswering(true);
  try {
    if (text.startsWith("/")) await slashCommand(text, queuedRow);
    else if (text.startsWith("%")) await directive(text, queuedRow);
    else await turn(text, queuedRow, false, pictures);
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

async function turn(text, queuedRow, waited = false, pictures = null) {
  const mine = settleMine(text, queuedRow);
  if (pictures) chatPictures(mine, pictures);  // v4.5: the pictures you show her, under your line
  const busy = async () => { // another client holds her: both rows go, the line is asked again in a moment —
    mine.node.remove(); her.node.remove(); // the note once per line, however long she stays busy (review #2)
    if (!waited) addNote(t("note.busy"));
    await sleep(3000);
    return turn(text, null, true, pictures);
  };
  const her = addHers(null, { time: clock() });
  her.think = thinkBlock(her);  // v2.12.2: her inner voice fills the wait
  const waiting = el("div", { class: "thinking" }, el("i"), el("i"), el("i"), el("span", { text: t("thinking") }));
  her.body.append(waiting);
  stick(true);
  const body = { text, turn_id: uid(), ...(pictures ? { sandbox_images: pictures } : {}) };  // v4.5: read by path
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
  chatPictures(her, result.pictures);  // v4.2: what she made or sent in this turn
  voiceLine(her, result.voice);  // v4.6: "voicing…" until its voice push (which may already have come)
  // v4.4: the tools of this reply (the server lists them only while files.trace is on — absent, no block)
  st.lastTools = Array.isArray(result.tool_calls) ? result.tool_calls : null;
  if (st.detailsOpen) renderDetails();
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
  if (micShown()) {  // v4.7: dictation's key and the «Send when I stop» preference
    parts.push(el("span", {}, ...keys("⌥Space", t("dictation.hint").replace(/^⌥Space/, ""))));
    parts.push(el("label", { class: "dict-send" }, el("input", { type: "checkbox", checked: dict.sendWhenStop,
      onchange: (ev) => setSendWhenStop(ev.target.checked) }), el("span", { text: t("dictation.send_when_stop") })));
  }
  if (st.queue.length) parts.push(el("span", { class: "queued", text: t("queue.count", { n: st.queue.length }) }));
  hints.replaceChildren(...parts); // a null passed to replaceChildren would print as text
  renderMic();
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
const VIEWS = ["chat", "files", "gallery", "journal", "thoughts", "state", "schedule"];  // v4.1–v4.4, one per phase
const VIEW_LABEL = { chat: "views.chat", files: "views.files", gallery: "views.gallery", journal: "views.journal",
  thoughts: "views.thoughts", state: "views.state", schedule: "views.schedule" };
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
  if (kind === "files") return features.files === true;
  if (kind === "gallery") return features.gallery === true;  // v4.2: her pictures and the thumbnail cache
  if (kind === "journal") return features.journal === true;  // v4.3: her journal, the owner's only
  if (kind === "thoughts") return features.thoughts === true;  // v4.3: her thought stream (unless /thoughts is off)
  if (kind === "state") return features.today === true;  // v4.4: her day
  if (kind === "schedule") return features.schedule === true;  // v4.4: her timetable, while a scheduler runs
  return false;
}

function shownViews() { return VIEWS.filter(served); }

let servedKey = null;
function syncServed() {  // a state push follows every turn: re-render the row and the panels only on a change
  const key = shownViews().join(",");
  if (key === servedKey) return;
  servedKey = key;
  if (!served(vw.view)) setView("chat"); else renderViews();
  renderSide();
  renderThoughts();  // v4.3: the strip's "Thoughts →" follows the Thoughts view
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
  dropPicture(states[kind]);  // v4.2: a picture view open over the old host goes with it
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
const READER_INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(\[([^\]\n]+)\]\(([^)\s]+)\))|(https?:\/\/[^\s<>"'«»`]+)/;
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

function releaseBlobs(host) {  // every picture a place (or a picture view) holds goes with it
  for (const kept of (blobsByHost.get(host) || new Map()).values()) kept.then((url) => URL.revokeObjectURL(url), () => {});
  blobsByHost.set(host, new Map());
}

function blobURL(host, key, route) {  // the only way a picture reaches the page: fetch with the key → a blob: URL
  if (!blobsByHost.has(host)) blobsByHost.set(host, new Map());
  const kept = blobsByHost.get(host);
  if (kept.has(key)) return kept.get(key);  // v4.2: one fetch per picture per place, however often it is drawn
  const got = (async () => {
    const res = await fetch(route, { headers: headers() });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new ApiError(res.status, data.kind, typeof data.detail === "string" ? data.detail : "");
    }
    return URL.createObjectURL(await res.blob());
  })();
  kept.set(key, got);
  got.catch(() => { if (kept.get(key) === got) kept.delete(key); });  // a failed one is fetched again next time
  return got;
}

function pictureURL(host, path) { return blobURL(host, "raw:" + path, "/v1/files/raw" + qpath(path)); }

function thumbURL(host, path) { return blobURL(host, "thumb:" + path, "/v1/images/thumb" + qpath(path)); }  // v4.2

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
  s.notice = null;  // v4.5: a notice belongs to the folder it was said in
  s.fresh = null;
  closePicture(ctx, false);
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
  closePicture(ctx, false);
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
  else if (PICTURE_EXT.test(path)) filesPictures(ctx, [{ path }], 0);  // v4.2: the full picture view
  else filesGo(ctx, { kind: "file", path, anchor: anchor || null });
}

function folderOf(path) { return path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : ""; }

function filesPictures(ctx, items, index) {  // v4.2: a picture in Files opens the full view over this place
  const cur = ctx.state.hist[ctx.state.idx];
  openPicture(ctx, { items, index, kicker: `${folderOf(items[index].path) || t("nav.root")}/`,
    toFiles: (item) => {
      closePicture(ctx, false);
      const folder = folderOf(item.path);
      if (!(cur.kind === "dir" && cur.path === folder)) filesGo(ctx, { kind: "dir", path: folder });
    } });
}

function paint(ctx, ...nodes) {  // a view's content; an open picture view stays over it (a new width re-renders)
  ctx.host.replaceChildren(...nodes);
  if (ctx.state.picture) ctx.host.append(ctx.state.picture.node);
}

function renderFilesPlace(ctx) {
  releaseBlobs(ctx.host);
  const cur = ctx.state.hist[ctx.state.idx];
  if (cur.kind === "dir") renderFolder(ctx, cur);
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

function fileRow(ctx, dir, e, narrow, pics) {
  const full = dir ? `${dir}/${e.name}` : e.name;
  const isDir = e.kind === "dir";
  const shown = isDir || e.type === "text" || e.type === "image";
  const glyph = isDir ? "folder" : e.type === "image" ? "image" : e.type === "text" ? "file-text" : "file";
  const size = isDir ? itemsLabel(e.count) : fmtSize(e.size);
  const open = !shown ? null : isDir ? () => filesGo(ctx, { kind: "dir", path: full })
    : e.type === "image" ? () => filesPictures(ctx, pics, pics.findIndex((p) => p.path === full))
      : () => filesGo(ctx, { kind: "file", path: full, meta: e });
  const name = el("span", { class: "fname" + (isDir ? " dir" : "") + (shown ? "" : " muted") },
    el("span", { class: "fnm", text: isDir ? `${e.name}/` : e.name }),
    shown ? null : el("span", { class: "tag", text: t("files.not_shown") }));
  const attrs = { type: "button", class: "frow", onclick: open, "aria-disabled": shown ? null : "true",
    title: shown ? null : t("files.not_shown_hint") };
  const glyphEl = el("span", { class: "ficon" + (isDir ? " dir" : "") }, icon(glyph));
  if (ctx.state.fresh && ctx.state.fresh.has(full)) attrs.class += " fresh";  // v4.5: just uploaded
  const row = narrow
    ? el("button", attrs, glyphEl, el("span", { class: "fstack" }, name,
      el("span", { class: "fcell", text: [size, fmtWhen(e.modified)].filter(Boolean).join(" · ") })))
    : el("button", attrs, el("span", { class: "fnamecell" }, glyphEl, name), el("span", { class: "fcell", text: size }),
      el("span", { class: "fcell", text: fmtDate(e.created) }), el("span", { class: "fcell", text: fmtWhen(e.modified) }));
  if (!actionsOn()) return row;
  return el("div", { class: "frowwrap" }, row, rowMenuButton(ctx, { path: full, name: e.name, isDir }));
}

async function renderFolder(ctx, cur) {
  const s = ctx.state;
  const ticket = ++s.ticket;
  const label = cur.path || t("nav.root");
  const bar = (count) => [...navButtons(ctx), crumbs(ctx, cur.path), count ? el("span", { class: "vmeta", text: count }) : null,
    folderActions(ctx, cur.path)];  // v4.5: Upload (the right end was kept free for it)
  paint(ctx, viewFrame(bar(), viewLoading(t("files.loading", { path: label }))));
  let data;
  try {
    data = await filesFetch(ctx, "dir:" + cur.path, () => api("/v1/files" + qpath(cur.path)));
  } catch (err) {
    if (ticket === s.ticket) paint(ctx, viewFrame(bar(), viewError(t("files.error_title", { path: label }), err,
      () => renderFilesPlace(ctx))));
    return;
  }
  if (ticket !== s.ticket) return;
  const entries = data.entries || [];
  const count = data.truncated ? t("files.items_cut") : itemsLabel(entries.length);
  if (!entries.length) {
    paint(ctx, dropTarget(ctx, cur.path, viewFrame(bar(count), [noticeLine(ctx),
      viewEmpty(`${label}/`, t("files.empty"), t("files.empty_note"))])));
    return;
  }
  const narrow = ctx.narrow();
  const pics = entries.filter((e) => e.kind === "file" && e.type === "image")  // v4.2: ← → step through them
    .map((e) => ({ path: cur.path ? `${cur.path}/${e.name}` : e.name, size: e.size, modified: e.modified }));
  const table = el("div", { class: "ftable" + (narrow ? " narrow" : "") + (actionsOn() ? " actions" : "") },
    narrow ? null : el("div", { class: "fhead" },
      ["files.col_name", "files.col_size", "files.col_created", "files.col_modified"].map((k) => el("span", { class: "kicker", text: t(k) }))),
    entries.map((e) => fileRow(ctx, cur.path, e, narrow, pics)),
    data.truncated ? el("div", { class: "fcut" }, el("span", { class: "title", text: t("files.cut_title") }),
      el("span", { class: "cut-note", text: t("files.cut_note") })) : null);
  paint(ctx, dropTarget(ctx, cur.path, viewFrame(bar(count), [noticeLine(ctx), table])));
}

/* ---------- the owner's actions (v4.5 LUMI-324): upload, download, rename, trash, restore, a link in the browser ----
   Shown only while the server serves them (features.actions). Every write goes to the server, which keeps them
   create-only and never deletes; the Mac's side (saving a download, showing it in Finder, opening a link) is the
   proxy's, behind the window's key. Rename, trash and restore ask first; a line under the header says what happened. */
function actionsOn() { return !!(st.server && st.server.features && st.server.features.actions === true); }

function setNotice(ctx, notice) {  // {text, kind?: "error", saved?} — redrawn with the place
  ctx.state.notice = notice;
  renderFilesPlace(ctx);
}

function noticeLine(ctx) {
  const n = ctx.state.notice;
  if (!n) return null;
  return el("div", { class: "anotice" + (n.kind === "error" ? " error" : ""), role: n.kind === "error" ? "alert" : "status" },
    el("span", { class: "atext", text: n.text }),
    n.saved ? el("button", { type: "button", class: "tlink", onclick: () => api("/local/reveal", { saved: n.saved })
      .catch((err) => setNotice(ctx, { kind: "error", text: err.message })) }, t("actions.reveal")) : null,
    el("button", { type: "button", class: "ib aclose", "aria-label": t("actions.dismiss"),
      onclick: () => setNotice(ctx, null) }, icon("x")));
}

function folderActions(ctx, folder) {
  if (!actionsOn()) return el("span", { class: "vactions", "aria-hidden": "true" });
  const input = el("input", { type: "file", multiple: true, hidden: true });
  input.addEventListener("change", () => { const files = [...input.files]; input.value = ""; uploadFiles(ctx, folder, files); });
  return el("span", { class: "vactions" }, input,
    el("button", { type: "button", class: "btn btn-secondary abtn", title: t("actions.upload_hint"),
      onclick: () => input.click() }, icon("arrow-up"), t("actions.upload")));
}

function dropTarget(ctx, folder, frame) {  // files dropped on the folder upload into it
  if (!actionsOn()) return frame;
  const area = frame.querySelector(".vscroll") || frame;
  area.setAttribute("data-drop", t("actions.drop"));
  area.addEventListener("dragover", (ev) => {
    if (![...(ev.dataTransfer ? ev.dataTransfer.types : [])].includes("Files")) return;
    ev.preventDefault();
    area.classList.add("dropping");
  });
  area.addEventListener("dragleave", () => area.classList.remove("dropping"));
  area.addEventListener("drop", (ev) => {
    area.classList.remove("dropping");
    const files = ev.dataTransfer ? [...ev.dataTransfer.files] : [];
    if (!files.length) return;
    ev.preventDefault();
    uploadFiles(ctx, folder, files);
  });
  return frame;
}

const UPLOAD_MAX_FILE = 10 * 1024 * 1024;  // the server's largest upload cap (a picture); its own caps decide the rest

async function uploadFiles(ctx, folder, files) {  // one at a time, each its own answer
  const s = ctx.state;
  const done = [];
  const failed = [];
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i];
    if (file.size > UPLOAD_MAX_FILE) {  // never sent: the server would refuse it (code review #3)
      failed.push(t("actions.failed", { name: file.name, reason: t("actions.too_large") }));
      continue;
    }
    s.notice = { text: t("actions.uploading", { i: i + 1, n: files.length, name: file.name }) };
    renderFilesPlace(ctx);
    try {
      const q = new URLSearchParams({ path: folder || "", name: file.name });
      const res = await fetch("/v1/files/upload?" + q.toString(), { method: "POST",
        headers: headers({ "Content-Type": "application/octet-stream" }), body: file });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(res.status, data.kind, typeof data.detail === "string" ? data.detail : "");
      done.push(data.path);
    } catch (err) {
      failed.push(t("actions.failed", { name: file.name, reason: err.message }));
    }
  }
  s.fresh = new Set(done);
  s.cache.clear();
  const ok = !done.length ? null
    : done.length === 1 ? t("actions.uploaded_one", { name: done[0] }) : t("actions.uploaded_many", { n: done.length });
  const text = [ok, ...failed].filter(Boolean).join(" · ");  // what landed, then each refusal with its reason
  setNotice(ctx, { text, kind: failed.length ? "error" : null });
}

function rowMenuButton(ctx, item) {
  if (!rowEntries(ctx, item).length) return null;  // the trash, a day in it: nothing to do there
  return el("button", { type: "button", class: "ib fmenu", "aria-haspopup": "menu", "aria-expanded": "false",
    "aria-label": t("actions.menu", { name: item.name }),
    onclick: (ev) => { ev.stopPropagation(); openRowMenu(ctx, ev.currentTarget, item); } }, icon("more"));
}

function rowEntries(ctx, item) {  // what a row's menu offers; nothing → no menu button
  const parts = item.path.split("/");
  const entries = [];
  if (!item.isDir) entries.push(["actions.download", () => downloadFile(ctx, item.path)]);
  if (parts[0] === ".trash" && parts.length === 3) entries.push(["actions.restore", () => restoreItem(ctx, item)]);  // the log knows these (code review #2)
  else if (parts[0] !== ".trash") {
    entries.push(["actions.rename", () => renameItem(ctx, item)], ["actions.trash", () => trashItem(ctx, item)]);
  }
  return entries;
}

function openRowMenu(ctx, button, item) {
  closeRowMenu();
  const entries = rowEntries(ctx, item);
  const menu = el("div", { class: "amenu", role: "menu" }, entries.map(([key, act]) =>
    el("button", { type: "button", role: "menuitem", onclick: () => { closeRowMenu(); act(); } }, t(key))));
  button.setAttribute("aria-expanded", "true");
  button.parentElement.append(menu);
  rowMenu = { menu, button };
  const first = menu.querySelector("button");
  if (first) first.focus();
  menu.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { ev.preventDefault(); closeRowMenu(true); } });
  setTimeout(() => document.addEventListener("click", closeRowMenuOnce), 0);
}

let rowMenu = null;
function closeRowMenuOnce() { closeRowMenu(); }
function closeRowMenu(refocus) {
  document.removeEventListener("click", closeRowMenuOnce);
  if (!rowMenu) return;
  const { menu, button } = rowMenu;
  rowMenu = null;
  menu.remove();
  button.setAttribute("aria-expanded", "false");
  if (refocus && button.isConnected) button.focus();
}

function actionDialog(ctx, opts) {  // {title, note?, value?, label?, go} → the typed value / true, or null on cancel
  return new Promise((resolve) => {
    const opener = document.activeElement;
    const input = opts.value !== undefined ? el("input", { class: "input adinput", value: opts.value,
      "aria-label": opts.label || opts.title }) : null;
    const finish = (value) => {
      node.remove();
      if (opener && opener.isConnected) opener.focus();
      resolve(value);
    };
    const go = () => finish(input ? input.value.trim() || null : true);
    const node = el("div", { class: "adialog", role: "dialog", "aria-modal": "true", "aria-label": opts.title },
      el("div", { class: "adbox" },
        el("h3", { class: "adtitle", text: opts.title }),
        opts.note ? el("p", { class: "adnote", text: opts.note }) : null,
        input,
        el("div", { class: "adbtns" },
          el("button", { type: "button", class: "btn btn-secondary", onclick: () => finish(null) }, t("actions.cancel")),
          el("button", { type: "button", class: "btn btn-primary adgo", onclick: go }, opts.go))));
    node.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); finish(null); }
      else if (ev.key === "Enter" && (ev.target === input || ev.target.classList.contains("adgo"))) { ev.preventDefault(); go(); }
    });
    ctx.host.append(node);
    if (input) { input.focus(); const dot = input.value.lastIndexOf("."); input.setSelectionRange(0, dot > 0 ? dot : input.value.length); }
    else node.querySelector(".adgo").focus();
  });
}

async function downloadFile(ctx, path) {
  const name = path.slice(path.lastIndexOf("/") + 1);
  setNotice(ctx, { text: t("actions.saving", { name }) });
  try {
    const res = await api("/local/save", { path });
    setNotice(ctx, { text: t("actions.saved", { name: res.name }), saved: res.saved });
  } catch (err) {
    setNotice(ctx, { kind: "error", text: t("actions.failed", { name, reason: err.message }) });
  }
}

async function renameItem(ctx, item) {
  const name = await actionDialog(ctx, { title: t("actions.rename_title", { name: item.name }), value: item.name,
    label: t("actions.rename_label"), go: t("actions.rename_go") });
  if (!name || name === item.name) return;
  await fileWrite(ctx, "/v1/files/rename", { path: item.path, name }, (res) => t("actions.renamed", { name: res.to }));
}

async function trashItem(ctx, item) {
  const ok = await actionDialog(ctx, { title: t("actions.trash_title", { path: item.path }), note: t("actions.trash_note"),
    go: t("actions.trash_go") });
  if (!ok) return;
  await fileWrite(ctx, "/v1/files/trash", { path: item.path }, () => t("actions.trashed", { name: item.name }));
}

async function restoreItem(ctx, item) {
  const ok = await actionDialog(ctx, { title: t("actions.restore_title", { path: item.path }), note: t("actions.restore_note"),
    go: t("actions.restore_go") });
  if (!ok) return;
  await fileWrite(ctx, "/v1/files/restore", { path: item.path }, (res) => t("actions.restored", { path: res.to }));
}

async function fileWrite(ctx, route, body, said) {  // a write; the folder is read again and the notice says what happened
  try {
    const res = await api(route, body);
    ctx.state.cache.clear();
    ctx.state.fresh = new Set([res.to]);
    setNotice(ctx, { text: said(res) });
  } catch (err) {
    setNotice(ctx, { kind: "error", text: t("actions.failed", { name: body.path, reason: err.message }) });
  }
}

function openExternal(href) {  // the proxy opens http / https in the default browser; the window never navigates
  api("/local/open", { url: href }).catch(() => {});
}

/* her markdown for the reader: headings h1–h4, fences, rules, a picture on its own line, quotes, pipe tables, lists,
   paragraphs; inline code, bold, italic and links — everything else is literal text */
function readerLink(ctx, label, href, base) {
  if (ctx.plain && !/^https?:\/\//i.test(href)) return document.createTextNode(label);  // v4.3: the journal's prose
  if (/^https?:\/\//i.test(href)) {  // external: shown with its address, never followed in this window
    const open = el("button", { type: "button", class: "xopen", onclick: () => openExternal(href) }, t("actions.open"));  // v4.5
    if (label === null) return el("span", { class: "xlink" }, el("span", { class: "xaddr", text: href }), " ", open);  // LUMI-326: a bare address
    const addr = href.replace(/^https?:\/\//i, "").replace(/\/$/, "");
    return el("span", { class: "xlink", title: t("reader.external") }, el("span", { class: "xtext", text: label }), " ",
      el("span", { class: "xaddr", text: `↗ ${addr}` }), " ", open);
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
    else if (m[4]) out.push(readerLink(ctx, m[5], m[6], base));
    else {  // v4.5 LUMI-326: a bare address
      const [url, after] = bareUrl(tok);
      if (url) out.push(readerLink(ctx, null, url, base));
      if (after) out.push(after);
    }
    rest = rest.slice(m.index + tok.length);
  }
  return out;
}

function readerPicture(ctx, alt, src, base) {
  if (/^https?:\/\//i.test(src)) return el("p", {}, readerLink(ctx, alt || src, src, base));
  if (ctx.plain) return el("p", { text: alt || src });  // v4.3: the journal's prose loads no picture
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
  paint(ctx, viewFrame(head, viewLoading(t("reader.loading", { path: cur.path }))));
  let doc;
  try {
    doc = await filesFetch(ctx, "file:" + cur.path, () => api("/v1/files/text" + qpath(cur.path)));
  } catch (err) {
    if (ticket === s.ticket) paint(ctx, viewFrame(head, viewError(t("reader.error_title", { name }), err,
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
  const bar = [...head, mode, el("span", { class: "vmeta", text: meta }), contents,
    actionsOn() ? el("span", { class: "vactions" }, el("button", { type: "button", class: "btn btn-secondary abtn",
      onclick: () => downloadFile(ctx, cur.path) }, icon("arrow-down"), t("actions.download"))) : null];  // v4.5
  const notice = noticeLine(ctx);
  if (notice) scroller.prepend(notice);
  const body = !narrow && outline
    ? el("div", { class: "rwrap" }, scroller, el("aside", { class: "routline" }, el("span", { class: "kicker", text: t("reader.outline") }), outline))
    : scroller;
  paint(ctx, el("div", { class: "view" }, el("div", { class: "vbar" }, ...bar), body));
  if (cur.anchor) requestAnimationFrame(() => scrollToSlug(ctx, cur.anchor));
}

/* ---------- the full picture view (v4.2 LUMI-307, design/v4/handoff §6) ----------
   One picture as large as fits, over the place it was opened from (the Gallery, a folder in Files, the reader; the chat
   in LUMI-308): its path, date, size and dimensions, "Open in Files", ← → through its list, Esc to close. The picture is
   fetched into a blob: URL of the view's own, released on every step and on close, so a long walk holds one picture. */
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
  "November", "December"];

function num(n) { return Number(n || 0).toLocaleString("en-US"); }

function fmtStamp(stamp) {  // "YYYY-MM-DD HH:MM" → "9 Nov 2026, 21:40"
  const time = String(stamp || "").split(" ")[1];
  const day = fmtDate(stamp);
  return day && time ? `${day}, ${time}` : day;
}

function sizeFrame(frame, w, h) {  // the picture's box: as large as fits, in its own proportions (a size container)
  if (!(w > 0 && h > 0)) return false;
  frame.style.width = `min(100cqw, calc(100cqh * ${(w / h).toFixed(4)}))`;
  frame.style.height = "auto";
  frame.style.aspectRatio = `${w} / ${h}`;
  return true;
}

function openPicture(ctx, opts) {  // opts: {items:[{path, size?, modified?, width?, height?}], index, total?, kicker, more?, toFiles?}
  if (!opts.items[opts.index]) return;
  closePicture(ctx, false);
  const node = el("div", { class: "pview", role: "dialog", "aria-modal": "true", tabindex: "-1" });
  const view = { node, opts, index: opts.index, opener: document.activeElement, ticket: 0, busy: false };
  ctx.state.picture = view;
  node.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); closePicture(ctx, true); }
    else if ((ev.key === "ArrowLeft" || ev.key === "ArrowRight") && !typing(ev.target)) {  // v4.5: not while typing a line
      ev.preventDefault(); stepPicture(ctx, ev.key === "ArrowLeft" ? -1 : 1);
    }
  });
  ctx.host.append(node);
  renderPictureView(ctx);
  node.focus();  // the focus moves into the dialog; Esc gives it back to what opened it
}

function closePicture(ctx, refocus) {
  const view = ctx.state.picture;
  if (!view) return;
  dropPicture(ctx.state);
  if (refocus && view.opener && view.opener.isConnected) view.opener.focus();
}

function dropPicture(state) {  // the picture view goes, and its picture with it (no focus moves)
  const view = state.picture;
  if (!view) return;
  state.picture = null;
  releaseBlobs(view.node);
  view.node.remove();
}

async function stepPicture(ctx, delta) {
  const view = ctx.state.picture;
  if (!view || view.busy) return;
  const i = view.index + delta;
  if (i < 0) return;
  if (i >= view.opts.items.length) {  // past the pictures loaded so far: the next page first (the Gallery)
    if (!view.opts.more) return;
    view.busy = true;
    await view.opts.more().catch(() => false);
    view.busy = false;
    if (ctx.state.picture !== view || i >= view.opts.items.length) return;
  }
  view.index = i;
  renderPictureView(ctx);
}

function renderPictureView(ctx) {
  const view = ctx.state.picture;
  if (!view) return;
  const { items, kicker } = view.opts;
  const item = items[view.index];
  const total = Math.max(view.opts.total || 0, items.length);
  const name = item.path.slice(item.path.lastIndexOf("/") + 1);
  const focused = view.node.contains(document.activeElement) ? document.activeElement.getAttribute("aria-label") : null;
  const ticket = ++view.ticket;
  releaseBlobs(view.node);  // the previous picture goes before the next one comes
  const bar = el("div", { class: "pvbar" },
    el("button", { type: "button", class: "ib", "aria-label": t("picture.close"), title: `${t("picture.close")}  Esc`,
      onclick: () => closePicture(ctx, true) }, icon("x")),
    el("span", { class: "kicker", text: kicker }),
    el("span", { class: "vmeta", text: `${num(view.index + 1)} / ${num(total)}` }),
    el("span", { class: "pvgap" }),
    el("button", { type: "button", class: "ib", "aria-label": t("picture.prev"), title: `${t("picture.prev")}  ←`,
      disabled: view.index <= 0, onclick: () => stepPicture(ctx, -1) }, icon("chevron-left")),
    el("button", { type: "button", class: "ib", "aria-label": t("picture.next"), title: `${t("picture.next")}  →`,
      disabled: view.index >= total - 1, onclick: () => stepPicture(ctx, 1) }, icon("chevron-right")));
  const img = el("img", { class: "pvimg", alt: name });
  const frame = el("div", { class: "pvframe" }, img);
  const known = sizeFrame(frame, item.width, item.height);
  const dims = el("dd", { class: "num", text: known ? `${item.width} × ${item.height}` : "" });
  img.addEventListener("load", () => {  // no dimensions from a listing: the loaded picture's own
    if (!known && sizeFrame(frame, img.naturalWidth, img.naturalHeight)) dims.textContent = `${img.naturalWidth} × ${img.naturalHeight}`;
  }, { once: true });
  const row = (key, value) => [el("dt", { class: "kicker", text: t(key) }), value];
  const meta = el("aside", { class: "pvmeta", "aria-label": t("picture.about") },
    el("div", { class: "pvname", text: name }),
    el("dl", { class: "pvkv" },
      row("picture.path", el("dd", { class: "mono", text: item.path })),
      item.modified ? row("picture.date", el("dd", { class: "num", text: fmtStamp(item.modified) })) : null,
      Number.isFinite(item.size) ? row("picture.size", el("dd", { class: "num", text: fmtSize(item.size) })) : null,
      row("picture.dimensions", dims)),
    view.opts.toFiles ? el("button", { type: "button", class: "btn btn-secondary", onclick: () => view.opts.toFiles(item) },
      icon("folder"), t("picture.open_in_files")) : null,
    pictureActions(ctx, view, item),  // v4.5: the reserved area holds the actions while the server serves them
    el("span", { class: "pvkeys", text: t("picture.keys") }));
  view.node.setAttribute("aria-label", name);
  view.node.replaceChildren(bar, el("div", { class: "pvbody" }, el("div", { class: "pvarea" }, el("div", { class: "pvcq" }, frame)), meta));
  const again = focused && [...bar.querySelectorAll("button")].find((b) => b.getAttribute("aria-label") === focused && !b.disabled);
  (again || view.node).focus();  // a step rebuilt the buttons: the focus stays in the dialog, its keys keep working
  pictureURL(view.node, item.path).then((url) => { if (ticket === view.ticket) img.src = url; })
    .catch(() => { if (ticket === view.ticket) frame.classList.add("broken"); });
}

/* ---------- the picture view's actions (v4.5 LUMI-325) — show it to her with a line, download, rename, trash ------
   In the view's reserved area (wide only, as the design keeps it). "Show to Lili" sends your line with the picture's
   sandbox path as a turn (the server reads the picture), offered while she can see pictures (features.image). */
function pictureActions(ctx, view, item) {
  if (!actionsOn()) return el("div", { class: "pvlater" }, el("span", { class: "kicker", text: t("picture.actions_later") }));
  const canShow = !!(st.server.features && st.server.features.image === true);
  const input = canShow ? el("input", { class: "input pvline", placeholder: t("actions.show_placeholder"),
    "aria-label": t("actions.show_label"), value: view.line || "" }) : null;
  const send = () => showToLili(ctx, view, item, input ? input.value : "");
  if (input) {
    input.addEventListener("input", () => { view.line = input.value; });
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); send(); } });
  }
  const status = view.status;
  return el("div", { class: "pvlater pvactions" }, el("span", { class: "kicker", text: t("actions.title") }),
    canShow ? el("div", { class: "pvshow" }, input,
      el("button", { type: "button", class: "btn btn-primary abtn", onclick: send }, t("actions.show"))) : null,
    el("div", { class: "pvbtns" },
      el("button", { type: "button", class: "btn btn-secondary abtn", onclick: () => pictureDownload(ctx, view, item) },
        icon("arrow-down"), t("actions.download")),
      el("button", { type: "button", class: "btn btn-secondary abtn", onclick: () => pictureRename(ctx, view, item) },
        t("actions.rename")),
      el("button", { type: "button", class: "btn btn-secondary abtn", onclick: () => pictureTrash(ctx, view, item) },
        t("actions.trash"))),
    status ? el("div", { class: "pvstatus" + (status.kind === "error" ? " error" : ""), role: "status" },
      el("span", { text: status.text }),
      status.saved ? el("button", { type: "button", class: "tlink", onclick: () => api("/local/reveal", { saved: status.saved })
        .catch(() => {}) }, t("actions.reveal")) : null) : null);
}

function setPictureStatus(ctx, view, status) {
  if (ctx.state.picture !== view) return;
  view.status = status;
  renderPictureView(ctx);
}

function showToLili(ctx, view, item, line) {
  const text = String(line || "").trim();
  if (!text) { setPictureStatus(ctx, view, { kind: "error", text: t("actions.show_empty") }); return; }
  if (st.answering || st.conn !== "online") { setPictureStatus(ctx, view, { kind: "error", text: t("actions.show_busy") }); return; }
  view.line = "";
  closePicture(ctx, false);
  setView("chat");
  sendLine(text, null, [item.path]);
}

async function pictureDownload(ctx, view, item) {
  const name = item.path.slice(item.path.lastIndexOf("/") + 1);
  setPictureStatus(ctx, view, { text: t("actions.saving", { name }) });
  try {
    const res = await api("/local/save", { path: item.path });
    setPictureStatus(ctx, view, { text: t("actions.saved", { name: res.name }), saved: res.saved });
  } catch (err) {
    setPictureStatus(ctx, view, { kind: "error", text: t("actions.failed", { name, reason: err.message }) });
  }
}

async function pictureRename(ctx, view, item) {
  const name = item.path.slice(item.path.lastIndexOf("/") + 1);
  const next = await actionDialog(ctx, { title: t("actions.rename_title", { name }), value: name,
    label: t("actions.rename_label"), go: t("actions.rename_go") });
  if (!next || next === name) return;
  await pictureWrite(ctx, view, "/v1/files/rename", { path: item.path, name: next }, (res) => t("actions.renamed", { name: res.to }));
}

async function pictureTrash(ctx, view, item) {
  const ok = await actionDialog(ctx, { title: t("actions.trash_title", { path: item.path }), note: t("actions.trash_note"),
    go: t("actions.trash_go") });
  if (!ok) return;
  const name = item.path.slice(item.path.lastIndexOf("/") + 1);
  await pictureWrite(ctx, view, "/v1/files/trash", { path: item.path }, () => t("actions.trashed", { name }));
}

async function pictureWrite(ctx, view, route, body, said) {  // done → the view closes and its place reads again
  try {
    const res = await api(route, body);
    closePicture(ctx, false);
    refreshPlace(ctx, said(res));
  } catch (err) {
    setPictureStatus(ctx, view, { kind: "error", text: t("actions.failed", { name: body.path, reason: err.message }) });
  }
}

function refreshPlace(ctx, text) {  // the place under a closed picture view, read again after a rename or a trash
  if (ctx.kind === "files") {
    ctx.state.cache.clear();
    ctx.state.notice = { text };
    renderFilesPlace(ctx);
  } else if (ctx.kind === "gallery") {
    galleryReload(ctx);
  }
}

/* ---------- Gallery (v4.2 LUMI-307, design/v4/handoff §6) ----------
   Every picture of her sandbox, newest first (/v1/images), a page of 100 at a time as the list nears its end, with a
   folder filter and month dividers. Thumbnails (/v1/images/thumb, 320 px) are fetched with the window's key into blob:
   URLs as their tiles come near the screen, kept per place. A fresh mount asks for the newest pictures again; a new
   width only redraws what is loaded. */
const GALLERY_PAGE = 100;
const GALLERY_NEAR_END_PX = 160;

function galleryMonth(stamp) {  // "YYYY-MM-DD HH:MM" → "November 2026"
  const [y, m] = String(stamp || "").slice(0, 7).split("-").map(Number);
  return y && m ? `${MONTHS[m - 1]} ${y}` : "";
}

function galleryQuery(s, before) {
  const q = new URLSearchParams({ limit: String(GALLERY_PAGE) });
  if (s.folder !== null) q.set("folder", s.folder);
  if (before) q.set("before", before);
  return "?" + q.toString();
}

VIEW_KINDS.gallery = (ctx) => {
  const s = ctx.state;
  if (s.ctx !== ctx) {  // a fresh mount (or a new folder): the newest pictures again
    Object.assign(s, { ctx, folder: s.folder === undefined ? null : s.folder, items: [], next: null, total: 0, filtered: 0,
      folders: [], phase: "loading", error: null, busy: false, ticket: (s.ticket || 0) + 1, moreFailed: false });
    renderGallery(ctx);
    galleryLoad(ctx, false);
    return;
  }
  renderGallery(ctx);
};

function galleryReload(ctx) { ctx.state.ctx = null; VIEW_KINDS.gallery(ctx); }

async function galleryLoad(ctx, more) {
  const s = ctx.state;
  if (s.busy || (more && !s.next)) return false;
  const ticket = s.ticket;
  s.busy = true;
  s.moreFailed = false;
  let data;
  try {
    data = await api("/v1/images" + galleryQuery(s, more ? s.next : null));
  } catch (err) {
    s.busy = false;
    if (ticket !== s.ticket) return false;
    if (more) { s.moreFailed = true; galleryMore(ctx); } else { s.phase = "error"; s.error = err; renderGallery(ctx); }
    return false;
  }
  s.busy = false;
  if (ticket !== s.ticket) return false;
  const fresh = Array.isArray(data.items) ? data.items : [];
  s.items.push(...fresh);
  s.next = data.next || null;
  s.total = data.total || 0;
  s.folders = Array.isArray(data.folders) ? data.folders : [];
  const mine = s.folders.find((f) => f.folder === s.folder);
  s.filtered = s.folder === null ? s.total : mine ? mine.count : 0;
  if (!more) { s.phase = "ok"; renderGallery(ctx); return true; }
  galleryTiles(ctx, fresh);
  galleryMore(ctx);
  return true;
}

function galleryFilter(ctx) {
  const s = ctx.state;
  const opts = [{ folder: null, label: t("gallery.all"), count: s.total },
    ...s.folders.map((f) => ({ folder: f.folder, label: f.folder || t("nav.root"), count: f.count }))];
  return el("div", { class: "seg gseg", role: "radiogroup", "aria-label": t("gallery.folder") },
    opts.map((o) => el("button", { type: "button", role: "radio", "aria-checked": String(o.folder === s.folder),
      class: o.folder === s.folder ? "on" : null,
      onclick: () => { if (o.folder !== s.folder) { s.folder = o.folder; galleryReload(ctx); } } },
    o.label, el("span", { class: "gcount", text: num(o.count) }))));
}

function renderGallery(ctx) {
  const s = ctx.state;
  if (s.watch) { s.watch.disconnect(); s.watch = null; }
  const bar = [el("h2", { class: "vtitle", text: t("gallery.title") })];
  if (s.phase === "ok") bar.push(el("span", { class: "vmeta", text: t("gallery.count", { n: num(s.filtered) }) }));
  bar.push(el("span", { class: "vactions", "aria-hidden": "true" }));
  if (s.phase === "ok" && s.total > 0) bar.push(galleryFilter(ctx));
  if (s.phase === "loading") { paint(ctx, viewFrame(bar, viewLoading(t("gallery.loading")))); return; }
  if (s.phase === "error") {
    paint(ctx, viewFrame(bar, viewError(t("gallery.error_title"), s.error, () => galleryReload(ctx))));
    return;
  }
  if (!s.items.length) {
    paint(ctx, viewFrame(bar, viewEmpty(t("gallery.title"), t("gallery.empty"), t("gallery.empty_note"))));
    return;
  }
  const narrow = ctx.narrow();
  const grid = el("div", { class: "ggrid" + (narrow ? " narrow" : "") });
  const frame = viewFrame(bar, grid);
  const scroller = frame.querySelector(".vscroll");
  s.grid = { node: grid, month: "", narrow, more: null, scroller };
  if (typeof IntersectionObserver !== "undefined") {  // a thumbnail is fetched as its tile nears the screen
    s.watch = new IntersectionObserver((seen) => {
      for (const e of seen) if (e.isIntersecting) { s.watch.unobserve(e.target); e.target.loadThumb(); }
    }, { root: scroller, rootMargin: "300px 0px" });
  }
  scroller.addEventListener("scroll", () => galleryNearEnd(ctx));
  galleryTiles(ctx, s.items);
  galleryMore(ctx);
  paint(ctx, frame);
  requestAnimationFrame(() => galleryNearEnd(ctx));  // a first page that doesn't fill the screen asks for the next
}

function galleryNearEnd(ctx) {
  const g = ctx.state.grid;
  if (!g || ctx.state.moreFailed) return;
  const sc = g.scroller;
  if (sc.scrollHeight - sc.scrollTop - sc.clientHeight < GALLERY_NEAR_END_PX) {
    galleryLoad(ctx, true).then((got) => { if (got) requestAnimationFrame(() => galleryNearEnd(ctx)); });
  }
}

function galleryTiles(ctx, items) {
  const s = ctx.state;
  const g = s.grid;
  if (!g) return;
  for (const item of items) {
    const month = galleryMonth(item.modified);
    if (month && month !== g.month) {
      g.month = month;
      g.node.append(el("div", { class: "divider gmonth" }, el("span", { class: "kicker", text: month }), el("span", { class: "line" })));
    }
    g.node.append(galleryTile(ctx, item, g.narrow));
  }
  if (g.more) g.node.append(g.more);  // the end row stays last
}

function galleryTile(ctx, item, narrow) {
  const s = ctx.state;
  const name = item.path.slice(item.path.lastIndexOf("/") + 1);
  const img = el("img", { alt: "" });
  const ph = el("span", { class: "gph" }, img);
  ph.loadThumb = () => thumbURL(ctx.host, item.path).then((url) => { img.src = url; }).catch(() => ph.classList.add("broken"));
  if (s.watch) s.watch.observe(ph); else ph.loadThumb();
  return el("button", { type: "button", class: "gtile", "aria-label": `${name}, ${fmtWhen(item.modified)}`,
    onclick: () => openPicture(ctx, { items: s.items, index: s.items.indexOf(item), total: s.filtered,
      kicker: t("gallery.title"), more: () => galleryLoad(ctx, true),
      toFiles: (it) => { closePicture(ctx, false); ctx.open("files", { goto: { kind: "dir", path: folderOf(it.path) } }); } }) },
  ph, narrow ? null : el("span", { class: "gname", text: name }),
  narrow ? null : el("span", { class: "gwhen", text: fmtWhen(item.modified) }));
}

function galleryMore(ctx) {  // the end row: loading more · that's all · a retry when a page failed
  const s = ctx.state;
  const g = s.grid;
  if (!g) return;
  let row;
  if (s.moreFailed) {
    row = el("div", { class: "gmore" }, el("span", { text: t("gallery.error_title") }),
      el("button", { type: "button", class: "btn btn-secondary", onclick: () => { s.moreFailed = false; galleryNearEnd(ctx); galleryMore(ctx); } },
        icon("refresh-cw"), t("state.try_again")));
  } else if (s.next) {
    row = el("div", { class: "gmore", role: "status" }, el("div", { class: "pulse" }, el("i"), el("i"), el("i"),
      el("span", { text: t("gallery.more", { shown: num(s.items.length), total: num(s.filtered) }) })));
  } else {
    row = el("div", { class: "gmore", text: t("gallery.end", { n: num(s.filtered) }) });
  }
  if (g.more) g.more.replaceWith(row); else g.node.append(row);
  g.more = row;
}

/* ---------- Journal (v4.3 LUMI-312, design/v4/handoff §7) ----------
   Her journal, read-only: /v1/journal (the days, newest first) and /v1/journal/<date> (one day: the code-owned stamp
   apart from her prose, then each `## HH:MM` section). Wide: the list of days beside the day; narrow (and pinned): the
   list is its own screen. A fresh mount asks for the days again and opens the newest (or the day it showed); ← → step
   through the days when the focus isn't in a field. Her prose goes through the reader's renderer — built nodes, never
   HTML — with links and pictures kept as text: the journal lives outside her sandbox. */
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function dayOf(ymd) {  // "YYYY-MM-DD" → that local day (no time-zone shift)
  const [y, m, d] = String(ymd || "").split("-").map(Number);
  return new Date(y || 1970, (m || 1) - 1, d || 1);
}

function longDay(ymd) { const d = dayOf(ymd); return `${WEEKDAYS[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`; }

function shortDay(ymd) { const d = dayOf(ymd); return `${WEEKDAYS[d.getDay()].slice(0, 3)} ${d.getDate()} ${MONTHS3[d.getMonth()]}`; }

function kbSize(n) { return n < 1024 ? `${num(n)} B` : `${(n / 1024).toFixed(1)} KB`; }

function entriesLabel(n) { return n === 1 ? t("journal.entry") : t("journal.entries", { n: num(n) }); }

function signed(v) {  // "+0.52" / "−0.31"; a value that rounds to zero reads "+0.00", never "−0.00"
  const r = Math.round((Number(v) || 0) * 100) / 100;
  return (r < 0 ? "−" : "+") + Math.abs(r).toFixed(2);
}

VIEW_KINDS.journal = (ctx) => {
  const s = ctx.state;
  if (s.ctx !== ctx) {  // a fresh mount: the days again, the day it showed (or the newest)
    Object.assign(s, { ctx, phase: "loading", days: [], error: null, entries: new Map(), dayError: null, list: false,
      ticket: (s.ticket || 0) + 1, dayTicket: 0, listScroll: 0 });
    renderJournal(ctx);
    journalLoad(ctx);
    return;
  }
  renderJournal(ctx);
};

function journalReload(ctx) { ctx.state.ctx = null; VIEW_KINDS.journal(ctx); }

async function journalLoad(ctx) {
  const s = ctx.state;
  const ticket = s.ticket;
  let data;
  try {
    data = await api("/v1/journal");
  } catch (err) {
    if (ticket === s.ticket) { s.phase = "error"; s.error = err; renderJournal(ctx); }
    return;
  }
  if (ticket !== s.ticket) return;
  s.days = Array.isArray(data.days) ? data.days : [];
  s.phase = "ok";
  if (!s.days.some((d) => d.date === s.day)) s.day = s.days.length ? s.days[0].date : null;
  if (s.day) journalOpen(ctx, s.day); else renderJournal(ctx);
}

async function journalOpen(ctx, date) {  // show a day: from what this mount read, else the server
  const s = ctx.state;
  s.day = date;
  s.list = false;
  s.dayError = null;
  renderJournal(ctx);
  if (s.entries.has(date)) return;
  const ticket = ++s.dayTicket;
  const mount = s.ticket;
  let entry;
  try {
    entry = await api("/v1/journal/" + encodeURIComponent(date));
  } catch (err) {
    if (ticket === s.dayTicket && mount === s.ticket) { s.dayError = err; renderJournal(ctx); }
    return;
  }
  if (mount !== s.ticket) return;
  s.entries.set(date, entry);
  if (ticket === s.dayTicket && s.day === date) renderJournal(ctx);
}

function journalStep(ctx, delta) {  // delta −1: the previous (older) day; +1: the next (newer) one
  const s = ctx.state;
  if (s.phase !== "ok") return;
  const i = s.days.findIndex((d) => d.date === s.day);
  const to = s.days[i - delta];
  if (i >= 0 && to) journalOpen(ctx, to.date);
}

function journalList(ctx, wide) {
  const s = ctx.state;
  const perMonth = new Map();
  for (const d of s.days) { const m = d.date.slice(0, 7); perMonth.set(m, (perMonth.get(m) || 0) + 1); }
  const nav = el("nav", { class: "jdays" + (wide ? "" : " vscroll"), "aria-label": t("journal.days_label") });
  let month = "";
  for (const d of s.days) {
    const m = d.date.slice(0, 7);
    if (m !== month) {
      month = m;
      const at = dayOf(d.date);
      nav.append(el("div", { class: "jmonth" }, el("span", { class: "kicker", text: `${MONTHS[at.getMonth()]} ${at.getFullYear()}` }),
        el("span", { class: "jmcount", text: t("journal.month_days", { n: num(perMonth.get(m)) }) })));
    }
    const at = dayOf(d.date);
    const note = entriesLabel(d.sections) + (d.stamp ? "" : ` · ${t("journal.no_stamp_short")}`);
    nav.append(el("button", { type: "button", class: "jrow", "aria-current": String(wide && d.date === s.day),
      "data-date": d.date, onclick: () => journalOpen(ctx, d.date) },
    el("span", { class: "jnum", text: String(at.getDate()) }),
    el("span", { class: "jwd" }, el("span", { text: WEEKDAYS[at.getDay()] }), el("span", { class: "jnote", text: note })),
    el("span", { class: "jsize", text: kbSize(d.size) })));
  }
  nav.addEventListener("scroll", () => { s.listScroll = nav.scrollTop; }, { passive: true });
  return nav;
}

function journalStamp(stamp, narrow) {
  if (!stamp) return el("p", { class: "jnostamp", text: t("journal.no_stamp") });
  const rows = [];
  if (stamp.mood) rows.push(["journal.mood", el("span", { lang: "uk", text: stamp.mood })]);
  if (Array.isArray(stamp.biorhythms) && stamp.biorhythms.length) {
    rows.push(["journal.biorhythms", el("div", { class: "jbio" + (narrow ? " narrow" : "") }, stamp.biorhythms.map((c) =>
      el("span", { class: "jcell" }, el("span", { class: "jcname", lang: "uk", text: c.name }),
        el("span", { class: "jcval", text: signed(c.value) }), el("span", { class: "jclabel", text: c.label }))))]);
  }
  if (stamp.forecast) rows.push(["journal.forecast", el("span", { lang: "uk", text: stamp.forecast })]);
  return el("section", { class: "jstamp" + (narrow ? " narrow" : ""), "aria-label": t("journal.stamp") },
    rows.map(([key, value]) => el("div", { class: "jsrow" }, el("span", { class: "kicker", text: t(key) }), value)));
}

function journalDay(ctx, narrow) {
  const s = ctx.state;
  const i = s.days.findIndex((d) => d.date === s.day);
  const day = s.days[i];
  const entry = s.entries.get(s.day);
  if (!entry) {
    if (s.dayError) return viewError(t("journal.error_title"), s.dayError, () => journalOpen(ctx, s.day));
    return viewLoading(t("journal.loading"));
  }
  const older = s.days[i + 1];
  const newer = s.days[i - 1];
  const prose = { host: ctx.host, state: {}, plain: true };  // the reader's renderer, links and pictures as text
  const sections = (entry.sections || []).map((sec) => el("div", { class: "jentry" + (narrow ? " narrow" : "") },
    el("span", { class: "jtime", text: sec.time || "" }),
    el("div", { class: "jprose rpage", lang: "uk" }, readerBlocks(prose, sec.text, "").blocks)));
  const step = (to, delta) => el("button", { type: "button", class: "btn btn-secondary", disabled: !to,
    onclick: to ? () => journalStep(ctx, delta) : null },
  delta < 0 ? icon("chevron-left") : null,
  to ? t(delta < 0 ? "journal.previous" : "journal.next", { day: shortDay(to.date) })
    : t(delta < 0 ? "journal.first" : "journal.latest"),
  delta > 0 ? icon("chevron-right") : null);
  return el("article", { class: "jday" + (narrow ? " narrow" : "") },
    el("div", { class: "jkick" }, el("span", { class: "kicker", text: t("journal.title") }),
      el("span", { class: "vmeta", text: [t("journal.day_of", { i: num(s.days.length - i), n: num(s.days.length) }),
        entriesLabel(day.sections), kbSize(day.size)].join(" · ") })),
    el("h1", { class: "jtitle" }, longDay(s.day), el("span", { class: "jyear", text: String(dayOf(s.day).getFullYear()) })),
    journalStamp(entry.stamp, narrow),
    sections,
    el("div", { class: "jfoot" }, step(older, -1), step(newer, 1)),
    narrow ? null : el("p", { class: "jkeys", text: t("journal.keys") }));
}

function renderJournal(ctx) {
  const s = ctx.state;
  const narrow = ctx.narrow();
  const title = el("h2", { class: "vtitle", text: t("journal.title") });
  if (s.phase === "loading") { paint(ctx, viewFrame([title], viewLoading(t("journal.loading")))); return; }
  if (s.phase === "error") {
    paint(ctx, viewFrame([title], viewError(t("journal.error_title"), s.error, () => journalReload(ctx))));
    return;
  }
  if (!s.days.length) {
    paint(ctx, viewFrame([title], viewEmpty(t("journal.title"), t("journal.empty"), t("journal.empty_note"))));
    return;
  }
  const meta = el("span", { class: "vmeta", text: t("journal.days", { n: num(s.days.length) }) });
  if (narrow && s.list) {  // the list is its own screen
    const list = journalList(ctx, false);
    paint(ctx, el("div", { class: "view" }, el("div", { class: "vbar" }, title, meta), list));
    list.scrollTop = s.listScroll;
    return;
  }
  const pane = el("div", { class: "vscroll jscroll" }, journalDay(ctx, narrow));
  if (narrow) {
    const i = s.days.findIndex((d) => d.date === s.day);
    const older = s.days[i + 1];
    const newer = s.days[i - 1];
    const bar = [
      el("button", { type: "button", class: "jback", onclick: () => { s.list = true; renderJournal(ctx); } },
        icon("chevron-left"), t("journal.all_days")),
      el("span", { class: "jgap" }),
      el("button", { type: "button", class: "ib", disabled: !older, onclick: () => journalStep(ctx, -1),
        "aria-label": older ? t("journal.previous", { day: shortDay(older.date) }) : t("journal.first") }, icon("chevron-left")),
      el("button", { type: "button", class: "ib", disabled: !newer, onclick: () => journalStep(ctx, 1),
        "aria-label": newer ? t("journal.next", { day: shortDay(newer.date) }) : t("journal.latest") }, icon("chevron-right"))];
    paint(ctx, el("div", { class: "view" }, el("div", { class: "vbar" }, ...bar), pane));
    return;
  }
  const list = journalList(ctx, true);
  paint(ctx, el("div", { class: "view" }, el("div", { class: "vbar" }, title, meta), el("div", { class: "jwrap" }, list, pane)));
  list.scrollTop = s.listScroll;
  const current = list.querySelector('.jrow[aria-current="true"]');
  if (current) current.scrollIntoView({ block: "nearest" });  // ← → keep the day in sight
}

/* ---------- Thoughts (v4.3 LUMI-313, design/v4/handoff §8) ----------
   Her whole thought stream, newest first (/v1/thoughts — the server's surfacing filter, so a thought sparked with
   another user never reaches the page), a page of 100 at a time as the list nears its end, back to her first thought.
   Filters: kinds (several at once), spoken or silent, and a date to jump to; each change starts again from the newest
   matching thought (or that day). Day dividers carry the day's count from the server, since a page can cut a day. */
const THOUGHTS_PAGE = 100;
const THOUGHTS_NEAR_END_PX = 160;

function thoughtsQuery(s, before) {
  const q = new URLSearchParams({ limit: String(THOUGHTS_PAGE) });
  for (const kind of s.kinds) q.append("kind", kind);
  if (s.spoken !== "all") q.set("spoken", s.spoken === "spoken" ? "true" : "false");
  if (before) q.set("before", before); else if (s.date) q.set("date", s.date);
  return "?" + q.toString();
}

function thoughtsDay(ymd, n) {  // the day divider: "Today · Monday, 16 November · 11"
  const now = new Date();
  const day = longDay(ymd);
  if (ymd === ymdOf(now)) return t("thoughts.today", { day, n: num(n) });
  if (ymd === ymdOf(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1))) {
    return t("thoughts.yesterday", { day, n: num(n) });
  }
  const year = dayOf(ymd).getFullYear();
  return `${day}${year === now.getFullYear() ? "" : ` ${year}`} · ${num(n)}`;
}

function thoughtsSummary(stats) {  // "5,589 · about 34 a day · 6% spoken"
  const perDay = stats.per_day >= 10 ? num(Math.round(stats.per_day)) : String(Math.round(stats.per_day * 10) / 10);
  return t("thoughts.summary", { n: num(stats.total), perDay, pct: String(Math.round((stats.spoken_share || 0) * 100)) });
}

VIEW_KINDS.thoughts = (ctx) => {
  const s = ctx.state;
  if (s.ctx !== ctx) {  // a fresh mount (or new filters): the newest matching thoughts again
    Object.assign(s, { ctx, kinds: s.kinds || [], spoken: s.spoken || "all", date: s.date || "", kindsOpen: !!s.kindsOpen,
      allKinds: s.allKinds || [], items: [], next: null, stats: null, days: {}, phase: "loading", error: null,
      busy: false, ticket: (s.ticket || 0) + 1, moreFailed: false });
    renderThoughtsView(ctx);
    thoughtsLoad(ctx, false);
    return;
  }
  renderThoughtsView(ctx);
};

function thoughtsReload(ctx) { ctx.state.ctx = null; VIEW_KINDS.thoughts(ctx); }

async function thoughtsLoad(ctx, more) {
  const s = ctx.state;
  if (s.busy || (more && !s.next)) return false;
  const ticket = s.ticket;
  s.busy = true;
  s.moreFailed = false;
  let data;
  try {
    data = await api("/v1/thoughts" + thoughtsQuery(s, more ? s.next : null));
  } catch (err) {
    s.busy = false;
    if (ticket !== s.ticket) return false;
    if (more) { s.moreFailed = true; thoughtsMore(ctx); } else { s.phase = "error"; s.error = err; renderThoughtsView(ctx); }
    return false;
  }
  s.busy = false;
  if (ticket !== s.ticket) return false;  // a late answer for old filters is dropped
  const fresh = Array.isArray(data.items) ? data.items : [];
  s.items.push(...fresh);
  s.next = data.next || null;
  Object.assign(s.days, data.days || {});
  if (!more) {
    s.stats = data.stats || null;
    s.allKinds = Array.isArray(data.kinds) ? data.kinds : [];
    s.phase = "ok";
    renderThoughtsView(ctx);
    return true;
  }
  thoughtsItems(ctx, fresh);
  thoughtsMore(ctx);
  return true;
}

function thoughtsFilters(ctx) {
  const s = ctx.state;
  const label = !s.kinds.length ? t("thoughts.kind_all") : s.kinds.length <= 2 ? s.kinds.join(", ")
    : t("thoughts.kinds_n", { n: num(s.kinds.length) });
  const kind = el("button", { type: "button", class: "chip", "aria-expanded": String(s.kindsOpen),
    onclick: () => { s.kindsOpen = !s.kindsOpen; renderThoughtsView(ctx); } },
  el("span", { class: "kicker", text: t("thoughts.kind") }), el("span", { class: "chipval", text: label }), icon("chevron-down"));
  const spoken = el("div", { class: "seg tseg", role: "radiogroup", "aria-label": t("thoughts.spoken_filter") },
    [["all", "thoughts.all"], ["spoken", "thoughts.spoken"], ["silent", "thoughts.silent"]].map(([value, key]) =>
      el("button", { type: "button", role: "radio", "aria-checked": String(s.spoken === value),
        class: s.spoken === value ? "on" : null,
        onclick: () => { if (s.spoken !== value) { s.spoken = value; thoughtsReload(ctx); } } }, t(key))));
  const input = el("input", { type: "date", class: "input", value: s.date, max: ymdOf(new Date()),
    "aria-label": t("thoughts.jump") });
  input.addEventListener("change", () => {
    const value = /^\d{4}-\d{2}-\d{2}$/.test(input.value) ? input.value : "";
    if (value !== s.date) { s.date = value; thoughtsReload(ctx); }
  });
  const jump = el("label", { class: "tjump" }, el("span", { class: "kicker", text: t("thoughts.jump") }), input);
  return [kind, spoken, jump];
}

function thoughtsKinds(ctx) {  // the in-flow panel under the header: every kind as a toggle, several at once
  const s = ctx.state;
  const all = [...s.allKinds, ...s.kinds.filter((k) => !s.allKinds.includes(k))];
  return el("div", { class: "tkinds" },
    el("div", { class: "tchips", role: "group", "aria-label": t("thoughts.kind") }, all.map((k) =>
      el("button", { type: "button", class: "chip mono", "aria-pressed": String(s.kinds.includes(k)),
        onclick: () => { s.kinds = s.kinds.includes(k) ? s.kinds.filter((x) => x !== k) : [...s.kinds, k]; thoughtsReload(ctx); } }, k))),
    el("div", { class: "tkinds-foot" },
      el("button", { type: "button", class: "tlink", disabled: !s.kinds.length,
        onclick: () => { s.kinds = []; thoughtsReload(ctx); } }, t("thoughts.clear_kinds")),
      el("span", { class: "thint", text: t("thoughts.kinds_hint") })));
}

function renderThoughtsView(ctx) {
  const s = ctx.state;
  const narrow = ctx.narrow();
  const bar = el("div", { class: "vbar" }, el("h2", { class: "vtitle", text: t("thoughts.title") }),
    s.phase === "ok" && s.stats ? el("span", { class: "vmeta", text: thoughtsSummary(s.stats) }) : null,
    el("span", { class: "tgap" }), ...thoughtsFilters(ctx));
  const head = [bar, s.kindsOpen ? thoughtsKinds(ctx) : null];
  const frame = (body) => el("div", { class: "view" }, ...head, el("div", { class: "vscroll" }, body));
  if (s.phase === "loading") { paint(ctx, frame(viewLoading(t("thoughts.loading")))); return; }
  if (s.phase === "error") {
    paint(ctx, frame(viewError(t("thoughts.error_title"), s.error, () => thoughtsReload(ctx))));
    return;
  }
  if (!s.items.length) {
    const filtered = s.kinds.length || s.spoken !== "all" || s.date;
    paint(ctx, frame(viewEmpty(t("thoughts.title"), t("thoughts.empty"), t("thoughts.empty_note"),
      filtered ? el("button", { type: "button", class: "btn btn-secondary", onclick: () => {
        Object.assign(s, { kinds: [], spoken: "all", date: "" });
        thoughtsReload(ctx);
      } }, t("thoughts.clear_filters")) : null)));
    return;
  }
  const stream = el("div", { class: "tstream" + (narrow ? " narrow" : "") });
  const scroller = el("div", { class: "vscroll" }, stream);
  s.stream = { node: stream, day: "", narrow, more: null, scroller };
  scroller.addEventListener("scroll", () => thoughtsNearEnd(ctx), { passive: true });
  thoughtsItems(ctx, s.items);
  thoughtsMore(ctx);
  paint(ctx, el("div", { class: "view" }, ...head, scroller));
  requestAnimationFrame(() => thoughtsNearEnd(ctx));  // a first page that doesn't fill the screen asks for the next
}

function thoughtsNearEnd(ctx) {
  const g = ctx.state.stream;
  if (!g || ctx.state.moreFailed) return;
  const sc = g.scroller;
  if (sc.scrollHeight - sc.scrollTop - sc.clientHeight < THOUGHTS_NEAR_END_PX) {
    thoughtsLoad(ctx, true).then((got) => { if (got) requestAnimationFrame(() => thoughtsNearEnd(ctx)); });
  }
}

function thoughtsItems(ctx, items) {
  const s = ctx.state;
  const g = s.stream;
  if (!g) return;
  for (const th of items) {
    const date = String(th.when || "").slice(0, 10);
    if (date && date !== g.day) {
      g.day = date;
      g.node.append(el("div", { class: "divider tday" }, el("span", { class: "kicker", text: thoughtsDay(date, s.days[date] || 0) }),
        el("span", { class: "line" })));
    }
    g.node.append(thoughtsItem(th, g.narrow));
  }
  if (g.more) g.node.append(g.more);  // the end row stays last
}

function thoughtsItem(th, narrow) {
  const time = el("span", { class: "ttime", text: String(th.when || "").slice(11, 16) });
  return el("article", { class: "titem" }, narrow ? null : time,
    el("div", { class: "tbody" },
      el("div", { class: "tmeta" }, narrow ? time : null, el("span", { class: "kicker tkind", text: th.kind }),
        th.spoken ? el("span", { class: "tspoken" }, icon("message-circle"), t("thoughts.spoken")) : null),
      el("p", { class: "ttext", lang: "uk", text: th.text }),
      S.captions && S.captions[th.emotion] ? el("span", { class: "tcap", text: S.captions[th.emotion] }) : null));
}

function thoughtsMore(ctx) {  // the end row: loading earlier thoughts · a retry when a page failed · nothing at the first
  const s = ctx.state;
  const g = s.stream;
  if (!g) return;
  let row;
  if (s.moreFailed) {
    row = el("div", { class: "tmore" }, el("span", { text: t("thoughts.error_title") }),
      el("button", { type: "button", class: "btn btn-secondary", onclick: () => { s.moreFailed = false; thoughtsNearEnd(ctx); thoughtsMore(ctx); } },
        icon("refresh-cw"), t("state.try_again")));
  } else if (s.next) {
    row = el("div", { class: "tmore", role: "status" }, el("div", { class: "pulse" }, el("i"), el("i"), el("i"),
      el("span", { text: t("thoughts.more") })));
  } else {
    row = el("div", { class: "tmore tend" });
  }
  if (g.more) g.more.replaceWith(row); else g.node.append(row);
  g.more = row;
}

/* ---------- State (v4.4 LUMI-317, design/v4/handoff §9) ----------
   Her day, read-only (/v1/today): the mood of the day in full (the theme, the resolution, the reading's body), the
   three biorhythms with their ±14-day cycles drawn as SVG built node by node, the closeness level, and the place for
   the day's plan. The mood is shown only once today's is made; the biorhythms and closeness don't wait for it. */
const SVG_NS = "http://www.w3.org/2000/svg";
const BIO_W = 280;
const BIO_H = 56;
const BIO_SPAN = 28;  // days across the drawing: ±14

function bioLevel(value) { return value >= 0.33 ? "high" : value <= -0.33 ? "low" : "mid"; }

function bioPoints(day, period) {  // the sine across ±14 days around today, as an SVG polyline's points
  const pts = [];
  for (let x = 0; x <= BIO_W; x += 4) {
    const d = (x / BIO_W - 0.5) * BIO_SPAN;
    pts.push(`${x},${(BIO_H / 2 - Math.sin((2 * Math.PI * (day + d)) / period) * (BIO_H / 2 - 4)).toFixed(1)}`);
  }
  return pts.join(" ");
}

function svgNode(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

function bioCycle(b) {
  const name = t(`state.${b.name}`);
  const label = `${name} ${signed(b.value)}, ${t(`state.${bioLevel(b.value)}`)}, ${t(`state.${b.trend}`)}, ` +
    `on a ${b.period}-day cycle`;
  const svg = svgNode("svg", { viewBox: `0 0 ${BIO_W} ${BIO_H}`, preserveAspectRatio: "none", role: "img",
    "aria-label": label, class: "bsvg" });
  const cy = BIO_H / 2 - b.value * (BIO_H / 2 - 4);
  svg.append(svgNode("line", { x1: 0, x2: BIO_W, y1: BIO_H / 2, y2: BIO_H / 2, class: "bzero" }),
    svgNode("line", { x1: BIO_W / 2, x2: BIO_W / 2, y1: 0, y2: BIO_H, class: "btoday" }),
    svgNode("polyline", { points: bioPoints(b.day, b.period), class: "bsine" }),
    svgNode("rect", { x: BIO_W / 2 - 4, y: (cy - 4).toFixed(1), width: 8, height: 8, class: "bdot" }));
  return el("div", { class: "bcycle" }, svg,
    el("div", { class: "bscale" }, el("span", { text: "−14 d" }), el("span", { text: t("state.cycle", { p: b.period }) }),
      el("span", { text: "+14 d" })));
}

function bioRow(b) {
  return el("div", { class: "brow" },
    el("div", { class: "bhead" }, el("span", { class: "bname", text: t(`state.${b.name}`) }),
      el("span", { class: "bval", text: signed(b.value) }),
      el("span", { class: "blabel", text: `${t(`state.${bioLevel(b.value)}`)} · ${t(`state.${b.trend}`)}` })),
    bioCycle(b));
}

/* v4.4 LUMI-319: the hormonal cycle as a fourth cycle — one bar a day across the same ±14 days, its height by phase
   (low in menstruation and PMS, highest at ovulation), today marked as on the sines */
const PHASE_BAR = { menstrual: 12, follicular: 26, ovulation: 46, luteal: 30, pms: 18 };

function phaseAt(c, offset) {  // the phase key `offset` days from today, the cycle repeating
  const n = c.length;
  return c.phases[(((c.day - 1 + offset) % n) + n) % n];
}

function phaseLeft(c) {  // days left in today's phase after today
  let left = 0;
  while (left < c.length - 1 && phaseAt(c, left + 1) === c.phase) left += 1;
  return left;
}

function hormonalRow(c) {
  const left = phaseLeft(c);
  const leftText = left === 0 ? t("state.last_day") : left === 1 ? t("state.day_left") : t("state.days_left", { n: left });
  const label = `${t("state.hormonal")}, ${t("panel.cycle.day", { d: c.day, n: c.length })}, ${phaseName(c.phase)}, ${leftText}`;
  const svg = svgNode("svg", { viewBox: `0 0 ${BIO_W} ${BIO_H}`, preserveAspectRatio: "none", role: "img",
    "aria-label": label, class: "bsvg" });
  const step = BIO_W / BIO_SPAN;
  for (let off = -BIO_SPAN / 2; off <= BIO_SPAN / 2; off += 1) {
    const key = phaseAt(c, off);
    const h = PHASE_BAR[key] || 20;
    svg.append(svgNode("rect", { x: ((off + BIO_SPAN / 2) * step - step * 0.4).toFixed(1), y: BIO_H - h,
      width: (step * 0.8).toFixed(1), height: h, class: `hbar ph-${key}` }));
  }
  const hToday = PHASE_BAR[c.phase] || 20;
  svg.append(svgNode("line", { x1: BIO_W / 2, x2: BIO_W / 2, y1: 0, y2: BIO_H, class: "btoday" }),
    svgNode("rect", { x: BIO_W / 2 - 4, y: Math.max(0, BIO_H - hToday - 10), width: 8, height: 8, class: "bdot" }));
  return el("div", { class: "brow hrow" },
    el("div", { class: "bhead" }, el("span", { class: "bname", text: t("state.hormonal") }),
      el("span", { class: "bval", text: `${c.day}/${c.length}` }),
      el("span", { class: "blabel", text: `${phaseName(c.phase)} \u00b7 ${leftText}` })),
    el("div", { class: "bcycle" }, svg,
      el("div", { class: "bscale" }, el("span", { text: "\u221214 d" }), el("span", { text: t("state.cycle", { p: c.length }) }),
        el("span", { text: "+14 d" })),
      c.note ? el("p", { class: "hnote", lang: "uk", text: c.note }) : null));
}

VIEW_KINDS.state = (ctx) => {
  const s = ctx.state;
  if (s.ctx !== ctx) {  // a fresh mount: today again
    Object.assign(s, { ctx, phase: "loading", data: null, error: null, ticket: (s.ticket || 0) + 1 });
    renderStateView(ctx);
    stateLoad(ctx);
    return;
  }
  renderStateView(ctx);
};

async function stateLoad(ctx) {
  const s = ctx.state;
  const ticket = s.ticket;
  try {
    const data = await api("/v1/today");
    if (ticket !== s.ticket) return;
    Object.assign(s, { phase: "ok", data });
  } catch (err) {
    if (ticket !== s.ticket) return;
    Object.assign(s, { phase: "error", error: err });
  }
  renderStateView(ctx);
}

function stateReload(ctx) { ctx.state.ctx = null; VIEW_KINDS.state(ctx); }

function renderStateView(ctx) {
  const s = ctx.state;
  const narrow = ctx.narrow();
  const title = el("h2", { class: "vtitle", text: t("state.title") });
  if (s.phase === "loading") { paint(ctx, viewFrame([title], viewLoading(t("state.loading")))); return; }
  if (s.phase === "error") {
    paint(ctx, viewFrame([title], viewError(t("state.error_title"), s.error, () => stateReload(ctx))));
    return;
  }
  const d = s.data || {};
  const mood = d.mood;
  const prose = { host: ctx.host, state: {}, plain: true };  // her reading through the reader's renderer, as text
  const th = mood ? themeInfo(mood.theme) : null;
  const moodSection = el("section", { class: "smood", "aria-label": t("state.mood") },
    el("span", { class: "kicker", text: t("state.mood") }),
    mood ? [
      el("div", { class: "stheme" }, el("span", { class: "sthname", text: th.name }), th.sub ? el("span", { class: "sthsub", text: th.sub }) : null),
      el("p", { class: "sres", lang: "uk", text: mood.resolution || "" }),
      mood.reading ? el("div", { class: "sreading jprose rpage", lang: "uk" }, readerBlocks(prose, mood.reading, "").blocks) : null,
    ] : el("div", { class: "snomood" }, el("p", { class: "sentence", text: t("state.empty") }),
      el("p", { class: "note", text: t("state.empty_note") })));
  const level = d.closeness ? Number(d.closeness.level) : NaN;
  const lv = level >= 1 && level <= 5 ? level : null;
  const meter = el("div", { class: "meter", role: "img", "aria-label": `${t("state.closeness")} ${lv ? t("state.closeness_of", { n: lv }) : ""}`.trim() },
    ...[1, 2, 3, 4, 5].map((n) => el("span", { class: lv && n <= lv ? "on" : null })));
  const bio = Array.isArray(d.biorhythms) ? d.biorhythms : [];
  const cycle = d.cycle && Array.isArray(d.cycle.phases) && d.cycle.phases.length ? d.cycle : null;
  const side = el("div", { class: "sside" },
    bio.length || cycle ? el("section", { class: "sbio", "aria-label": t("state.biorhythms") },
      el("span", { class: "kicker", text: t("state.biorhythms") }), bio.map(bioRow), cycle ? hormonalRow(cycle) : null) : null,
    el("section", { class: "sclose", "aria-label": t("state.closeness") },
      el("span", { class: "kicker", text: t("state.closeness") }),
      el("div", { class: "sclname" }, el("span", { class: "sclv", text: closenessName(lv) }),
        lv ? el("span", { class: "sclof", text: t("state.closeness_of", { n: lv }) }) : null), meter),
    el("section", { class: "splan", "aria-label": t("state.plan") },
      el("span", { class: "kicker", text: t("state.plan") }),
      el("div", { class: "splanbox" }, el("span", { class: "splan1", text: t("state.plan_placeholder") }),
        el("span", { class: "splan2", text: t("state.plan_later") }))));
  const meta = d.date ? el("span", { class: "vmeta", text: longDay(d.date) }) : null;
  paint(ctx, viewFrame([title, meta], el("div", { class: "sgrid" + (narrow ? " narrow" : "") }, moodSection, side)));
}

/* ---------- Schedule (v4.4 LUMI-317, design/v4/handoff §10) ----------
   Her timetable, read-only (/v1/schedule): what wakes her thoughts — each directive of the schedule with its rule in
   English, its last firing and its next due time, computed on the server by the scheduler's own rules (quiet hours,
   the day cap). The earliest next one is marked; a disabled one is dimmed. Times are the server's local minutes. */
const WEEKDAYS3 = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];  // the scheduler's weekday numbers, Monday first

function durText(sec) {  // 900 → "15 min", 10800 → "3 h", 5400 → "1 h 30 min"
  const n = Math.max(0, Math.round(Number(sec) || 0));
  if (n < 60) return `${n} s`;
  const h = Math.floor(n / 3600);
  const m = Math.round((n % 3600) / 60);
  return [h ? `${h} h` : "", m ? `${m} min` : ""].filter(Boolean).join(" ") || "0 min";
}

function daysText(days) {
  const set = [...new Set(days || [])].sort((a, b) => a - b);
  if (!set.length || set.length === 7) return "every day";
  if (set.join() === "0,1,2,3,4") return "weekdays";
  if (set.join() === "5,6") return "weekends";
  if (set.length === 1) return `${["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][set[0]]}s`;
  return set.map((d) => WEEKDAYS3[d]).join(", ");
}

function ruleText(tr) {  // the structured trigger in English
  if (!tr) return "";
  if (tr.kind === "every") return `every ${durText(tr.interval_s)}`;
  if (tr.kind === "idle") return `after ${durText(tr.interval_s)} of silence`;
  if (tr.kind === "at") return `${daysText(tr.days)} at ${tr.at}`;
  if (tr.kind === "between") return `${(tr.window || [])[0]}–${(tr.window || [])[1]} every ${durText(tr.interval_s)}`;
  if (tr.kind === "cron") return `cron ${tr.cron}`;
  return tr.kind;
}

function whenText(stamp, now) {  // "YYYY-MM-DDTHH:MM" against the server's now → "Today 23:41" / "Tomorrow 08:30" / "15 Nov 20:00"
  if (!stamp) return "—";
  const [day, time] = String(stamp).split("T");
  const ref = String(now || "").slice(0, 10);
  const shift = (ymd, n) => { const d = dayOf(ymd); d.setDate(d.getDate() + n); return ymdOf(d); };
  if (ref && day === ref) return `${t("date.today")} ${time}`;
  if (ref && day === shift(ref, 1)) return `${t("date.tomorrow")} ${time}`;
  if (ref && day === shift(ref, -1)) return `${t("date.yesterday")} ${time}`;
  const d = dayOf(day);
  return `${d.getDate()} ${MONTHS3[d.getMonth()]} ${time}`;
}

VIEW_KINDS.schedule = (ctx) => {
  const s = ctx.state;
  if (s.ctx !== ctx) {
    Object.assign(s, { ctx, phase: "loading", data: null, error: null, ticket: (s.ticket || 0) + 1,
      openSeeds: s.openSeeds || new Set() });
    renderScheduleView(ctx);
    scheduleLoad(ctx);
    return;
  }
  renderScheduleView(ctx);
};

async function scheduleLoad(ctx) {
  const s = ctx.state;
  const ticket = s.ticket;
  try {
    const data = await api("/v1/schedule");
    if (ticket !== s.ticket) return;
    Object.assign(s, { phase: "ok", data });
  } catch (err) {
    if (ticket !== s.ticket) return;
    Object.assign(s, { phase: "error", error: err });
  }
  renderScheduleView(ctx);
}

function scheduleReload(ctx) { ctx.state.ctx = null; VIEW_KINDS.schedule(ctx); }

/* v4.4 LUMI-320: what a row does — its topic (placeholders fill in when it fires), or a seeds row's menu behind a
   toggle; kept open per row while the view lives */
function scheduleWhat(ctx, e) {
  const s = ctx.state;
  const parts = [];
  if (e.topic) {
    parts.push(el("span", { class: "stopic", lang: "uk", text: e.topic }));
    if (/\{[a-z_]+\}/.test(e.topic)) parts.push(el("span", { class: "sph", text: t("schedule.placeholders") }));
  }
  if (Array.isArray(e.seed_lines)) {
    const key = e.name || e.directive;
    const open = s.openSeeds.has(key);
    const n = e.seed_lines.length;
    parts.push(el("button", { type: "button", class: "sseedbtn", "aria-expanded": String(open), onclick: () => {
      if (open) s.openSeeds.delete(key); else s.openSeeds.add(key);
      renderScheduleView(ctx);
    } }, n === 1 ? t("schedule.seeds_one") : t("schedule.seeds_n", { n: num(n) }), icon("chevron-down")));
    if (open) {
      parts.push(n ? el("ul", { class: "sseeds" }, e.seed_lines.map((line) => el("li", { lang: "uk", text: line })))
        : el("span", { class: "sph", text: t("schedule.no_seeds") }));
    }
  }
  return parts.length ? el("div", { class: "swhat" }, parts) : null;
}

function scheduleNext(entries) {  // the entry that fires first (the earliest next due among the enabled)
  let best = null;
  for (const e of entries) if (e.enabled && e.next_due && (!best || e.next_due < best.next_due)) best = e;
  return best;
}

function renderScheduleView(ctx) {
  const s = ctx.state;
  const narrow = ctx.narrow();
  const title = el("h2", { class: "vtitle", text: t("schedule.title") });
  if (s.phase === "loading") { paint(ctx, viewFrame([title], viewLoading(t("schedule.loading")))); return; }
  if (s.phase === "error") {
    paint(ctx, viewFrame([title], viewError(t("schedule.error_title"), s.error, () => scheduleReload(ctx))));
    return;
  }
  const d = s.data || {};
  const entries = Array.isArray(d.entries) ? d.entries : [];
  const now = d.now || "";
  const meta = el("span", { class: "vmeta", text: t("schedule.summary", { time: now.slice(11, 16) }) });
  if (!entries.length) {
    paint(ctx, viewFrame([title, meta], viewEmpty(t("schedule.title"), t("schedule.empty"), t("schedule.empty_note"))));
    return;
  }
  const next = scheduleNext(entries);
  const enabled = entries.filter((e) => e.enabled).length;
  const q = Array.isArray(d.quiet_hours) ? d.quiet_hours : null;
  const hh = (h) => `${String(h).padStart(2, "0")}:00`;
  const cells = [
    ["schedule.quiet_hours", q ? `${hh(q[0])} – ${hh(q[1])}` : "—", q ? t("schedule.quiet_note") : ""],
    ["schedule.day_cap", num(d.day_cap), t("schedule.fired_today", { n: num(d.fired_today) })],
    ["schedule.directives", num(entries.length),
      enabled === entries.length ? t("schedule.all_enabled") : t("schedule.some_enabled", { n: num(enabled) })],
    ["schedule.next", next ? next.name || next.directive : "—",
      next ? `${next.next_due.slice(11, 16)}${next.if_silent ? ` · ${t("schedule.if_silent")}` : ""}` : ""],
  ];
  const summary = el("div", { class: "ssum" + (narrow ? " narrow" : "") }, cells.map(([k, v, note]) =>
    el("div", { class: "scell" }, el("span", { class: "kicker", text: t(k) }),
      el("div", { class: "sval" + (k === "schedule.next" ? " mono" : ""), text: v }), note ? el("div", { class: "snote", text: note }) : null)));
  const nextTag = () => el("span", { class: "snext", text: t("schedule.next") });
  const rows = entries.map((e) => {
    const isNext = e === next;
    const due = el("span", { class: "sdue" }, e.enabled ? whenText(e.next_due, now) : "—",
      e.enabled && e.if_silent ? el("span", { class: "scond", text: ` · ${t("schedule.if_silent")}` }) : null);
    const cls = "srow" + (isNext ? " next" : "") + (e.enabled ? "" : " off") + (narrow ? " narrow" : "");
    const name = el("span", { class: "sname", text: e.name || e.directive });
    if (narrow) {
      return el("div", { class: cls },
        el("div", { class: "sline" }, name, el("span", { class: "kicker", text: e.directive }), isNext ? nextTag() : null),
        el("span", { class: "srule", text: ruleText(e.trigger) }), scheduleWhat(ctx, e),
        el("span", { class: "swhen", text: t("schedule.last_next", { last: whenText(e.last_fired, now),
          next: e.enabled ? whenText(e.next_due, now) : "—" }) }));
    }
    return el("div", { class: cls }, el("span", { class: "snamecell" }, name, isNext ? nextTag() : null),
      el("span", { class: "kicker sdir", text: e.directive }),
      el("div", { class: "srulecell" }, el("span", { class: "srule", text: ruleText(e.trigger) }), scheduleWhat(ctx, e)),
      el("span", { class: "slast", text: whenText(e.last_fired, now) }), due);
  });
  const head = narrow ? null : el("div", { class: "sthead" }, ["schedule.col_name", "schedule.col_directive", "schedule.col_rule",
    "schedule.col_last", "schedule.col_next"].map((k) => el("span", { class: "kicker", text: t(k) })));
  const before = ctx.host.querySelector(".sched");  // a toggle re-renders: keep the scroll where it was
  const top = before && before.parentElement ? before.parentElement.scrollTop : 0;
  paint(ctx, viewFrame([title, meta], el("div", { class: "sched" }, summary, head, rows,
    el("p", { class: "sfoot", text: t("schedule.footnote") }))));
  const after = ctx.host.querySelector(".sched");
  if (after && after.parentElement) after.parentElement.scrollTop = top;
}

function typing(target) {  // the focus is in a field (the keys belong to it)
  return !!(target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)));
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
  const mic = $("mic");  // v4.7: dictation
  mic.addEventListener("pointerdown", micDown);
  mic.addEventListener("pointerup", micUp);
  mic.addEventListener("pointerleave", () => { if (dict.press && !dict.press.stop) micUp(); });
  mic.addEventListener("click", (ev) => {  // a keyboard press on the focused button (no pointer): a toggle
    if (ev.detail !== 0) return;
    if (dict.state === "idle") dictStart(); else dictStop();
  });
  try { dict.sendWhenStop = localStorage.getItem(DICT_STORE) === "on"; } catch (e) { /* off */ }
  document.addEventListener("keyup", (ev) => { micKey(ev); });
  $("composer").addEventListener("submit", (ev) => { ev.preventDefault(); const text = input.value; input.value = ""; grow(); updateSlash(); submit(text); });
  input.addEventListener("keydown", onKey);
  input.addEventListener("input", () => { grow(); updateSlash(); renderInput(); });
  input.addEventListener("blur", () => { st.slash.open = false; renderSlash(); });
  document.addEventListener("keydown", (ev) => {
    if (micKey(ev)) return;  // v4.7: ⌥Space dictates
    if (ev.key === "Escape" && dict.state !== "idle") { ev.preventDefault(); dictCancel(); return; }  // v4.7: cancel
    if (ev.key === "Escape" && voice.now && !ev.defaultPrevented) { voiceStop(); return; }  // v4.6: Esc stops her voice
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
    if ((ev.key === "ArrowLeft" || ev.key === "ArrowRight") && vw.view === "journal" && places.main.ctx
        && !ev.defaultPrevented && !ev.metaKey && !ev.ctrlKey && !ev.altKey && !ev.shiftKey && !typing(ev.target)) {
      ev.preventDefault();  // v4.3: ← → the previous / next day, when the focus isn't in a field
      journalStep(places.main.ctx, ev.key === "ArrowLeft" ? -1 : 1);
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
  dictCheck();  // v4.7: can this window record?
}

start();
