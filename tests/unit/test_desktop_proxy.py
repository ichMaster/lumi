"""v2.12.1 LUMI-266 — the desktop proxy's pure parts: the tree's client settings → the upstream, the launch key in
``index.html``, the static file lookup that never leaves ``desktop/ui/``."""

from __future__ import annotations

from types import SimpleNamespace

from desktop.proxy import KEY_META, UI_DIR, Upstream, ui_file, upstream_from, with_key

TOKEN = "client-token-SECRET-0123456789"


def test_the_client_settings_become_the_upstream():
    up = upstream_from(SimpleNamespace(server_host="192.168.1.197", server_port=8766, server_token=f" {TOKEN} "))
    assert up == Upstream("http://192.168.1.197:8766", TOKEN) and up.configured
    assert TOKEN not in repr(up)
    assert upstream_from(SimpleNamespace(server_host="::1", server_port=8765, server_token=TOKEN)).url == \
        "http://[::1]:8765"
    assert not upstream_from(SimpleNamespace(server_host="", server_port=8765, server_token=TOKEN)).configured
    assert not upstream_from(SimpleNamespace(server_host="127.0.0.1", server_port=8765, server_token="")).configured


def test_the_launch_key_goes_right_after_head():
    html = "<!doctype html><html><HEAD><title>x</title></head></html>"
    assert with_key(html, "k1") == ('<!doctype html><html><HEAD><meta name="' + KEY_META + '" content="k1">'
                                    "<title>x</title></head></html>")
    assert with_key("<p>no head</p>", "k2").startswith(f'<meta name="{KEY_META}" content="k2">')


def test_a_static_path_never_leaves_the_ui_folder(tmp_path):
    (tmp_path / "index.html").write_text("<head></head>")
    (tmp_path / "app.js").write_text("")
    (tmp_path.parent / "secret.txt").write_text("no")
    assert ui_file("/", tmp_path) == (tmp_path / "index.html").resolve()
    assert ui_file("/app.js?v=1".split("?")[0], tmp_path) == (tmp_path / "app.js").resolve()
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/missing.css", "/."):
        assert ui_file(path, tmp_path) is None, path
    assert UI_DIR.name == "ui" and UI_DIR.parent.name == "desktop"
