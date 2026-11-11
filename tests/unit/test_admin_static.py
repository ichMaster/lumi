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
    assert "cancelled — nothing sent" in act
    assert "refused" in act and "true)" in act  # a refusal is shown as an error
    assert 'out.textContent' not in act


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
    for name in ("applySettings", "saveSecret", "clearSecret", "rollback", "applySection", "applyPromote"):
        body = _fn(js, name)
        assert "askConfirm(" in body and "cancelled — nothing sent" in body, name
    assert 'prompt(`${what} on PROD' in _fn(js, "askConfirm")


def test_v2_11_a_secret_is_typed_into_a_password_field_and_never_shown():
    js = _js()
    row = _fn(js, "settingRow")
    assert 'type: "password"' in row and 'autocomplete: "off"' in row
    assert "s.value" not in row[:row.index("if (s.container)")]  # the secret branch shows set/unset + last4 only
    assert 'input.value = ""' in _fn(js, "saveSecret")


def test_v2_11_the_configure_tabs_exist_and_keep_their_outcome_on_the_page():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for tab in ("settings", "sections", "history", "promote", "audit"):
        assert f'data-tab="{tab}"' in html and f'<section id="{tab}"' in html
    js = _js()
    assert "setAction(" in _fn(js, "runWrite") and "actionLine()" in _fn(js, "settingsBar")
    assert "actionLine()" in _fn(js, "renderHistory") and "actionLine()" in _fn(js, "renderPromote")
