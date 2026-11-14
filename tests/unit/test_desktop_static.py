"""v2.12.1 LUMI-267 — the desktop page's static rules (no browser automation in CI — rendering is the owner's live
check): no HTML injection, an English interface (every string from ``strings.en.json``), only the routes the proxy
forwards, the turn's ``turn_id`` fallback, http/https-only links, and the key the proxy hands it."""

from __future__ import annotations

import json
import re
from pathlib import Path

from desktop import proxy as px

UI = Path(__file__).resolve().parents[2] / "desktop" / "ui"
CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _js() -> str:
    return (UI / "app.js").read_text(encoding="utf-8")


def _strings() -> dict:
    return json.loads((UI / "strings.en.json").read_text(encoding="utf-8"))


def _has(strings: dict, key: str) -> bool:
    node = strings
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return key in strings
        node = node[part]
    return True


def test_nothing_is_rendered_as_html():
    page = _js() + (UI / "index.html").read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser", "eval(",
                 "new Function"):
        assert sink not in page, sink


def test_the_interface_is_english_and_every_string_key_exists():
    for name in ("app.js", "index.html", "style.css", "tokens.css"):
        assert not CYRILLIC.search((UI / name).read_text(encoding="utf-8")), name
    strings = _strings()
    tops = {k.split(".")[0] for k in strings}
    used = set(re.findall(r'"([a-z][A-Za-z]*(?:\.[A-Za-z]+)+)"', _js()))
    keys = {k for k in used if k.split(".")[0] in tops}
    assert {"input.placeholder", "status.ready", "reply.broken", "divider.missed", "auth.refused"} <= keys
    missing = sorted(k for k in keys if not _has(strings, k))
    assert missing == []
    assert set(strings["captions"]) == {"joy", "calm", "playful", "tender", "thoughtful", "serious", "surprise",
                                        "doubt", "sad"}


def test_the_page_calls_only_what_the_proxy_forwards_by_relative_urls():
    js = _js()
    paths = set(re.findall(r'"(/v1/[a-z/]+)"', js))
    assert paths and paths <= {path for _, path in px.FORWARDED}
    assert "http://" not in js.replace("http://www.w3.org/2000/svg", "")  # the SVG namespace only
    assert f'"{px.KEY_HEADER}"' in js and f'meta[name="{px.KEY_META}"]' in js


def test_a_turn_keeps_its_id_and_falls_back_the_blocking_way():
    js = _js()
    body = js[js.index("async function turn("):js.index("async function streamTurn(")]
    assert "turn_id: uid()" in body and '"/v1/turn"' in body  # the same body (and id) asked again
    assert "streamTurn(body" in body and "result = await api(\"/v1/turn\", body)" in body


def test_links_in_her_markdown_are_http_or_https_only():
    js = _js()
    inline = js[js.index("const INLINE"):js.index("function inline(")]
    assert "https?:\\/\\/" in inline  # the only link form the regex accepts
    assert 'rel: "noopener noreferrer"' in js


def test_the_page_loads_only_local_files():
    html = (UI / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert refs and all(not r.startswith(("http", "//")) for r in refs)
    assert "fonts.googleapis" not in (UI / "tokens.css").read_text(encoding="utf-8")


def test_review_2_a_busy_retry_notes_once_per_line():
    js = _js()
    body = js[js.index("async function turn("):js.index("async function streamTurn(")]
    assert "async function turn(text, queuedRow, waited = false)" in body
    assert 'if (!waited) addNote(t("note.busy"))' in body and "return turn(text, null, true)" in body
    assert body.count('addNote(t("note.busy"))') == 1


# --- v2.12.2 LUMI-272: the portrait panel ---------------------------------------------------------------------


def test_lumi_272_the_portrait_crossfades_between_two_layers_from_face_only():
    js, css = _js(), (UI / "style.css").read_text(encoding="utf-8")
    assert 'id="panel"' in (UI / "index.html").read_text(encoding="utf-8")
    box = js[js.index("function portraitBox("):js.index("async function showFace(")]
    assert '"layer on"' in box and '{ class: "layer", alt: "" }' in box  # two stacked layers, one shown
    show = js[js.index("async function showFace("):js.index("function themeInfo(")]
    assert "fetch(`/face?${q}`" in show and "back.src = data.src" in show  # /face is the only portrait source
    assert "data.src === face.src" in show  # the same picture → no swap
    assert ".portrait .layer" in css and "transition: opacity var(--t-portrait)" in css
    assert "--t-portrait: 480ms" in (UI / "tokens.css").read_text(encoding="utf-8")


def test_lumi_272_the_caption_is_never_the_emotions_name():
    js = _js()
    cap = js[js.index("function captionOf("):js.index("function portraitBox(")]
    assert "S.captions" in cap and "caps.calm" in cap
    assert "text: face.emotion" not in js and "text: emotion" not in js


def test_lumi_272_the_panel_follows_every_driver_and_both_widths():
    js = _js()
    assert 'event === "turn"' in js and "showFace(data.theme" in js  # any client's turn
    assert "showFace((result.state && result.state.theme)" in js  # this window's own turn
    assert "state.last_emotion" in js and "state.theme !== before.theme" in js  # connect + a new day's theme
    assert 'matchMedia("(max-width: 759px)")' in js and 'NARROW.addEventListener("change", renderPanel)' in js
    assert '"aria-label": `${t("panel.closeness")} ${of}`' in js  # the meter reads "Closeness N of 5"
