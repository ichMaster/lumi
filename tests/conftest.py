"""Shared pytest fixtures.

**Isolate every test from the developer's real runtime environment.** In particular, the user's
`.env` may set `LUMI_BRIDGE=on` with the real `inbox`/`outbox` paths. Without this, a TUI test that
runs a mock turn would call `mirror_reply(...)` into the **real** `.lumi/outbox.jsonl` — and the
running Telegram daemon would then send those mock replies to Telegram. v2.9: `LUMI_SETTINGS_DIR`
points at an empty folder, so a developer's real `settings.yaml` / `secrets.yaml` / `.env` at the repo
root never reaches a test (and a `monkeypatch.setenv` always wins anyway — the environment is the top layer).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_runtime(monkeypatch, tmp_path_factory):
    """Never let a test join the real Telegram bus or write the real outbox via voice; tmp bus paths."""
    monkeypatch.setenv("LUMI_BRIDGE", "off")
    monkeypatch.setenv("LUMI_VOICE", "off")  # v0.14: no test writes the real outbox via voice either
    # v0.16: never let a dev's LUMI_RECALL=on make a test load the real embedding model (network +
    # slow). Tests that exercise recall inject a MockEmbedder / recall_enabled=True explicitly.
    monkeypatch.setenv("LUMI_RECALL", "off")
    bus = tmp_path_factory.mktemp("bus")
    monkeypatch.setenv("LUMI_INBOX_PATH", str(bus / "inbox.jsonl"))
    monkeypatch.setenv("LUMI_OUTBOX_PATH", str(bus / "outbox.jsonl"))
    # v2.9: no settings file of the developer's is read — tests that need one pass settings_dir= explicitly.
    monkeypatch.setenv("LUMI_SETTINGS_DIR", str(tmp_path_factory.mktemp("settings")))
