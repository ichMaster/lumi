"""Shared pytest fixtures.

**Isolate every test from the developer's real runtime environment.** In particular, the user's
`.env` may set `LUMI_BRIDGE=on` with the real `inbox`/`outbox` paths. Without this, a TUI test that
runs a mock turn would call `mirror_reply(...)` into the **real** `.lumi/outbox.jsonl` — and the
running Telegram daemon would then send those mock replies to Telegram. `load_config()` uses
`load_dotenv()` with `override=False`, so a `monkeypatch.setenv` here takes precedence over `.env`.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_runtime(monkeypatch, tmp_path_factory):
    """Never let a test join the real Telegram file bus; point the bus paths at a temp dir."""
    monkeypatch.setenv("LUMI_BRIDGE", "off")
    bus = tmp_path_factory.mktemp("bus")
    monkeypatch.setenv("LUMI_INBOX_PATH", str(bus / "inbox.jsonl"))
    monkeypatch.setenv("LUMI_OUTBOX_PATH", str(bus / "outbox.jsonl"))
