"""One brain per data root (v2.2 code review #1) — the lock, the refusal, crash safety, who takes it."""

import ast
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.brainlock import LOCK_NAME, BrainLockHeld, acquire, guard_single_brain, release

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def root(tmp_path):
    yield tmp_path / "data"
    release(tmp_path / "data")


def test_a_second_brain_on_the_same_root_is_refused(root):
    acquire(root, "tui")
    with pytest.raises(BrainLockHeld) as exc:
        acquire(root, "server")  # a fresh open of the same lock file — a second "process"
    message = str(exc.value)
    assert "another brain already runs" in message and "tui" in message and f"pid {os.getpid()}" in message


def test_released_lock_can_be_taken_again(root):
    acquire(root, "tui")
    release(root)
    acquire(root, "server")  # no exception


def test_a_running_server_suggests_the_client(root):
    acquire(root, "server")
    with pytest.raises(BrainLockHeld, match=r"\./lumi-client"):
        acquire(root, "tui")


def test_different_roots_never_conflict(tmp_path):
    acquire(tmp_path / "prod", "tui")
    acquire(tmp_path / "dev", "tui")  # prod and dev side by side, as v2.1 intends
    release(tmp_path / "prod")
    release(tmp_path / "dev")


def test_a_crashed_holder_leaves_no_stale_lock(root):
    holder = subprocess.Popen([sys.executable, "-c", (
        f"import sys, time; sys.path.insert(0, {str(_REPO)!r});"
        f"from core.brainlock import acquire; acquire({str(root)!r}, 'server'); print('held', flush=True);"
        "time.sleep(60)")], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(BrainLockHeld, match="server"):
            acquire(root, "tui")
        holder.send_signal(signal.SIGKILL)  # a crash — no orderly release
        holder.wait(timeout=10)
        for _ in range(50):
            try:
                acquire(root, "tui")
                break
            except BrainLockHeld:
                time.sleep(0.1)
        else:
            pytest.fail("the lock outlived its crashed holder")
    finally:
        if holder.poll() is None:
            holder.kill()


def test_the_entry_point_helper_exits_readably(root):
    acquire(root, "server")
    with pytest.raises(SystemExit, match="another brain already runs"):
        guard_single_brain(SimpleNamespace(data_root=root), "tui")


def test_the_lock_file_names_its_holder(root):
    acquire(root, "server")
    content = (root / LOCK_NAME).read_text(encoding="utf-8")
    assert '"role": "server"' in content and f'"pid": {os.getpid()}' in content


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    return names | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}


def test_only_brains_take_the_lock():
    brains = [_REPO / "tui" / "__main__.py", _REPO / "server" / "__main__.py"]
    for path in brains:
        assert "core.brainlock" in _imports(path), f"{path} must hold the brain lock"
    peers = [*(_REPO / "telegram").glob("*.py"), *(_REPO / "voice").glob("*.py"), *(_REPO / "viewer").glob("*.py"),
             *(_REPO / "cli").glob("*.py"), _REPO / "tui" / "remote.py"]
    for path in peers:
        assert "core.brainlock" not in _imports(path), f"{path} is not a brain and must not take the lock"
