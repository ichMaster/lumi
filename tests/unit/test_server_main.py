"""`python -m server` glue (LUMI-212) — the token check comes first; startup housekeeping never crashes."""

import threading
import time
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
    assert "LUMI_SERVER_TOKEN" in str(exc.value) and exc.value.code == sm.EX_CONFIG  # v2.6: no restart loop
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


# --- code review v2.3 #1: shutdown never races an in-flight turn ---------------------------------------

class _Service:
    def __init__(self, end_session):
        self.lock = threading.Lock()
        self.session = object()
        self.core = SimpleNamespace(end_session=end_session)


def test_close_session_with_no_turn_running_closes_at_once():
    calls: list = []
    svc = _Service(lambda s: calls.append(s))
    assert sm.close_session(svc, wait_s=1) is True and calls == [svc.session]
    assert svc.lock.acquire(blocking=False)  # released afterwards


def test_close_session_waits_for_the_turn_in_flight_and_never_overlaps_it():
    events: list[str] = []
    svc = _Service(lambda s: events.append("end_session"))
    svc.lock.acquire()  # the runner is inside core.reply

    def finish_turn():
        time.sleep(0.2)
        events.append("turn done")
        svc.lock.release()

    threading.Thread(target=finish_turn).start()
    assert sm.close_session(svc, wait_s=5) is True
    assert events == ["turn done", "end_session"]  # closed only after the turn — no overlap


def test_a_turn_that_outlasts_the_wait_leaves_the_session_open():
    calls: list = []
    svc = _Service(lambda s: calls.append(s))
    svc.lock.acquire()  # a turn that won't finish in time
    assert sm.close_session(svc, wait_s=0.1) is False
    assert calls == []  # never raced


def test_a_failing_end_session_still_releases_the_lock():
    def boom(session):
        raise RuntimeError("summary failed")

    svc = _Service(boom)
    assert sm.close_session(svc, wait_s=1) is False
    assert svc.lock.acquire(blocking=False)


# --- code review v2.4 #2: a taken port is refused before the core (no model call, no banner) -------------

def _taken_port():
    import socket

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    return holder, holder.getsockname()[1]


def test_port_free_sees_a_listener():
    holder, port = _taken_port()
    try:
        assert sm.port_free("127.0.0.1", port) is False
    finally:
        holder.close()
    assert sm.port_free("127.0.0.1", port) is True


def test_a_taken_port_is_refused_before_the_core_is_built(monkeypatch):
    import dataclasses

    holder, port = _taken_port()
    cfg = dataclasses.replace(load_config(load_env=False), server_token="t", server_host="127.0.0.1",
                              server_port=port)
    monkeypatch.setattr("core.config.load_config", lambda: cfg)
    monkeypatch.setattr("core.envguard.guard_entry", lambda c: None)
    monkeypatch.setattr("core.brainlock.guard_single_brain", lambda c, role: None)
    built: list = []
    monkeypatch.setattr("core.agent.build_core", lambda **kw: built.append(kw))
    try:
        with pytest.raises(SystemExit) as exc:
            sm.main()
    finally:
        holder.close()
    assert f"Port {port}" in str(exc.value) and "already in use" in str(exc.value)
    assert exc.value.code == sm.EX_CONFIG  # v2.6: another service holds it — systemd must not loop on it
    assert built == []  # refused before the core — no startup model calls


# --- v2.6 (LUMI-228): LAN/Tailscale only — a wildcard or public bind is refused --------------------------


@pytest.mark.parametrize("host", [
    "127.0.0.1", "localhost", "LOCALHOST", "::1", "[::1]", "10.0.0.5", "172.16.3.4", "172.31.255.1",
    "192.168.1.20", "100.101.102.103", "100.64.0.1", "fd7a:115c:a1e0::1", "fc00::5",
])
def test_lan_tailscale_and_loopback_binds_pass(host):
    assert sm.bind_problem(host) is None


@pytest.mark.parametrize(("host", "why"), [
    ("0.0.0.0", "every interface"), ("::", "every interface"), ("", "empty"), ("  ", "empty"),
    ("8.8.8.8", "public"), ("172.32.0.1", "public"), ("100.128.0.1", "public"), ("2001:4860::8888", "public"),
    ("lumi-box", "hostname"), ("lumi-box.tail1234.ts.net", "hostname"),
])
def test_wildcard_public_and_hostname_binds_are_refused(host, why):
    problem = sm.bind_problem(host)
    assert problem is not None and why in problem and "LAN or Tailscale IP" in problem


def test_a_refused_bind_stops_before_the_guard_or_the_core(monkeypatch):
    import dataclasses

    cfg = dataclasses.replace(load_config(load_env=False), server_token="t", server_host="0.0.0.0")
    monkeypatch.setattr("core.config.load_config", lambda: cfg)
    touched: list = []
    monkeypatch.setattr("core.envguard.guard_entry", lambda c: touched.append("guard"))
    monkeypatch.setattr("core.agent.build_core", lambda **kw: touched.append("core"))
    with pytest.raises(SystemExit) as exc:
        sm.main()
    assert "0.0.0.0" in str(exc.value) and "every interface" in str(exc.value)
    assert exc.value.code == sm.EX_CONFIG
    assert touched == []  # refused before the data root and the core — nothing ran


# --- v2.6 (LUMI-230): under systemd — a taken port never loops, a missing address retries, SIGTERM = Ctrl+C ----


def test_an_address_not_on_this_machine_yet_is_a_retryable_failure(monkeypatch):
    import errno

    monkeypatch.setattr(sm, "bind_error", lambda h, p: OSError(errno.EADDRNOTAVAIL, "Can't assign requested address"))
    with pytest.raises(SystemExit) as exc:
        sm.check_port("100.101.102.103", 8766)
    assert not isinstance(exc.value, sm.ConfigRefusal)  # exit 1 → systemd restarts until Tailscale is up
    assert "not an address of this machine" in str(exc.value)


def test_a_port_another_service_holds_is_a_config_refusal_with_this_os_hint(monkeypatch):
    import errno

    monkeypatch.setattr(sm, "bind_error", lambda h, p: OSError(errno.EADDRINUSE, "Address already in use"))
    for platform, hint in (("linux", "ss -ltnp 'sport = :8765'"), ("darwin", "lsof -nP -iTCP:8765")):
        monkeypatch.setattr(sm.sys, "platform", platform)
        with pytest.raises(sm.ConfigRefusal) as exc:
            sm.check_port("100.101.102.103", 8765)
        assert exc.value.code == sm.EX_CONFIG and hint in str(exc.value)


def test_bind_error_names_why():
    import errno

    holder, port = _taken_port()
    try:
        assert sm.bind_error("127.0.0.1", port).errno == errno.EADDRINUSE
    finally:
        holder.close()
    assert sm.bind_error("127.0.0.1", port) is None


def test_sigterm_is_handled_like_ctrl_c():
    import os
    import signal

    previous = signal.getsignal(signal.SIGTERM)
    try:
        sm.term_as_interrupt()
        with pytest.raises(KeyboardInterrupt):  # serve() swallows it, so main()'s finally closes the session
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(1)  # the handler runs at the next bytecode; never reached
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_run_prints_a_refusal_then_exits_78(monkeypatch, capsys):
    """v2.6 code review #2: the one entry point (`python -m server`, `./lumi-cli serve`) never exits silently."""
    import dataclasses

    cfg = dataclasses.replace(load_config(load_env=False), server_token="t", server_host="0.0.0.0")
    monkeypatch.setattr("core.config.load_config", lambda: cfg)
    with pytest.raises(SystemExit) as exc:
        sm.run()
    assert exc.value.code == sm.EX_CONFIG and not isinstance(exc.value, sm.ConfigRefusal)  # a plain exit 78
    assert "listens on every interface" in capsys.readouterr().err
