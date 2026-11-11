"""v2.11 LUMI-261 — the audit log (``admin/audit.py``) and the deploy-agent lock guard (``admin/guard.py``, the
v2.10 code review #4). ``tmp_path`` only; the agent is a subprocess holding the lock the way the real one does."""

from __future__ import annotations

import fcntl
import json
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from admin import edit as ed
from admin import guard as gd
from admin.audit import Audit

TZ = timezone(timedelta(hours=2))
SECRET = "tok-NEW-SECRET-wxyz9876"


def _clock():
    return datetime(2026, 11, 11, 17, 0, 5, tzinfo=TZ)


# --- the audit log -----------------------------------------------------------------------------------------------


def test_every_action_kind_is_one_line_newest_first(tmp_path):
    audit = Audit(tmp_path / "admin" / "audit.jsonl", clock=_clock)
    audit.record("restart", "prod", "server")
    audit.record("settings", "dev", "memory.window", changes={"memory.window": [40, 30]})
    audit.record("secret", "dev", "server.token", changes={"server.token": ed.SECRET_SHOWN})
    audit.record("rollback", "dev", "20261111-153012Z", changes={"memory.window": [30, 40]})
    audit.record("section", "dev", "schedule", changes={"schedule": "(section edited)"})
    audit.record("promote", "prod", "memory.window", result="refused: a change on prod needs the typed confirmation")
    rows = audit.read()
    assert [r["action"] for r in rows] == ["promote", "section", "rollback", "secret", "settings", "restart"]
    assert rows[0]["result"].startswith("refused:") and rows[-1]["result"] == "done"
    assert rows[4] == {"ts": "2026-11-11T17:00:05+02:00", "action": "settings", "instance": "dev",
                       "target": "memory.window", "changes": {"memory.window": [40, 30]}, "result": "done"}
    assert [r["action"] for r in audit.read(instance="prod")] == ["promote", "restart"]
    assert stat.S_IMODE(audit.path.stat().st_mode) == 0o600


def test_a_secret_change_is_logged_as_secret_and_a_stray_value_is_redacted(tmp_path):
    audit = Audit(tmp_path / "audit.jsonl", clock=_clock)
    audit.record("secret", "dev", "server.token", changes={"server.token": ed.SECRET_SHOWN})
    audit.record("restart", "dev", "server", result=f"failed: a careless error with {SECRET}",
                 redact=lambda text: text.replace(SECRET, "•••"))
    text = audit.path.read_text(encoding="utf-8")
    assert SECRET not in text and '"server.token": "(secret)"' in text and "•••" in text


def test_the_log_rotates_at_its_cap_and_reads_across_the_generations(tmp_path):
    audit = Audit(tmp_path / "audit.jsonl", clock=_clock, max_bytes=600)
    for n in range(12):
        audit.record("restart", "dev", f"server-{n}")
    assert (tmp_path / "audit.jsonl.1").exists() and audit.path.stat().st_size <= 600
    rows = audit.read(limit=5)
    assert [r["target"] for r in rows] == [f"server-{n}" for n in (11, 10, 9, 8, 7)]
    assert all(json.loads(line) for line in audit.path.read_text().splitlines())


# --- the agent lock guard -----------------------------------------------------------------------------------------

_HOLDER = """
import fcntl, sys, time
f = open(sys.argv[1], "w")
fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("locked", flush=True)
sys.stdin.read()
"""


def test_busy_while_the_agent_holds_its_lock_and_free_after(tmp_path):
    lock = tmp_path / "agent.lock"
    lock.write_text("")
    agent = subprocess.Popen([sys.executable, "-c", _HOLDER, str(lock)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    try:
        assert agent.stdout.readline().strip() == "locked"
        assert gd.agent_busy(lock) is True
        with pytest.raises(gd.Busy, match="deploy step is running"):
            with gd.agent_lock(lock):
                raise AssertionError("the body must not run")  # pragma: no cover
    finally:
        agent.stdin.close()
        agent.wait(timeout=10)
    assert gd.agent_busy(lock) is False


def test_a_missing_lock_guards_nothing(tmp_path):
    assert gd.agent_busy(tmp_path / "nope.lock") is False and gd.agent_busy(None) is False


def test_the_agent_cant_start_while_a_panel_action_holds_the_lock(tmp_path):
    lock = tmp_path / "agent.lock"
    lock.write_text("")
    lock.chmod(0o444)  # mounted read-only into the panel: flock needs no write access
    with gd.agent_lock(lock):
        with open(lock) as other:  # the agent's own open — another open file description
            with pytest.raises(OSError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(lock) as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released with the action


def test_an_unreadable_lock_fails_closed(tmp_path):
    lock = tmp_path / "agent.lock"
    lock.write_text("")
    lock.chmod(0)
    try:
        if not _readable_by_us(lock):
            with pytest.raises(gd.Busy, match="can't read"):
                with gd.agent_lock(lock):
                    pass  # pragma: no cover
    finally:
        lock.chmod(0o600)


def _readable_by_us(path) -> bool:
    try:
        open(path).close()
    except OSError:
        return False
    return True  # root reads anything: nothing to prove there
