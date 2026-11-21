// The Lumi admin panel — vanilla JS, no bundler. v2.10 watch, v2.11 configure, v5.1 the shell (design/v5/handoff/):
// the attention strip, the rail with the server and the agent areas, the header with the instance switcher and the
// theme, the shared states, the confirmation dialog, the toast, the keyboard and deep links.
// Everything rendered goes through textContent (no raw HTML from data), so a log line is shown, never run. The panel's
// token lives in sessionStorage only. A secret is never shown: the API gives set / unset + the last four characters,
// and a new value goes one way. Every interface string of the shell comes from strings.json.
"use strict";

const $ = (sel) => document.querySelector(sel);
const TOKEN_KEY = "lumi-admin-token";
const UI_KEY = "lumi-admin-ui";  // the view, the instance and the theme — a per-browser convenience
const REFRESH_MS = 30000;
// Until the registry names its agents (LUMI-342: GET /api/agents), the one agent the box runs.
const DEFAULT_AGENT = { id: "lili", name: "Lili", instances: ["prod", "dev"] };

const state = {
  token: sessionStorage.getItem(TOKEN_KEY) || "", view: "overview", instance: "dev", theme: "dark",
  instances: [], versions: {}, lastAction: null, data: { overview: null, health: null, alerts: null },
  lastOk: {}, nextRefresh: 0, loadedAt: null, agents: [DEFAULT_AGENT], agent: DEFAULT_AGENT.id, picker: false,
  usageRange: "30d", toolsRange: "30d", tool: null,  // v5.1 LUMI-345: the Usage and Tools views' choices
};
let STRINGS = {};
let dialogOpen = false;
let toastTimer = null;
let ticker = null;

// --- strings -------------------------------------------------------------------------------------------------
function t(key, vars = {}) {  // a dotted key of strings.json, its {placeholders} filled; a missing key shows itself
  const v = key.split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), STRINGS);
  if (typeof v !== "string") return key;
  return v.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) node.append(c instanceof Node ? c : document.createTextNode(c ?? ""));
  return node;
}

async function api(path, opts = {}) {
  const resp = await fetch(path, { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${state.token}`,
                                                       "Content-Type": "application/json" } });
  if (resp.status === 401) { logout(t("signin.refused")); throw new Error("401"); }
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(body.detail || `HTTP ${resp.status}`);
  return body;
}

// --- formatting ----------------------------------------------------------------------------------------------
const dur = (s) => s == null ? "—" : s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min`
  : s < 172800 ? `${(s / 3600).toFixed(1)} h` : `${(s / 86400).toFixed(1)} d`;
const bytes = (n) => n == null ? "—" : n < 1e6 ? `${(n / 1e3).toFixed(0)} KB` : n < 1e9 ? `${(n / 1e6).toFixed(1)} MB`
  : `${(n / 1e9).toFixed(2)} GB`;
const gb = (n) => n == null ? "—" : `${(n / 1e9).toFixed(1)} GB`;
const usd = (x) => x == null ? "—" : `$${x.toFixed(x < 1 ? 4 : 2)}`;
const ms = (x) => x == null ? "—" : x < 1000 ? `${Math.round(x)} ms` : `${(x / 1000).toFixed(1)} s`;
const tokens = (b) => b ? (b.input + b.output + b.cache_read + b.cache_write).toLocaleString() : "—";
const badge = (status, text) => el("span", { class: `badge ${status}` }, text ?? status);
const clock = (d) => d.toTimeString().slice(0, 8);
const TONE = { green: "ok", amber: "warn", red: "bad", off: "off" };  // a check's status → its state color

// --- navigation: the views, the areas, deep links --------------------------------------------------------------
// A view is listed once its phase lands (no dead entries). `both`: it shows prod and dev side by side and ignores
// the switcher (no red edge); `section`: the element it renders into.
const NAV = {
  top: [{ id: "overview", label: "nav.overview", icon: "squares-four", both: true, section: "overview" }],
  server: [
    { name: "nav.groups.operations", items: [
      { id: "health", label: "nav.views.health", icon: "heartbeat", both: true, section: "health" },
      { id: "containers", label: "nav.views.containers", icon: "cube", section: "services" },
      { id: "logs", label: "nav.views.logs", icon: "scroll", section: "logs" }] },
    { name: "nav.groups.configuration", items: [
      { id: "settings", label: "nav.views.settings", icon: "sliders-horizontal", section: "settings" },
      { id: "secrets", label: "nav.views.secrets", icon: "key", section: "secrets" },
      { id: "confighistory", label: "nav.views.configHistory", icon: "clock-counter-clockwise", section: "history" },
      { id: "promote", label: "nav.views.promote", icon: "arrow-fat-lines-up", both: true, section: "promote" }] },
    { name: "nav.groups.audit", items: [
      { id: "audit", label: "nav.views.audit", icon: "fingerprint", both: true, section: "audit" }] }],
  agent: [
    { name: "nav.groups.howSheRuns", items: [
      { id: "usage", label: "nav.views.usage", icon: "chart-bar", both: true, section: "usage" },
      { id: "tools", label: "nav.views.tools", icon: "wrench", both: true, section: "tools" },
      { id: "schedule", label: "nav.views.scheduleModels", icon: "calendar-blank", section: "sections" }] }],
};
const BODY = { logs: "#log-lines" };  // a section with fixed controls renders into its body

function navItems() {
  return [...NAV.top, ...NAV.server.flatMap((g) => g.items), ...NAV.agent.flatMap((g) => g.items)];
}

function navIds() {
  return { server: NAV.server.flatMap((g) => g.items.map((i) => i.id)),
           agent: NAV.agent.flatMap((g) => g.items.map((i) => i.id)) };
}

function viewDef(id) { return navItems().find((i) => i.id === id) || NAV.top[0]; }

function areaOf(id) {
  const ids = navIds();
  return ids.server.includes(id) ? "server" : ids.agent.includes(id) ? "agent" : "top";
}

function hashFor(view, area, agent) {
  return area === "agent" ? `#/agent/${agent}/${view}` : area === "server" ? `#/server/${view}` : "#/overview";
}

// "#/server/logs", "#/agent/lili/usage", "#/overview" → { view, agent? }; anything else (an unknown view or agent, a
// missing part) → null, so a stale link falls back to the saved view instead of a blank page.
function parseHash(hash, ids, agents) {
  const parts = String(hash || "").replace(/^#\/?/, "").split("?")[0].split("/").filter(Boolean);
  if (parts.length === 1 && parts[0] === "overview") return { view: "overview" };
  if (parts.length === 2 && parts[0] === "server" && ids.server.includes(parts[1])) return { view: parts[1] };
  if (parts.length === 3 && parts[0] === "agent" && agents.includes(parts[1]) && ids.agent.includes(parts[2])) {
    return { view: parts[2], agent: parts[1] };
  }
  return null;
}

function nextView(ids, current, step) {  // J / K: the next or previous view, wrapping round
  const i = Math.max(0, ids.indexOf(current));
  return ids[(i + step + ids.length) % ids.length];
}

// --- the attention strip and the rail's badges (pure: health + overview in, items out) --------------------------
function runStart(history, status) {  // when the check's current state began: the first ts of its last run
  let since = null;
  for (let i = (history || []).length - 1; i >= 0; i--) {
    if (history[i][1] !== status) break;
    since = history[i][0];
  }
  return since;
}

function attentionItems(health, overview) {
  const out = [];
  const checked = (health && health.instances) || {};
  for (const inst of (overview && overview.instances) || []) {
    if (!inst.up && !(checked[inst.name] || []).length) {
      out.push({ inst: inst.name, kind: "panel", tone: "bad", check: null, detail: "", since: null, to: "health" });
    }
  }
  for (const [name, checks] of Object.entries(checked)) {
    for (const c of checks) {
      const tone = TONE[c.status];
      if (tone !== "bad" && tone !== "warn") continue;
      out.push({ inst: name, kind: "check", tone, check: c.name, detail: c.detail || "",
                 since: runStart(c.history, c.status), to: "health" });
    }
  }
  return out.sort((a, b) => (a.tone === b.tone ? 0 : a.tone === "bad" ? -1 : 1));
}

function railBadges(health, overview) {
  const out = {};
  const checks = Object.values((health && health.instances) || {}).flat();
  const red = checks.filter((c) => c.status === "red").length, amber = checks.filter((c) => c.status === "amber").length;
  if (red + amber) out.health = { n: red + amber, tone: red ? "bad" : "warn" };
  const down = ((overview && overview.instances) || []).flatMap((i) => Object.values(i.services || {}))
    .filter((s) => s !== "running").length;
  if (down) out.containers = { n: down, tone: "bad" };
  return out;
}

const KIND_KEYS = { check: "strip.kinds.check", panel: "strip.kinds.panel", alert: "strip.kinds.alert",
                    incident: "strip.kinds.incident" };

function ageText(iso) {
  if (!iso) return "";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  return s < 60 ? t("common.agoNow") : dur(s);
}

// --- preferences, theme --------------------------------------------------------------------------------------
function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(UI_KEY) || "{}") || {}; } catch (e) { return {}; }
}

function savePrefs() {
  try {
    localStorage.setItem(UI_KEY, JSON.stringify({ view: state.view, instance: state.instance, theme: state.theme }));
  } catch (e) { /* private mode or blocked storage: the page works without it */ }
}

function applyTheme() {
  document.documentElement.dataset.theme = state.theme;
  const b = $("#theme");
  b.replaceChildren(icon(state.theme === "dark" ? "sun" : "moon", 15));
  b.title = t("common.theme");
  b.setAttribute("aria-label", t("common.theme"));
}

// --- the shared pieces: states, the confirmation dialog, the toast ---------------------------------------------
function loadingState(view, instance) {
  return el("div", { class: "state-loading", "aria-busy": "true" },
    el("div", { class: "line" }, icon("circle-notch", 14, "spin"), t("common.loading", { view, instance })),
    el("div", { class: "skel-blocks" }, el("div"), el("div"), el("div")),
    [62, 48, 70, 40, 55, 66, 38, 52].map((w) => el("div", { class: "skel-row" }, el("span"),
      el("span", { style: `width:${w}%` }), el("span"))));
}

function instanceInfo(name) {
  return ((state.data.overview && state.data.overview.instances) || []).find((i) => i.name === name) || null;
}

function lastAnswerText(name) {
  const at = state.lastOk[name];
  if (!at) return t("common.unreachable.never");
  return t("common.unreachable.lastAnswer", { time: clock(new Date(at)),
                                              ago: dur(Math.round((Date.now() - at) / 1000)) });
}

function unreachableState(view, name) {
  const info = instanceInfo(name);
  const retry = Math.max(0, Math.round((state.nextRefresh - Date.now()) / 1000));
  return el("div", { class: "state-unreach" },
    el("div", { class: "state-tile" }, icon("plugs", 22)),
    el("h2", {}, t("common.unreachable.title", { instance: name })),
    el("div", { class: "body" }, t("common.unreachable.body", { view, instance: name })),
    el("div", { class: "req" }, t("common.unreachable.request", { url: `:${(info && info.port) || "?"}/v1/health` }),
      el("br"), `${lastAnswerText(name)} · ${t("common.unreachable.retryIn", { s: retry })}`),
    el("div", { class: "state-actions" },
      el("button", { class: "btn btn-primary", type: "button", onclick: retryNow }, icon("arrow-clockwise", 14),
        t("common.unreachable.retry")),
      el("button", { class: "btn btn-secondary", type: "button", onclick: () => go("containers") }, icon("cube", 14),
        t("common.unreachable.containers")),
      el("button", { class: "btn btn-secondary", type: "button", onclick: () => go("logs") }, icon("scroll", 14),
        t("common.unreachable.logs"))));
}

function errorState(view, message) {
  return el("div", { class: "state-error", role: "alert" },
    el("div", { class: "state-tile" }, icon("warning-circle", 22)),
    el("h2", {}, t("common.error.title", { view })),
    el("div", { class: "body" }, message),
    el("div", { class: "state-actions" }, el("button", { class: "btn btn-primary", type: "button", onclick: retryNow },
      icon("arrow-clockwise", 14), t("common.error.retry"))));
}

function emptyState(iconName, title, sub, action, onAction) {
  return el("div", { class: "state-empty" }, icon(iconName, 28), el("div", { class: "t" }, title),
    el("div", { class: "s" }, sub),
    action ? el("button", { class: "btn btn-secondary", type: "button", onclick: onAction }, action) : "");
}

// The confirmation (design: the Nocturne dialog with an instance tag, an optional diff, the typed "prod"). Resolves
// to "prod" when a prod change is confirmed (the confirm button stays disabled until "prod" is typed), undefined
// when a dev change is confirmed, null when cancelled (Cancel, Esc or a click outside).
function confirmDialog({ instance, title, body = "", diff = [], action, typed = true }) {
  return new Promise((resolve) => {
    const prod = instance === "prod";
    const needsType = prod && typed;
    const input = needsType ? el("input", { class: "input", placeholder: "prod", autocomplete: "off",
                                            spellcheck: "false", "aria-label": t("common.confirm.typeToConfirm",
                                                                                  { word: "prod" }) }) : null;
    const ok = el("button", { class: `btn btn-primary ${prod ? "btn-confirm-prod" : ""}`, type: "button" },
                  action || t("common.confirm.confirm"));
    const cancel = el("button", { class: "btn btn-secondary", type: "button" }, t("common.confirm.cancel"), " ",
                      el("span", { class: "esc" }, t("common.confirm.esc")));
    const canConfirm = () => !needsType || input.value.trim() === "prod";
    const sync = () => { ok.disabled = !canConfirm(); };
    let backdrop = null;
    let settled = false;
    const close = (value) => {
      if (settled) return;
      settled = true;
      dialogOpen = false;
      document.removeEventListener("keydown", onKey, true);
      backdrop.remove();
      resolve(value);
    };
    const accept = () => { if (canConfirm()) close(prod ? "prod" : undefined); };
    const onKey = (ev) => {
      if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); close(null); }
      else if (ev.key === "Enter" && ev.target === input) { ev.preventDefault(); accept(); }
    };
    ok.addEventListener("click", accept);
    cancel.addEventListener("click", () => close(null));
    if (input) input.addEventListener("input", sync);
    const [before, after] = t("common.confirm.typeToConfirm").split("{word}");  // "Type {word} to confirm"
    const label = el("label", {}, before ?? "", el("b", {}, "prod"), after ?? "");
    const box = el("div", { class: `dialog confirm ${prod ? "prod" : "dev"}`, role: "dialog", "aria-modal": "true",
                            onclick: (ev) => ev.stopPropagation() },
      el("div", { class: "dialog-head" }, el("span", { class: `inst-tag ${prod ? "prod" : ""}` }, instance),
        el("div", { class: "dialog-title" }, title)),
      body ? el("div", { class: "dialog-body" }, body) : "",
      diff.length ? el("div", { class: "dialog-diff" }, diff.map((d) => el("div", { class: "row" },
        el("span", { class: "k" }, d.k), el("span", {}, el("span", { class: "from" }, d.from), " → ",
          el("span", { class: "to" }, d.to))))) : "",
      needsType ? el("div", { class: "field typed-field" }, label, input) : "",
      el("div", { class: "dialog-actions" }, cancel, ok));
    backdrop = el("div", { class: "dialog-backdrop", onclick: () => close(null) }, box);
    dialogOpen = true;
    document.addEventListener("keydown", onKey, true);
    $("#dialog-root").append(backdrop);
    sync();
    (input || ok).focus();
  });
}

function hideToast() { clearTimeout(toastTimer); $("#toast-root").replaceChildren(); }

function showToast(message) {
  clearTimeout(toastTimer);
  $("#toast-root").replaceChildren(el("div", { class: "toast", role: "status" }, icon("check-circle-fill", 16),
    el("span", {}, message), el("button", { class: "btn btn-ghost", type: "button", "aria-label": t("common.dismiss"),
                                           onclick: hideToast }, icon("x", 14))));
  toastTimer = setTimeout(hideToast, 4200);
}

// --- the frame: strip, rail, header ----------------------------------------------------------------------------
function agentName() {
  return (state.agents.find((a) => a.id === state.agent) || DEFAULT_AGENT).name;
}

function renderStrip() {
  const items = attentionItems(state.data.health, state.data.overview);
  const worst = items.some((i) => i.tone === "bad") ? "bad" : items.length ? "warn" : null;
  const red = items.filter((i) => i.tone === "bad").length, amber = items.length - red;
  const alerts = ((state.data.alerts && state.data.alerts.failing) || []).length;
  const strip = $("#strip");
  strip.className = `strip ${worst || ""}`.trim();
  strip.replaceChildren(
    el("span", { class: "strip-label" }, icon(worst ? "bell-simple-ringing" : "check-circle", 14),
      t(worst ? "strip.attention" : "strip.quiet")),
    el("div", { class: "strip-items" }, items.map((i) => el("button", {
      class: "strip-chip", type: "button", title: t("strip.open", { view: t("nav.views.health") }),
      onclick: () => go(i.to) },
      el("span", { class: `dot ${i.tone}` }),
      el("span", { class: `inst-tag ${i.inst === "prod" ? "prod" : ""}` }, i.inst),
      el("span", { class: "kind" }, t(KIND_KEYS[i.kind])),
      el("span", {}, i.kind === "panel" ? t("strip.unreachable", { instance: i.inst })
        : `${i.check}${i.detail ? ` — ${i.detail}` : ""}`),
      i.since ? el("span", { class: "age" }, ageText(i.since)) : "",
      icon("arrow-right", 12)))),
    el("span", { class: "strip-summary" }, t("strip.summaryChecks", { alerts, red, amber })));
}

function railItem(it, badges, top) {
  const b = badges[it.id];
  return el("button", { class: `rail-item ${top ? "top" : ""}`.trim(), type: "button",
                        "aria-current": it.id === state.view ? "page" : null, onclick: () => go(it.id) },
    icon(it.icon, 15), el("span", { class: "label-text" }, t(it.label)),
    b ? el("span", { class: `rail-badge ${b.tone}` }, String(b.n)) : "");
}

function renderRail() {
  const badges = railBadges(state.data.health, state.data.overview);
  const name = agentName();
  const initial = (name || "?").slice(0, 1).toUpperCase();
  const picker = el("div", { class: "picker-wrap" },
    el("button", { class: "picker", type: "button", "aria-expanded": state.picker ? "true" : "false",
                   onclick: () => { state.picker = !state.picker; renderRail(); } },
      el("span", { class: "initial s26" }, initial),
      el("span", { class: "names" }, el("span", { class: "name" }, name),
        el("span", { class: "sub" }, t("nav.agentPicker.count", { n: state.agents.length,
                                                                  role: t("nav.agentPicker.role") }))),
      icon("caret-up-down", 14)),
    state.picker ? el("div", { class: "popover" },
      el("button", { class: "popover-row", type: "button", onclick: () => { state.picker = false; renderRail(); } },
        el("span", { class: "initial s22" }, initial), el("span", { class: "names" }, name), icon("check", 14)),
      el("div", { class: "popover-row disabled" }, el("span", { class: "initial s22 slot" }, icon("plus", 12)),
        el("span", { class: "names" }, el("span", {}, t("nav.agentPicker.secondAgent")),
          el("span", { class: "sub" }, t("nav.agentPicker.secondAgentNote"))))) : "");
  const tail = state.token.slice(-4);
  $("#rail").replaceChildren(
    el("div", { class: "rail-brand" },
      el("div", { class: "brand-line" }, el("span", { class: "brand-name" }, "lumi"),
        el("span", { class: "brand-sub" }, "admin"), el("span", { class: "version" }, "v5")),
      el("div", { class: "host" }, location.host)),
    el("div", { class: "rail-items" },
      NAV.top.map((it) => railItem(it, badges, true)),
      el("div", { class: "rail-area" }, icon("hard-drives", 13), el("span", {}, t("nav.server"))),
      NAV.server.map((g) => [el("div", { class: "rail-group" }, t(g.name)), g.items.map((it) => railItem(it, badges))]),
      el("div", { class: "rail-agent" },
        el("div", { class: "rail-area" }, icon("user-circle", 13), el("span", {}, t("nav.agent"))),
        picker,
        NAV.agent.map((g) => [el("div", { class: "rail-group" }, t(g.name)), g.items.map((it) => railItem(it, badges))]))),
    el("div", { class: "rail-foot" },
      el("div", { class: "token" }, icon("key", 13), el("span", { class: "mono" }, t("nav.token", { tail })),
        el("button", { id: "logout", class: "btn btn-ghost", type: "button", title: t("nav.signOut"),
                       onclick: () => logout("") }, icon("sign-out", 13), t("nav.signOut"))),
      el("div", { class: "keys" }, el("span", {}, t("nav.keys.instance")), el("span", {}, t("nav.keys.view")))));
}

function renderHeader() {
  const def = viewDef(state.view), area = areaOf(def.id);
  const group = [...NAV.server, ...NAV.agent].find((g) => g.items.some((i) => i.id === def.id));
  const crumb = $("#crumb");
  crumb.textContent = area === "server" ? `${t("nav.server")} · ${t(group.name)}`
    : area === "agent" ? `${agentName()} · ${t(group.name)}` : t("overview.crumb", { host: location.hostname });
  crumb.className = `crumb ${area === "agent" ? "agent" : ""}`.trim();
  $("#title").textContent = t(def.label);
  const updated = $("#updated");
  updated.title = t(def.both ? "switcher.both" : "switcher.follows");
  updated.replaceChildren(icon(def.both ? "columns" : "arrows-clockwise", 13),
    state.loadedAt ? clock(state.loadedAt) : "");
  const sw = $("#switcher");
  sw.setAttribute("aria-label", t("switcher.label"));
  sw.replaceChildren(...state.instances.map((name) => el("button", {
    class: name === "prod" ? "prod" : "dev", type: "button", role: "radio",
    "aria-checked": name === state.instance ? "true" : "false", onclick: () => setInstance(name) },
    el("span", { class: "pip" }), el("span", { class: "name" }, t(`switcher.${name === "prod" ? "prod" : "dev"}`)),
    el("span", { class: "ver" }, state.versions[name] || "—"))));
  $("#main").classList.toggle("prod-edge", !def.both && state.instance === "prod");
}

function renderFrame() { renderStrip(); renderRail(); renderHeader(); }

// --- going somewhere -------------------------------------------------------------------------------------------
function go(view, { push = true } = {}) {
  const def = viewDef(view);
  state.view = def.id;
  state.picker = false;
  savePrefs();
  const h = hashFor(def.id, areaOf(def.id), state.agent);
  if (location.hash !== h) {
    try { push ? history.pushState(null, "", h) : history.replaceState(null, "", h); } catch (e) { location.hash = h; }
  }
  $("#scroll").scrollTop = 0;
  renderFrame();
  renderView(true);
}

function applyHash() {
  const target = parseHash(location.hash, navIds(), state.agents.map((a) => a.id));
  if (target && target.agent) state.agent = target.agent;
  if (target && target.view !== state.view) go(target.view, { push: false });
}

function setInstance(name) {
  if (!state.instances.includes(name) || name === state.instance) return;
  state.instance = name;
  savePrefs();
  renderHeader();
  if (!viewDef(state.view).both) renderView(true);
}

const VIEWS = { overview: renderOverview, health: renderHealth, containers: renderServices, logs: renderLogs,
                settings: renderSettings, secrets: renderSecrets, confighistory: renderHistory, promote: renderPromote, audit: renderAudit,
                usage: renderUsage, tools: renderTools, schedule: renderSections };

async function renderView(loading) {
  const def = viewDef(state.view);
  const section = $(`#${def.section}`);
  document.querySelectorAll(".view").forEach((s) => { s.hidden = s !== section; });
  const body = BODY[def.section] ? $(BODY[def.section]) : section;
  const inst = def.both ? t("common.bothInstances") : state.instance;
  if (loading && def.id !== "overview") body.replaceChildren(loadingState(t(def.label), inst));
  try {
    await VIEWS[def.id]();
  } catch (e) {
    if (e.message === "401" || state.view !== def.id) return;
    const info = instanceInfo(state.instance);
    body.replaceChildren(!def.both && info && !info.up ? unreachableState(t(def.label), state.instance)
      : errorState(t(def.label), e.message));
  }
}

const refresh = () => renderView(false);

async function refreshData() {
  const [overview, health, alerts] = await Promise.all([api("/api/overview"), api("/api/health").catch(() => null),
                                                        api("/api/alerts").catch(() => null)]);
  state.data = { overview, health, alerts };
  state.instances = overview.instances.map((i) => i.name);
  for (const i of overview.instances) {
    state.versions[i.name] = i.version || (i.image ? i.image.split(":").pop() : null);
    if (i.up) state.lastOk[i.name] = Date.now();
  }
  if (!state.instances.includes(state.instance)) state.instance = state.instances[0];
  state.loadedAt = new Date();
  state.nextRefresh = Date.now() + REFRESH_MS;
}

async function retryNow() {
  try { await refreshData(); } catch (e) { if (e.message === "401") return; }
  renderFrame();
  renderView(true);
}

async function tick() {
  if ($("#app").hidden) return;
  try { await refreshData(); } catch (e) { return; }
  renderFrame();
  if (state.view === "overview" || state.view === "health") refresh();
}

async function loadAgents() {  // the registry's agents once LUMI-342 serves them; until then the default one
  try {
    const data = await api("/api/agents");
    if (Array.isArray(data.agents) && data.agents.length) state.agents = data.agents;
  } catch (e) { /* not served yet: the default agent */ }
}

// --- views: the Overview (v5.1) --------------------------------------------------------------------------------
function boxStrip(box, instances) {
  const services = instances.flatMap((i) => Object.values(i.services || {}));
  const running = services.filter((s) => s === "running").length;
  const restarting = services.filter((s) => s === "restarting").length;
  const disk = Object.values((box && box.disks) || {})[0];
  const mem = box && box.memory;
  const usedMem = mem && mem.total != null && mem.available != null ? mem.total - mem.available : null;
  const cell = (label, ...rest) => el("div", { class: "box-cell" }, el("div", { class: "label" }, label), rest);
  return el("section", { class: "box-strip" },
    cell(t("overview.box"), el("div", { class: "name" }, location.hostname),
      el("div", { class: "meta" }, box && box.uptime_s != null ? t("overview.boxSub", { d: dur(box.uptime_s) }) : "")),
    cell(t("overview.load"), el("div", { class: "row-baseline" },
      el("span", { class: "big" }, box && box.load ? box.load[0].toFixed(2) : "—"),
      el("span", { class: "small" }, box && box.load ? t("overview.loadSub", { five: box.load[1].toFixed(2),
                                                                                fifteen: box.load[2].toFixed(2) }) : ""))),
    cell(t("overview.memory"), el("div", { class: "row-baseline" },
      el("span", { class: "big" }, usedMem == null ? "—" : (usedMem / 1e9).toFixed(1)),
      el("span", { class: "small" }, mem ? t("overview.memoryOf", { total: gb(mem.total) }) : "")),
      el("div", { class: "bar" }, el("span", { style: `width:${usedMem == null ? 0 : Math.round(usedMem / mem.total * 100)}%` }))),
    cell(t("overview.disk"), el("div", { class: "row-baseline" },
      el("span", { class: "big" }, disk ? `${Math.round(disk.free / disk.total * 100)} %` : "—"),
      el("span", { class: "small" }, disk ? t("overview.diskFree", { total: gb(disk.total) }) : "")),
      el("div", { class: "bar" }, el("span", { style: `width:${disk ? 100 - Math.round(disk.free / disk.total * 100) : 0}%` }))),
    cell(t("overview.docker"), el("div", { class: "row-baseline" }, el("span", { class: "big" }, String(running)),
      el("span", { class: "small" }, t("overview.dockerOf", { n: services.length }))),
      el("div", { class: `meta ${services.length - running ? "bad" : ""}`.trim() },
        t("overview.dockerSub", { stopped: services.length - running - restarting, restarting }))));
}

function instanceCard(inst, alerts) {
  const checks = inst.checks || [];
  const green = checks.filter((c) => c.status === "green").length;
  const red = checks.filter((c) => c.status === "red"), amber = checks.filter((c) => c.status === "amber");
  const services = Object.values(inst.services || {});
  const running = services.filter((s) => s === "running").length;
  const restarts = Object.values(inst.restarts || {}).reduce((a, n) => a + (n || 0), 0);
  const watched = alerts && alerts.on && (alerts.instances || []).includes(inst.name);
  const firing = watched && (alerts.failing || []).includes(inst.name);
  const reachable = inst.up || inst.version != null;
  const tone = !reachable ? "off" : inst.up ? "ok" : "bad";
  const stateWord = !reachable ? t("overview.cantReach") : inst.up ? t("overview.up") : t("overview.down");
  const metric = (k, v, sub, vTone, to) => el("button", { class: "metric", type: "button", onclick: () => go(to) },
    el("span", { class: "k" }, k), el("span", { class: `v ${vTone || ""}`.trim() }, v), el("span", { class: "s" }, sub));
  const notGreen = [...red, ...amber].map((c) => c.name).join(", ");
  let banner = "";
  if (!reachable) {
    banner = el("div", { class: "banner off" }, icon("plugs", 16),
      el("div", { class: "txt" }, el("span", {}, t("common.unreachable.title", { instance: inst.name })),
        el("span", { class: "m" }, t("overview.bannerUnreachMeta", { port: inst.port || "?", last: lastAnswerText(inst.name) }))),
      el("button", { class: "btn btn-secondary", type: "button", onclick: retryNow }, t("overview.retry")));
  } else if (red.length) {
    banner = el("div", { class: "banner bad" }, icon("warning-diamond", 16),
      el("div", { class: "txt" }, el("span", {}, t("overview.bannerRed", { check: red[0].name, detail: red[0].detail })),
        el("span", { class: "m" }, t("overview.bannerRedMeta", { n: red.length }))),
      el("button", { class: "btn btn-secondary", type: "button", onclick: () => go("health") }, t("nav.views.health")));
  }
  const det = inst.state || {};
  const mood = inst.mood_today || {};
  return el("div", { class: `inst-card ${inst.name === "prod" ? "prod" : ""}`.trim() },
    el("div", { class: "inst-head" }, el("span", { class: `halo ${tone}` }), el("span", { class: "inst-name" }, inst.name),
      el("span", { class: `inst-state ${tone}` }, stateWord),
      el("span", { class: "chip-ver" }, inst.version || (inst.image ? inst.image.split(":").pop() : "—")),
      el("span", { class: "inst-uptime" }, inst.uptime_s != null ? t("overview.upFor", { d: dur(inst.uptime_s) }) : "")),
    el("div", { class: "metrics" },
      metric(t("overview.metrics.checks"), checks.length ? `${green}/${checks.length}` : "—",
        !checks.length ? "" : notGreen || t("overview.allGreen"), red.length ? "bad" : amber.length ? "warn" : "", "health"),
      metric(t("overview.metrics.containers"), `${running}/${services.length}`, t("overview.restarts", { n: restarts }),
        running < services.length ? "bad" : "", "containers"),
      metric(t("overview.metrics.alerts"), watched ? (firing ? "1" : "0") : "—",
        !watched ? t("overview.alertsOff") : firing ? t("overview.firing") : t("overview.noneFiring"),
        firing ? "bad" : "", "health"),
      metric(t("overview.metrics.errors"), String((inst.errors || {}).errors_24h ?? "—"),
        t("overview.modelFailures", { n: (inst.errors || {}).model_failures_1h ?? 0 }),
        (inst.errors || {}).errors_24h ? "warn" : "", "logs")),
    checks.length ? el("div", { class: "checkstrip" },
      el("div", { class: "cells" }, checks.map((c) => el("span", { class: TONE[c.status] || "off",
                                                                     title: `${c.name} — ${c.status}` }))),
      el("div", { class: "ends" }, el("span", {}, checks[0].name), el("span", {}, t("overview.checksN", { n: checks.length })),
        el("span", {}, checks[checks.length - 1].name))) : "",
    banner,
    el("dl", { class: "details" },
      el("dt", {}, t("overview.details.model")), el("dd", {}, `${det.provider ?? "?"} / ${det.model ?? "?"}`
        + (det.profile ? ` · ${t("overview.details.profile", { profile: det.profile })}` : "")),
      el("dt", {}, t("overview.details.mood")), el("dd", {}, (mood.present ? (mood.complete
        ? t("overview.details.moodComplete") : t("overview.details.moodIncomplete")) : t("overview.details.moodNotYet"))
        + (det.theme ? ` · ${t("overview.details.theme", { theme: det.theme })}` : "")),
      el("dt", {}, t("overview.details.latency")), el("dd", {}, t("overview.details.latencyLine", {
        median: ms((inst.latency || {}).turn_median_ms), p95: ms((inst.latency || {}).turn_p95_ms),
        model: ms((inst.latency || {}).model_median_ms) })),
      el("dt", {}, t("overview.details.dataRoot")), el("dd", {}, bytes(inst.data_size))),
    el("div", { class: "ghost-links" },
      el("button", { class: "btn btn-ghost", type: "button", onclick: () => go("health") }, icon("heartbeat", 13),
        t("nav.views.health")),
      el("button", { class: "btn btn-ghost", type: "button", onclick: () => go("containers") }, icon("cube", 13),
        t("nav.views.containers")),
      el("button", { class: "btn btn-ghost", type: "button", onclick: () => go("logs") }, icon("scroll", 13),
        t("nav.views.logs"))));
}

function agentCard(instances, month) {
  const name = agentName();
  const monthCost = (n) => month && month.by_instance ? month.by_instance[n] : null;
  return el("div", { class: "agent-card" },
    el("div", { class: "agent-head" }, el("span", { class: "initial s30" }, name.slice(0, 1).toUpperCase()),
      el("div", { class: "names" }, el("span", { class: "n" }, name), el("span", { class: "r" }, t("nav.agentPicker.role"))),
      el("button", { class: "btn btn-ghost btn-small", type: "button", style: "margin-left:auto",
                     onclick: () => go("usage") }, t("nav.views.usage"), icon("arrow-right", 12))),
    el("div", { class: "agent-inst" }, instances.map((i) => el("div", {},
      el("span", { class: `inst-tag ${i.name === "prod" ? "prod" : ""}`.trim() }, i.name),
      el("span", { class: "cost" }, month ? usd(monthCost(i.name)) : usd((i.today || {}).cost)),
      el("span", { class: "turns" }, (month ? t("overview.thisMonth") + " · " : "")
        + t("overview.turns", { n: i.turns_today ?? 0 })),
      el("span", { class: "sub" }, i.last_turn_age_s == null ? t("overview.noTurn")
        : t("overview.lastTurn", { ago: t("common.ago", { t: dur(i.last_turn_age_s) }) }))))));
}

async function renderOverview() {
  if (!state.data.overview) await refreshData();
  const data = state.data.overview;
  const instances = data.instances || [];
  if (!instances.length) {
    $("#overview").replaceChildren(emptyState("squares-four", t("overview.instances"), t("common.unreachable.never")));
    return;
  }
  const usage = await api(`/api/agents/${state.agent}/usage?range=month`).catch(() => null);
  const month = usage ? usage.month : null;
  $("#overview").replaceChildren(el("div", { class: "ov" },
    boxStrip(data.box, instances),
    el("section", {}, el("div", { class: "ov-head" }, el("h2", {}, t("overview.instances")),
      el("span", { class: "sub" }, t("overview.checksRefresh"))),
      el("div", { class: "cards", style: "margin-top:10px" }, instances.map((i) => instanceCard(i, state.data.alerts)))),
    el("section", {}, el("div", { class: "ov-head" }, el("h2", {}, t("overview.agents")),
      el("span", { class: "sub" }, t("overview.today"))),
      el("div", { class: "agents-row", style: "margin-top:10px" }, agentCard(instances, month),
        el("div", { class: "agent-slot" }, el("span", { class: "initial s30 slot" }, icon("plus", 14)),
          el("span", { class: "t" }, t("overview.secondAgent")), el("span", { class: "s" }, t("overview.secondAgentNote"))))),
    usage ? monthCard(usage) : ""));
}

// The month on the Overview (design: "<Month> · all agents"): to date, a bar per day so far, the forecast, the
// previous month. Every agent's sum — one agent today.
function monthCard(usage) {
  const m = usage.month;
  const days = (usage.per_day || []).filter((d) => !d.future);
  const max = Math.max(0.0001, ...days.map((d) => d.total || 0));
  const today = days.length ? days[days.length - 1].day : "";
  return el("section", {}, el("div", { class: "ov-head" }, el("h2", {}, t("overview.month", { month: m.label })),
    el("span", { class: "sub" }, t("overview.monthSub"))),
    el("div", { class: "month-card" },
      el("div", { class: "month-total" }, el("span", { class: "big" }, usd(m.to_date)),
        el("span", { class: "small" }, t("overview.toDate"))),
      el("div", { class: "month-bars", role: "img", "aria-label": t("usage.costPerDay") },
        days.map((d) => el("span", { class: `${d.day === today ? "now" : ""} ${d.gap ? "gap" : ""}`.trim(),
          style: `height:${d.gap ? 2 : Math.max(2, Math.round((d.total || 0) / max * 100))}%`,
          title: `${d.day}: ${d.gap ? t("usage.noData") : usd(d.total)}` }))),
      el("div", { class: "month-meta" },
        el("span", {}, t("overview.forecastLine", { value: usd(m.forecast) })),
        el("span", {}, t("overview.previousLine", { month: m.previous_label, value: usd(m.previous) })))));
}

// --- views carried over from v2.10–v2.11 (their look is restyled in LUMI-344 / LUMI-345) ------------------------
async function renderHealth() {
  const data = await api("/api/health");
  const al = await api("/api/alerts");
  const alerts = al.on ? t("healthView.alertsOn", { instances: al.instances.join(", ") })
    + (al.failing.length ? ` · ${t("healthView.failingNow", { instances: al.failing.join(", ") })}` : "")
    : (al.why || t("healthView.alertsOff"));
  const cards = Object.entries(data.instances).map(([name, checks]) => el("div",
    { class: `card ${name === "prod" ? "prod" : ""}` }, el("h2", {}, name),
    el("table", {}, el("tr", {}, ["check", "status", "detail", "lastHour"].map((h) => el("th", {}, t(`healthView.columns.${h}`)))),
      checks.map((c) => el("tr", {}, el("td", {}, c.name), el("td", {}, badge(c.status)), el("td", { class: "detail" }, c.detail),
        el("td", {}, el("span", { class: "strip-cells" },
          c.history.slice(-60).map(([ts, s]) => el("i", { class: s, title: `${ts} ${s}` })))))))));
  $("#health").replaceChildren(el("p", { class: "muted" }, t("healthView.checked", { when: data.last_run ?? t("healthView.notYet") })
    + ` · ${alerts}`), el("div", { class: "grid" }, cards));
}

async function renderServices() {
  const all = await api("/api/services");
  const name = state.instance;
  const rows = Object.entries(all[name] || {}).map(([service, info]) => el("tr", {},
    el("td", {}, service), el("td", {}, badge(info.state === "running" ? "green" : info.state === "restarting" ? "amber"
      : info.state === "unknown" ? "off" : "red", info.state)),
    el("td", {}, info.uptime_s == null ? "—" : dur(info.uptime_s)), el("td", {}, String(info.restart_count ?? "—")),
    el("td", {}, info.image ?? "—"),
    el("td", { class: "actions" }, ["start", "stop", "restart"].map((a) =>
      el("button", { onclick: () => act(name, service, a) }, t(`containers.${a}`))))));
  const cols = ["service", "state", "up", "restarts", "image", "actions"];
  $("#services").replaceChildren(el("div", { class: `card ${name === "prod" ? "prod" : ""}` }, el("h2", {}, name),
    el("table", {}, el("tr", {}, cols.map((h) => el("th", {}, t(`containers.table.${h}`)))),
      rows), actionLine()));
}

// The last action's outcome survives every re-render (code review #1: it was wiped after 1.5 s, and a refused prod
// restart looked done).
function actionLine() {
  const a = state.lastAction;
  return el("p", { id: "act-result", class: a && a.error ? "error" : "muted" },
            a ? `${a.at} — ${a.text}` : "");
}

function setAction(text, error = false) {
  state.lastAction = { text, error, at: new Date().toTimeString().slice(0, 8) };
  const line = $("#act-result");
  if (line) line.replaceWith(actionLine());
}

async function act(name, service, action) {
  const verb = t(`containers.${action}`);
  const what = t("actions.service", { action: verb, instance: name, service });
  const confirm = await askConfirm(name, t("containers.actTitle", { action: verb, service }),
                                   { body: t("containers.actBodyProd", { action: verb, service }), action: verb });
  if (confirm === null) { setAction(t("actions.cancelled", { what }), true); return; }
  setAction(t("actions.running", { what }));
  try {
    const r = await api(`/api/services/${name}/${service}/${action}`, { method: "POST",
                                                                         body: JSON.stringify({ confirm }) });
    setAction(t("actions.serviceDone", { what, result: r.result }));
    showToast(t("actions.serviceDone", { what, result: r.result }));
  } catch (e) { setAction(t("actions.refused", { what, error: e.message }), true); }
  setTimeout(renderServices, 1500);
}

// --- v5.1 LUMI-345: the Usage and Tools views (both instances of the agent, summed) ----------------------------------
const PROVIDERS = ["anthropic", "google", "elevenlabs", "deepgram", "openai", "deepseek", "minimax", "other"];
const RANGES = ["7d", "30d", "90d", "month"];

// A round axis maximum and its step for a money axis (gridlines every step).
function niceScale(max) {
  const steps = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500];
  const step = steps.find((s) => max / s <= 5) || 1000;
  return { step, top: Math.max(step, Math.ceil(max / step) * step) };
}

// The geometry of the stacked bars (pure): one column per day, its providers' segments bottom-up; a gap day and a
// forecast day are marked, not drawn as data.
function stackedBars(days, providers, width, height, top) {
  const slot = width / Math.max(1, days.length);
  return days.map((d, i) => {
    const x = i * slot + slot * 0.14, w = slot * 0.72;
    if (d.future) return { day: d.day, x, w, future: true, h: Math.round((d.forecast || 0) / top * height) };
    if (d.gap) return { day: d.day, x, w, gap: true, segments: [] };
    let y = height;
    const segments = providers.filter((p) => (d.providers || {})[p] > 0).map((p) => {
      const h = d.providers[p] / top * height;
      y -= h;
      return { provider: p, y, h, cost: d.providers[p] };
    });
    return { day: d.day, x, w, segments, total: d.total };
  });
}

// The line and the band of a series with gaps (pure): one path per run of days with data, so a gap stays a gap.
function runPaths(points, key, width, height, top) {
  const slot = width / Math.max(1, points.length);
  const runs = [];
  let cur = [];
  points.forEach((p, i) => {
    if (p.gap || p[key] == null) { if (cur.length) runs.push(cur); cur = []; return; }
    cur.push([i * slot + slot / 2, height - Math.min(1, p[key] / top) * height]);
  });
  if (cur.length) runs.push(cur);
  return runs.map((run) => run.map(([x, y], j) => `${j ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" "));
}

function bandPaths(points, lo, hi, width, height, top) {
  const slot = width / Math.max(1, points.length);
  const runs = [];
  let cur = [];
  points.forEach((p, i) => {
    if (p.gap || p[lo] == null || p[hi] == null) { if (cur.length) runs.push(cur); cur = []; return; }
    cur.push([i * slot + slot / 2, height - Math.min(1, p[lo] / top) * height, height - Math.min(1, p[hi] / top) * height]);
  });
  if (cur.length) runs.push(cur);
  return runs.map((run) => {
    const up = run.map(([x, , yh], j) => `${j ? "L" : "M"}${x.toFixed(1)} ${yh.toFixed(1)}`).join(" ");
    const down = run.slice().reverse().map(([x, yl]) => `L${x.toFixed(1)} ${yl.toFixed(1)}`).join(" ");
    return `${up} ${down} Z`;
  });
}

function svgEl(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) node.setAttribute(k, v);
  for (const c of children) if (c) node.append(c);
  return node;
}

function svgTitle(text) { const n = document.createElementNS(SVG_NS, "title"); n.textContent = text; return n; }

function rangeSeg(group, current, onPick, monthLabel) {
  return el("div", { class: "seg", role: "radiogroup", "aria-label": t("usage.rangeLabel") }, RANGES.map((r) => el("label", { class: "seg-opt" },
    el("input", { type: "radio", name: `range-${group}`, value: r, ...(r === current ? { checked: "" } : {}),
                  onchange: () => onPick(r) }),
    r === "month" ? (monthLabel || t("usage.rangeMonth")) : t(`usage.range${r}`))));
}

function costChart(u) {
  const W = 720, H = 150, AX = 44;
  const days = u.per_day || [];
  const max = Math.max(0.0001, ...days.map((d) => d.future ? d.forecast || 0 : d.total || 0));
  const { step, top } = niceScale(max);
  const providers = PROVIDERS.filter((p) => (u.providers || []).some((x) => x.provider === p));
  const svg = svgEl("svg", { viewBox: `0 0 ${W + AX} ${H + 24}`, class: "u-chart", preserveAspectRatio: "none",
                             role: "img", "aria-label": t("usage.costPerDay") });
  for (let v = 0; v <= top + 1e-9; v += step) {
    const y = H - v / top * H;
    svg.append(svgEl("line", { x1: AX, x2: AX + W, y1: y, y2: y, class: "grid" }),
      svgEl("text", { x: AX - 6, y: y + 4, "text-anchor": "end", class: "axis" }, document.createTextNode(usd(v))));
  }
  const g = svgEl("g", { transform: `translate(${AX},0)` });
  const every = Math.max(1, Math.ceil(days.length / 10));
  stackedBars(days, providers, W, H, top).forEach((b, i) => {
    if (b.future) g.append(svgEl("rect", { x: b.x, y: H - b.h, width: b.w, height: Math.max(1, b.h), class: "forecast" },
      svgTitle(`${b.day}: ${t("usage.forecastDay", { value: usd(days[i].forecast) })}`)));
    else if (b.gap) g.append(svgEl("rect", { x: b.x, y: H - 3, width: b.w, height: 3, class: "gap" },
      svgTitle(`${b.day}: ${t("usage.noData")}`)));
    else b.segments.forEach((s) => g.append(svgEl("rect", { x: b.x, y: s.y, width: b.w, height: Math.max(0.5, s.h),
      class: `prov prov-${s.provider}` }, svgTitle(`${b.day} · ${s.provider}: ${usd(s.cost)}`))));
    if (i % every === 0 || i === days.length - 1) g.append(svgEl("text", { x: b.x + b.w / 2, y: H + 16,
      "text-anchor": "middle", class: "axis" }, document.createTextNode(b.day.slice(5))));
  });
  svg.append(g);
  const keyed = Object.fromEntries((u.providers || []).map((p) => [p.provider, p.keyed]));
  const legend = el("div", { class: "u-legend" }, providers.map((p) => el("span", {},
    el("i", { class: `prov-${p} ${keyed[p] === false ? "nokey" : ""}`.trim() }), p)),
    days.some((d) => d.future) ? el("span", {}, el("i", { class: "forecast" }), t("usage.forecast")) : "");
  return el("div", { class: "u-card u-wide" }, el("div", { class: "u-head" }, el("h3", {}, t("usage.costPerDay")),
    legend), svg);
}

function barList(title, rows, { money = true } = {}) {
  const max = Math.max(0.0001, ...rows.map((r) => r.cost));
  return el("div", { class: "u-card" }, el("h3", {}, title),
    rows.length ? el("div", { class: "u-bars" }, rows.slice(0, 8).map((r) => el("div", { class: "u-bar" },
      el("span", { class: "k", title: r.key }, r.key),
      el("span", { class: "v" }, r.unpriced && !r.cost ? t("usage.notPriced") : money ? usd(r.cost) : String(r.calls)),
      el("span", { class: "p" }, `${Math.round(r.share * 100)} %`),
      el("span", { class: "track" }, el("span", { style: `width:${Math.round(r.cost / max * 100)}%` })))))
      : el("p", { class: "u-none" }, t("usage.noData")));
}

function cacheCard(c) {
  const ratio = c.ratio || 0, R = 38, C = 2 * Math.PI * R;
  const svg = svgEl("svg", { viewBox: "0 0 96 96", class: "u-donut", role: "img",
                             "aria-label": t("usage.cacheHit", { pct: Math.round(ratio * 100) }) },
    svgEl("circle", { cx: 48, cy: 48, r: R, class: "track" }),
    svgEl("circle", { cx: 48, cy: 48, r: R, class: "fill", "stroke-dasharray": `${(ratio * C).toFixed(1)} ${C.toFixed(1)}`,
                      transform: "rotate(-90 48 48)" }));
  return el("div", { class: "u-card" }, el("h3", {}, t("usage.cache")), el("div", { class: "u-cache" }, svg,
    el("div", {}, el("div", { class: "big" }, c.ratio == null ? "—" : `${Math.round(ratio * 100)} %`),
      el("div", { class: "small" }, t("usage.cacheHitSub")),
      el("div", { class: "small" }, t("usage.savedRange", { value: usd(c.saved) })))));
}

function latencyCard(u) {
  const W = 360, H = 110;
  const pts = u.latency || [];
  const max = Math.max(1, ...pts.filter((p) => !p.gap).map((p) => p.p95 || p.median || 0));
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "u-chart", preserveAspectRatio: "none", role: "img",
                             "aria-label": t("usage.latency") });
  bandPaths(pts, "p25", "p95", W, H, max).forEach((d) => svg.append(svgEl("path", { d, class: "band" })));
  runPaths(pts, "median", W, H, max).forEach((d) => svg.append(svgEl("path", { d, class: "line" })));
  const slot = W / Math.max(1, pts.length);
  pts.forEach((p, i) => { if (p.gap) svg.append(svgEl("rect", { x: i * slot, y: 0, width: slot, height: H, class: "nodata" },
    svgTitle(`${p.day}: ${t("usage.noData")}`))); });
  const s = u.latency_split || {};
  const sum = (s.pre || 0) + (s.model || 0) + (s.post || 0) || 1;
  return el("div", { class: "u-card" }, el("div", { class: "u-head" }, el("h3", {}, t("usage.latency")),
    el("span", { class: "small" }, t("usage.latencySub", { max: ms(max) }))), svg,
    el("div", { class: "u-split" }, ["pre", "model", "post"].map((k) => el("span", { class: k,
      style: `flex:${Math.max(0.02, (s[k] || 0) / sum)}` }, `${t(`usage.split.${k}`)} ${ms(s[k])}`))));
}

function slowestCard(rows) {
  return el("div", { class: "u-card" }, el("h3", {}, t("usage.slowest")),
    rows.length ? el("div", { class: "u-rows" }, rows.map((r) => el("div", { class: "u-row" },
      el("span", { class: "mono" }, String(r.ts || "").slice(5, 16).replace("T", " ")),
      el("span", { class: `inst-tag ${r.instance === "prod" ? "prod" : ""}`.trim() }, r.instance),
      el("span", { class: "mono" }, r.model || "?"), el("span", { class: "mono dim" }, r.origin || "—"),
      el("span", { class: "mono" }, ms(r.latency_ms)))))
      : el("p", { class: "u-none" }, t("usage.noData")));
}

async function downloadCsv(range) {
  const resp = await fetch(`/api/agents/${state.agent}/usage.csv?range=${range}`,
                           { headers: { Authorization: `Bearer ${state.token}` } });
  if (!resp.ok) { showToast(t("usage.csvFailed")); return; }
  const url = URL.createObjectURL(await resp.blob());
  const a = el("a", { href: url, download: `${state.agent}-usage-${range}.csv` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function renderUsage() {
  const range = state.usageRange;
  const u = await api(`/api/agents/${state.agent}/usage?range=${range}`);
  if (state.view !== "usage") return;
  const pick = (r) => { state.usageRange = r; renderView(true); };
  const m = u.month;
  const split = Object.entries(m.by_instance || {}).map(([n, v]) => `${n} ${usd(v)}`).join(" · ");
  const kpi = (k, v, sub) => el("div", { class: "u-kpi" }, el("span", { class: "k" }, k), el("span", { class: "v" }, v),
    el("span", { class: "s" }, sub || ""));
  const toolbar = el("div", { class: "u-toolbar" }, rangeSeg("usage", range, pick, m.label),
    el("span", { class: "u-note" }, t("usage.pricingNote", { agent: agentName(), instances: u.instances.join(" + ") })),
    el("button", { class: "btn btn-secondary", type: "button", onclick: () => downloadCsv(range) },
      icon("file-csv", 14), t("usage.csv")));
  if (!u.calls) {
    $("#usage").replaceChildren(el("div", { class: "u" }, toolbar,
      emptyState("chart-bar", t("usage.emptyTitle"), t("usage.emptySub"))));
    return;
  }
  $("#usage").replaceChildren(el("div", { class: "u" }, toolbar,
    el("div", { class: "u-kpis" },
      kpi(t("usage.kpi.toDate"), usd(m.to_date), split),
      kpi(t("usage.kpi.forecast"), `≈ ${usd(m.forecast)}`, t("usage.kpi.forecastSub", { month: m.label })),
      kpi(m.previous_label, usd(m.previous), t("usage.kpi.previousSub")),
      kpi(t("usage.kpi.turns"), (u.turns || 0).toLocaleString("en-US"), t("usage.kpi.turnsSub")),
      kpi(t("usage.kpi.perTurn"), u.per_turn == null ? "—" : `≈ ${usd(u.per_turn)}`,
        u.unpriced ? t("usage.kpi.unpriced", { n: u.unpriced }) : t("usage.kpi.perTurnSub"))),
    costChart(u),
    el("div", { class: "u-grid" },
      barList(t("usage.by.model"), u.by_model), barList(t("usage.by.kind"), u.by_kind),
      barList(t("usage.by.origin"), u.by_origin), barList(t("usage.by.channel"), u.by_channel)),
    el("div", { class: "u-grid" }, cacheCard(u.cache), latencyCard(u), slowestCard(u.slowest || []))));
}

function rateTone(rate, failed) { return !failed ? "" : rate > 0.05 ? "bad" : "warn"; }

function toolDays(tool) {
  const W = 720, H = 110;
  const days = tool.per_day || [];
  const max = Math.max(1, ...days.map((d) => d.ok + d.failed));
  const slot = W / Math.max(1, days.length);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H + 20}`, class: "u-chart", preserveAspectRatio: "none", role: "img",
                             "aria-label": t("tools.perDay") });
  const every = Math.max(1, Math.ceil(days.length / 10));
  days.forEach((d, i) => {
    const x = i * slot + slot * 0.14, w = slot * 0.72;
    const ok = d.ok / max * H, bad = d.failed / max * H;
    if (d.gap) svg.append(svgEl("rect", { x, y: H - 3, width: w, height: 3, class: "gap" }, svgTitle(`${d.day}: ${t("usage.noData")}`)));
    if (ok) svg.append(svgEl("rect", { x, y: H - ok, width: w, height: ok, class: "ok" }, svgTitle(`${d.day}: ${d.ok}`)));
    if (bad) svg.append(svgEl("rect", { x, y: H - ok - bad, width: w, height: bad, class: "failed" },
      svgTitle(`${d.day}: ${t("tools.failedN", { n: d.failed })}`)));
    if (i % every === 0 || i === days.length - 1) svg.append(svgEl("text", { x: x + w / 2, y: H + 15,
      "text-anchor": "middle", class: "axis" }, document.createTextNode(d.day.slice(5))));
  });
  return svg;
}

function callRows(rows) {
  return rows.length ? el("div", { class: "u-rows" }, rows.map((r) => el("div", { class: `u-row ${r.outcome === "ok" ? "" : "bad"}`.trim() },
    el("span", { class: "mono" }, String(r.ts || "").slice(5, 16).replace("T", " ")),
    el("span", { class: `inst-tag ${r.instance === "prod" ? "prod" : ""}`.trim() }, r.instance),
    el("span", { class: "mono" }, r.outcome || "?"), el("span", { class: "mono dim", title: r.input || "" }, r.input || ""),
    el("span", { class: "mono" }, ms(r.ms))))) : el("p", { class: "u-none" }, t("usage.noData"));
}

async function renderTools() {
  const range = state.toolsRange;
  const q = state.tool ? `&tool=${encodeURIComponent(state.tool)}` : "";
  const data = await api(`/api/agents/${state.agent}/tools?range=${range}${q}`);
  if (state.view !== "tools") return;
  const pick = (r) => { state.toolsRange = r; renderView(true); };
  const select = (name) => { state.tool = state.tool === name ? null : name; renderView(false); };
  const toolbar = el("div", { class: "u-toolbar" }, rangeSeg("tools", range, pick),
    el("span", { class: "u-note" }, t("tools.note", { agent: agentName(), instances: data.instances.join(" + ") })));
  if (!data.tools.length) {
    $("#tools").replaceChildren(el("div", { class: "u" }, toolbar,
      emptyState("wrench", t("tools.emptyTitle"), t("tools.emptySub"))));
    return;
  }
  const cols = ["tool", "calls", "failed", "rate", "median", "p95", "lastError", "usedBy"];
  const table = el("div", { class: "u-table-wrap" }, el("div", { class: "u-table" },
    el("div", { class: "u-tr head" }, cols.map((c) => el("span", {}, t(`tools.col.${c}`)))),
    data.tools.map((r) => el("button", { type: "button", class: `u-tr ${state.tool === r.tool ? "sel" : ""}`.trim(),
                                        onclick: () => select(r.tool) },
      el("span", { class: "mono" }, r.tool), el("span", { class: "mono" }, String(r.calls)),
      el("span", { class: `mono ${r.failed ? "bad" : ""}`.trim() }, String(r.failed)),
      el("span", { class: `mono ${rateTone(r.rate, r.failed)}`.trim() }, `${(r.rate * 100).toFixed(1)} %`),
      el("span", { class: "mono" }, ms(r.median_ms)), el("span", { class: "mono" }, ms(r.p95_ms)),
      el("span", { class: `mono ${r.last_error ? "bad" : "dim"}`.trim() }, r.last_error
        ? `${r.last_error.outcome} · ${String(r.last_error.ts || "").slice(5, 16).replace("T", " ")}` : t("tools.noError")),
      el("span", { class: "chips" }, r.used_by.slice(0, 4).map((o) => el("span", { class: "chip" }, o)))))));
  const detail = data.tool ? el("div", { class: "u-grid" },
    el("div", { class: "u-card u-wide" }, el("div", { class: "u-head" }, el("h3", {}, t("tools.perDayOf", { tool: data.tool.name })),
      el("span", { class: "u-legend" }, el("span", {}, el("i", { class: "ok" }), t("tools.ok")),
        el("span", {}, el("i", { class: "failed" }), t("tools.failed")))), toolDays(data.tool)),
    el("div", { class: "u-card" }, el("h3", {}, t("tools.slowest")), callRows(data.tool.slowest)),
    el("div", { class: "u-card" }, el("h3", {}, t("tools.failedCalls")), callRows(data.tool.failed)))
    : el("p", { class: "u-hint" }, t("tools.pickHint"));
  $("#tools").replaceChildren(el("div", { class: "u" }, toolbar, table, detail));
}

async function renderLogs() {
  const q = new URLSearchParams({ instance: state.instance, source: $("#log-source").value, level: $("#log-level").value,
                                  q: $("#log-q").value, tail: $("#log-tail").value });
  const data = await api(`/api/logs?${q}`);
  const pre = $("#log-lines");
  pre.replaceChildren(...data.lines.map((l) => el("div", { class: l.level || "" },
    l.time ? `${l.date} ${l.time} ${l.level} ${l.source}: ${l.message}` : l.message)));
  pre.scrollTop = pre.scrollHeight;
}

// --- v2.11 configure, restyled in v5.1 as the design draws it (design/v5/handoff/prototype/Configuration.dc.html) --
// Every write: preview (the diff) → the confirmation dialog (the diff; prod asks for "prod") → apply → the running
// services restart → the audit log. The outcome stays on the page (actionLine, the v2.10 review #1 rule).
const asText = (v) => v == null ? "" : Array.isArray(v) ? v.join(", ") : String(v);
const cfg = { view: null, edits: {}, preview: null, filter: "", group: null };
const sec = { text: {}, preview: {} };
const promo = { chosen: new Set(), preview: null, data: null };
const secr = { view: null, history: [], open: null };
const hist = { entries: [], pick: 0, instance: null };

// undefined on dev (no dialog unless `always`); on prod the typed "prod", or null when cancelled
async function askConfirm(name, what, { always = false, body, action, diff } = {}) {
  if (name !== "prod" && !always) return undefined;
  return confirmDialog({ instance: name, title: what, action,
                         body: body ?? (name === "prod" ? t("actions.restartsOnProd", { what }) : ""),
                         diff: diff || [], typed: true });
}

const restartedText = (r) => Object.entries(r.restarted || {}).map(([s, x]) => `${s} ${x}`).join(" · ")
  || t("actions.nothingToRestart");
const changesText = (c) => Object.entries(c || {}).map(([k, v]) => Array.isArray(v)
  ? `${k}: ${asText(v[0]) || t("settings.defaultValue")} → ${asText(v[1]) || t("settings.defaultValue")}`
  : `${k} ${v}`).join("; ");
const changesDiff = (c) => Object.entries(c || {}).filter(([, v]) => Array.isArray(v))
  .map(([k, v]) => ({ k, from: asText(v[0]) || t("settings.defaultValue"), to: asText(v[1]) || t("settings.defaultValue") }));

function plural(key, n, vars) {  // the key, or its "One" twin when the count is 1 ("1 change", "2 changes")
  return t(n === 1 ? `${key}One` : key, vars);
}

// --- pure helpers (checked in node) ------------------------------------------------------------------------------
function settingMatches(s, filter) {
  const f = String(filter || "").trim().toLowerCase();
  return !f || s.key.toLowerCase().includes(f) || String(s.env || "").toLowerCase().includes(f)
    || String(s.description || "").toLowerCase().includes(f);
}

function plainGroups(groups) {  // the Settings view's groups: a secret lives in the Secrets view, never here
  return (groups || []).map((g) => ({ name: g.name, settings: g.settings.filter((s) => !s.secret) }))
    .filter((g) => g.settings.length);
}

function pendingSummary(edits, groups) {  // how many edits wait, and how many of them need a restart
  const all = (groups || []).flatMap((g) => g.settings);
  const keys = Object.keys(edits || {});
  return { n: keys.length, restart: keys.filter((k) => (all.find((s) => s.key === k) || {}).restart).length };
}

function numbered(entries) {  // the newest first, numbered from the oldest kept one (#1) up
  return (entries || []).map((e, i, all) => Object.assign({}, e, { id: all.length - i }));
}

function whenOf(ts) {  // a history stamp "20261119-221400Z" → "2026-11-19 22:14 UTC"; anything else as it is
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}Z/.exec(String(ts || ""));
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]} UTC` : String(ts || "");
}

function entryDiff(changes, dflt) {  // a history entry's changes as diff lines; a secret is named only, never shown
  const out = [];
  for (const [k, v] of Object.entries(changes || {})) {
    if (Array.isArray(v)) {
      out.push({ sign: "-", text: `${k}: ${asText(v[0]) || dflt}` });
      out.push({ sign: "+", text: `${k}: ${asText(v[1]) || dflt}` });
    } else {
      out.push({ sign: " ", text: `${k}: ${v}` });
    }
  }
  return out;
}

function lastChanged(entries, key) {  // the newest history entry that touched `key` (its ts), or null
  const e = (entries || []).find((x) => x.changes && Object.prototype.hasOwnProperty.call(x.changes, key));
  return e ? e.ts : null;
}

// --- the shared write path ---------------------------------------------------------------------------------------
function previewBlock(p) {
  return el("div", { class: "preview" },
    p.problems.length ? el("ul", { class: "cfg-problems" }, p.problems.map((x) => el("li", {}, x))) : "",
    (p.notes || []).map((n) => el("p", { class: "note" }, n)),
    p.diff ? el("pre", { class: "diff" }, p.diff.split("\n").map((line) => el("div",
      { class: line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---")
        ? "del" : line.startsWith("secrets.yaml") ? "add" : "" }, line))) : "");
}

async function runWrite(label, call) {  // the outcome stays on the page (the v2.10 review #1 rule)
  setAction(t("actions.running", { what: label }));
  try {
    const r = await call();
    setAction(t("actions.done", { what: label, restarted: restartedText(r) })
      + ((r.notes || []).length ? ` · ${r.notes.join(" ")}` : ""));
    showToast(t("actions.doneShort", { what: label }));
    return r;
  } catch (e) { setAction(t("actions.refused", { what: label, error: e.message }), true); return null; }
}

function toggleSwitch(on, label, onToggle) {
  return el("button", { class: `switch ${on ? "on" : ""}`.trim(), type: "button", role: "switch",
                        "aria-checked": on ? "true" : "false", "aria-label": label, onclick: () => onToggle(!on) },
    el("span", { class: "knob" }));
}

// --- Settings ----------------------------------------------------------------------------------------------------
async function renderSettings() {
  const name = state.instance;
  if (!cfg.view || cfg.view.instance !== name) { cfg.edits = {}; cfg.preview = null; cfg.group = null; }
  cfg.view = await api(`/api/settings/${name}`);
  drawSettings();
}

function drawSettings() {
  const search = el("input", { class: "input", placeholder: t("settings.find"), "aria-label": t("settings.find"),
                               value: cfg.filter,
                               oninput: (ev) => { cfg.filter = ev.target.value; drawGroupList(); drawRows(); } });
  $("#settings").replaceChildren(el("div", { class: "cfg-settings" },
    el("div", { class: "cfg-groups" }, el("div", { class: "cfg-search" }, icon("magnifying-glass", 13), search),
      el("div", { id: "cfg-group-list" })),
    el("div", { class: "cfg-main" }, el("div", { id: "cfg-rows" }), el("div", { id: "settings-bar" }))));
  drawGroupList();
  drawRows();
  refreshBar();
}

function settingsGroups() {
  const groups = plainGroups(cfg.view.groups);
  if (!cfg.group || !groups.some((g) => g.name === cfg.group)) {
    const withEdit = groups.find((g) => g.settings.some((s) => s.key in cfg.edits));
    cfg.group = (withEdit || groups[0] || {}).name || null;
  }
  return groups;
}

function drawGroupList() {
  const groups = settingsGroups();
  const searching = cfg.filter.trim() !== "";
  $("#cfg-group-list").replaceChildren(...groups.map((g) => {
    const n = g.settings.filter((s) => settingMatches(s, cfg.filter)).length;
    const edited = g.settings.some((s) => s.key in cfg.edits);
    return el("button", { class: `cfg-group ${!searching && g.name === cfg.group ? "on" : ""} ${n ? "" : "none"}`.trim(),
                          type: "button", "aria-current": !searching && g.name === cfg.group ? "true" : null,
                          onclick: () => { cfg.group = g.name; cfg.filter = ""; drawSettings(); } },
      el("span", {}, g.name, edited ? el("span", { class: "edited-dot", title: t("settings.groupEdited") }) : ""),
      el("span", { class: "n" }, String(n)));
  }));
}

function drawRows() {
  const name = state.instance;
  const groups = settingsGroups();
  const f = cfg.filter.trim();
  const rows = f ? groups.flatMap((g) => g.settings.filter((s) => settingMatches(s, f)))
    : ((groups.find((g) => g.name === cfg.group) || {}).settings || []);
  const title = f ? t("settings.matches", { n: rows.length }) : (cfg.group || "");
  $("#cfg-rows").replaceChildren(
    el("div", { class: "cfg-title" }, el("h2", {}, title), el("span", {}, t("settings.file", { instance: name }))),
    rows.length ? el("div", { class: "cfg-table" },
      el("div", { class: "cfg-row head" }, el("span", {}, t("settings.columns.setting")),
        el("span", {}, t("settings.columns.value")), el("span", {}, t("settings.columns.default"))),
      rows.map(settingRow))
      : emptyState("magnifying-glass", t("settings.nothing", { q: f }), t("settings.nothingSub"), t("settings.clear"),
                   () => { cfg.filter = ""; drawSettings(); }));
}

function settingRow(s) {
  const pending = s.key in cfg.edits;
  const shown = pending ? (cfg.edits[s.key] ?? asText(s.default)) : asText(s.value);
  const head = el("div", { class: "cfg-key" },
    el("div", { class: "line" }, el("span", { class: "k" }, s.key),
      s.restart ? el("span", { class: "cfg-badge" }, icon("arrow-clockwise", 11), t("settings.needsRestart")) : ""),
    el("span", { class: "d" }, s.description), el("span", { class: "env" }, s.env || ""));
  let control;
  if (s.container) {
    control = el("span", { class: "cfg-note" }, asText(s.value) ? `${asText(s.value)} — ${s.container}` : s.container);
  } else if (s.type === "bool") {
    control = toggleSwitch(shown === "true", s.key, (on) => editSetting(s, on ? "true" : "false"));
  } else if (s.type === "enum") {
    const choices = [...s.choices];
    if (!choices.includes(shown)) choices.unshift(shown);
    control = el("select", { class: "input mono", "aria-label": s.key,
                             onchange: (ev) => editSetting(s, ev.target.value) },
      choices.map((c) => el("option", { value: c, selected: c === shown ? "" : null }, c)));
  } else {
    control = el("input", { class: "input mono", value: shown, placeholder: asText(s.default), "aria-label": s.key,
                            onchange: (ev) => editSetting(s, ev.target.value) });
  }
  const backToDefault = pending && cfg.edits[s.key] === null;
  const canReset = !s.container && s.source === "settings.yaml" && !backToDefault;
  return el("div", { class: `cfg-row ${pending ? "edited" : ""}`.trim(), "data-key": s.key }, head,
    el("div", { class: "cfg-value" }, control),
    el("div", { class: "cfg-default" },
      el("span", {}, t("settings.default", { value: asText(s.default) || "—" })),
      backToDefault ? el("span", { class: "back" }, t("settings.backToDefault")) : "",
      canReset ? el("button", { class: "btn btn-ghost", type: "button", onclick: () => editSetting(s, null) },
        t("settings.resetToDefault")) : ""));
}

function editSetting(s, value) {  // null = back to the default (the line removed from settings.yaml)
  const unchanged = value === null ? s.source === "default" : value === asText(s.value);
  if (unchanged) {
    delete cfg.edits[s.key];
  } else {
    cfg.edits[s.key] = value;
  }
  cfg.preview = null;
  drawRows();
  drawGroupList();
  refreshBar();
}

function settingsBar() {
  const name = state.instance, v = cfg.view;
  const sum = pendingSummary(cfg.edits, plainGroups(v.groups));
  const text = !sum.n ? t("settings.none", { instance: name }) : plural("settings.pending", sum.n, { n: sum.n })
    + (sum.restart ? ` · ${plural("settings.restart", sum.restart, { r: sum.restart, instance: name })}` : "");
  return el("div", { id: "settings-bar", class: "cfg-bar" },
    el("div", { class: "line" },
      el("span", { class: `txt ${sum.n ? "on" : ""}`.trim() }, text),
      el("button", { class: "btn btn-secondary", type: "button", disabled: sum.n ? null : "", onclick: discardSettings },
        t("common.discard")),
      el("button", { class: `btn btn-primary ${name === "prod" ? "btn-confirm-prod" : ""}`.trim(), type: "button",
                     disabled: sum.n ? null : "", onclick: reviewSettings }, icon("git-diff", 14), t("settings.review"))),
    v.deploy_busy ? el("p", { class: "cfg-warn" }, t("settings.deployBusy")) : "",
    v.problems.length ? el("ul", { class: "cfg-problems" },
      v.problems.map((x) => el("li", {}, t("settings.problemNow", { problem: x })))) : "",
    cfg.preview && !cfg.preview.ok ? previewBlock(cfg.preview) : "",
    actionLine());
}

function refreshBar() {
  const bar = $("#settings-bar");
  if (bar) bar.replaceWith(settingsBar());
}

function discardSettings() {
  cfg.edits = {};
  cfg.preview = null;
  drawSettings();
}

async function reviewSettings() {  // "Review the diff and apply": the preview, then the dialog with its diff
  await previewSettings();
  if (cfg.preview && cfg.preview.ok) await applySettings();
}

async function previewSettings() {
  try {
    cfg.preview = await api(`/api/settings/${state.instance}/preview`, { method: "POST",
                                                                        body: JSON.stringify({ changes: cfg.edits }) });
  } catch (e) { cfg.preview = null; setAction(t("actions.previewRefused", { error: e.message }), true); }
  refreshBar();
}

async function applySettings() {
  const name = state.instance, keys = Object.keys(cfg.edits).join(", ");
  const what = t("actions.applySettings", { keys, instance: name });
  const sum = pendingSummary(cfg.edits, plainGroups(cfg.view.groups));
  // previewed and confirmed on dev too: the dialog is where the diff is reviewed; prod also asks for "prod"
  const confirm = await askConfirm(name, plural("settings.applyTitle", sum.n, { n: sum.n, instance: name }), {
    always: true, action: t("common.apply"), diff: changesDiff(cfg.preview && cfg.preview.changes),
    body: t(sum.restart ? "settings.applyBodyRestart" : "settings.applyBody", { instance: name }) });
  if (confirm === null) { setAction(t("actions.cancelled", { what }), true); return; }
  const r = await runWrite(what, () => api(`/api/settings/${name}/apply`, { method: "POST",
    body: JSON.stringify({ changes: cfg.edits, hashes: cfg.preview.hashes, confirm }) }));
  if (r) { cfg.edits = {}; cfg.preview = null; }
  renderSettings();
}

// --- Secrets -----------------------------------------------------------------------------------------------------
async function renderSecrets() {
  const name = state.instance;
  if (secr.instance !== name) { secr.open = null; secr.instance = name; }
  const [view, history] = await Promise.all([api(`/api/settings/${name}`),
                                             api(`/api/history/${name}?limit=200`).catch(() => ({ entries: [] }))]);
  secr.view = view;
  secr.history = history.entries || [];
  drawSecrets();
}

function drawSecrets() {
  const name = state.instance;
  const secrets = secr.view.groups.flatMap((g) => g.settings).filter((s) => s.secret);
  const cols = ["name", "state", "last4", "usedFor", "changed", "actions"];
  $("#secrets").replaceChildren(el("div", { class: "cfg-secrets" },
    el("div", { class: "cfg-info" }, icon("lock-key", 16), el("span", {}, t("secrets.note", { instance: name }))),
    secr.view.deploy_busy ? el("p", { class: "cfg-warn" }, t("settings.deployBusy")) : "",
    el("div", { class: "cfg-scroll" }, el("div", { class: "sec-table" },
      el("div", { class: "sec-grid head" }, cols.map((k) => el("span", {}, t(`secrets.columns.${k}`)))),
      secrets.map(secretRow))),
    actionLine()));
  const input = $("#secrets input[type=password]");
  if (input) input.focus();
}

function secretRow(s) {
  const open = secr.open === s.key;
  const changed = lastChanged(secr.history, s.key);
  const close = () => { secr.open = null; drawSecrets(); };
  // a new value goes one way: a password field, never filled from the page, cleared after the save
  const input = open ? el("input", { class: "input mono", type: "password", autocomplete: "off", spellcheck: "false",
                                     placeholder: t("secrets.paste"), "aria-label": t("secrets.newValue", { key: s.key }),
                                     onkeydown: (ev) => {
                                       if (ev.key === "Enter") { ev.preventDefault(); saveSecret(s.key, input); }
                                       else if (ev.key === "Escape") { ev.preventDefault(); close(); }
                                     } }) : null;
  return el("div", { class: `sec-row ${open ? "open" : ""}`.trim() },
    el("div", { class: "sec-grid" },
      el("span", { class: "k" }, s.key),
      el("span", { class: `st ${s.set ? "on" : ""}`.trim() }, icon(s.set ? "check-circle" : "circle-dashed", 14),
        t(s.set ? "secrets.set" : "secrets.unset")),
      el("span", { class: "mono" }, s.set && s.last4 ? `···${s.last4}` : "—"),
      el("span", { class: "d", title: s.description }, s.description),
      el("span", { class: "mono when" }, changed ? whenOf(changed) : "—"),
      el("div", { class: "acts" },
        el("button", { class: "btn btn-secondary", type: "button", "aria-expanded": open ? "true" : "false",
                       onclick: () => { secr.open = open ? null : s.key; drawSecrets(); } },
          t(s.set ? "secrets.rotate" : "secrets.setAction")),
        el("button", { class: "btn btn-ghost", type: "button", disabled: s.set ? null : "",
                       onclick: () => clearSecret(s.key) }, t("secrets.clear")))),
    open ? el("div", { class: "sec-edit" }, input,
      el("button", { class: `btn btn-primary ${state.instance === "prod" ? "btn-confirm-prod" : ""}`.trim(),
                     type: "button", onclick: () => saveSecret(s.key, input) }, t("common.save")),
      el("button", { class: "btn btn-ghost", type: "button", onclick: close }, t("common.confirm.cancel")),
      el("span", { class: "note" }, t(s.key === "server.token" ? "secrets.noteServerToken" : "secrets.noteRestarts")))
      : "");
}

async function saveSecret(key, input) {
  const name = state.instance, value = input.value;
  const what = t("actions.setSecret", { key, instance: name });
  if (!value) { setAction(t("actions.typeFirst", { key }), true); return; }
  const confirm = await askConfirm(name, t("secrets.setTitle", { key }));
  if (confirm === null) { setAction(t("actions.cancelled", { what }), true); return; }
  input.value = "";
  const r = await runWrite(what, () => api(`/api/secrets/${name}`, { method: "POST",
    body: JSON.stringify({ key, value, confirm }) }));
  if (r) secr.open = null;
  renderSecrets();
}

async function clearSecret(key) {
  const name = state.instance;
  const what = t("actions.clearSecret", { key, instance: name });
  const confirm = await askConfirm(name, t("secrets.clearTitle", { key }), { always: true, action: t("secrets.clear"),
                                                                           body: t("secrets.clearBody", { key, instance: name }) });
  if (confirm === null) { setAction(t("actions.cancelled", { what }), true); return; }
  await runWrite(what, () => api(`/api/secrets/${name}`, { method: "POST",
    body: JSON.stringify({ key, clear: true, confirm }) }));
  renderSecrets();
}

// --- Config history ----------------------------------------------------------------------------------------------
const ACTIONS = { settings: "configHistory.actions.settings", secret: "configHistory.actions.secret",
                  rollback: "configHistory.actions.rollback", section: "configHistory.actions.section",
                  promote: "configHistory.actions.promote" };

function entrySummary(e) {
  if (e.action === "rollback" && e.of) return t("configHistory.rollbackOf", { when: whenOf(e.of) });
  const label = ACTIONS[e.action] ? t(ACTIONS[e.action]) : (e.action || "");
  const what = e.target || Object.keys(e.changes || {}).join(", ");
  return what ? `${label} · ${what}` : label;
}

async function renderHistory() {
  const name = state.instance;
  if (hist.instance !== name) { hist.pick = 0; hist.instance = name; }
  const data = await api(`/api/history/${name}`);
  hist.entries = numbered(data.entries);
  drawHistory();
}

function drawHistory() {
  const name = state.instance, list = hist.entries;
  if (!list.length) {
    $("#history").replaceChildren(emptyState("clock-counter-clockwise", t("configHistory.emptyTitle"),
                                             t("configHistory.emptySub", { instance: name })), actionLine());
    return;
  }
  const pick = Math.min(hist.pick, list.length - 1);
  const cur = list[pick];
  $("#history").replaceChildren(el("div", { class: "cfg-history" },
    el("div", { class: "hist-list" }, list.map((e, i) => el("button", {
      class: `hist-item ${i === pick ? "on" : ""}`.trim(), type: "button", "aria-current": i === pick ? "true" : null,
      onclick: () => { hist.pick = i; drawHistory(); } },
      el("span", { class: "id" }, `#${e.id}`),
      el("span", { class: "txt" }, el("span", { class: "s" }, entrySummary(e)),
        el("span", { class: "m" }, el("span", {}, whenOf(e.ts)), el("span", {}, (e.files || []).join(", "))))))),
    el("div", { class: "hist-panel" },
      el("div", { class: "head" }, el("span", { class: "id" }, `#${cur.id}`), el("span", { class: "s" }, entrySummary(cur)),
        el("button", { class: `btn btn-primary ${name === "prod" ? "btn-confirm-prod" : ""}`.trim(), type: "button",
                       onclick: () => rollback(name, cur.ts) }, icon("arrow-counter-clockwise", 14),
          t("configHistory.rollback", { id: cur.id }))),
      el("div", { class: "meta" }, `${whenOf(cur.ts)} · ${(cur.files || []).join(", ") || "—"}`),
      el("div", { class: "hist-diff" }, entryDiff(cur.changes, t("settings.defaultValue")).map((d) => el("div",
        { class: `ln ${d.sign === "+" ? "add" : d.sign === "-" ? "del" : ""}`.trim() },
        el("span", {}, d.sign), el("span", {}, d.text)))),
      el("p", { class: "hint" }, t("configHistory.hint")))),
    actionLine());
}

async function rollback(name, ts) {  // preview first: a file goes back whole, later changes to it too (review #1)
  const what = t("actions.rollback", { when: whenOf(ts), instance: name });
  let p;
  try { p = await api(`/api/history/${name}/${ts}/preview`, { method: "POST", body: "{}" }); }
  catch (e) { setAction(t("actions.refused", { what, error: e.message }), true); return; }
  if (!p.ok) { setAction(t("actions.refused", { what, error: p.problems.join("; ") }), true); return; }
  const body = [t("configHistory.rollbackBody", { instance: name }), ...(p.notes || [])].join("\n");
  // previewed and confirmed on dev too: the dialog shows the changes and the later ones a file takes back with it
  const confirm = await askConfirm(name, t("configHistory.rollbackTitle", { when: whenOf(ts) }), {
    always: true, body, action: t("configHistory.rollbackAction"), diff: changesDiff(p.changes) });
  if (confirm === null) { setAction(t("actions.cancelled", { what }), true); return; }
  await runWrite(what, () => api(`/api/history/${name}/${ts}/rollback`, { method: "POST",
    body: JSON.stringify({ confirm, hashes: p.hashes }) }));
  hist.pick = 0;
  renderHistory();
}

// --- Promote -----------------------------------------------------------------------------------------------------
async function renderPromote() {
  promo.data = await api("/api/promote");
  const keys = new Set(promo.data.differences.map((d) => d.key));
  for (const k of [...promo.chosen]) if (!keys.has(k)) promo.chosen.delete(k);
  drawPromote();
}

function drawPromote() {
  const diffs = promo.data.differences;
  const picked = diffs.filter((d) => promo.chosen.has(d.key)).length;
  const p = promo.preview;
  const head = el("div", { class: "promo-head" }, el("span", { class: "inst-tag" }, "dev"), icon("arrow-right", 13),
    el("span", { class: "inst-tag prod" }, "prod"),
    el("span", { class: "intro" }, plural("promote.intro", diffs.length, { n: diffs.length })),
    el("span", { class: "images" }, t("promote.imagesNote")));
  if (!diffs.length) {
    $("#promote").replaceChildren(el("div", { class: "cfg-promote" }, head,
      emptyState("check-circle", t("promote.agreeTitle"), t("promote.agreeSub"))), actionLine());
    return;
  }
  const value = (v) => asText(v) || t("settings.defaultValue");
  $("#promote").replaceChildren(el("div", { class: "cfg-promote" }, head,
    el("div", { class: "cfg-scroll" }, el("div", { class: "promo-table" },
      el("div", { class: "promo-row head" }, el("span", {}), el("span", {}, t("promote.columns.setting")),
        el("span", {}, t("promote.columns.dev")), el("span", {}, t("promote.columns.prod")), el("span", {})),
      diffs.map((d) => {
        const on = promo.chosen.has(d.key);
        return el("label", { class: `promo-row ${on ? "on" : ""}`.trim() },
          el("input", { type: "checkbox", checked: on ? "" : null, onchange: (ev) => {
            if (ev.target.checked) promo.chosen.add(d.key); else promo.chosen.delete(d.key);
            promo.preview = null;
            drawPromote();
          } }),
          el("span", { class: "k" }, d.key), el("span", { class: "dev" }, value(d.source)),
          el("span", { class: "prodv" }, value(d.target)),
          el("span", { class: "note" }, d.source === "(section)" || d.target === "(section)" ? t("promote.section") : ""));
      }))),
    el("div", { class: "promo-foot" },
      el("span", {}, t("promote.picked", { n: picked, total: diffs.length })),
      el("button", { class: "btn btn-primary btn-confirm-prod", type: "button", disabled: picked ? null : "",
                     onclick: reviewPromote }, icon("arrow-fat-lines-up", 14), t("promote.action"))),
    p && !p.ok ? previewBlock(p) : "", actionLine()));
}

async function reviewPromote() {  // "Review and promote to prod": the preview, then the typed confirmation
  await previewPromote();
  if (promo.preview && promo.preview.ok) await applyPromote();
}

async function previewPromote() {
  try {
    promo.preview = await api("/api/promote/preview", { method: "POST",
                                                        body: JSON.stringify({ keys: [...promo.chosen] }) });
  } catch (e) { promo.preview = null; setAction(t("actions.previewRefused", { error: e.message }), true); }
  drawPromote();
}

async function applyPromote() {
  const keys = [...promo.chosen];
  const what = t("actions.promote", { keys: keys.join(", ") });
  const confirm = await askConfirm("prod", plural("promote.title", keys.length, { n: keys.length }), {
    action: t("promote.confirm"), body: t("promote.body"), diff: changesDiff(promo.preview && promo.preview.changes) });
  if (confirm === null) { setAction(t("actions.cancelled", { what }), true); return; }
  const r = await runWrite(what, () => api("/api/promote/apply", { method: "POST",
    body: JSON.stringify({ keys, hashes: promo.preview.hashes, confirm }) }));
  if (r) { promo.chosen.clear(); promo.preview = null; }
  renderPromote();
}

// --- the schedule and models sections (YAML, as in v2.11 — their forms are v5.5) ---------------------------------
function mergedView(s, merged) {
  if (s === "schedule") {
    const when = (r) => ["at", "between", "every", "idle", "cron"].filter((k) => r[k] != null)
      .map((k) => `${k} ${asText(r[k])}`).join(" ") + (r.days ? ` · ${asText(r.days)}` : "");
    return el("table", {}, el("tr", {}, ["name", "directive", "when", "on"].map((h) => el("th", {}, t(`sections.columns.${h}`)))),
      merged.map((r) => el("tr", {}, el("td", {}, r.name ?? ""), el("td", {}, r.directive ?? ""), el("td", {}, when(r)),
        el("td", {}, badge(r.enabled === false ? "off" : "green", t(r.enabled === false ? "sections.off" : "sections.on"))))));
  }
  const roles = ["provider", "reply", "think", "mood", "housekeeping", "voice"];
  return el("table", {}, el("tr", {}, ["profile", ...roles].map((h) => el("th", {}, t(`sections.columns.${h}`)))),
    Object.entries(merged.profiles).map(([n, p]) => el("tr", {}, el("td", {}, n), roles.map((k) => el("td", {}, p[k] || "—")))));
}

async function renderSections() {
  const name = state.instance;
  const cards = [];
  for (const s of ["schedule", "models"]) {
    const data = await api(`/api/sections/${name}/${s}`);
    const k = `${name}/${s}`;
    if (!(k in sec.text)) sec.text[k] = data.text;
    const ta = el("textarea", { rows: "10", spellcheck: "false", placeholder: t(`sections.${s}.placeholder`),
                                "aria-label": t(`sections.${s}.title`, { instance: name }),
                                oninput: (ev) => { sec.text[k] = ev.target.value; sec.preview[k] = null; } });
    ta.value = sec.text[k];
    const p = sec.preview[k];
    cards.push(el("div", { class: `card ${name === "prod" ? "prod" : ""}` },
      el("h2", {}, t(`sections.${s}.title`, { instance: name })),
      el("p", { class: "muted" }, t(`sections.${s}.help`)),
      ta,
      el("div", { class: "controls" },
        el("button", { onclick: () => previewSection(k, s) }, t("sections.preview")),
        el("button", { class: name === "prod" ? "danger" : "primary", disabled: p && p.ok ? null : "",
                       onclick: () => applySection(k, s) }, t(name === "prod" ? "sections.applyProd" : "sections.apply"))),
      p ? previewBlock(p) : "",
      el("h2", {}, t("sections.merged")), mergedView(s, data.merged)));
  }
  $("#sections").replaceChildren(actionLine(), el("div", { class: "grid wide" }, cards));
}

async function previewSection(k, s) {
  try {
    sec.preview[k] = await api(`/api/sections/${state.instance}/${s}/preview`, { method: "POST",
                                                                                body: JSON.stringify({ text: sec.text[k] }) });
  } catch (e) { setAction(t("actions.previewRefused", { error: e.message }), true); }
  renderSections();
}

async function applySection(k, s) {  // a schedule or models save on prod keeps the typed "prod" (a plan, like a setting)
  const name = state.instance;
  const what = t("actions.applySection", { section: s, instance: name });
  const confirm = await askConfirm(name, t("sections.applyTitle", { section: s }));
  if (confirm === null) { setAction(t("actions.cancelled", { what }), true); return; }
  const r = await runWrite(what, () => api(`/api/sections/${name}/${s}/apply`, { method: "POST",
    body: JSON.stringify({ text: sec.text[k], hashes: sec.preview[k].hashes, confirm }) }));
  if (r) { delete sec.text[k]; delete sec.preview[k]; }
  renderSections();
}

// --- the audit (the panel's own lines, as in v2.11 — its sources and chain are v5.4) -----------------------------
async function renderAudit() {
  const data = await api("/api/audit?limit=300");
  const cols = ["when", "action", "instance", "target", "changes", "result"];
  $("#audit").replaceChildren(el("div", { class: "card" }, el("h2", {}, t("auditLog.title")),
    el("p", { class: "muted" }, t("auditLog.note")),
    el("table", {}, el("tr", {}, cols.map((h) => el("th", {}, t(`auditLog.columns.${h}`)))),
      data.entries.map((a) => el("tr", { class: a.instance === "prod" ? "prodrow" : "" },
        el("td", {}, (a.ts || "").replace("T", " ").slice(0, 19)), el("td", {}, a.action), el("td", {}, a.instance ?? ""),
        el("td", {}, a.target ?? ""), el("td", { class: "detail" }, changesText(a.changes)),
        el("td", { class: a.result === "done" ? "" : "error" }, a.result))))));
}

// --- sign in / out ---------------------------------------------------------------------------------------------
function loginMessage(text, isError = true) {
  const line = $("#login-error");
  line.hidden = !text;
  line.replaceChildren(...(text ? [isError ? icon("warning-circle", 15) : "", el("span", {}, text)] : []));
}

function setupSignIn() {
  $("#login-title").textContent = t("signin.title");
  $("#login-host").textContent = t("signin.network", { host: location.host });
  $("#login-label").textContent = t("signin.token");
  $("#token").placeholder = t("signin.placeholder");
  $("#login-submit").textContent = t("signin.submit");
  $("#login-note").textContent = t("signin.note", { agent: agentName() });
}

function logout(reason) {
  sessionStorage.removeItem(TOKEN_KEY);
  state.token = "";
  clearInterval(ticker);
  $("#app").hidden = true; $("#login").hidden = false;
  loginMessage(reason || "");
}

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  loginMessage(t("signin.checking"), false);
  const token = $("#token").value.trim();
  const resp = await fetch("/api/overview", { headers: { Authorization: `Bearer ${token}` } }).catch(() => null);
  if (!resp || !resp.ok) {  // never switch to the app on a refused token or a dead panel — say why, stay here
    loginMessage(!resp ? t("signin.noAnswer") : resp.status === 401 ? t("signin.refused")
      : t("signin.answered", { status: resp.status }));
    return;
  }
  state.token = token;
  sessionStorage.setItem(TOKEN_KEY, token);
  $("#token").value = "";
  loginMessage("");
  $("#login").hidden = true; $("#app").hidden = false;
  enterApp();
});

async function enterApp() {
  const prefs = loadPrefs();
  if (prefs.instance) state.instance = prefs.instance;
  await loadAgents();
  const target = parseHash(location.hash, navIds(), state.agents.map((a) => a.id));
  if (target && target.agent) state.agent = target.agent;
  state.view = viewDef((target && target.view) || prefs.view || "overview").id;
  try { await refreshData(); } catch (e) { if (e.message === "401") return; }
  clearInterval(ticker);
  ticker = setInterval(tick, REFRESH_MS);
  go(state.view, { push: false });
}

// --- the keyboard: P / D the instance, J / K the view, Esc closes — never while typing in a field ----------------
document.addEventListener("keydown", (ev) => {
  if ($("#app").hidden || dialogOpen) return;  // the dialog handles its own keys
  const tg = ev.target;
  const typing = tg && (tg.tagName === "INPUT" || tg.tagName === "TEXTAREA" || tg.tagName === "SELECT"
                        || tg.isContentEditable);
  if (ev.key === "Escape") {
    if (state.picker) { state.picker = false; renderRail(); }
    return;
  }
  if (typing || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const k = ev.key.toLowerCase();
  if (k === "p") setInstance("prod");
  else if (k === "d") setInstance("dev");
  else if (k === "j" || k === "k") go(nextView(navItems().map((i) => i.id), state.view, k === "j" ? 1 : -1));
});

window.addEventListener("popstate", applyHash);
window.addEventListener("hashchange", applyHash);
$("#theme").addEventListener("click", () => {
  state.theme = state.theme === "dark" ? "light" : "dark";
  savePrefs();
  applyTheme();
});
$("#log-go").addEventListener("click", refresh);

async function start() {
  try {
    const resp = await fetch("/static/strings.json");
    STRINGS = resp.ok ? await resp.json() : {};
  } catch (e) { STRINGS = {}; }
  const prefs = loadPrefs();
  state.theme = prefs.theme === "light" ? "light" : "dark";
  applyTheme();
  setupSignIn();
  if (state.token) { $("#app").hidden = false; enterApp(); } else { logout(""); }
}

start();
