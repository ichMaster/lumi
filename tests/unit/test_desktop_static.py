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
    """The page's lookup(): the longest flat prefix the strings have, then the nested rest — a string at the end."""
    parts = key.split(".")
    for i in range(len(parts), 0, -1):
        head = ".".join(parts[:i])
        if head not in strings:
            continue
        node = strings[head]
        for part in parts[i:]:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str):
            return True
    return False


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
    missing = sorted(k for k in keys if not (_has(strings, k) or k in strings))  # a text, or a block (auth.refused)
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
    assert 'matchMedia("(max-width: 759px)")' in js and 'NARROW.addEventListener("change", () => { renderPanel(); renderViews(); renderSide(); stick(true); })' in js  # v4.1
    assert '"aria-label": `${t("panel.closeness")} ${of}`' in js  # the meter reads "Closeness N of 5"


# --- v2.12.2 LUMI-273: the think block + the quiet-thoughts strip ---------------------------------------------------


def test_lumi_273_the_think_frames_fill_the_block_in_three_modes():
    js = _js()
    stream = js[js.index("async function streamTurn("):js.index("function brokenReply(")]
    assert 'frame.event === "think"' in stream and "setThink(her.think" in stream
    assert "result.thinking" in js  # nothing streamed → the done payload's thinking
    assert 'THINK_MODES = ["shown", "collapsed", "hidden"]' in js
    assert 'THINK_FROM_SERVER = { open: "shown", debug: "collapsed", off: "hidden" }' in js
    assert "block.override = null" in js and "localStorage.setItem(THINK_STORE" in js


def test_lumi_273_thoughts_go_to_the_strip_never_the_chat():
    js = _js()
    body = js[js.index("function onThought("):js.index("function thoughtTime(")]
    assert "unread: !data.replay || st.collectMissed" in body  # review #2: missed while offline = new
    for chat in ("addHers(", "addMine(", "addCommand(", "append("):
        assert chat not in body
    assert 'event === "thought"' in js and "onThought(data)" in js


def test_lumi_273_the_replay_flag_decides_times_and_the_missed_count():
    js = _js()
    push = js[js.index("function onPush("):js.index("/* ---------- turns, commands, directives")]
    assert "st.collectMissed && data.replay" in push and "data.replay ? undefined : clock()" in push


def test_no_null_is_ever_handed_to_replace_children():
    js = _js()
    for call in re.findall(r"replaceChildren\(([^;]*)\);", js):
        # a ternary's null as the call's last argument prints "null" (found live twice); a null inside a nested
        # el(...) is dropped by el() and fine
        assert not call.rstrip().endswith(": null"), call



def test_lumi_276_every_reply_of_hers_carries_its_emotion():
    js = _js()
    setter = js[js.index("function setEmotion("):js.index("function addHers(")]
    assert "captionOf(emotion)" in setter and "title:" in setter  # the hover says the caption, not the name
    assert "if (opts.emotion) setEmotion(r.node, opts.emotion, opts.intensity)" in js  # pushed + replayed lines
    assert "emotion: data.emotion, intensity: data.intensity" in js
    assert "setEmotion(her.node, result.emotion, result.intensity)" in js  # this window's own turn, at done
    assert 'fetch("/emoji", { headers: headers() })' in js



def test_lumi_277_the_portrait_can_be_hidden_and_stays_so():
    js = _js()
    render = js[js.index("function renderPanel("):js.index("/* ---------- state from the server")]
    assert "const pic = st.portraitShown" in render and "if (pic) kids.push(portraitBox())" in render
    assert "pic ? thumb : null" in render and "if (pic && panel.expanded)" in render  # the narrow strip too
    assert 'localStorage.setItem(PORTRAIT_STORE, st.portraitShown ? "shown" : "hidden")' in js
    assert 'localStorage.getItem(PORTRAIT_STORE) !== "hidden"' in js  # shown unless hidden was kept
    assert "setPortraitShown(m === \"shown\")" in js



def test_the_switches_keys_resolve_to_text():
    """The template-literal keys the regex above can't see (found live: the switches showed raw keys)."""
    js, strings = _js(), _strings()
    assert "t(`think.modes.${m}`)" in js and "t(`portrait.modes.${m}`)" in js
    for key in ("think.modes.shown", "think.modes.collapsed", "think.modes.hidden", "portrait.modes.shown",
                "portrait.modes.hidden", "auth.refused.title", "details.thinking.on", "details.style.normal"):
        assert _has(strings, key), key
    lookup = js[js.index("function lookup("):js.index("function t(")]
    assert "parts.slice(0, i).join" in lookup  # the longest flat prefix first, like _has


# --- v4.1 LUMI-303: the view row, the right panels, the frame, the design's tokens and strings ---------------------

def test_v41_every_new_string_key_resolves_underscores_included():
    """The key regex above skips keys with an underscore ("views.chat_unread"); the view code uses many."""
    js, strings = _js(), _strings()
    used = set(re.findall(r'"((?:views|panels|nav|files|date|reader|state)\.[A-Za-z_]+)"', js))
    assert {"views.chat", "views.files", "views.chat_unread", "panels.pin_right", "panels.resize_width",
            "state.try_again"} <= used
    assert sorted(k for k in used if k not in strings) == []


def test_v41_the_views_and_panels_keep_their_layout_guarded():
    js = _js()
    assert 'const PANELS_STORE = "lumi.desktop.panels"' in js
    for body in (js[js.index("function loadPanels("):js.index("function savePanels(")],
                 js[js.index("function savePanels("):js.index("function viewName(")]):
        assert "try {" in body and "catch (e)" in body  # every localStorage access is guarded
    assert "localStorage" not in js[js.index("function setView("):js.index("function viewFrame(")].replace(
        "localStorage.setItem(PANELS_STORE", "")  # the views touch storage only through the two helpers
    assert "SIDE = { min: 280, max: 520, default: 340, step: 20 }" in js
    assert "SPLIT = { min: 0.2, max: 0.8, default: 0.55, step: 0.05 }" in js
    assert "VIEW_NARROW_PX = 640" in js and "ResizeObserver" in js


def test_v41_a_view_replaces_the_list_and_input_and_chat_returns_to_the_newest_line():
    js = _js()
    apply = js[js.index("function applyView("):js.index("function pin(")]
    assert '$("list").hidden = !chat' in apply and '$("composer").hidden = !chat' in apply
    assert "stick(true)" in apply
    assert 'if (vw.view !== "chat" && !vw.chatUnread) { vw.chatUnread = true; renderViews(); }' in js  # unread
    assert 'if (kind === "chat") vw.chatUnread = false' in js


def test_v41_the_tokens_only_add_to_v212():
    """The v2.12 handoff's values are all still in tokens.css (but the documented system-font change), and every
    v4 addition is declared exactly once, with the design's value."""
    root = Path(__file__).resolve().parents[2]
    decl = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
    page = (UI / "tokens.css").read_text(encoding="utf-8")
    have = {(k, " ".join(v.split())) for k, v in decl.findall(page)}
    v212 = (root / "design" / "v2.12" / "handoff" / "tokens.css").read_text(encoding="utf-8")
    for k, v in decl.findall(v212):
        if k in ("--font-body", "--font-heading"):  # Archivo dropped for the system stack (no Cyrillic, no network)
            continue
        assert (k, " ".join(v.split())) in have, k
    add = (root / "design" / "v4" / "handoff" / "tokens.add.css").read_text(encoding="utf-8")
    for k, v in decl.findall(add):
        assert page.count(f"{k}:") == 1, k
        assert (k, " ".join(v.split())) in have, k
