"""The Lumi image (v2.8, LUMI-238) — pins what the box relies on, without Docker.

One image runs the server and both Telegram daemons: built from the lock exactly, a non-root user that owns the
bind-mounted data root (uid 1000 on the box), ffmpeg for her voice replies, tzdata for her local day, a health check
on ``/v1/health``, the container flag + the baked build for the guards — and nothing secret or lived in the build
context. CI builds and smokes the real image (LUMI-239); this keeps the file honest between builds.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
IGNORED = [line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
           if line.strip() and not line.startswith("#")]
_SECRET = re.compile(r"(TOKEN|_KEY|SECRET|PASSWORD)\s*=|[A-Za-z0-9_-]{32,}", re.IGNORECASE)


def _instructions() -> list[str]:
    """The Dockerfile's instructions, continuation lines joined, comments dropped."""
    text = re.sub(r"\\\n\s*", " ", DOCKERFILE)
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_it_builds_from_the_lock_with_the_runtime_extras():
    runs = [i for i in _instructions() if i.startswith("RUN ")]
    sync = [r for r in runs if "uv sync" in r]
    assert len(sync) == 1 and "--frozen" in sync[0] and "--all-extras" in sync[0]
    assert "--no-extra dev" in sync[0]  # the test tools stay out of the image
    assert any(i.startswith("FROM python:3.13-slim") for i in _instructions())  # CI's Python


def test_it_has_ffmpeg_and_tzdata():
    apt = next(i for i in _instructions() if "apt-get install" in i)
    assert "ffmpeg" in apt and "tzdata" in apt


def test_it_runs_as_the_box_user_not_root():
    users = [i for i in _instructions() if i.startswith("USER ")]
    assert users and users[-1] == "USER 1000:1000"
    assert any("--uid 1000" in i for i in _instructions())


def test_it_carries_the_container_flag_the_build_and_the_data_root():
    env = " ".join(i for i in _instructions() if i.startswith("ENV "))
    assert "LUMI_CONTAINER=1" in env and "LUMI_BUILD=${LUMI_BUILD}" in env and "LUMI_HOME=/data" in env
    assert any(i.startswith("ARG LUMI_BUILD") for i in _instructions())


def test_it_checks_its_health_and_serves_by_default():
    health = next(i for i in _instructions() if i.startswith("HEALTHCHECK "))
    assert "/v1/health" in health and "LUMI_SERVER_PORT" in health
    assert _instructions()[-1] == 'CMD ["python", "-m", "server"]'


@pytest.mark.parametrize("path", [".git", ".env", ".env.*", ".lumi", ".venv", "tests"])
def test_nothing_secret_or_lived_enters_the_build_context(path):
    assert path in IGNORED


def test_no_secret_is_baked_in():
    for line in _instructions():
        assert not _SECRET.search(line), f"secret-shaped value in the Dockerfile: {line!r}"
