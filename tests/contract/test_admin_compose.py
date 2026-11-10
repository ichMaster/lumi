"""v2.10 LUMI-257 — the admin panel's compose contract, read from the files (Docker's parser runs in
``scripts/compose_check.sh``): one service from the Lumi image, no image default, the instance folders read-only,
its own folder the only writable one, the Docker socket, published on the LAN address only, nothing secret."""

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


def test_the_instance_folders_are_read_only_and_the_admin_folder_is_the_only_writable_one():
    vols = SVC["volumes"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in vols
    instance = [v for v in vols if "/instances/" in v]
    assert sorted(v.rsplit(":", 2)[1] for v in instance) == ["/instances/dev", "/instances/prod"]
    assert all(v.endswith(":ro") for v in instance)
    writable = [v for v in vols if not v.endswith(":ro") and "docker.sock" not in v]
    assert writable == ["${LUMI_HOME_DIR}/admin:/admin"]
    assert SVC["environment"]["LUMI_ADMIN_HOME"] == "/admin"


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
