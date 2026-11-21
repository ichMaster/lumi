"""v2.10 — the admin panel's static page: the login really hides, data is never rendered as HTML."""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "admin" / "static"


def test_hidden_always_hides_whatever_the_class_says():
    # live bug 2026-11-11: `.login { display: grid }` beat the `hidden` attribute, so the login stayed on screen and
    # the panel rendered below it — the page seemed to hang after "Open"
    css = (STATIC / "panel.css").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in css


def test_the_login_checks_the_token_before_switching():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    login = js[js.index('$("#login-form")'):]
    assert login.index('fetch("/api/overview"') < login.index('$("#app").hidden = false')


def test_no_data_is_rendered_as_html():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js


def test_the_charts_scale_to_their_card():
    # live 2026-11-11: 31 days of fixed 36 px bars overflowed the Usage card; the 60-run history strip of 9 px
    # cells pushed the Health table past prod's card
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "panel.css").read_text(encoding="utf-8")
    assert 'setAttribute("viewBox"' in js and 'svg.setAttribute("width"' not in js
    assert "svg.chart { display: block; width: 100%" in css
    assert "min-width: 0; overflow-x: auto;" in css and "max-width: 244px" in css


def test_review_1_an_actions_outcome_survives_the_re_render():
    # live 2026-11-11: a refused (or cancelled) prod restart looked done — the line was wiped after 1.5 s
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    act = js[js.index("async function act("):js.index("async function renderUsage")]
    assert "lastAction" in js and "actionLine()" in js[js.index("async function renderServices"):]
    assert _says_cancelled(act)  # v5.1 LUMI-344: the line comes from strings.json
    assert "refused" in act and "true)" in act  # a refusal is shown as an error
    assert 'out.textContent' not in act


def _says_cancelled(body: str) -> bool:
    """A write's cancel path says so on the page: the "cancelled — nothing sent" line, from strings.json."""
    import json

    strings = json.loads((STATIC / "strings.json").read_text(encoding="utf-8"))
    line = strings["actions"]["cancelled"]
    return 't("actions.cancelled", { what })' in body and "cancelled — nothing sent" in line and "true)" in body


def _js():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def _fn(js, name):
    start = js.index(f"function {name}(")
    nxt = js.find("\nasync function ", start + 1)
    nxt2 = js.find("\nfunction ", start + 1)
    end = min(x for x in (nxt, nxt2, len(js)) if x > 0)
    return js[start:end]


def test_v2_11_every_write_asks_prod_for_its_confirmation_and_says_when_cancelled():
    js = _js()
    for name in ("applySettings", "saveSecret", "clearSecret", "rollback", "applySection", "applyPromote", "act"):
        body = _fn(js, name)
        assert "await askConfirm(" in body and _says_cancelled(body), name
    ask = _fn(js, "askConfirm")  # v5.1: the dialog with the typed "prod" instead of prompt()
    assert 'if (name !== "prod" && !always) return undefined;' in ask and "confirmDialog(" in ask
    assert "typed: true" in ask


def test_v5_1_the_typed_prod_gate_holds_in_the_dialog():
    dialog = _fn(_js(), "confirmDialog")
    assert "const needsType = prod && typed;" in dialog
    assert 'input.value.trim() === "prod"' in dialog and "ok.disabled = !canConfirm();" in dialog
    assert 'close(prod ? "prod" : undefined)' in dialog and "if (canConfirm())" in dialog  # nothing sent unconfirmed
    js = _js()
    assert "prompt(" not in js and "window.confirm(" not in js  # every confirmation goes through the dialog


def test_v2_11_a_secret_is_typed_into_a_password_field_and_never_shown():
    js = _js()
    row = _fn(js, "secretRow")  # v5.1 LUMI-344: a secret has its own view (Secrets), its own row
    assert 'type: "password"' in row and 'autocomplete: "off"' in row
    assert "s.value" not in row and ".value =" not in row  # set / unset + last4 only; the field is never filled
    assert 'input.value = ""' in _fn(js, "saveSecret")
    # and the Settings view never lists one: its groups drop every secret
    assert "!s.secret" in _fn(js, "plainGroups") and "plainGroups(" in _fn(js, "settingsGroups")


def test_v2_11_the_configure_views_exist_and_keep_their_outcome_on_the_page():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for section in ("settings", "secrets", "sections", "history", "promote", "audit"):
        assert f'<section id="{section}"' in html
    js = _js()
    for view, section in (("settings", "settings"), ("secrets", "secrets"), ("schedule", "sections"),
                          ("confighistory", "history"), ("promote", "promote"), ("audit", "audit")):  # in the rail
        assert f'id: "{view}"' in js and f'section: "{section}"' in js
    assert "setAction(" in _fn(js, "runWrite") and "actionLine()" in _fn(js, "settingsBar")
    assert "actionLine()" in _fn(js, "drawHistory") and "actionLine()" in _fn(js, "drawPromote")
    assert "actionLine()" in _fn(js, "drawSecrets")


def test_review_1_a_rollback_is_previewed_and_confirmed_on_dev_too():
    body = _fn(_js(), "rollback")
    assert body.index("/preview`") < body.index("/rollback`")  # the diff and the later changes first
    assert "always: true" in body and "p.notes" in body and "hashes: p.hashes" in body


# --- v5.1 LUMI-343: the shell ------------------------------------------------------------------------------------

PAGE_FILES = ("index.html", "app.js", "icons.js", "panel.css", "nocturne.css", "tokens.css")
SVG_NS = "http://www.w3.org/2000/svg"  # a namespace name for createElementNS, never fetched


def test_v5_1_the_page_loads_nothing_from_the_network():
    import re

    for name in PAGE_FILES:
        text = (STATIC / name).read_text(encoding="utf-8").replace(SVG_NS, "")
        assert not re.search(r"https?://", text), name
        assert "@import" not in text, name
    css = (STATIC / "nocturne.css").read_text(encoding="utf-8")
    assert '@font-face' in css and 'url("/static/fonts/InterVariable.woff2")' in css


def test_v5_1_inter_and_its_license_ship_with_the_panel():
    font = STATIC / "fonts" / "InterVariable.woff2"
    assert font.read_bytes()[:4] == b"wOF2"
    assert "SIL Open Font License" in (STATIC / "fonts" / "OFL.txt").read_text(encoding="utf-8")
    assert "Phosphor Icons" in (STATIC / "icons.js").read_text(encoding="utf-8")  # the icons' MIT notice


def test_v5_1_no_data_is_rendered_as_html_anywhere_in_the_page():
    for name in ("app.js", "icons.js"):
        js = (STATIC / name).read_text(encoding="utf-8")
        assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js, name
        assert "document.write" not in js, name


def _strings():
    import json

    return json.loads((STATIC / "strings.json").read_text(encoding="utf-8"))


def _has(strings, key):
    node = strings
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


def test_v5_1_every_string_key_the_page_uses_exists():
    import re

    js, strings = _js(), _strings()
    keys = set(re.findall(r'\bt\("([\w.]+)"', js))
    keys |= set(re.findall(r'"((?:nav|strip|common|overview|signin|switcher|state|settings|secrets|configHistory|'
                           r'promote|actions|sections|healthView|containers|auditLog)\.(?!yaml")[\w.]+)"', js))
    assert len(keys) > 60
    missing = sorted(k for k in keys if not _has(strings, k))
    assert not missing, missing
    for prefix in ("prod", "dev"):  # the switcher's dynamic key
        assert _has(strings, f"switcher.{prefix}")


def test_v5_1_her_name_in_the_interface_is_lili_and_the_files_are_the_real_ones():
    s = (STATIC / "strings.json").read_text(encoding="utf-8")
    assert "secrets.yaml" in s and "secrets.env" not in s and "panel.env" not in s
    js = _js()
    assert 'name: "Lili"' in js  # the default agent until the registry names it


def _node_eval(script: str):
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed — the page's pure helpers are checked where it is")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _whole(name: str) -> str:
    js = _js()
    start = js.index(f"function {name}(")
    return js[start:js.index("\n}\n", start) + 2]


def test_v5_1_deep_links_parse_and_a_stale_one_falls_back():
    got = _node_eval(_whole("parseHash") + _whole("hashFor") + """
const ids = { server: ["health", "logs", "settings"], agent: ["usage", "schedule"] };
const agents = ["lili"];
const cases = ["#/overview", "#/server/logs", "#/server/logs?src=prod-lumi&at=1", "#/agent/lili/usage",
  "#/agent/other/usage", "#/agent/lili/health", "#/server/usage", "#/server", "", "#/nowhere/x", "#/server/logs/x"];
console.log(JSON.stringify({ parsed: cases.map((c) => parseHash(c, ids, agents)),
  hashes: [hashFor("logs", "server", "lili"), hashFor("usage", "agent", "lili"), hashFor("overview", "top", "lili")] }));
""")
    assert got["parsed"] == [{"view": "overview"}, {"view": "logs"}, {"view": "logs"},
                             {"view": "usage", "agent": "lili"}, None, None, None, None, None, None, None]
    assert got["hashes"] == ["#/server/logs", "#/agent/lili/usage", "#/overview"]


def test_v5_1_j_and_k_walk_the_views_round():
    got = _node_eval(_whole("nextView") + """
const ids = ["overview", "health", "logs"];
console.log(JSON.stringify([nextView(ids, "overview", 1), nextView(ids, "logs", 1), nextView(ids, "overview", -1),
  nextView(ids, "gone", 1)]));
""")
    assert got == ["health", "overview", "logs", "health"]


def test_v5_1_the_attention_strip_names_the_red_and_amber_checks_and_a_silent_server():
    got = _node_eval("const TONE = { green: 'ok', amber: 'warn', red: 'bad', off: 'off' };\n" + _whole("runStart")
                     + _whole("attentionItems") + _whole("railBadges") + """
const health = { instances: {
  prod: [{ name: "server", status: "green", detail: "", history: [] },
         { name: "scheduler", status: "amber", detail: "the newest firing 6 h ago",
           history: [["t1", "green"], ["t2", "amber"], ["t3", "amber"]] }],
  dev: [{ name: "server", status: "red", detail: "connection refused", history: [["t1", "red"]] }] } };
const overview = { instances: [
  { name: "prod", up: true, services: { server: "running" } },
  { name: "dev", up: false, services: { server: "stopped", "telegram-inbound": "running" } },
  { name: "x", up: false, services: {} }] };
console.log(JSON.stringify({ items: attentionItems(health, overview), quiet: attentionItems({ instances: {} }, { instances: [] }),
  badges: railBadges(health, overview) }));
""")
    items = got["items"]
    assert [(i["inst"], i["kind"], i["tone"]) for i in items] == [("x", "panel", "bad"), ("dev", "check", "bad"),
                                                                   ("prod", "check", "warn")]
    assert items[2]["since"] == "t2" and items[1]["detail"] == "connection refused"  # the run's start, not its end
    assert got["quiet"] == []
    assert got["badges"] == {"health": {"n": 2, "tone": "bad"}, "containers": {"n": 1, "tone": "bad"}}



# --- v5.1 LUMI-344: the v2.10–v2.11 views carried into the shell ---------------------------------------------------


def test_v5_1_secrets_is_its_own_view_under_configuration():
    js = _js()
    conf = js[js.index('name: "nav.groups.configuration"'):js.index('name: "nav.groups.audit"')]
    order = [conf.index(f'id: "{v}"') for v in ("settings", "secrets", "confighistory", "promote")]
    assert order == sorted(order)
    assert "secrets: renderSecrets" in js
    assert "/api/settings/${name}" in _fn(js, "renderSecrets")  # set / unset + last4 from the same safe view


def test_v5_1_settings_review_the_diff_before_applying_on_dev_too():
    js = _js()
    review = _fn(js, "reviewSettings")
    assert review.index("previewSettings()") < review.index("applySettings()")
    assert "cfg.preview.ok" in review  # a preview with problems never reaches the dialog
    apply = _fn(js, "applySettings")
    assert "always: true" in apply and "changesDiff(" in apply  # the dialog shows the diff, on dev as well
    bar = _fn(js, "settingsBar")
    assert "reviewSettings" in bar and "discardSettings" in bar and 'name === "prod" ? "btn-confirm-prod"' in bar


def test_v5_1_promote_is_previewed_then_confirmed_with_the_typed_prod():
    js = _js()
    review = _fn(js, "reviewPromote")
    assert review.index("previewPromote()") < review.index("applyPromote()") and "promo.preview.ok" in review
    apply = _fn(js, "applyPromote")
    assert 'askConfirm("prod",' in apply and "changesDiff(" in apply and "hashes: promo.preview.hashes" in apply


def test_v5_1_a_schedule_or_models_save_on_prod_keeps_the_typed_prod():
    # design/v5/BRIEF.md, corrected after the export: a schedule save is a plan, like a setting — never "reversible"
    js = _js()
    apply = _fn(js, "applySection")
    assert "await askConfirm(name," in apply and "always" not in apply  # prod → the typed "prod"; dev → no dialog
    assert "typed: false" not in js  # no write skips the typed step on prod


def test_v5_1_every_configuration_string_comes_from_strings_json():
    import re

    js = _js()
    for name in ("settingsBar", "drawRows", "settingRow", "drawSecrets", "secretRow", "drawHistory", "drawPromote"):
        body = _fn(js, name)
        assert "t(" in body, name
        # no English sentence typed into the page: a quoted literal with two words or more is a string key's job
        body = re.sub(r'class: (?:`[^`]*`|"[^"]*")', "", body)  # class lists are not words a person reads
        literals = re.findall(r'"([^"\\n]*)"', body) + re.findall(r"'([^'\\n]*)'", body)
        prose = [s for s in literals if re.search(r"[A-Za-z]{2,} [A-Za-z]{2,}", s)]
        assert not prose, (name, prose)


def _helpers():
    return "".join(_whole(n) for n in ("settingMatches", "plainGroups", "pendingSummary", "numbered", "whenOf",
                                       "entryDiff", "lastChanged"))


def test_v5_1_the_configuration_helpers():
    got = _node_eval("const asText = (v) => v == null ? '' : Array.isArray(v) ? v.join(', ') : String(v);\n"
                     + _helpers() + """
const groups = [
  { name: "server", settings: [{ key: "server.port", env: "LUMI_SERVER_PORT", description: "The port", restart: true },
                               { key: "server.token", secret: true, description: "The client token" }] },
  { name: "agora", settings: [{ key: "agora.token", secret: true, description: "The room's token" }] },
  { name: "thoughts", settings: [{ key: "thoughts.quiet_hours", env: "LUMI_QUIET", description: "Hours of quiet" }] }];
const plain = plainGroups(groups);
const entries = [{ ts: "20261119-221400Z", changes: { "thoughts.quiet_hours": ["1-8", "1-7"] } },
                 { ts: "20261117-230200Z", changes: { "providers.gemini_api_key": "(secret)" } },
                 { ts: "20261110-090000Z-2", changes: { "thoughts.quiet_hours": [null, "1-8"] } }];
console.log(JSON.stringify({
  plain: plain.map((g) => [g.name, g.settings.map((s) => s.key)]),
  match: [settingMatches(groups[0].settings[0], "PORT"), settingMatches(groups[0].settings[0], "lumi_server"),
          settingMatches(groups[2].settings[0], "quiet"), settingMatches(groups[2].settings[0], "voice"),
          settingMatches(groups[2].settings[0], "  ")],
  pending: pendingSummary({ "server.port": "9000", "thoughts.quiet_hours": null }, plain),
  none: pendingSummary({}, plain),
  ids: numbered(entries).map((e) => e.id),
  when: [whenOf("20261119-221400Z"), whenOf("20261110-090000Z-2"), whenOf("odd")],
  diff: entryDiff(entries[0].changes, "(default)").concat(entryDiff(entries[1].changes, "(default)"),
                                                         entryDiff(entries[2].changes, "(default)")),
  last: [lastChanged(entries, "thoughts.quiet_hours"), lastChanged(entries, "providers.gemini_api_key"),
         lastChanged(entries, "voice.model"), lastChanged([], "x")],
}));
""")
    assert got["plain"] == [["server", ["server.port"]], ["thoughts", ["thoughts.quiet_hours"]]]  # no secret, no empty
    assert got["match"] == [True, True, True, False, True]
    assert got["pending"] == {"n": 2, "restart": 1} and got["none"] == {"n": 0, "restart": 0}
    assert got["ids"] == [3, 2, 1]  # the newest first, numbered from the oldest kept one
    assert got["when"] == ["2026-11-19 22:14 UTC", "2026-11-10 09:00 UTC", "odd"]
    assert got["diff"] == [{"sign": "-", "text": "thoughts.quiet_hours: 1-8"},
                           {"sign": "+", "text": "thoughts.quiet_hours: 1-7"},
                           {"sign": " ", "text": "providers.gemini_api_key: (secret)"},  # named, never shown
                           {"sign": "-", "text": "thoughts.quiet_hours: (default)"},
                           {"sign": "+", "text": "thoughts.quiet_hours: 1-8"}]
    assert got["last"] == ["20261119-221400Z", "20261117-230200Z", None, None]


def test_v5_1_every_icon_the_page_draws_is_inlined():
    import re

    js = _js()
    icons = (STATIC / "icons.js").read_text(encoding="utf-8")
    known = set(re.findall(r'^\s+"([a-z-]+)": "M', icons, re.M))
    used = set()
    for call in re.findall(r"\bicon\(([^;]*?), \d+", js):  # the name, a ternary of names, or an item's .icon
        call = re.sub(r'[=!]==? "[^"]*"', "", call)  # a comparison's operand is not an icon
        used |= set(re.findall(r'"([a-z][a-z-]+)"', call))
    used |= set(re.findall(r'icon: "([a-z-]+)"', js))  # the rail's items
    assert len(used) > 30
    assert not sorted(used - known), sorted(used - known)


def test_v5_1_a_counted_string_has_its_singular():
    import re

    js, strings = _js(), _strings()
    bases = set(re.findall(r'\bplural\("([\w.]+)"', js))
    assert {"settings.pending", "settings.restart", "settings.applyTitle", "promote.intro", "promote.title"} <= bases
    missing = sorted(k for b in bases for k in (b, f"{b}One") if not _has(strings, k))
    assert not missing, missing
