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
