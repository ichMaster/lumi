"""Contract: everything the TUI asks of its core, `RemoteCore` answers (LUMI-213).

The TUI talks to ``self._core`` — an in-process ``Core``, or in client mode a ``RemoteCore`` over the
v2.2 API. This pins the surface: if the TUI starts using a new core member, this fails until
``RemoteCore`` implements it (or the use moves into the command layer), so client mode never breaks
silently. And client mode is opt-in: ``LUMI_SERVER`` unset → the in-process TUI, as before.
"""

import re
from pathlib import Path

from core.config import load_config
from tui.remote import RemoteCore

_TUI = Path(__file__).resolve().parents[2] / "tui" / "app.py"


def _tui_core_surface() -> set[str]:
    src = _TUI.read_text(encoding="utf-8")
    direct = set(re.findall(r"self\._core\.([a-zA-Z_]\w*)", src))
    probed = set(re.findall(r'(?:getattr|hasattr)\(self\._core,\s*"([a-zA-Z_]\w*)"', src))
    return direct | probed


def test_remote_core_implements_the_whole_tui_surface():
    remote = RemoteCore("http://unused", "t")
    missing = sorted(name for name in _tui_core_surface() if not hasattr(remote, name))
    assert missing == [], f"the TUI uses core members RemoteCore lacks: {missing}"


def test_the_surface_stays_small():
    # v2.2: with the commands in the layer, the TUI's own core surface is this handful (24 incl. the
    # remote-only `command`); v2.4 adds the remote-only push channel (`listen` / `stop`). Growth here is
    # a design signal — prefer the command layer.
    assert len(_tui_core_surface()) <= 26


def test_client_mode_is_opt_in(monkeypatch):
    monkeypatch.delenv("LUMI_SERVER", raising=False)
    assert load_config(load_env=False).server is False
