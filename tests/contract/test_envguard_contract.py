"""Contract: LUMI_ENV unset + an unmarked root → the guard has no effect at all (LUMI-209).

The v2.1 guard must be additive: an install that never sets LUMI_ENV starts exactly as before — no
marker written, no git subprocess, no notes, no refusal.
"""

from types import SimpleNamespace

from core.envguard import MARKER_NAME, enforce


def test_unset_env_and_unmarked_root_is_a_no_op(tmp_path):
    calls: list = []

    def run(argv, **_):  # pragma: no cover - must never be reached
        calls.append(argv)
        raise AssertionError("the guard must not call git when LUMI_ENV is unset")

    (tmp_path / ".git").mkdir()  # even inside a repo
    notes = enforce(SimpleNamespace(data_root=tmp_path, env=None), repo=tmp_path, run=run, environ={})
    assert notes == ()
    assert calls == []
    assert not (tmp_path / MARKER_NAME).exists()
