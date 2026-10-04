"""Unit tests for the prod update script's pure helpers (LUMI-210) — no real git/uv/network."""

from datetime import datetime
from pathlib import Path

import pytest

from scripts.prod_update import (
    backup_name,
    env_keys,
    main,
    new_unset_keys,
    pids_working_in,
    prod_processes,
    set_keys,
    valid_tag,
)


@pytest.mark.parametrize("tag", ["v2.1.0", "v1.6.3", "v10.20.30", "v2.1.0-rc1", "v2.1.0-rc.2"])
def test_valid_tags(tag):
    assert valid_tag(tag)


@pytest.mark.parametrize("tag", ["", "2.1.0", "v2.1", "main", "v2.1.0;rm -rf /", "v2.1.0 ", "latest"])
def test_invalid_tags(tag):
    assert not valid_tag(tag)


def test_backup_name_is_sortable_and_labelled():
    assert backup_name(datetime(2026, 10, 4, 21, 5), "v2.1.0-rc1") == "data-20261004-2105-v2.1.0-rc1.tar.gz"
    assert backup_name(datetime(2026, 10, 4, 21, 5), None) == "data-20261004-2105-untagged.tar.gz"


EXAMPLE_OLD = """# comment line
ANTHROPIC_API_KEY=
# LUMI_MODEL=claude-haiku   # optional
# LUMI_STREAM=off
"""
EXAMPLE_NEW = EXAMPLE_OLD + """# LUMI_HOME=~/lumi/prod/data
# LUMI_ENV=dev
"""


def test_env_keys_reads_set_and_commented_names():
    assert env_keys(EXAMPLE_OLD) == {"ANTHROPIC_API_KEY", "LUMI_MODEL", "LUMI_STREAM"}


def test_set_keys_ignores_commented_names():
    assert set_keys("A=1\n# B=2\n  C = 3\n") == {"A", "C"}


def test_only_keys_new_in_the_release_and_unset_in_prod_are_flagged():
    prod_env = "ANTHROPIC_API_KEY=sk\nLUMI_ENV=prod\n"
    assert new_unset_keys(EXAMPLE_OLD, EXAMPLE_NEW, prod_env) == ["LUMI_HOME"]  # LUMI_ENV already set


def test_no_new_keys_no_warning():
    assert new_unset_keys(EXAMPLE_NEW, EXAMPLE_NEW, "") == []


def test_prod_processes_match_the_prod_venv_only(tmp_path):
    home = tmp_path / "prod"
    lines = [
        f"  101 {home}/app/.venv/bin/python -m tui",
        f"  102 {home}/app/.venv/bin/python -m telegram.inbound",
        "  103 /Users/me/development/lumi/.venv/bin/python -m tui",  # the DEV instance — not prod
        "  104 uv run --all-extras python -m tui",
    ]
    found = prod_processes(lines, home)
    assert len(found) == 2 and all("101" in f or "102" in f for f in found)


def test_bad_tag_refused_before_anything_runs(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--tag", "main", "--home", str(tmp_path)])
    assert "not a release tag" in str(exc.value)


def test_missing_checkout_refused(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--tag", "v2.1.0", "--home", str(tmp_path)])
    assert "not a git checkout" in str(exc.value)


def test_dry_run_changes_nothing(tmp_path, capsys, monkeypatch):
    home = tmp_path / "prod"
    (home / "app" / ".git").mkdir(parents=True)
    (home / "data").mkdir()

    class _R:
        def __init__(self, out=""):
            self.stdout, self.returncode = out, 0

    calls: list = []

    def fake_run(argv, **_):
        calls.append(argv)
        return _R("")  # no processes; describe → empty (untagged)

    monkeypatch.setattr("scripts.prod_update.subprocess.run", fake_run)
    main(["--tag", "v2.1.0", "--home", str(home), "--dry-run"])
    out = capsys.readouterr().out
    assert "dry run" in out and "checkout v2.1.0" in out
    assert not (home / "backups").exists()  # no backup taken
    assert all("checkout" not in a and "fetch" not in a and "sync" not in a for a in calls)
    assert sorted(p.name for p in Path(home).iterdir()) == ["app", "data"]


def test_pids_working_in_parses_lsof_cwd_output(tmp_path):
    app = tmp_path / "prod" / "app"
    out = (
        f"p101\nfcwd\nn{app}\n"
        f"p102\nfcwd\nn{app}/core\n"
        "p103\nfcwd\nn/Users/me/development/lumi\n"
        f"p104\nfcwd\nn{app}-old\n"  # a sibling dir with the same prefix — not inside app/
    )
    assert pids_working_in(out, app) == {"101", "102"}


def test_uv_run_children_are_caught_by_cwd(tmp_path):
    # Regression (found migrating prod, 2026-10-04): under `uv run` the python child shows in ps as the
    # resolved framework interpreter, NOT <home>/app/.venv/bin/python — matching argv alone missed a
    # running prod and would have backed up a live WAL store.
    home = tmp_path / "prod"
    lines = [
        "  201 uv run python -m tui",
        "  202 /opt/homebrew/Cellar/python@3.14/.../Python.app/Contents/MacOS/Python -m tui",
        "  203 /opt/homebrew/Cellar/python@3.14/.../Python.app/Contents/MacOS/Python -m tui",  # the dev one
    ]
    found = prod_processes(lines, home, cwd_pids={"201", "202"})
    assert [f.split()[0] for f in found] == ["201", "202"]
