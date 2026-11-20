"""``python -m desktop`` (v2.12.1, LUMI-266) — Лілі's desktop window.

Reads the tree's client settings (``server.host`` / ``server.port`` in ``settings.yaml``, ``server.token`` in
``secrets.yaml``), starts the local proxy (:mod:`desktop.proxy`) and opens a pywebview window onto it. The window
holds no Лілі logic and no token; closing it stops the proxy. pywebview is optional (the ``desktop`` extra).
"""

from __future__ import annotations

import sys

MISSING = ("the desktop window needs pywebview, which isn't installed — start it with ./lumi-desktop (it installs "
           "the desktop extra), or: uv run --extra desktop python -m desktop")
TITLE = "Lili"
SIZE = (1200, 800)
MIN_SIZE = (720, 520)
APP_NAP_REASON = "Lili's window plays her voice and keeps its push channel open"


def keep_awake(process_info=None, options=None):
    """v4.8.1: on macOS, opt this process out of App Nap while the window is open.

    A napped app in the background got her voice paused mid-line by WebKit, and its timers stretched. Returns the
    activity token to end on close, or ``None`` off macOS or without PyObjC (pywebview brings it on macOS). Idle
    system sleep stays allowed. ``process_info`` / ``options`` are injected in tests."""
    if process_info is None:
        if sys.platform != "darwin":
            return None
        try:
            from Foundation import NSActivityUserInitiatedAllowingIdleSystemSleep, NSProcessInfo
        except ImportError:
            return None
        process_info, options = NSProcessInfo.processInfo(), NSActivityUserInitiatedAllowingIdleSystemSleep
    try:
        return process_info.beginActivityWithOptions_reason_(options, APP_NAP_REASON)
    except Exception:  # noqa: BLE001 — never a reason for the window not to open
        return None


def end_awake(token, process_info=None) -> None:
    """End the activity :func:`keep_awake` began (``None`` → nothing to end)."""
    if token is None:
        return
    try:
        if process_info is None:
            from Foundation import NSProcessInfo

            process_info = NSProcessInfo.processInfo()
        process_info.endActivity_(token)
    except Exception:  # noqa: BLE001 — the process is ending anyway
        pass


def main(argv: list[str] | None = None) -> int:
    try:
        import webview  # pywebview — imported here, so the package imports without it
    except ImportError:
        print(MISSING, file=sys.stderr)
        return 1
    from core.config import load_config
    from desktop.dictation import Dictation
    from desktop.faces import FacePicker, emoji_table
    from desktop.proxy import Proxy, upstream_from

    cfg = load_config()
    upstream = upstream_from(cfg)
    proxy = Proxy(upstream, faces=FacePicker.for_pack(cfg.faces_dir),  # v2.12.2: her local portraits
                  emoji=emoji_table(cfg.emoji_path),  # LUMI-276: the TUI's emoji map, under each of her replies
                  dictation=Dictation(upstream, device=cfg.stt_device))  # v4.7: the Mac's mic, the server's words
    url = proxy.start()
    awake = keep_awake()  # v4.8.1: no App Nap while the window is open (her voice, the push channel)
    try:
        # text_select: pywebview turns text selection off by default — the owner selects and copies her words
        webview.create_window(TITLE, url, width=SIZE[0], height=SIZE[1], min_size=MIN_SIZE, text_select=True)
        webview.start()
    finally:
        end_awake(awake)
        proxy.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
