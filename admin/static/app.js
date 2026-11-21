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
      { id: "confighistory", label: "nav.views.configHistory", icon: "clock-counter-clockwise", section: "history" },
      { id: "promote", label: "nav.views.promote", icon: "arrow-fat-lines-up", both: true, section: "promote" }] },
    { name: "nav.groups.audit", items: [
      { id: "audit", label: "nav.views.audit", icon: "fingerprint", both: true, section: "audit" }] }],
  agent: [
    { name: "nav.groups.howSheRuns", items: [
      { id: "usage", label: "nav.views.usage", icon: "chart-bar", section: "usage" },
      { id: "schedule", label: "nav.views.scheduleModels", icon: "calendar-blank", section: "sections" }] }],
};
const BODY = { usage: "#usage-body", logs: "#log-lines" };  // a section with fixed controls renders into its body

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
                settings: renderSettings, confighistory: renderHistory, promote: renderPromote, audit: renderAudit,
                usage: renderUsage, schedule: renderSections };

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

function agentCard(instances) {
  const name = agentName();
  return el("div", { class: "agent-card" },
    el("div", { class: "agent-head" }, el("span", { class: "initial s30" }, name.slice(0, 1).toUpperCase()),
      el("div", { class: "names" }, el("span", { class: "n" }, name), el("span", { class: "r" }, t("nav.agentPicker.role"))),
      el("button", { class: "btn btn-ghost btn-small", type: "button", style: "margin-left:auto",
                     onclick: () => go("usage") }, t("nav.views.usage"), icon("arrow-right", 12))),
    el("div", { class: "agent-inst" }, instances.map((i) => el("div", {},
      el("span", { class: `inst-tag ${i.name === "prod" ? "prod" : ""}`.trim() }, i.name),
      el("span", { class: "cost" }, usd((i.today || {}).cost)),
      el("span", { class: "turns" }, t("overview.turns", { n: i.turns_today ?? 0 })),
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
  $("#overview").replaceChildren(el("div", { class: "ov" },
    boxStrip(data.box, instances),
    el("section", {}, el("div", { class: "ov-head" }, el("h2", {}, t("overview.instances")),
      el("span", { class: "sub" }, t("overview.checksRefresh"))),
      el("div", { class: "cards", style: "margin-top:10px" }, instances.map((i) => instanceCard(i, state.data.alerts)))),
    el("section", {}, el("div", { class: "ov-head" }, el("h2", {}, t("overview.agents")),
      el("span", { class: "sub" }, t("overview.today"))),
      el("div", { class: "agents-row", style: "margin-top:10px" }, agentCard(instances),
        el("div", { class: "agent-slot" }, el("span", { class: "initial s30 slot" }, icon("plus", 14)),
          el("span", { class: "t" }, t("overview.secondAgent")), el("span", { class: "s" }, t("overview.secondAgentNote")))))));
}

// --- views carried over from v2.10–v2.11 (their look is restyled in LUMI-344 / LUMI-345) ------------------------
async function renderHealth() {
  const data = await api("/api/health");
  const al = await api("/api/alerts");
  const alerts = al.on ? `alerts on — ${al.instances.join(", ")} → the admin bot`
    + (al.failing.length ? ` · failing now: ${al.failing.join(", ")}` : "") : (al.why || "alerts are off");
  const cards = Object.entries(data.instances).map(([name, checks]) => el("div",
    { class: `card ${name === "prod" ? "prod" : ""}` }, el("h2", {}, name),
    el("table", {}, el("tr", {}, el("th", {}, "check"), el("th", {}, "status"), el("th", {}, "detail"),
                       el("th", {}, "last hour")),
      checks.map((c) => el("tr", {}, el("td", {}, c.name), el("td", {}, badge(c.status)), el("td", { class: "detail" }, c.detail),
        el("td", {}, el("span", { class: "strip-cells" },
          c.history.slice(-60).map(([ts, s]) => el("i", { class: s, title: `${ts} ${s}` })))))))));
  $("#health").replaceChildren(el("p", { class: "muted" }, `checked ${data.last_run ?? "not yet"} — every minute,`
    + ` no model call · ${alerts}`), el("div", { class: "grid" }, cards));
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
      el("button", { onclick: () => act(name, service, a) }, a)))));
  $("#services").replaceChildren(el("div", { class: `card ${name === "prod" ? "prod" : ""}` }, el("h2", {}, name),
    el("table", {}, el("tr", {}, ["service", "state", "up", "restarts", "image", ""].map((h) => el("th", {}, h))),
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
  const confirm = await askConfirm(name, `${action} ${service}`,
                                   { body: `${action} ${service} on prod — her session closes.`, action });
  if (confirm === null) { setAction(`${action} ${name}/${service}: cancelled — nothing sent`, true); return; }
  setAction(`${action} ${name}/${service} …`);
  try {
    const r = await api(`/api/services/${name}/${service}/${action}`, { method: "POST",
                                                                         body: JSON.stringify({ confirm }) });
    setAction(`${action} ${name}/${service}: ${r.result}`);
    showToast(`${action} ${name}/${service}: ${r.result}`);
  } catch (e) { setAction(`${action} ${name}/${service} refused: ${e.message}`, true); }
  setTimeout(renderServices, 1500);
}

async function renderUsage() {
  const days = $("#usage-days").value;
  const u = await api(`/api/usage?instance=${state.instance}&days=${days}`);
  const perDay = Object.entries(u.per_day);
  const max = Math.max(0.0001, ...perDay.map(([, b]) => b.cost));
  // A fixed 600-wide viewBox scaled to the card: the bars share the width whatever the number of days, and only
  // every k-th day is labelled so the labels never collide (live: 31 days of 36 px bars overflowed the card).
  const VW = 600, hgt = 120, pad = 6;
  const slot = (VW - 2 * pad) / Math.max(1, perDay.length);
  const every = Math.max(1, Math.ceil(perDay.length / 8));
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${VW} ${hgt + 30}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  perDay.forEach(([day, b], i) => {
    const h = Math.max(b.cost > 0 ? 1 : 0, Math.round((b.cost / max) * hgt));
    const r = document.createElementNS(SVG_NS, "rect");
    Object.entries({ x: pad + i * slot + slot * 0.12, y: hgt - h + 5, width: slot * 0.76, height: h, class: "bar" })
      .forEach(([k, v]) => r.setAttribute(k, v));
    const tt = document.createElementNS(SVG_NS, "title"); tt.textContent = `${day}: ${usd(b.cost)}`;
    r.append(tt);
    svg.append(r);
    if (i % every === 0 || i === perDay.length - 1) {
      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("x", pad + i * slot + slot / 2); label.setAttribute("y", hgt + 24);
      label.setAttribute("text-anchor", "middle"); label.textContent = day.slice(5);
      svg.append(label);
    }
  });
  const table = (title, rows) => el("div", { class: "card" }, el("h2", {}, title), el("table", {},
    el("tr", {}, ["", "calls", "tokens", "cost"].map((x) => el("th", {}, x))),
    Object.entries(rows).sort((a, b) => b[1].cost - a[1].cost).map(([k, b]) =>
      el("tr", {}, el("td", {}, k), el("td", {}, String(b.calls)), el("td", {}, tokens(b)), el("td", {}, usd(b.cost))))));
  const l = u.latency;
  $("#usage-body").replaceChildren(el("div", { class: "grid" },
    el("div", { class: "card" }, el("h2", {}, `cost per day — ${state.instance}`), svg,
      el("p", { class: "muted" }, `total ${usd(u.total.cost)} · ${tokens(u.total)} tokens · cache hit `
        + (u.cache_hit_ratio == null ? "—" : `${Math.round(u.cache_hit_ratio * 100)} %`))),
    el("div", { class: "card" }, el("h2", {}, "turn latency"), el("dl", {},
      el("dt", {}, "turns"), el("dd", {}, String(l.turns)),
      el("dt", {}, "turn"), el("dd", {}, `median ${ms(l.turn_median_ms)} · p95 ${ms(l.turn_p95_ms)}`),
      el("dt", {}, "PRE / MODEL / POST"), el("dd", {}, `${ms(l.pre_median_ms)} / ${ms(l.model_median_ms)} / `
        + `${ms(l.post_median_ms)}`)),
      el("h2", {}, "slowest calls"), el("table", {}, u.slowest.map((s) => el("tr", {},
        el("td", {}, (s.ts || "").slice(5, 16)), el("td", {}, s.model || ""), el("td", {}, ms(s.latency_ms)))))),
    table("per model", u.per_model), table("per kind", u.per_kind)));
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

// --- v2.11: configure ------------------------------------------------------------------------------------------
// Every write: preview (the diff) → apply (prod asks for "prod" in the confirmation dialog) → the running services
// restart → the audit log.
const asText = (v) => v == null ? "" : Array.isArray(v) ? v.join(", ") : String(v);
const cfg = { view: null, edits: {}, preview: null, filter: "" };
const sec = { text: {}, preview: {} };
const promo = { chosen: new Set(), preview: null };

// undefined on dev (no dialog unless `always`); on prod the typed "prod", or null when cancelled
async function askConfirm(name, what, { always = false, body, action, diff } = {}) {
  if (name !== "prod" && !always) return undefined;
  return confirmDialog({ instance: name, title: what, action,
                         body: body ?? (name === "prod" ? `${what} on prod — her services restart.` : ""),
                         diff: diff || [], typed: true });
}

const restartedText = (r) => Object.entries(r.restarted || {}).map(([s, x]) => `${s} ${x}`).join(" · ")
  || "nothing to restart";
const changesText = (c) => Object.entries(c || {}).map(([k, v]) => Array.isArray(v)
  ? `${k}: ${asText(v[0]) || "default"} → ${asText(v[1]) || "default"}` : `${k} ${v}`).join("; ");
const changesDiff = (c) => Object.entries(c || {}).filter(([, v]) => Array.isArray(v))
  .map(([k, v]) => ({ k, from: asText(v[0]) || "default", to: asText(v[1]) || "default" }));

function previewBlock(p) {
  return el("div", { class: "preview" },
    p.problems.length ? el("ul", { class: "error" }, p.problems.map((x) => el("li", {}, x))) : "",
    (p.notes || []).map((n) => el("p", { class: "note" }, n)),
    p.diff ? el("pre", { class: "diff" }, p.diff.split("\n").map((line) => el("div",
      { class: line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---")
        ? "del" : line.startsWith("secrets.yaml") ? "add" : "" }, line))) : "");
}

async function runWrite(label, call) {  // the outcome stays on the page (the v2.10 review #1 rule)
  setAction(`${label} …`);
  try {
    const r = await call();
    setAction(`${label}: done — ${restartedText(r)}` + ((r.notes || []).length ? ` · ${r.notes.join(" ")}` : ""));
    showToast(`${label}: done`);
    return r;
  } catch (e) { setAction(`${label} refused: ${e.message}`, true); return null; }
}

async function renderSettings() {
  const name = state.instance;
  if (!cfg.view || cfg.view.instance !== name) { cfg.edits = {}; cfg.preview = null; }
  cfg.view = await api(`/api/settings/${name}`);
  $("#settings").replaceChildren(settingsBar(), el("div", { id: "settings-groups" }));
  renderGroups();
}

function settingsBar() {
  const name = state.instance, n = Object.keys(cfg.edits).length, v = cfg.view;
  const filter = el("input", { placeholder: "find a setting", value: cfg.filter,
                               oninput: (ev) => { cfg.filter = ev.target.value; renderGroups(); } });
  return el("div", { id: "settings-bar", class: `card ${name === "prod" ? "prod" : ""}` },
    el("h2", {}, `${name} — settings`),
    el("div", { class: "controls" }, filter,
      el("span", {}, n ? `${n} pending: ${Object.keys(cfg.edits).join(", ")}` : "no pending change"),
      el("button", { onclick: previewSettings, disabled: n ? null : "" }, "Preview"),
      el("button", { class: name === "prod" ? "danger" : "primary", onclick: applySettings,
                     disabled: cfg.preview && cfg.preview.ok ? null : "" }, name === "prod" ? "Apply to PROD" : "Apply"),
      el("button", { onclick: () => { cfg.edits = {}; cfg.preview = null; renderSettings(); },
                     disabled: n ? null : "" }, "Discard")),
    v.deploy_busy ? el("p", { class: "error" }, "a deploy step is running — every change waits until it ends") : "",
    v.problems.length ? el("ul", { class: "error" }, v.problems.map((x) => el("li", {}, `now: ${x}`))) : "",
    cfg.preview ? previewBlock(cfg.preview) : "",
    actionLine());
}

function refreshBar() { $("#settings-bar").replaceWith(settingsBar()); }

function renderGroups() {
  const f = cfg.filter.trim().toLowerCase();
  const match = (s) => !f || s.key.includes(f) || s.env.toLowerCase().includes(f)
    || s.description.toLowerCase().includes(f);
  const groups = cfg.view.groups.map((g) => {
    const rows = g.settings.filter(match);
    if (!rows.length) return null;
    const set = rows.filter((s) => s.source !== "default" && s.source !== "unset").length;
    return el("details", { class: "card group", open: f || rows.some((s) => s.key in cfg.edits) ? "" : null },
      el("summary", {}, g.name, el("span", { class: "muted" }, ` · ${rows.length} settings · ${set} set here`)),
      el("table", {}, rows.map(settingRow)));
  }).filter(Boolean);
  $("#settings-groups").replaceChildren(...(groups.length ? groups : [el("p", { class: "muted" }, "nothing matches")]));
}

function settingRow(s) {
  const desc = el("div", { class: "muted" }, `${s.description} · ${s.env}${s.restart ? " · needs a restart" : ""}`);
  const key = el("td", { class: "key" }, el("code", {}, s.key), desc);
  if (s.secret) {
    const input = el("input", { type: "password", autocomplete: "off", placeholder: s.set ? "a new value" : "value" });
    return el("tr", {}, key,
      el("td", {}, badge(s.set ? "green" : "off", s.set ? (s.last4 ? `set •••${s.last4}` : "set") : "unset")),
      el("td", { class: "edit" }, input, el("button", { onclick: () => saveSecret(s.key, input) }, s.set ? "rotate" : "set"),
        s.set ? el("button", { onclick: () => clearSecret(s.key) }, "clear") : ""));
  }
  if (s.container) return el("tr", {}, key, el("td", {}, asText(s.value)), el("td", { class: "muted" }, s.container));
  const pending = s.key in cfg.edits;
  const shown = pending ? (cfg.edits[s.key] ?? asText(s.default)) : asText(s.value);
  const onchange = (ev) => {
    if (ev.target.value === asText(s.value)) delete cfg.edits[s.key]; else cfg.edits[s.key] = ev.target.value;
    ev.target.closest("tr").classList.toggle("pending", s.key in cfg.edits);
    cfg.preview = null; refreshBar();
  };
  let input;
  if (s.type === "bool" || s.type === "enum") {
    const choices = s.type === "bool" ? ["true", "false"] : [...s.choices];
    if (!choices.includes(shown)) choices.unshift(shown);
    input = el("select", { onchange }, choices.map((c) => el("option", { value: c, selected: c === shown ? "" : null }, c)));
  } else {
    input = el("input", { value: shown, placeholder: asText(s.default), onchange });
  }
  return el("tr", { class: pending ? "pending" : "" }, key,
    el("td", { class: "edit" }, input, s.source === "settings.yaml" ? el("button", {
      title: "remove the line — back to the default",
      onclick: () => { cfg.edits[s.key] = null; cfg.preview = null; renderSettings(); } }, "default") : ""),
    el("td", { class: "muted" }, s.source === "default" ? "the default" : `set here · default ${asText(s.default) || "—"}`));
}

async function previewSettings() {
  try {
    cfg.preview = await api(`/api/settings/${state.instance}/preview`, { method: "POST",
                                                                        body: JSON.stringify({ changes: cfg.edits }) });
  } catch (e) { setAction(`preview refused: ${e.message}`, true); }
  refreshBar();
}

async function applySettings() {
  const name = state.instance, keys = Object.keys(cfg.edits).join(", ");
  const confirm = await askConfirm(name, `apply ${keys}`, { diff: changesDiff(cfg.preview && cfg.preview.changes) });
  if (confirm === null) { setAction(`apply ${keys} on prod: cancelled — nothing sent`, true); return; }
  const r = await runWrite(`apply ${keys} on ${name}`, () => api(`/api/settings/${name}/apply`, { method: "POST",
    body: JSON.stringify({ changes: cfg.edits, hashes: cfg.preview.hashes, confirm }) }));
  if (r) { cfg.edits = {}; cfg.preview = null; }
  renderSettings();
}

async function saveSecret(key, input) {
  const name = state.instance, value = input.value;
  if (!value) { setAction(`${key}: type the new value first`, true); return; }
  const confirm = await askConfirm(name, `set ${key}`);
  if (confirm === null) { setAction(`set ${key} on prod: cancelled — nothing sent`, true); return; }
  input.value = "";
  await runWrite(`set ${key} on ${name}`, () => api(`/api/secrets/${name}`, { method: "POST",
    body: JSON.stringify({ key, value, confirm }) }));
  renderSettings();
}

async function clearSecret(key) {
  const name = state.instance;
  const confirm = await askConfirm(name, `clear ${key}`, { always: true, action: "Clear",
                                                          body: `${key} stops working on ${name} until it is set again.` });
  if (confirm === null) { setAction(`clear ${key}: cancelled — nothing sent`, true); return; }
  await runWrite(`clear ${key} on ${name}`, () => api(`/api/secrets/${name}`, { method: "POST",
    body: JSON.stringify({ key, clear: true, confirm }) }));
  renderSettings();
}

async function renderHistory() {
  const name = state.instance;
  const data = await api(`/api/history/${name}`);
  const rows = data.entries.map((e) => el("tr", {}, el("td", {}, e.ts),
    el("td", {}, e.action + (e.of ? ` of ${e.of}` : "")), el("td", { class: "detail" }, changesText(e.changes)),
    el("td", {}, el("button", { onclick: () => rollback(name, e.ts) }, "Roll back"))));
  $("#history").replaceChildren(el("div", { class: `card ${name === "prod" ? "prod" : ""}` },
    el("h2", {}, `${name} — config history`),
    el("p", { class: "muted" }, "Each change kept the files as they were before it. “Roll back” restores them — the "
      + "current files are kept first, so a rollback can be rolled back too."),
    rows.length ? el("table", {}, el("tr", {}, ["when (UTC)", "what", "changes", ""].map((h) => el("th", {}, h))), rows)
      : el("p", {}, "no change yet"), actionLine()));
}

async function rollback(name, ts) {  // preview first: a file goes back whole, later changes to it too (review #1)
  let p;
  try { p = await api(`/api/history/${name}/${ts}/preview`, { method: "POST", body: "{}" }); }
  catch (e) { setAction(`roll back ${ts} refused: ${e.message}`, true); return; }
  if (!p.ok) { setAction(`roll back ${ts} refused: ${p.problems.join("; ")}`, true); return; }
  const what = [`roll back ${ts}: ${changesText(p.changes)}`, ...(p.notes || [])].join("\n");
  // previewed and confirmed on dev too: the dialog shows the changes and the later ones a file takes back with it
  const confirm = await askConfirm(name, `roll back ${ts}`, { always: true, body: what, action: "Roll back",
                                                             diff: changesDiff(p.changes) });
  if (confirm === null) { setAction(`roll back ${ts} on ${name}: cancelled — nothing sent`, true); return; }
  await runWrite(`roll back ${ts} on ${name}`, () => api(`/api/history/${name}/${ts}/rollback`, { method: "POST",
    body: JSON.stringify({ confirm, hashes: p.hashes }) }));
  renderHistory();
}

function mergedView(s, merged) {
  if (s === "schedule") {
    const when = (r) => ["at", "between", "every", "idle", "cron"].filter((k) => r[k] != null)
      .map((k) => `${k} ${asText(r[k])}`).join(" ") + (r.days ? ` · ${asText(r.days)}` : "");
    return el("table", {}, el("tr", {}, ["name", "directive", "when", "on"].map((h) => el("th", {}, h))),
      merged.map((r) => el("tr", {}, el("td", {}, r.name ?? ""), el("td", {}, r.directive ?? ""), el("td", {}, when(r)),
        el("td", {}, badge(r.enabled === false ? "off" : "green", r.enabled === false ? "off" : "on")))));
  }
  return el("table", {}, el("tr", {}, ["profile", "provider", "reply", "think", "mood", "housekeeping", "voice"]
    .map((h) => el("th", {}, h))), Object.entries(merged.profiles).map(([n, p]) => el("tr", {}, el("td", {}, n),
    ["provider", "reply", "think", "mood", "housekeeping", "voice"].map((k) => el("td", {}, p[k] || "—")))));
}

async function renderSections() {
  const name = state.instance;
  const cards = [];
  for (const s of ["schedule", "models"]) {
    const data = await api(`/api/sections/${name}/${s}`);
    const k = `${name}/${s}`;
    if (!(k in sec.text)) sec.text[k] = data.text;
    const ta = el("textarea", { rows: "10", spellcheck: "false", placeholder: s === "schedule"
      ? "- name: morning-brief\n  enabled: false" : "profiles:\n  anthropic:\n    reply: claude-sonnet-5",
      oninput: (ev) => { sec.text[k] = ev.target.value; sec.preview[k] = null; } });
    ta.value = sec.text[k];
    const p = sec.preview[k];
    cards.push(el("div", { class: `card ${name === "prod" ? "prod" : ""}` },
      el("h2", {}, `${name} — ${s}`),
      el("p", { class: "muted" }, s === "schedule"
        ? "this instance's rows over core/schedule.yaml, merged by name — a new name adds a row; quote every time (\"19:00\")"
        : "this instance's aliases / profiles over core/models.yaml — a profile's fields over the authored profile's"),
      ta,
      el("div", { class: "controls" },
        el("button", { onclick: () => previewSection(k, s) }, "Preview"),
        el("button", { class: name === "prod" ? "danger" : "primary", disabled: p && p.ok ? null : "",
                       onclick: () => applySection(k, s) }, name === "prod" ? "Apply to PROD" : "Apply")),
      p ? previewBlock(p) : "",
      el("h2", {}, "what runs (merged)"), mergedView(s, data.merged)));
  }
  $("#sections").replaceChildren(actionLine(), el("div", { class: "grid wide" }, cards));
}

async function previewSection(k, s) {
  try {
    sec.preview[k] = await api(`/api/sections/${state.instance}/${s}/preview`, { method: "POST",
                                                                                body: JSON.stringify({ text: sec.text[k] }) });
  } catch (e) { setAction(`preview ${s} refused: ${e.message}`, true); }
  renderSections();
}

async function applySection(k, s) {
  const name = state.instance;
  const confirm = await askConfirm(name, `apply the ${s} section`);
  if (confirm === null) { setAction(`apply ${s} on prod: cancelled — nothing sent`, true); return; }
  const r = await runWrite(`apply ${s} on ${name}`, () => api(`/api/sections/${name}/${s}/apply`, { method: "POST",
    body: JSON.stringify({ text: sec.text[k], hashes: sec.preview[k].hashes, confirm }) }));
  if (r) { delete sec.text[k]; delete sec.preview[k]; }
  renderSections();
}

async function renderPromote() {
  const data = await api("/api/promote");
  const rows = data.differences.map((d) => el("tr", {},
    el("td", {}, el("input", { type: "checkbox", checked: promo.chosen.has(d.key) ? "" : null,
      onchange: (ev) => { ev.target.checked ? promo.chosen.add(d.key) : promo.chosen.delete(d.key); promo.preview = null;
                          renderPromote(); } })),
    el("td", {}, el("code", {}, d.key)), el("td", {}, asText(d.source) || "default"),
    el("td", {}, asText(d.target) || "default")));
  const p = promo.preview;
  $("#promote").replaceChildren(el("div", { class: "card prod" }, el("h2", {}, "promote dev → prod"),
    el("p", { class: "muted" }, "Settings that differ between dev and prod. Secrets and the per-instance keys never "
      + "move."),
    rows.length ? el("table", {}, el("tr", {}, ["", "setting", "dev", "prod"].map((h) => el("th", {}, h))), rows)
      : el("p", {}, "dev and prod agree"),
    el("div", { class: "controls" },
      el("button", { disabled: promo.chosen.size ? null : "", onclick: previewPromote }, "Preview"),
      el("button", { class: "danger", disabled: p && p.ok ? null : "", onclick: applyPromote }, "Apply to PROD")),
    p ? previewBlock(p) : "", actionLine()));
}

async function previewPromote() {
  try {
    promo.preview = await api("/api/promote/preview", { method: "POST",
                                                        body: JSON.stringify({ keys: [...promo.chosen] }) });
  } catch (e) { setAction(`preview refused: ${e.message}`, true); }
  renderPromote();
}

async function applyPromote() {
  const keys = [...promo.chosen];
  const confirm = await askConfirm("prod", `promote ${keys.join(", ")}`,
                                   { diff: changesDiff(promo.preview && promo.preview.changes) });
  if (confirm === null) { setAction("promote: cancelled — nothing sent", true); return; }
  const r = await runWrite(`promote ${keys.join(", ")} to prod`, () => api("/api/promote/apply", { method: "POST",
    body: JSON.stringify({ keys, hashes: promo.preview.hashes, confirm }) }));
  if (r) { promo.chosen.clear(); promo.preview = null; }
  renderPromote();
}

async function renderAudit() {
  const data = await api("/api/audit?limit=300");
  $("#audit").replaceChildren(el("div", { class: "card" }, el("h2", {}, "audit log"),
    el("p", { class: "muted" }, "Every action the panel took or refused, the newest first. A secret shows as (secret)."),
    el("table", {}, el("tr", {}, ["when", "action", "instance", "target", "changes", "result"].map((h) => el("th", {}, h))),
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
$("#usage-days").addEventListener("change", refresh);
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
