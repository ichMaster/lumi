"""The image workflow (v2.8, LUMI-239) — every push builds and smokes the image; only `main` and release tags push.

The box pulls what this workflow publishes and the deploy agent waits for its check runs, so it is part of the
pipeline: the tag rules the agent derives (`sha-<short>` from `main`, `vA.B.C` from a tag), the smoke before any
push, and the workflow's own GITHUB_TOKEN as the only secret.
"""

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "image.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
FLOW = yaml.safe_load(TEXT)
STEPS = FLOW["jobs"]["image"]["steps"]
_PUBLISH = "github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')"


def _step(name: str) -> dict:
    return next(s for s in STEPS if s.get("name") == name)


def test_it_builds_on_every_push_and_on_release_tags():
    triggers = FLOW.get("on") or FLOW.get(True)  # YAML 1.1 reads a bare `on` as true
    assert set(triggers) == {"push"}
    assert triggers["push"]["branches"] == ["**"] and triggers["push"]["tags"] == ["v*"]


def test_it_can_write_packages_and_nothing_else():
    assert FLOW["permissions"] == {"contents": "read", "packages": "write"}


def test_the_only_secret_is_the_workflows_own_token():
    assert set(re.findall(r"secrets\.(\w+)", TEXT)) == {"GITHUB_TOKEN"}


def test_the_tags_the_agent_derives():
    tags = next(s for s in STEPS if s.get("id") == "meta")["with"]["tags"]
    assert "type=raw,value=edge,enable=${{ github.ref == 'refs/heads/main' }}" in tags
    assert "type=sha,prefix=sha-,enable=${{ github.ref == 'refs/heads/main' }}" in tags  # sha-<7>, immutable
    assert "type=ref,event=tag" in tags
    assert "type=raw,value=latest,enable=${{ startsWith(github.ref, 'refs/tags/v') }}" in tags
    build = next(s for s in STEPS if s.get("id") == "build")["run"]
    assert "sha-${GITHUB_SHA::7}" in build and "$GITHUB_REF_NAME" in build  # the LUMI_BUILD the env guard reads


def test_the_smoke_runs_before_anything_is_pushed():
    names = [s.get("name") for s in STEPS]
    assert names.index("Container smoke") < names.index("Log in to GHCR") < names.index("Push")
    assert _step("Container smoke")["run"] == "scripts/container_smoke.sh lumi:smoke"
    assert _step("Build")["with"]["load"] is True and "push" not in _step("Build")["with"]


def test_only_main_and_release_tags_push():
    assert _step("Log in to GHCR")["if"] == _PUBLISH and _step("Push")["if"] == _PUBLISH
    assert _step("Push")["with"]["push"] is True


def test_both_builds_bake_the_same_build_ref():
    for name in ("Build", "Push"):
        args = _step(name)["with"]["build-args"]
        assert "LUMI_BUILD=${{ steps.build.outputs.ref }}" in args and "LUMI_REVISION=${{ github.sha }}" in args


def test_the_smoke_script_checks_health_and_a_clean_stop():
    script = (ROOT / "scripts" / "container_smoke.sh").read_text(encoding="utf-8")
    assert "/v1/health" in script and '"env": "dev"' in script and "VERSION" in script
    assert "docker stop" in script and "ExitCode" in script and '!= "0"' in script
    assert "--init" in script  # as compose runs it (init: true)
    for key in re.findall(r"-e (\w+_KEY)=(\S+)", script):
        assert key[1] == "dummy"  # never a real key


def test_the_compose_files_are_checked_before_the_build():
    """v2.8 (LUMI-240): both instances resolve with a tag and refuse without one — on Docker's own parser."""
    names = [s.get("name") for s in STEPS]
    assert _step("Compose config")["run"] == "scripts/compose_check.sh"
    assert names.index("Compose config") < names.index("Build")
