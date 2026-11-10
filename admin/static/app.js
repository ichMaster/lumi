// The Lumi admin panel (v2.10) — vanilla JS, no bundler. Everything rendered goes through textContent (no raw
// HTML from data), so a log line is shown, never run. The token lives in sessionStorage only.
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { token: sessionStorage.getItem("lumi-admin-token") || "", tab: "overview", instance: "dev",
                instances: [] };

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
  $("#switcher").hidden = tab === "overview" || tab === "health";
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
  const cards = Object.entries(data.instances).map(([name, checks]) => el("div",
    { class: `card ${name === "prod" ? "prod" : ""}` }, el("h2", {}, name),
    el("table", {}, el("tr", {}, el("th", {}, "check"), el("th", {}, "status"), el("th", {}, "detail"),
                       el("th", {}, "last hour")),
      checks.map((c) => el("tr", {}, el("td", {}, c.name), el("td", {}, badge(c.status)), el("td", { class: "detail" }, c.detail),
        el("td", {}, el("span", { class: "strip" },
          c.history.slice(-60).map(([ts, s]) => el("i", { class: s, title: `${ts} ${s}` })))))))));
  $("#health").replaceChildren(el("p", { class: "muted" }, `checked ${data.last_run ?? "not yet"} — every minute,`
    + " no model call"), el("div", { class: "grid" }, cards));
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
      rows), el("p", { id: "act-result", class: "muted" })));
}

async function act(name, service, action) {
  let confirm;
  if (name === "prod") {
    confirm = prompt(`${action} ${service} on PROD — her session closes. Type "prod" to confirm:`);
    if (confirm === null) return;
  }
  const out = $("#act-result");
  out.textContent = `${action} ${name}/${service} …`;
  try {
    const r = await api(`/api/services/${name}/${service}/${action}`, { method: "POST",
                                                                         body: JSON.stringify({ confirm }) });
    out.textContent = `${action} ${name}/${service}: ${r.result}`;
  } catch (e) { out.textContent = `refused: ${e.message}`; }
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

const VIEWS = { overview: renderOverview, health: renderHealth, services: renderServices, usage: renderUsage,
                logs: renderLogs };

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
