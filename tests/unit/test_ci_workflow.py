"""The CI workflow (v2.7, LUMI-233) — the project's two gates on Linux, every push and pull request, no secret.

The v2.7 deploy agent deploys only a commit whose checks here are green, so this file is part of the pipeline: it
must keep running ruff + pytest, on push and pull_request, and must never need a key (the suite mocks every paid
service).
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_it_runs_on_every_push_and_pull_request():
    triggers = _workflow().get("on") or _workflow().get(True)  # YAML 1.1 reads a bare `on` as true
    assert set(triggers) == {"push", "pull_request"}
    assert not (triggers.get("push") or {}).get("branches")  # every branch, not just main


def test_it_runs_exactly_the_two_gates_on_the_exact_lock():
    steps = _workflow()["jobs"]["gates"]["steps"]
    commands = [s["run"] for s in steps if "run" in s]
    assert commands == ["uv sync --frozen --all-extras", "uv run ruff check .", "uv run pytest -q"]
    assert _workflow()["jobs"]["gates"]["runs-on"].startswith("ubuntu")


def test_it_needs_no_secret_and_can_only_read():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text and "_KEY" not in text and "TOKEN" not in text
    assert _workflow()["permissions"] == {"contents": "read"}
