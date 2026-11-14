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


def main(argv: list[str] | None = None) -> int:
    try:
        import webview  # pywebview — imported here, so the package imports without it
    except ImportError:
        print(MISSING, file=sys.stderr)
        return 1
    from core.config import load_config
    from desktop.proxy import Proxy, upstream_from

    proxy = Proxy(upstream_from(load_config()))
    url = proxy.start()
    try:
        webview.create_window(TITLE, url, width=SIZE[0], height=SIZE[1], min_size=MIN_SIZE)
        webview.start()
    finally:
        proxy.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
