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
    # v4.3: a route with its value in the path is called by its prefix ("/v1/journal/" + the date)
    prefixes = {route.split("{", 1)[0] for _, route, _ in px.FORWARDED_PATTERNS}
    assert paths and paths <= {path for _, path in px.FORWARDED} | prefixes
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
    assert "async function turn(text, queuedRow, waited = false, pictures = null)" in body  # v4.5: + the pictures
    assert 'if (!waited) addNote(t("note.busy"))' in body and "return turn(text, null, true, pictures)" in body
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
    # the owner (2026-11-17): no fixed 520 px cap — the window's width less the portrait and a 360 px chat
    assert "SIDE = { min: 280, default: 340, step: 20, chatMin: 360 }" in js and "max: 520" not in js
    width = js[js.index("function sideMax("):js.index("function setSideWidth(")]
    assert "total - (portrait ? portrait.offsetWidth : 0) - SIDE.chatMin" in width
    assert 'window.addEventListener("resize"' in js
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


# --- v4.1 LUMI-304: Files and the markdown reader -----------------------------------------------------------------

def _files_section() -> str:
    js = _js()
    return js[js.index("/* ---------- Files and the reader"):js.index("/* ---------- start ---------- */")]


def test_v41_the_reader_never_builds_a_link_out_of_the_window():
    files = _files_section()
    # no <a href> anywhere in Files: no anchor element, no href attribute, nothing navigates the window
    assert 'el("a"' not in files and "href:" not in files and 'setAttribute("href"' not in files
    assert "location" not in files and "window.open" not in files
    link = files[files.index("function readerLink("):files.index("function readerInline(")]
    assert "/^https?:\\/\\//i.test(href)" in link and 'class: "xlink"' in link  # external: shown as text + address
    assert "resolveIn(base, pathPart)" in link and "document.createTextNode(label)" in link  # out of the sandbox: text


def test_v41_pictures_reach_the_page_only_by_fetch_into_blob_urls():
    """v4.2: one fetcher (``blobURL``) serves the full picture and the thumbnail, each kept once per place."""
    js, files = _js(), _files_section()
    getter = files[files.index("function blobURL("):files.index("function resolveIn(")]
    assert "fetch(route, { headers: headers() })" in getter  # with the window's key
    assert "URL.createObjectURL" in getter
    upload = files[files.index("async function uploadFiles("):files.index("function rowMenuButton(")]
    assert upload.count("fetch(") == 1  # v4.5: a file's bytes go up as the raw body (api() sends JSON)
    assert files.replace(upload, "").count("fetch(") == 1  # else the only fetch in Files, the reader, the picture view
    # and the Gallery
    assert files.count('"/v1/files/raw"') == 1 and files.count('"/v1/images/thumb"') == 1  # nowhere else
    assert 'src: "/v1' not in js and '.src = "/v1' not in js and "src=\"/v1" not in js
    assert "URL.revokeObjectURL" in files and "releaseBlobs(host)" in js  # released when the place changes


def test_v41_back_and_forward_keys_drive_the_files_history():
    js = _js()
    assert '(ev.key === "[" || ev.key === "]") && vw.view === "files"' in js
    assert 'filesStep(places.main.ctx, ev.key === "[" ? -1 : 1)' in js


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


def _fn(name: str) -> str:
    js = _js()
    start = js.index(f"function {name}(")
    return js[start:js.index("\n}\n", start) + 2]


def test_v41_relative_links_resolve_inside_her_sandbox_or_not_at_all():
    got = _node_eval(_fn("resolveIn") + """
const cases = [["notes", "rain.md"], ["notes", "./rain.md"], ["notes", "../art/p.png"], ["", "notes/a%20b.md"],
  ["notes", "../../secret.txt"], ["notes", "/etc/passwd"], ["notes", "https://x.org/a"], ["notes", "javascript:alert(1)"],
  ["notes", "file:///etc/passwd"], ["a/b", "../../c.md"], ["notes", ""]];
console.log(JSON.stringify(cases.map(([b, h]) => resolveIn(b, h))));""")
    assert got == ["notes/rain.md", "notes/rain.md", "art/p.png", "notes/a b.md", None, None, None, None, None, "c.md",
                   None]


def test_v41_sizes_and_heading_slugs():
    got = _node_eval(_fn("fmtSize") + _fn("slugOf") + """
console.log(JSON.stringify([fmtSize(820), fmtSize(18 * 1024), fmtSize(412 * 1024), fmtSize(3 * 1024 * 1024),
  slugOf("Ідеї на **зиму**"), slugOf("Code: `x` & y")]));""")
    assert got == ["820 B", "18 KB", "412 KB", "3.0 MB", "ідеї-на-зиму", "code-x-y"]



def test_review_v41_1_the_split_moves_in_place_never_rebuilding_the_panels():
    """v4.1 code review #1: a split drag that rebuilt the column re-mounted (and refetched) both views on every move
    and dropped the handle holding the pointer capture."""
    js = _js()
    split = js[js.index('if (both && slot === "b") {'):js.index("mountView(slot, body, active, true);")]
    assert split.count("applySplit()") == 2 and "renderSide()" not in split
    apply = js[js.index("function applySplit("):js.index("function renderSide(")]
    assert '.spanel[data-slot="r"]' in apply and '.spanel[data-slot="b"]' in apply and "replaceChildren" not in apply



def test_review_v41_3_the_page_shows_only_the_views_the_server_serves():
    """v4.1 code review #3: the Files tab showed against a server without the routes (an error state behind it)."""
    js = _js()
    served = js[js.index("function served("):js.index("function shownViews(")]
    assert 'features.files === true' in served and 'kind === "chat"' in served
    sync = js[js.index("function syncServed("):js.index("function viewName(")]
    assert "if (key === servedKey) return;" in sync  # only a change re-renders — a state push follows every turn
    assert "syncServed();" in js[js.index("function absorb("):js.index("function renderStatus(")]
    assert "shownViews().map((kind, i) => tab(" in js and "const kind = shownViews()[Number(ev.key) - 1]" in js
    assert "if (!VIEWS.includes(kind) || !served(kind)) return;" in js
    side = js[js.index("function renderSide("):js.index("/* A view mounts into a host")]
    assert "vw.r.filter(served)" in side and "vw.b.filter(served)" in side


# --- v4.2 LUMI-307: the Gallery and the full picture view ---------------------------------------------------------

def test_v42_the_gallery_and_picture_strings_resolve_and_are_english():
    js, strings = _js(), _strings()
    used = set(re.findall(r'"((?:gallery|picture)\.[A-Za-z_]+)"', js))
    assert {"gallery.title", "gallery.count", "gallery.more", "gallery.end", "gallery.empty_note", "picture.close",
            "picture.open_in_files", "picture.actions_later", "picture.keys"} <= used
    assert sorted(k for k in used if k not in strings) == []
    add = json.loads((UI.parents[1] / "design" / "v4" / "handoff" / "strings.en.add.json").read_text(encoding="utf-8"))
    for key, value in add.items():
        if key.startswith(("gallery.", "picture.")):
            assert strings[key] == value, key  # the design's words, as handed off
    assert not CYRILLIC.search(json.dumps({k: v for k, v in strings.items() if k.startswith(("gallery.", "picture."))},
                                          ensure_ascii=False))


def test_v42_the_gallery_is_a_view_gated_on_features_gallery():
    js = _js()
    assert 'const VIEWS = ["chat", "files", "gallery", ' in js and 'gallery: "views.gallery"' in js
    served = js[js.index("function served("):js.index("function shownViews(")]
    assert 'if (kind === "gallery") return features.gallery === true;' in served
    assert "VIEW_KINDS.gallery = (ctx) =>" in js


def test_v42_the_gallery_pages_by_its_cursor_and_fetches_thumbnails_near_the_screen():
    js = _js()
    load = js[js.index("async function galleryLoad("):js.index("function galleryFilter(")]
    assert 'api("/v1/images" + galleryQuery(s, more ? s.next : null))' in load
    assert "if (s.busy || (more && !s.next)) return false;" in load  # one page at a time, none past the end
    assert "if (ticket !== s.ticket) return false;" in load  # a late answer for an old folder is dropped
    near = js[js.index("function galleryNearEnd("):js.index("function galleryTiles(")]
    assert "< GALLERY_NEAR_END_PX" in near and "GALLERY_NEAR_END_PX = 160" in js and "GALLERY_PAGE = 100" in js
    tile = js[js.index("function galleryTile("):js.index("function galleryMore(")]
    assert "thumbURL(ctx.host, item.path)" in tile and "s.watch.observe(ph)" in tile
    assert "new IntersectionObserver(" in js


def test_v42_the_picture_view_is_a_dialog_with_its_keys_and_focus():
    js = _js()
    opener = js[js.index("function openPicture("):js.index("function closePicture(")]
    assert 'role: "dialog", "aria-modal": "true"' in opener and "node.focus()" in opener
    assert 'ev.key === "Escape"' in opener and "closePicture(ctx, true)" in opener
    assert 'ev.key === "ArrowLeft" || ev.key === "ArrowRight"' in opener
    close = js[js.index("function closePicture("):js.index("async function stepPicture(")]
    assert "view.opener.focus()" in close  # Esc gives the focus back to what opened it
    drop = js[js.index("function dropPicture("):js.index("async function stepPicture(")]
    assert "releaseBlobs(view.node)" in drop  # its picture goes with it
    render = js[js.index("function renderPictureView("):js.index("/* ---------- Gallery")]
    assert "releaseBlobs(view.node);" in render and "pictureURL(view.node, item.path)" in render  # one picture held
    assert "(again || view.node).focus()" in render  # a step keeps the focus inside the dialog


def test_v42_files_opens_pictures_in_the_full_view_and_the_v41_page_is_gone():
    js = _js()
    assert "renderPicturePage" not in js and 'kind: "picture"' not in js
    assert "filesPictures(ctx, pics, pics.findIndex((p) => p.path === full))" in js  # ← → through the folder
    assert "else if (PICTURE_EXT.test(path)) filesPictures(ctx, [{ path }], 0);" in js  # the reader's pictures
    assert "dropPicture(states[kind]);" in js[js.index("function mountView("):js.index("const widthWatch")]
    assert "ctx.host.replaceChildren(" not in _files_section().replace("ctx.host.replaceChildren(...nodes)", "")


def test_v42_the_gallery_helpers():
    got = _node_eval("const MONTHS = " + _js().split("const MONTHS = ", 1)[1].split(";", 1)[0] + ";" + _fn("galleryMonth")
                     + _fn("num") + _fn("fmtDate").replace("MONTHS3[", "['Jan','Feb','Mar','Apr','May','Jun','Jul',"
                                                          "'Aug','Sep','Oct','Nov','Dec'][") + _fn("fmtStamp") + """
console.log(JSON.stringify([galleryMonth("2026-11-09 21:40"), galleryMonth("2026-01-31 00:00"), galleryMonth(""),
  num(1234), num(undefined), fmtStamp("2026-11-09 21:40"), fmtStamp("2026-11-09")]));""")
    assert got == ["November 2026", "January 2026", "", "1,234", "0", "9 Nov 2026, 21:40", "9 Nov 2026"]


# --- v4.2 LUMI-308: her pictures in the chat -------------------------------------------------------------------------

def test_v42_the_outcome_and_the_push_both_put_her_pictures_under_her_line():
    js = _js()
    turn = js[js.index("async function turn("):js.index("async function streamTurn(")]
    assert "chatPictures(her, result.pictures);" in turn  # this window's own turn, once the reply is complete
    push = js[js.index("function onPush("):js.index("/* ---------- turns, commands, directives")]
    assert "pictures: data.pictures" in push  # another client's turn, Telegram, a spoken thought, the replay
    hers = js[js.index("function addHers("):js.index("function chatCtx(")]
    assert "if (opts.pictures) chatPictures(r, opts.pictures);" in hers  # no pictures → the line as before


def test_v42_a_chat_picture_is_a_button_loaded_by_blob_and_opens_over_the_chat():
    js, css = _js(), (UI / "style.css").read_text(encoding="utf-8")
    pic = js[js.index("function chatPicture("):js.index("function openChatPicture(")]
    assert 'el("button", { type: "button", class: "mthumb"' in pic  # click and Space open it
    assert 'pictureURL($("col"), path)' in pic  # fetched with the key into a blob: URL, once per window
    assert "dims.textContent = ` · ${img.naturalWidth} × ${img.naturalHeight}`" in pic  # the picture's own size
    assert 'fig.classList.add("broken")' in pic and "box.replaceChildren(" in pic  # never a broken <img>
    assert 'text: path' in pic and "innerHTML" not in pic
    opener = js[js.index("function openChatPicture("):js.index("function addCommand(")]
    assert "openPicture(ctx, {" in opener and "const host = $(\"chat\");" in js
    assert '.chat { position: relative;' in css and ".mfig { margin: 12px 0 0; display: flex; flex-direction: column; gap: 6px; width: min(100%, 320px); }" in css


# --- v4.3 LUMI-312: the Journal ----------------------------------------------------------------------------------

def test_v43_the_journal_strings_resolve_and_are_english():
    js, strings = _js(), _strings()
    used = set(re.findall(r'"(journal\.[A-Za-z_]+)"', js))
    assert {"journal.title", "journal.days", "journal.month_days", "journal.entry", "journal.entries",
            "journal.no_stamp_short", "journal.no_stamp", "journal.day_of", "journal.stamp", "journal.mood",
            "journal.biorhythms", "journal.forecast", "journal.previous", "journal.next", "journal.first",
            "journal.latest", "journal.all_days", "journal.keys", "journal.empty", "journal.empty_note",
            "journal.loading", "journal.error_title"} <= used  # every one of the design's
    assert sorted(k for k in used if k not in strings) == []
    add = json.loads((UI.parents[1] / "design" / "v4" / "handoff" / "strings.en.add.json").read_text(encoding="utf-8"))
    for key, value in add.items():
        if key.startswith("journal."):
            assert strings[key] == value, key  # the design's words, as handed off
    assert not CYRILLIC.search(json.dumps({k: v for k, v in strings.items() if k.startswith("journal.")},
                                          ensure_ascii=False))


def test_v43_the_journal_is_a_view_gated_on_features_journal():
    js = _js()
    assert 'const VIEWS = ["chat", "files", "gallery", "journal"' in js and 'journal: "views.journal"' in js
    served = js[js.index("function served("):js.index("function shownViews(")]
    assert 'if (kind === "journal") return features.journal === true;' in served
    assert "VIEW_KINDS.journal = (ctx) =>" in js


def test_v43_the_journal_reads_its_two_routes_and_keeps_her_prose_as_text():
    js = _js()
    section = js[js.index("/* ---------- Journal (v4.3"):js.index("function typing(")]
    assert 'api("/v1/journal")' in section and 'api("/v1/journal/" + encodeURIComponent(date))' in section
    assert "if (ticket !== s.ticket) return;" in section  # a late answer for an old mount is dropped
    assert "{ host: ctx.host, state: {}, plain: true }" in section and "readerBlocks(prose, sec.text" in section
    assert 'lang: "uk"' in section and "innerHTML" not in section
    link = js[js.index("function readerLink("):js.index("function readerInline(")]
    assert "if (ctx.plain && !/^https?:" in link  # a relative link in her journal stays text
    picture = js[js.index("function readerPicture("):js.index("const MD_STARTS")]
    assert "if (ctx.plain) return el(\"p\", { text: alt || src });" in picture  # no picture is loaded for it


def test_v43_arrows_step_days_only_when_the_focus_is_not_in_a_field():
    js = _js()
    keys = js[js.index('if ((ev.key === "ArrowLeft" || ev.key === "ArrowRight") && vw.view === "journal"'):]
    keys = keys[:keys.index("return;\n    }")]
    assert "!ev.defaultPrevented" in keys and "!typing(ev.target)" in keys and "!ev.metaKey" in keys
    assert 'journalStep(places.main.ctx, ev.key === "ArrowLeft" ? -1 : 1)' in keys
    got = _node_eval(_fn("typing") + """
console.log(JSON.stringify([typing({ tagName: "INPUT" }), typing({ tagName: "TEXTAREA" }), typing({ tagName: "BUTTON" }),
  typing({ tagName: "DIV", isContentEditable: true }), typing(null)]));""")
    assert got == [True, True, False, True, False]


def test_v43_the_journal_helpers():
    js = _js()
    consts = "".join("const " + name + " = " + js.split("const " + name + " = ", 1)[1].split(";", 1)[0] + ";"
                     for name in ("MONTHS", "MONTHS3", "WEEKDAYS"))
    def one_line(name):  # a one-line function: its own line
        start = js.index(f"function {name}(")
        return js[start:js.index("\n", start) + 1]

    got = _node_eval(consts + one_line("num") + _fn("dayOf") + "".join(one_line(n) for n in
                                                                  ("longDay", "shortDay", "kbSize")) + _fn("signed") + """
console.log(JSON.stringify([longDay("2026-11-16"), shortDay("2026-11-15"), longDay("2026-06-17"), kbSize(441),
  kbSize(1434), kbSize(7200), signed(0.52), signed(-0.31), signed(0), signed(1), signed(-0.0001)]));""")
    assert got == ["Monday, 16 November", "Sun 15 Nov", "Wednesday, 17 June", "441 B", "1.4 KB", "7.0 KB", "+0.52",
                   "\u22120.31", "+0.00", "+1.00", "+0.00"]


# --- v4.3 LUMI-313: Thoughts and "Thoughts →" in the strip --------------------------------------------------------

def test_v43_the_thoughts_strings_resolve_and_are_english():
    js, strings = _js(), _strings()
    used = set(re.findall(r'"(thoughts\.[A-Za-z_]+|strip\.open_thoughts)"', js))
    assert {"thoughts.title", "thoughts.summary", "thoughts.kind", "thoughts.kind_all", "thoughts.kinds_n",
            "thoughts.clear_kinds", "thoughts.kinds_hint", "thoughts.spoken_filter", "thoughts.all", "thoughts.spoken",
            "thoughts.silent", "thoughts.jump", "thoughts.today", "thoughts.yesterday", "thoughts.more",
            "thoughts.empty", "thoughts.empty_note", "thoughts.clear_filters", "thoughts.loading",
            "thoughts.error_title", "strip.open_thoughts"} <= used
    assert sorted(k for k in used if k not in strings) == []
    add = json.loads((UI.parents[1] / "design" / "v4" / "handoff" / "strings.en.add.json").read_text(encoding="utf-8"))
    for key, value in add.items():
        if key.startswith("thoughts.") or key == "strip.open_thoughts":
            assert strings[key] == value, key  # the design's words, as handed off
    assert not CYRILLIC.search(json.dumps({k: v for k, v in strings.items()
                                           if k.startswith("thoughts.") or k == "strip.open_thoughts"},
                                          ensure_ascii=False))


def test_v43_thoughts_is_a_view_gated_on_features_thoughts_and_the_strip_opens_it():
    js = _js()
    assert 'const VIEWS = ["chat", "files", "gallery", "journal", "thoughts"' in js and 'thoughts: "views.thoughts"' in js
    served = js[js.index("function served("):js.index("function shownViews(")]
    assert 'if (kind === "thoughts") return features.thoughts === true;' in served
    assert "VIEW_KINDS.thoughts = (ctx) =>" in js
    strip = js[js.index("function renderThoughts("):js.index("/* ---------- state from the server")]
    assert 'served("thoughts") ? el("button"' in strip and 'onclick: () => setView("thoughts")' in strip
    assert "renderThoughts();" in js[js.index("function syncServed("):js.index("function viewName(")]


def test_v43_the_stream_pages_by_its_cursor_with_its_filters():
    js = _js()
    query = js[js.index("function thoughtsQuery("):js.index("function thoughtsDay(")]
    assert 'q.append("kind", kind)' in query  # several kinds at once
    assert 'q.set("spoken", s.spoken === "spoken" ? "true" : "false")' in query
    assert 'if (before) q.set("before", before); else if (s.date) q.set("date", s.date);' in query
    load = js[js.index("async function thoughtsLoad("):js.index("function thoughtsFilters(")]
    assert 'api("/v1/thoughts" + thoughtsQuery(s, more ? s.next : null))' in load
    assert "if (s.busy || (more && !s.next)) return false;" in load and "if (ticket !== s.ticket) return false;" in load
    near = js[js.index("function thoughtsNearEnd("):js.index("function thoughtsItems(")]
    assert "< THOUGHTS_NEAR_END_PX" in near and "THOUGHTS_PAGE = 100" in js
    item = js[js.index("function thoughtsItem("):js.index("function thoughtsMore(")]
    assert 'lang: "uk", text: th.text' in item and "innerHTML" not in item


def test_v43_the_thoughts_helpers():
    js = _js()
    consts = "".join("const " + name + " = " + js.split("const " + name + " = ", 1)[1].split(";", 1)[0] + ";"
                     for name in ("MONTHS", "WEEKDAYS", "THOUGHTS_PAGE"))

    def one_line(name):
        start = js.index(f"function {name}(")
        return js[start:js.index("\n", start) + 1]

    got = _node_eval(consts + "const S = {'thoughts.summary': '{n} · about {perDay} a day · {pct}% spoken'};"
                     + "function t(k, v) { return S[k].replace(/\\{(\\w+)\\}/g, (_, x) => v[x]); }"
                     + one_line("num") + _fn("thoughtsSummary") + _fn("thoughtsQuery") + """
const s = { kinds: ["wonder", "learn"], spoken: "silent", date: "2026-11-15" };
console.log(JSON.stringify([thoughtsSummary({ total: 5589, per_day: 34.4, spoken_share: 0.0581 }),
  thoughtsSummary({ total: 22, per_day: 3.14, spoken_share: 1 }), thoughtsQuery(s, null), thoughtsQuery(s, "dDE"),
  thoughtsQuery({ kinds: [], spoken: "all", date: "" }, null)]));""")
    assert got == ["5,589 · about 34 a day · 6% spoken", "22 · about 3.1 a day · 100% spoken",
                   "?limit=100&kind=wonder&kind=learn&spoken=false&date=2026-11-15",
                   "?limit=100&kind=wonder&kind=learn&spoken=false&before=dDE", "?limit=100"]


# --- v4.4 LUMI-317: State and Schedule ----------------------------------------------------------------------------

def test_v44_the_state_and_schedule_strings_resolve_and_are_english():
    js, strings = _js(), _strings()
    used = set(re.findall(r'"((?:state|schedule)\.[A-Za-z_]+)"', js))
    assert {"state.title", "state.mood", "state.biorhythms", "state.cycle", "state.closeness", "state.closeness_of",
            "state.plan", "state.plan_placeholder", "state.plan_later", "state.empty", "state.empty_note",
            "state.loading", "state.error_title", "schedule.title", "schedule.summary", "schedule.quiet_hours",
            "schedule.quiet_note", "schedule.day_cap", "schedule.fired_today", "schedule.directives",
            "schedule.all_enabled", "schedule.next", "schedule.col_name", "schedule.col_directive", "schedule.col_rule",
            "schedule.col_last", "schedule.col_next", "schedule.last_next", "schedule.if_silent", "schedule.footnote",
            "schedule.empty", "schedule.empty_note", "schedule.loading", "schedule.error_title"} <= used
    for key in ("state.physical", "state.emotional", "state.intellectual", "state.low", "state.mid", "state.high",
                "state.rising", "state.falling"):  # built as `state.${…}` from the server's names
        assert key in strings, key
    assert sorted(k for k in used if k not in strings) == []
    add = json.loads((UI.parents[1] / "design" / "v4" / "handoff" / "strings.en.add.json").read_text(encoding="utf-8"))
    for key, value in add.items():
        if key.startswith(("state.", "schedule.")):
            assert strings[key] == value, key  # the design's words, as handed off
    assert not CYRILLIC.search(json.dumps({k: v for k, v in strings.items() if k.startswith(("state.", "schedule."))},
                                          ensure_ascii=False))


def test_v44_state_and_schedule_are_views_gated_on_their_features():
    js = _js()
    assert 'const VIEWS = ["chat", "files", "gallery", "journal", "thoughts", "state", "schedule"]' in js
    served = js[js.index("function served("):js.index("function shownViews(")]
    assert 'if (kind === "state") return features.today === true;' in served
    assert 'if (kind === "schedule") return features.schedule === true;' in served
    assert "VIEW_KINDS.state = (ctx) =>" in js and "VIEW_KINDS.schedule = (ctx) =>" in js
    section = js[js.index("/* ---------- State (v4.4"):js.index("function typing(")]
    assert 'api("/v1/today")' in section and 'api("/v1/schedule")' in section
    assert "document.createElementNS(SVG_NS, tag)" in section and "innerHTML" not in section
    assert "if (ticket !== s.ticket) return;" in section


def test_v44_the_schedule_and_biorhythm_helpers():
    js = _js()
    consts = "".join("const " + name + " = " + js.split("const " + name + " = ", 1)[1].split(";", 1)[0] + ";"
                     for name in ("MONTHS3", "WEEKDAYS3", "BIO_W", "BIO_H", "BIO_SPAN"))

    def one_line(name):
        start = js.index(f"function {name}(")
        return js[start:js.index("\n", start) + 1]

    got = _node_eval(consts + "const S = {'date.today': 'Today', 'date.tomorrow': 'Tomorrow', 'date.yesterday': "
                     "'Yesterday'}; function t(k) { return S[k]; } const pad = (n) => String(n).padStart(2, '0');"
                     + _fn("dayOf") + one_line("ymdOf") + _fn("durText") + _fn("daysText") + _fn("ruleText")
                     + _fn("whenText") + one_line("bioLevel") + _fn("bioPoints") + """
const at = (at, days) => ({ kind: "at", at, days: days || [] });
console.log(JSON.stringify([
  ruleText({ kind: "idle", interval_s: 900 }), ruleText({ kind: "every", interval_s: 5400 }),
  ruleText(at("08:00")), ruleText(at("08:00", [0, 1, 2, 3, 4])), ruleText(at("10:00", [5, 6])),
  ruleText(at("20:00", [6])), ruleText(at("09:00", [0, 2])),
  ruleText({ kind: "between", interval_s: 10800, window: ["08:30", "23:30"] }),
  ruleText({ kind: "cron", cron: "*/30 9-18 * * 1-5" }), durText(45),
  whenText("2026-11-18T23:41", "2026-11-18T23:56"), whenText("2026-11-19T08:00", "2026-11-18T23:56"),
  whenText("2026-11-17T21:10", "2026-11-18T23:56"), whenText("2026-11-22T20:00", "2026-11-18T23:56"),
  whenText(null, "2026-11-18T23:56"), bioLevel(0.5), bioLevel(0.1), bioLevel(-0.4),
  bioPoints(0, 23).split(" ").length, bioPoints(0, 28).split(" ")[35]]));""")
    assert got == ["after 15 min of silence", "every 1 h 30 min", "every day at 08:00", "weekdays at 08:00",
                   "weekends at 10:00", "Sundays at 20:00", "Mon, Wed at 09:00", "08:30\u201323:30 every 3 h",
                   "cron */30 9-18 * * 1-5", "45 s", "Today 23:41", "Tomorrow 08:00", "Yesterday 21:10",
                   "22 Nov 20:00", "\u2014", "high", "mid", "low", 71, "140,28.0"]


# --- v4.4 LUMI-318: a turn's tools in the details panel ------------------------------------------------------------

def test_v44_the_details_panel_lists_the_last_replys_tools_only_when_the_server_sent_them():
    js, strings = _js(), _strings()
    assert strings["tools.title"] == "Tools \u00b7 last reply" and strings["tools.none"] == "No tools in the last reply."
    turn = js[js.index("async function turn("):js.index("async function streamTurn(")]
    assert "st.lastTools = Array.isArray(result.tool_calls) ? result.tool_calls : null;" in turn  # absent → no block
    block = js[js.index("function toolsBlock("):js.index("function setPortraitShown(")]
    assert "if (!Array.isArray(calls)) return null;" in block and "calls.slice(0, 20)" in block
    assert 't("tools.title")' in block and 't("tools.none")' in block and "innerHTML" not in block
    details = js[js.index("function renderDetails("):js.index("function toolsBlock(")]
    assert "box.replaceChildren(grid, toolsBlock(), " in details  # between the cells and the controls


# --- v4.4 LUMI-319: the hormonal cycle in State and in the portrait panel -----------------------------------------

def test_v44_the_hormonal_cycle_is_drawn_and_named():
    js, strings = _js(), _strings()
    for key in ("state.hormonal", "state.days_left", "state.day_left", "state.last_day", "panel.cycle",
                "panel.cycle.day", "phase.menstrual", "phase.follicular", "phase.ovulation", "phase.luteal", "phase.pms"):
        assert key in strings and not CYRILLIC.search(strings[key]), key
    row = js[js.index("function hormonalRow("):js.index("VIEW_KINDS.state = (ctx) =>")]
    assert 'svgNode("rect"' in row and 'lang: "uk", text: c.note' in row and "innerHTML" not in row
    assert "cycle ? hormonalRow(cycle) : null" in js
    assert "cycleCell(s.cycle)" in js[js.index("function metaGrid("):js.index("function cycleCell(")]


def test_v44_the_hormonal_cycle_helpers():
    js = _js()
    consts = "const BIO_SPAN = 28; const PHASE_BAR = " + js.split("const PHASE_BAR = ", 1)[1].split(";", 1)[0] + ";"
    got = _node_eval(consts + _fn("phaseAt") + _fn("phaseLeft") + """
const phases = [...Array(5).fill("menstrual"), ...Array(7).fill("follicular"), ...Array(3).fill("ovulation"),
  ...Array(8).fill("luteal"), ...Array(5).fill("pms")];
const at = (day) => ({ day, length: 28, phase: phases[day - 1], phases });
console.log(JSON.stringify([phaseAt(at(19), 0), phaseAt(at(19), 5), phaseAt(at(28), 1), phaseAt(at(1), -1),
  phaseAt(at(1), -14), phaseLeft(at(19)), phaseLeft(at(23)), phaseLeft(at(14)), phaseLeft(at(1))]));""")
    assert got == ["luteal", "pms", "menstrual", "pms", "ovulation", 4, 0, 1, 4]


# --- v4.4 LUMI-320: what each scheduled directive does ------------------------------------------------------------

def test_v44_the_schedule_shows_each_rows_topic_and_its_seeds_behind_a_toggle():
    js, strings = _js(), _strings()
    for key in ("schedule.seeds_n", "schedule.seeds_one", "schedule.placeholders", "schedule.no_seeds"):
        assert key in strings and not CYRILLIC.search(strings[key]), key
    what = js[js.index("function scheduleWhat("):js.index("function scheduleNext(")]
    assert 'lang: "uk", text: e.topic' in what and '"aria-expanded": String(open)' in what
    assert 'e.seed_lines.map((line) => el("li", { lang: "uk", text: line }))' in what and "innerHTML" not in what
    view = js[js.index("function renderScheduleView("):js.index("function typing(")]
    assert view.count("scheduleWhat(ctx, e)") == 2  # wide and narrow


# --- v4.5 LUMI-324: the owner's actions in Files and the reader ----------------------------------------------------

def test_v45_the_actions_ask_first_and_show_only_while_served():
    js, strings = _js(), _strings()
    section = js[js.index("/* ---------- the owner's actions (v4.5"):js.index("/* her markdown for the reader")]
    assert "function actionsOn() { return !!(st.server && st.server.features && st.server.features.actions === true); }" in section
    for act in ("renameItem", "trashItem", "restoreItem"):
        body = section[section.index(f"async function {act}("):]
        body = body[:body.index("\n}\n")]
        assert "await actionDialog(" in body and "if (!" in body, act  # nothing is written before the owner says yes
    assert '"Content-Type": "application/octet-stream"' in section and 'fetch("/v1/files/upload?"' in section
    assert 'api("/local/save", { path })' in section and 'api("/local/open", { url: href })' in section
    assert 'api("/local/reveal", { saved: n.saved })' in section
    assert "innerHTML" not in section and 'role: "dialog", "aria-modal": "true"' in section
    assert 'ev.key === "Escape"' in section
    assert "if (!actionsOn()) return row;" in js and "if (!actionsOn()) return frame;" in section
    used = set(re.findall(r'"(actions\.[a-z_]+)"', js))
    assert used and sorted(k for k in used if k not in strings) == []
    assert not CYRILLIC.search(json.dumps({k: v for k, v in strings.items() if k.startswith("actions.")},
                                          ensure_ascii=False))


def test_v45_the_reader_can_open_a_link_in_the_browser_and_download():
    js = _js()
    link = js[js.index("function readerLink("):js.index("function readerInline(")]
    assert 'onclick: () => openExternal(href)' in link and 't("actions.open")' in link
    reader = js[js.index("async function renderReader("):js.index("/* ---------- the full picture view")]
    assert "downloadFile(ctx, cur.path)" in reader and "actionsOn() ?" in reader


# --- v4.5 LUMI-325: "Show to Lili" and the picture view's actions --------------------------------------------------

def test_v45_show_to_lili_sends_the_line_with_the_pictures_sandbox_path():
    js, strings = _js(), _strings()
    section = js[js.index("/* ---------- the picture view's actions (v4.5"):js.index("/* ---------- Gallery (v4.2")]
    assert "st.server.features.image === true" in section  # offered only while she can see pictures
    assert "sendLine(text, null, [item.path]);" in section and "innerHTML" not in section
    assert "await actionDialog(ctx" in section  # rename and trash ask first here too
    turn = js[js.index("async function turn("):js.index("async function streamTurn(")]
    assert "...(pictures ? { sandbox_images: pictures } : {})" in turn and "chatPictures(mine, pictures)" in turn
    assert "return turn(text, null, true, pictures);" in turn  # a busy retry keeps them
    view = js[js.index("function openPicture("):js.index("function closePicture(")]
    assert "!typing(ev.target)" in view  # arrows in the line's field type, they don't step pictures
    assert "chatPictures(mine, data.pictures)" in js  # another window's line shows what it showed her
    for key in ("actions.show", "actions.show_placeholder", "actions.show_busy", "actions.title"):
        assert key in strings and not CYRILLIC.search(strings[key])


# --- v4.5 LUMI-326: links in the chat and bare addresses in the reader --------------------------------------------

def test_v45_a_bare_address_keeps_its_punctuation_outside_and_needs_a_host():
    js = _js()
    inline_re = js[js.index("const INLINE"):js.index("\n", js.index("const INLINE"))]
    got = _node_eval(inline_re + "\n" + _fn("bareUrl") + r"""
const cases = ["https://a.com/x.", "https://uk.wikipedia.org/wiki/Дюна_(роман),", "https://a.com/x)", "https://c.net/z?q=1#top!",
  "https://.", "https://b.org/y…"];
const found = ["(https://a.com/x) і «https://b.org/y». javascript:alert(1) file:///etc/passwd `https://code.x`"]
  .map((s) => { const out = []; let rest = s; for (;;) { const m = INLINE.exec(rest); if (!m) break;
    out.push(m[6] ? bareUrl(m[0])[0] : m[1] ? "code" : "other"); rest = rest.slice(m.index + m[0].length); } return out; });
console.log(JSON.stringify([cases.map(bareUrl), found]));""")
    assert got == [[["https://a.com/x", "."], ["https://uk.wikipedia.org/wiki/Дюна_(роман)", ","],
                    ["https://a.com/x", ")"], ["https://c.net/z?q=1#top", "!"], ["", "https://."],
                    ["https://b.org/y", "…"]],
                   [["https://a.com/x", "https://b.org/y", "code"]]]


def test_v45_a_link_in_the_chat_opens_through_the_proxy_never_in_the_window():
    js = _js()
    head = js[js.index("const INLINE"):js.index("function inline(")]
    assert '|(https?:\\/\\/[^\\s<>"\'«»`]+)/i;' in head  # the bare address: http / https only
    link = _fn("chatLink")
    assert "onclick: (ev) => { ev.preventDefault(); openExternal(href); }" in link
    assert 'target: "_blank", rel: "noopener noreferrer"' in link and "innerHTML" not in link
    body = _fn("inline")
    assert 'out.push(chatLink(m[5], tok.slice(1, tok.indexOf("]("))));' in body  # a markdown link too
    assert "if (url) out.push(chatLink(url, url));" in body
    mine = _fn("addMine")
    assert "withLinks(String(text))" in mine and "text }" not in mine  # your line: only its addresses become links
    assert "out.push(chatLink(url, url));" in _fn("withLinks")
    assert "openExternal(" in _fn("openExternal") and 'api("/local/open", { url: href })' in _fn("openExternal")


def test_v45_a_bare_address_in_the_reader_gets_open_in_browser():
    js = _js()
    reader_re = js[js.index("const READER_INLINE"):js.index("\n", js.index("const READER_INLINE"))]
    assert reader_re.endswith('|(https?:\\/\\/[^\\s<>"\'«»`]+)/;')
    body = _fn("readerInline")
    assert "if (url) out.push(readerLink(ctx, null, url, base));" in body
    link = _fn("readerLink")
    assert 'if (label === null) return el("span", { class: "xlink" }, el("span", { class: "xaddr", text: href }), " ", open);' in link


def test_review_v45_2_restore_is_offered_only_where_the_log_knows_the_origin():
    """v4.5 code review #2: Restore on an item inside a trashed folder always got 409 (the log names the folder)."""
    menu = _fn("rowEntries")
    assert 'if (parts[0] === ".trash" && parts.length === 3) entries.push(["actions.restore"' in menu
    assert 'else if (parts[0] !== ".trash")' in menu  # nothing to rename or trash inside the trash
    assert "const entries = rowEntries(ctx, item);" in _fn("openRowMenu")
    assert "if (!rowEntries(ctx, item).length) return null;" in _fn("rowMenuButton")  # no empty menu


def test_review_v45_3_a_file_over_the_largest_cap_is_never_sent():
    """v4.5 code review #3: a large file was sent whole before the 413 (over 64 MB the proxy closed the connection)."""
    js, strings = _js(), _strings()
    assert "const UPLOAD_MAX_FILE = 10 * 1024 * 1024;" in js
    body = _fn("uploadFiles")
    check = body.index("if (file.size > UPLOAD_MAX_FILE) {")
    assert check < body.index('fetch("/v1/files/upload?"') and 't("actions.too_large")' in body[check:]
    assert "actions.too_large" in strings and not CYRILLIC.search(strings["actions.too_large"])
    import core.actions as actions
    assert actions.UPLOAD_MAX_PICTURE == 10 * 1024 * 1024 >= actions.UPLOAD_MAX_TEXT  # the page's bound is the largest cap
