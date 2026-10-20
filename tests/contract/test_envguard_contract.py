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


def test_the_container_flag_alone_never_relaxes_the_guard_on_a_host(tmp_path):
    """v2.8: LUMI_CONTAINER=1 set on a host (no /.dockerenv) — prod still needs a clean checkout at a tag."""
    calls: list = []

    def run(argv, **_):
        calls.append(argv)

        class R:
            returncode = 0 if argv[3] == "status" else 128
            stdout = ""

        return R()

    (tmp_path / "repo" / ".git").mkdir(parents=True)
    from core.envguard import EnvGuardRefusal

    try:
        enforce(SimpleNamespace(data_root=tmp_path / "data", env="prod"), repo=tmp_path / "repo", run=run,
                environ={"LUMI_CONTAINER": "1", "LUMI_BUILD": "v2.8.0"}, dockerenv=tmp_path / "no-dockerenv")
    except EnvGuardRefusal as exc:
        assert "HEAD is not at a release tag" in str(exc)
    else:  # pragma: no cover - the guard must refuse
        raise AssertionError("an untagged host checkout ran as prod")
    assert calls  # git was asked: the host rules applied
