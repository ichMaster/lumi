"""The v2.1 env badge in the live TUI status line (LUMI-208).

Hermetic: ``tui.app.load_config`` is patched to read the environment only (never the repo's ``.env``),
so the developer's own ``LUMI_ENV`` can't leak into the assertions.
"""

import pytest

from core.agent import Core
from core.config import load_config
from core.llm import MockLLMClient
from state.local_store import JsonRepository
from tui.app import STATUS_READY, LumiApp


def _core(tmp_path):
    return Core(
        llm=MockLLMClient("Привіт!"),
        repository=JsonRepository(tmp_path / "store.json"),
        canon="Ти — Лілі.",
        model="claude-haiku-4-5-20251001",
    )


@pytest.fixture
def env_only_config(monkeypatch):
    monkeypatch.setattr("tui.app.load_config", lambda: load_config(load_env=False))
    return monkeypatch


async def _status_with_env(tmp_path, monkeypatch, env: str | None) -> tuple[str, str, str]:
    if env is None:
        monkeypatch.delenv("LUMI_ENV", raising=False)
    else:
        monkeypatch.setenv("LUMI_ENV", env)
    app = LumiApp(_core(tmp_path))
    async with app.run_test():
        ready = app._status_text()
        busy = app._status_text("requesting…")
        app._connected = False
        offline = app._status_text()
    return ready, busy, offline


async def test_prod_badge_leads_every_status_variant(tmp_path, env_only_config):
    version = load_config(load_env=False).app_version
    for line in await _status_with_env(tmp_path, env_only_config, "prod"):
        assert line.startswith(f"[bold red]prod v{version}[/] · status: ")


async def test_dev_badge_is_dim(tmp_path, env_only_config):
    ready, _, _ = await _status_with_env(tmp_path, env_only_config, "dev")
    assert ready.startswith("[dim]dev v")
    assert STATUS_READY in ready


async def test_unset_env_status_line_is_unchanged(tmp_path, env_only_config):
    # Contract (v2.1): no LUMI_ENV → no badge — every variant starts exactly as before.
    ready, busy, offline = await _status_with_env(tmp_path, env_only_config, None)
    assert ready.startswith(f"status: [green]{STATUS_READY}[/] · ")
    assert busy.startswith("status: [yellow]requesting…[/] · ")
    assert offline.startswith("status: [red]offline[/] · ")
