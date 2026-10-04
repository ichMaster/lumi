"""`python -m server` glue (LUMI-212) — the token check comes first; startup housekeeping never crashes."""

import threading
from types import SimpleNamespace

import pytest

import server.__main__ as sm
from core.config import load_config


def test_no_token_refuses_before_the_guard_or_the_core(monkeypatch):
    monkeypatch.delenv("LUMI_SERVER_TOKEN", raising=False)
    monkeypatch.setattr("core.config.load_config", lambda: load_config(load_env=False))
    touched: list = []
    monkeypatch.setattr("core.envguard.guard_entry", lambda cfg: touched.append("guard"))
    with pytest.raises(SystemExit) as exc:
        sm.main()
    assert "LUMI_SERVER_TOKEN" in str(exc.value)
    assert touched == []  # the guard (and so the data root) is never reached without a token


def test_world_refresh_is_skipped_without_a_place():
    calls: list = []
    core = SimpleNamespace(set_world_context=calls.append, clock=None)
    cfg = SimpleNamespace(location=None, lat=None, lon=None, news_url=None)
    sm._refresh_world(core, cfg)
    assert calls == []


def test_background_housekeeping_swallows_failures():
    done = threading.Event()

    def boom():
        done.set()
        raise RuntimeError("mood call failed")

    sm._background("mood", boom)
    assert done.wait(2)  # it ran — and the exception stayed inside its thread
