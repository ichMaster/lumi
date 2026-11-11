"""v2.10 LUMI-257 — the admin panel's compose contract, read from the files (Docker's parser runs in
``scripts/compose_check.sh``): one service from the Lumi image, no image default, the Docker socket, published on
the LAN address only, nothing secret. v2.11 LUMI-264: the instance folders writable (settings + history) with each
``data/`` read-only on top, the deploy agent's lock read-only, nothing else writable but the panel's own folder."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ADMIN = Path(__file__).resolve().parents[2] / "deploy" / "admin"
SPEC = yaml.safe_load((ADMIN / "compose.yaml").read_text(encoding="utf-8"))
SVC = SPEC["services"]["admin"]
_SECRET = re.compile(r"(TOKEN|_KEY|SECRET|PASSWORD)\s*[=:]|[A-Za-z0-9_-]{32,}", re.IGNORECASE)


def test_one_service_from_the_lumi_image_with_no_default_tag():
    assert SPEC["name"] == "lumi-admin" and list(SPEC["services"]) == ["admin"]
    assert SVC["image"].startswith("ghcr.io/ichmaster/lumi:${LUMI_IMAGE_TAG:?")
    assert SVC["command"] == ["python", "-m", "admin"]


def _short(v: str) -> tuple[str, str, bool]:
    """``(source, target, read_only)`` of a short-syntax volume (a ``${…:?…}`` source may hold a colon)."""
    ro = v.endswith(":ro")
    body = v[:-3] if ro else v
    source, target = body.rsplit(":", 1)
    return source, target, ro


def test_the_data_roots_are_read_only_under_the_writable_instance_folders():
    short = [_short(v) for v in SVC["volumes"] if isinstance(v, str)]
    by_target = {t: (s, ro) for s, t, ro in short}
    for name in ("prod", "dev"):
        folder, data = by_target[f"/instances/{name}"], by_target[f"/instances/{name}/data"]
        assert folder[1] is False and folder[0].endswith(f"/{name}")  # settings.yaml / secrets.yaml / history
        assert data[1] is True and data[0] == "${LUMI_HOME_DIR}/" + f"{name}/data"  # her memory: never written
        order = [t for _, t, _ in short]
        assert order.index(f"/instances/{name}") < order.index(f"/instances/{name}/data")  # data/ mounted on top
    writable = sorted(t for _, t, ro in short if not ro)
    assert writable == ["/admin", "/instances/dev", "/instances/prod", "/var/run/docker.sock"]
    assert SVC["environment"]["LUMI_ADMIN_HOME"] == "/admin"


def test_the_deploy_agents_lock_is_mounted_read_only_and_named():
    [lock] = [v for v in SVC["volumes"] if isinstance(v, dict)]
    assert lock == {"type": "bind", "source": "${LUMI_HOME_DIR}/deploy/agent.lock", "target": "/deploy/agent.lock",
                    "read_only": True, "bind": {"create_host_path": False}}
    assert SVC["environment"]["LUMI_AGENT_LOCK"] == "/deploy/agent.lock"


def test_published_on_the_lan_address_only():
    (port,) = SVC["ports"]
    assert port.startswith("${LUMI_PUBLISH_HOST") and port.endswith(":8767:8767")
    params = dict(line.split("=", 1) for line in (ADMIN / "admin.env").read_text().splitlines()
                  if line and not line.startswith("#"))
    assert ipaddress.ip_address(params["LUMI_PUBLISH_HOST"]).is_private
    assert params["DOCKER_GID"].isdigit()


def test_it_runs_as_the_box_user_with_the_docker_group_and_its_own_healthcheck():
    assert SVC["user"] == "1000:1000" and SVC["group_add"] == ["${DOCKER_GID:-999}"]
    assert SVC["init"] is True and SVC["restart"] == "unless-stopped"
    assert "/healthz" in " ".join(SVC["healthcheck"]["test"])  # not the image's /v1/health
    assert SVC["logging"]["options"]["max-file"] == "3"


@pytest.mark.parametrize("name", ["compose.yaml", "admin.env", "admin.example.yaml"])
def test_no_secret_in_the_admin_files(name):
    for line in (ADMIN / name).read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert not _SECRET.search(line), f"secret-shaped value in {name}: {line!r}"


def test_the_example_registry_loads(tmp_path):
    from admin.registry import load

    home = tmp_path / "admin"
    home.mkdir()
    (home / "admin.yaml").write_text((ADMIN / "admin.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (home / "secrets.yaml").write_text("admin:\n  token: " + "x" * 40 + "\n", encoding="utf-8")
    cfg = load(home, environ={})
    assert [i.name for i in cfg.instances] == ["prod", "dev"] and cfg.instance("prod").project == "lumi-prod"
