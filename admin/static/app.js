// The Lumi admin panel (v2.10; configure v2.11) — vanilla JS, no bundler. Everything rendered goes through
// textContent (no raw HTML from data), so a log line is shown, never run. The token lives in sessionStorage only.
// A secret is never shown: the API gives set / unset + the last four characters, and a new value goes one way.
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { token: sessionStorage.getItem("lumi-admin-token") || "", tab: "overview", instance: "dev",
                instances: [], lastAction: null };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) node.append(c instanceof Node ? c : document.createTextNode(c ?? ""));
  return node;
}

async function api(path, opts = {}) {
  const resp = await fetch(path, { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${state.token}`,
                                                       "Content-Type": "application/json" } });
  if (resp.status === 401) { logout("the token was refused"); throw new Error("401"); }
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(body.detail || `HTTP ${resp.status}`);
  return body;
}

// --- formatting ----------------------------------------------------------------------------------------------
const dur = (s) => s == null ? "—" : s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min`
  : s < 172800 ? `${(s / 3600).toFixed(1)} h` : `${(s / 86400).toFixed(1)} d`;
const bytes = (n) => n == null ? "—" : n < 1e6 ? `${(n / 1e3).toFixed(0)} KB` : n < 1e9 ? `${(n / 1e6).toFixed(1)} MB`
  : `${(n / 1e9).toFixed(2)} GB`;
const usd = (x) => x == null ? "—" : `$${x.toFixed(x < 1 ? 4 : 2)}`;
const ms = (x) => x == null ? "—" : x < 1000 ? `${Math.round(x)} ms` : `${(x / 1000).toFixed(1)} s`;
const tokens = (b) => b ? (b.input + b.output + b.cache_read + b.cache_write).toLocaleString() : "—";
const badge = (status, text) => el("span", { class: `badge ${status}` }, text ?? status);

// --- tabs + the instance switcher ----------------------------------------------------------------------------
function show(tab) {
  state.tab = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== tab));
  $("#switcher").hidden = ["overview", "health", "promote", "audit"].includes(tab);
  refresh();
}

function renderSwitcher() {
  const sw = $("#switcher");
  sw.replaceChildren(...state.instances.map((name) => el("button", {
    class: `${name === "prod" ? "prod" : ""} ${name === state.instance ? "active" : ""}`,
    onclick: () => { state.instance = name; renderSwitcher(); refresh(); } }, name)));
}

// --- views ---------------------------------------------------------------------------------------------------
async function renderOverview() {
  const data = await api("/api/overview");
  state.instances = data.instances.map((i) => i.name);
  if (!state.instances.includes(state.instance)) state.instance = state.instances[0];
  renderSwitcher();
  const cards = data.instances.map((i) => el("div", { class: `card ${i.name === "prod" ? "prod" : ""}` },
    el("h2", {}, i.name, badge(i.up ? "green" : "red", i.up ? "up" : "down")),
    el("dl", {},
      el("dt", {}, "version"), el("dd", {}, `${i.version ?? "—"} · image ${i.image ?? "—"}`),
      el("dt", {}, "uptime"), el("dd", {}, `${dur(i.uptime_s)} · restarts ${Object.entries(i.restarts)
        .map(([s, n]) => `${s} ${n ?? "?"}`).join(", ")}`),
      el("dt", {}, "services"), el("dd", {}, Object.entries(i.services).map(([s, st]) => `${s}: ${st}`).join(" · ")),
      el("dt", {}, "model"), el("dd", {}, `${i.state.provider ?? "?"} / ${i.state.model ?? "?"}`
        + (i.state.profile ? ` · profile ${i.state.profile}` : "")),
      el("dt", {}, "mood"), el("dd", {}, (i.mood_today.present ? (i.mood_today.complete ? "complete" : "INCOMPLETE")
        : "not yet today") + (i.state.theme ? ` · theme ${i.state.theme}` : "")),
      el("dt", {}, "today"), el("dd", {}, `${i.turns_today} turns · ${tokens(i.today)} tokens · ${usd(i.today.cost)}`),
      el("dt", {}, "latency"), el("dd", {}, `turn median ${ms(i.latency.turn_median_ms)} · p95 ${ms(i.latency.turn_p95_ms)}`
        + ` · model median ${ms(i.latency.model_median_ms)}`),
      el("dt", {}, "last turn"), el("dd", {}, i.last_turn_age_s == null ? "—" : `${dur(i.last_turn_age_s)} ago`),
      el("dt", {}, "errors 24 h"), el("dd", {}, `${i.errors.errors_24h} · model failures 1 h ${i.errors.model_failures_1h}`),
      el("dt", {}, "data root"), el("dd", {}, bytes(i.data_size)),
    )));
  const b = data.box;
  const disks = Object.values(b.disks || {})[0];
  cards.push(el("div", { class: "card" }, el("h2", {}, "the box"), el("dl", {},
    el("dt", {}, "uptime"), el("dd", {}, dur(b.uptime_s)),
    el("dt", {}, "load"), el("dd", {}, (b.load || []).join(" · ") || "—"),
    el("dt", {}, "memory"), el("dd", {}, b.memory ? `${bytes(b.memory.available)} free of ${bytes(b.memory.total)}` : "—"),
    el("dt", {}, "disk"), el("dd", {}, disks ? `${bytes(disks.free)} free of ${bytes(disks.total)}` : "—"))));
  $("#overview").replaceChildren(el("div", { class: "grid" }, cards));
  $("#updated").textContent = `updated ${data.now.slice(11, 19)}`;
}

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
        el("td", {}, el("span", { class: "strip" },
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
  let confirm;
  if (name === "prod") {
    confirm = prompt(`${action} ${service} on PROD — her session closes. Type "prod" to confirm:`);
    if (confirm === null) { setAction(`${action} prod/${service}: cancelled — nothing sent`, true); return; }
  }
  setAction(`${action} ${name}/${service} …`);
  try {
    const r = await api(`/api/services/${name}/${service}/${action}`, { method: "POST",
                                                                         body: JSON.stringify({ confirm }) });
    setAction(`${action} ${name}/${service}: ${r.result}`);
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
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${VW} ${hgt + 30}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  perDay.forEach(([day, b], i) => {
    const h = Math.max(b.cost > 0 ? 1 : 0, Math.round((b.cost / max) * hgt));
    const r = document.createElementNS(svg.namespaceURI, "rect");
    Object.entries({ x: pad + i * slot + slot * 0.12, y: hgt - h + 5, width: slot * 0.76, height: h, class: "bar" })
      .forEach(([k, v]) => r.setAttribute(k, v));
    const t = document.createElementNS(svg.namespaceURI, "title"); t.textContent = `${day}: ${usd(b.cost)}`;
    r.append(t);
    svg.append(r);
    if (i % every === 0 || i === perDay.length - 1) {
      const label = document.createElementNS(svg.namespaceURI, "text");
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
// Every write: preview (the diff) → apply (prod asks for "prod") → the running services restart → the audit log.
const asText = (v) => v == null ? "" : Array.isArray(v) ? v.join(", ") : String(v);
const cfg = { view: null, edits: {}, preview: null, filter: "" };
const sec = { text: {}, preview: {} };
const promo = { chosen: new Set(), preview: null };

function askConfirm(name, what) {  // undefined on dev; on prod the typed text, or null when cancelled
  if (name !== "prod") return undefined;
  return prompt(`${what} on PROD — her services restart. Type "prod" to confirm:`);
}

const restartedText = (r) => Object.entries(r.restarted || {}).map(([s, x]) => `${s} ${x}`).join(" · ")
  || "nothing to restart";
const changesText = (c) => Object.entries(c || {}).map(([k, v]) => Array.isArray(v)
  ? `${k}: ${asText(v[0]) || "default"} → ${asText(v[1]) || "default"}` : `${k} ${v}`).join("; ");

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
  const confirm = askConfirm(name, `apply ${keys}`);
  if (confirm === null) { setAction(`apply ${keys} on prod: cancelled — nothing sent`, true); return; }
  const r = await runWrite(`apply ${keys} on ${name}`, () => api(`/api/settings/${name}/apply`, { method: "POST",
    body: JSON.stringify({ changes: cfg.edits, hashes: cfg.preview.hashes, confirm }) }));
  if (r) { cfg.edits = {}; cfg.preview = null; }
  renderSettings();
}

async function saveSecret(key, input) {
  const name = state.instance, value = input.value;
  if (!value) { setAction(`${key}: type the new value first`, true); return; }
  const confirm = askConfirm(name, `set ${key}`);
  if (confirm === null) { setAction(`set ${key} on prod: cancelled — nothing sent`, true); return; }
  input.value = "";
  await runWrite(`set ${key} on ${name}`, () => api(`/api/secrets/${name}`, { method: "POST",
    body: JSON.stringify({ key, value, confirm }) }));
  renderSettings();
}

async function clearSecret(key) {
  const name = state.instance;
  const confirm = name === "prod" ? askConfirm(name, `clear ${key}`) : (window.confirm(`Clear ${key} on ${name}?`)
    ? undefined : null);
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

async function rollback(name, ts) {
  const confirm = askConfirm(name, `roll back ${ts}`);
  if (confirm === null) { setAction(`roll back ${ts} on prod: cancelled — nothing sent`, true); return; }
  await runWrite(`roll back ${ts} on ${name}`, () => api(`/api/history/${name}/${ts}/rollback`, { method: "POST",
    body: JSON.stringify({ confirm }) }));
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
  const confirm = askConfirm(name, `apply the ${s} section`);
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
  const confirm = askConfirm("prod", `promote ${keys.join(", ")}`);
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

const VIEWS = { overview: renderOverview, health: renderHealth, services: renderServices, usage: renderUsage,
                logs: renderLogs, settings: renderSettings, sections: renderSections, history: renderHistory,
                promote: renderPromote, audit: renderAudit };

async function refresh() {
  try { await VIEWS[state.tab](); } catch (e) { if (e.message !== "401") $("#updated").textContent = `error: ${e.message}`; }
}

// --- sign in / out -------------------------------------------------------------------------------------------
function logout(reason) {
  sessionStorage.removeItem("lumi-admin-token");
  state.token = "";
  $("#app").hidden = true; $("#login").hidden = false;
  const err = $("#login-error"); err.hidden = !reason; err.textContent = reason || "";
}

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const err = $("#login-error");
  err.hidden = false; err.textContent = "checking…";
  const token = $("#token").value.trim();
  const resp = await fetch("/api/overview", { headers: { Authorization: `Bearer ${token}` } }).catch(() => null);
  if (!resp || !resp.ok) {  // never switch to the app on a refused token or a dead panel — say why, stay here
    err.textContent = !resp ? "the panel didn't answer" : resp.status === 401 ? "the token was refused"
      : `the panel answered ${resp.status}`;
    return;
  }
  state.token = token;
  sessionStorage.setItem("lumi-admin-token", token);
  err.hidden = true;
  $("#login").hidden = true; $("#app").hidden = false;
  show("overview");
});
$("#logout").addEventListener("click", () => logout(""));
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
$("#usage-days").addEventListener("change", refresh);
$("#log-go").addEventListener("click", refresh);

if (state.token) { $("#app").hidden = false; show("overview"); } else { logout(""); }
setInterval(() => { if (!$("#app").hidden && (state.tab === "overview" || state.tab === "health")) refresh(); },
            30000);
