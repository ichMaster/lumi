"""Unit tests for the v2.1 env guard (LUMI-209) — the decision matrix, the marker, the git glue."""

from itertools import product
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.envguard import (
    BUILD_ENV,
    CONTAINER_ENV,
    MARKER_NAME,
    OVERRIDE_ENV,
    EnvGuardRefusal,
    GitState,
    check,
    enforce,
    git_state,
    guard_entry,
    in_container,
    read_marker,
    write_marker,
)

CLEAN = GitState(available=True, tag="v2.1.0", dirty=())
DIRTY = GitState(available=True, tag="v2.1.0", dirty=("core/agent.py",))
UNTAGGED = GitState(available=True, tag=None, dirty=())
NO_GIT = GitState(available=False)


def _expected_refusal(env, marker, git, allow_dirty=False) -> bool:
    """The spec (ROADMAP v2.1 / LUMI-209), restated: refuse iff one of the three rules fires."""
    dev_on_prod = marker == "prod" and env != "prod"
    prod_on_other = env == "prod" and marker not in (None, "prod")
    unreleased = env == "prod" and git.available and (git.tag is None or bool(git.dirty)) and not allow_dirty
    return dev_on_prod or prod_on_other or unreleased


@pytest.mark.parametrize(
    ("marker", "env", "git"),
    list(product([None, "prod", "dev"], [None, "prod", "dev"], [CLEAN, DIRTY, UNTAGGED, NO_GIT])),
)
def test_decision_matrix(marker, env, git):
    verdict = check(env, marker, git)
    assert verdict.ok is not _expected_refusal(env, marker, git)
    assert bool(verdict.reasons) is not verdict.ok  # a refusal always says why


def test_dev_tree_on_prod_root_names_the_mismatch():
    verdict = check(None, "prod", CLEAN)
    assert not verdict.ok
    assert "belongs to prod" in verdict.reasons[0] and "unset" in verdict.reasons[0]


def test_prod_on_dev_root_is_refused():
    verdict = check("prod", "dev", CLEAN)
    assert not verdict.ok and "marked 'dev'" in verdict.reasons[0]


def test_dirty_prod_names_the_files():
    verdict = check("prod", "prod", GitState(available=True, tag="v2.1.0", dirty=("a.py", "b.py")))
    assert not verdict.ok and "a.py, b.py" in verdict.reasons[0]


def test_many_dirty_files_are_truncated():
    files = tuple(f"f{i}.py" for i in range(15))
    reason = check("prod", "prod", GitState(available=True, tag="v1", dirty=files)).reasons[0]
    assert "f9.py" in reason and "f10.py" not in reason and reason.endswith("…")


def test_untagged_prod_is_refused():
    verdict = check("prod", None, UNTAGGED)
    assert not verdict.ok and "not at a release tag" in verdict.reasons[0]


def test_override_skips_only_the_git_check():
    assert check("prod", "prod", DIRTY, allow_dirty=True).ok
    assert check("prod", "prod", UNTAGGED, allow_dirty=True).ok
    assert not check("prod", "dev", CLEAN, allow_dirty=True).ok  # never the marker checks
    assert not check("dev", "prod", CLEAN, allow_dirty=True).ok


def test_override_is_always_warned_for_prod():
    notes = check("prod", "prod", CLEAN, allow_dirty=True).notes
    assert any(OVERRIDE_ENV in n for n in notes)


def test_no_git_passes_with_a_note():
    verdict = check("prod", "prod", NO_GIT)
    assert verdict.ok and any("no git" in n for n in verdict.notes)


# --- the marker -------------------------------------------------------------------------------------


def test_marker_round_trip(tmp_path):
    assert read_marker(tmp_path) is None
    assert write_marker(tmp_path, "prod") is True
    assert read_marker(tmp_path) == "prod"


def test_marker_is_never_rewritten(tmp_path):
    write_marker(tmp_path, "prod")
    assert write_marker(tmp_path, "dev") is False
    assert read_marker(tmp_path) == "prod"


def test_marker_creates_a_missing_root(tmp_path):
    root = tmp_path / "fresh" / "data"
    write_marker(root, "dev")
    assert (root / MARKER_NAME).read_text(encoding="utf-8") == "dev\n"


# --- the git glue (fake runner — no real git needed) ------------------------------------------------


class _FakeRun:
    def __init__(self, status=(0, ""), tag=(0, "v2.1.0\n"), raises=None):
        self.status, self.tag, self.raises, self.calls = status, tag, raises, []

    def __call__(self, argv, **_):
        self.calls.append(argv)
        if self.raises:
            raise self.raises
        code, out = self.status if "status" in argv else self.tag
        return SimpleNamespace(returncode=code, stdout=out)


def _repo(tmp_path) -> Path:
    (tmp_path / ".git").mkdir(parents=True)
    return tmp_path


def test_git_clean_at_tag(tmp_path):
    state = git_state(_repo(tmp_path), _FakeRun())
    assert state == GitState(available=True, tag="v2.1.0", dirty=())


def test_git_dirty_files_parsed(tmp_path):
    run = _FakeRun(status=(0, " M core/agent.py\nM  tui/app.py\n"))
    assert git_state(_repo(tmp_path), run).dirty == ("core/agent.py", "tui/app.py")


def test_git_not_at_a_tag(tmp_path):
    run = _FakeRun(tag=(128, ""))
    assert git_state(_repo(tmp_path), run).tag is None


def test_no_repo_is_unavailable_and_never_runs_git(tmp_path):
    run = _FakeRun()
    assert git_state(tmp_path, run) == GitState(available=False)
    assert run.calls == []


def test_missing_git_binary_is_unavailable(tmp_path):
    assert git_state(_repo(tmp_path), _FakeRun(raises=FileNotFoundError("git"))).available is False


def test_git_status_error_is_unavailable(tmp_path):
    assert git_state(_repo(tmp_path), _FakeRun(status=(128, ""))).available is False


# --- enforce / guard_entry -------------------------------------------------------------------------


def _cfg(root, env):
    return SimpleNamespace(data_root=root, env=env)


def test_enforce_marks_an_unmarked_root_on_first_env_run(tmp_path):
    enforce(_cfg(tmp_path, "dev"), repo=tmp_path, run=_FakeRun(), environ={})
    assert read_marker(tmp_path) == "dev"


def test_enforce_refusal_does_not_mark(tmp_path):
    repo = _repo(tmp_path / "repo")
    with pytest.raises(EnvGuardRefusal):
        enforce(_cfg(tmp_path / "data", "prod"), repo=repo, run=_FakeRun(tag=(128, "")), environ={})
    assert read_marker(tmp_path / "data") is None


def test_enforce_reads_the_override_from_the_environment(tmp_path):
    repo = _repo(tmp_path / "repo")
    dirty = _FakeRun(status=(0, " M x.py\n"))
    notes = enforce(_cfg(tmp_path / "data", "prod"), repo=repo, run=dirty, environ={OVERRIDE_ENV: "1"})
    assert any(OVERRIDE_ENV in n for n in notes)


def test_enforce_dev_never_asks_git(tmp_path):
    run = _FakeRun()
    enforce(_cfg(tmp_path, "dev"), repo=_repo(tmp_path / "repo"), run=run, environ={})
    assert run.calls == []


def test_guard_entry_turns_a_refusal_into_a_readable_exit(tmp_path):
    write_marker(tmp_path, "prod")
    with pytest.raises(SystemExit) as exc:
        guard_entry(_cfg(tmp_path, None))
    message = str(exc.value)
    assert "refusing to start" in message and str(tmp_path) in message and "belongs to prod" in message


# --- v2.8 (LUMI-238): the container mode — the image's build stands in for the checkout ------------------------


def test_in_container_needs_the_env_var_and_dockerenv(tmp_path):
    dockerenv = tmp_path / ".dockerenv"
    assert not in_container({CONTAINER_ENV: "1"}, dockerenv=dockerenv)  # set on a host by mistake → no
    dockerenv.touch()
    assert in_container({CONTAINER_ENV: "1"}, dockerenv=dockerenv)
    assert not in_container({}, dockerenv=dockerenv)  # a container that isn't the Lumi image


@pytest.mark.parametrize(("build", "ok"), [
    ("v2.8.0", True), ("v10.0.12", True),
    ("sha-b6a4ca6", False), ("edge", False), ("local", False), ("v2.8.0-rc1", False), (None, False),
])
def test_prod_in_a_container_runs_only_a_release_build(build, ok):
    verdict = check("prod", "prod", NO_GIT, container=True, build=build)
    assert verdict.ok is ok
    if not ok:
        assert "release tag (vA.B.C)" in verdict.reasons[0]
    assert not any("no git checkout" in n for n in verdict.notes)  # not a missing checkout — an image


def test_dev_in_a_container_runs_any_build():
    assert check("dev", "dev", NO_GIT, container=True, build="sha-b6a4ca6").ok


def test_the_override_and_the_markers_keep_their_meaning_in_a_container():
    overridden = check("prod", "prod", NO_GIT, allow_dirty=True, container=True, build="sha-b6a4ca6")
    assert overridden.ok and any(OVERRIDE_ENV in n for n in overridden.notes)
    assert not check("dev", "prod", NO_GIT, container=True, build="v2.8.0").ok  # dev on her memory: still no
    assert not check("prod", "dev", NO_GIT, container=True, build="v2.8.0").ok  # prod on test data: still no


def test_enforce_in_a_container_reads_the_build_and_never_runs_git(tmp_path):
    dockerenv = tmp_path / ".dockerenv"
    dockerenv.touch()
    run, repo = _FakeRun(), _repo(tmp_path / "repo")  # a checkout exists — the image's build still decides
    data = tmp_path / "data"
    with pytest.raises(EnvGuardRefusal, match="sha-b6a4ca6"):
        enforce(_cfg(data, "prod"), repo=repo, run=run, dockerenv=dockerenv,
                environ={CONTAINER_ENV: "1", BUILD_ENV: "sha-b6a4ca6"})
    enforce(_cfg(data, "prod"), repo=repo, run=run, dockerenv=dockerenv,
            environ={CONTAINER_ENV: "1", BUILD_ENV: "v2.8.0"})
    assert run.calls == [] and read_marker(data) == "prod"
