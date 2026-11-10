"""The compose contract (v2.8, LUMI-240) — what every instance's containers must be, read from the files.

One file, two parameter sets: three services from one image, her data root bind-mounted at /data (the file bus
needs one filesystem), settings (v2.9) only through the instance's ``settings.yaml`` + ``secrets.yaml`` mounted
read-only at /settings (and the pre-v2.9 ``.env.migrated`` at /app/.env — the way back), published **only** on the
box's LAN address, no image default, and nothing secret in any of the three files. Docker's own parser checks them in CI
(``scripts/compose_check.sh``); this pins the rules without Docker.
"""

import ipaddress
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "compose"
TEXT = (COMPOSE / "compose.yaml").read_text(encoding="utf-8")
SPEC = yaml.safe_load(TEXT)
SERVICES = SPEC["services"]
_SECRET = re.compile(r"(TOKEN|_KEY|SECRET|PASSWORD)\s*[=:]|[A-Za-z0-9_-]{32,}", re.IGNORECASE)


def _params(instance: str) -> dict[str, str]:
    lines = (COMPOSE / f"{instance}.env").read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


def test_one_image_three_services():
    assert set(SERVICES) == {"server", "telegram-inbound", "telegram-outbound"}
    assert len({s["image"] for s in SERVICES.values()}) == 1
    assert SERVICES["telegram-inbound"]["command"] == ["python", "-m", "telegram.inbound"]
    assert SERVICES["telegram-outbound"]["command"] == ["python", "-m", "telegram.outbound"]
    assert "command" not in SERVICES["server"]  # the image's default: python -m server


def test_the_image_tag_has_no_default():
    image = SERVICES["server"]["image"]
    assert image.startswith("ghcr.io/ichmaster/lumi:${LUMI_IMAGE_TAG:?")  # `:?` — required, never `:-edge`


@pytest.mark.parametrize("name", ["server", "telegram-inbound", "telegram-outbound"])
def test_every_service_shares_the_data_root_and_the_settings(name):
    svc = SERVICES[name]
    data, *files = svc["volumes"]
    assert data == "${LUMI_INSTANCE_DIR:?pass --env-file deploy/compose/prod.env or dev.env}/data:/data"
    # v2.9 (LUMI-249): the settings files, read-only, never auto-created as directories
    assert [(f["source"], f["target"]) for f in files] == [
        ("${LUMI_INSTANCE_DIR}/settings.yaml", "/settings/settings.yaml"),
        ("${LUMI_INSTANCE_DIR}/secrets.yaml", "/settings/secrets.yaml"),
        ("${LUMI_INSTANCE_DIR}/.env.migrated", "/app/.env"),  # only a pre-v2.9 image reads it (the way back)
    ]
    assert all(f["type"] == "bind" and f["read_only"] is True and f["bind"]["create_host_path"] is False
               for f in files)
    assert "env_file" not in svc  # v2.9: no .env layer in a container
    env = svc["environment"]
    assert env["LUMI_HOME"] == "/data" and env["LUMI_SERVER_HOST"] == "0.0.0.0"
    assert env["LUMI_SETTINGS_DIR"] == "/settings"
    assert "TZ" in env  # her local day
    assert svc["user"] == "1000:1000" and svc["init"] is True and svc["restart"] == "unless-stopped"
    assert int(svc["stop_grace_period"].rstrip("s")) >= 300  # the session close makes model calls


def test_only_the_server_publishes_and_only_on_the_lan_address():
    assert "ports" not in SERVICES["telegram-inbound"] and "ports" not in SERVICES["telegram-outbound"]
    (port,) = SERVICES["server"]["ports"]
    host_part = port.split("}:")[0]
    assert host_part.startswith("${LUMI_PUBLISH_HOST")  # never a bare port (= every host interface)
    for instance in ("prod", "dev"):
        ip = ipaddress.ip_address(_params(instance)["LUMI_PUBLISH_HOST"])
        assert ip.is_private and not ip.is_loopback and not ip.is_unspecified


def test_the_daemons_skip_the_servers_healthcheck():
    assert SERVICES["telegram-inbound"]["healthcheck"] == {"disable": True}
    assert SERVICES["telegram-outbound"]["healthcheck"] == {"disable": True}
    assert "healthcheck" not in SERVICES["server"]  # the image's /v1/health check


def test_the_two_instances_never_collide():
    prod, dev = _params("prod"), _params("dev")
    assert (prod["LUMI_INSTANCE"], dev["LUMI_INSTANCE"]) == ("prod", "dev")
    assert prod["LUMI_SERVER_PORT"] != dev["LUMI_SERVER_PORT"]
    assert prod["LUMI_INSTANCE_DIR"] != dev["LUMI_INSTANCE_DIR"]
    assert all(p["LUMI_INSTANCE_DIR"].startswith("/") for p in (prod, dev))  # compose needs absolute paths


@pytest.mark.parametrize("name", ["compose.yaml", "prod.env", "dev.env"])
def test_no_secret_in_the_compose_files(name):
    for line in (COMPOSE / name).read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert not _SECRET.search(line), f"secret-shaped value in {name}: {line!r}"


@pytest.mark.parametrize("name", ["server", "telegram-inbound", "telegram-outbound"])
def test_dockers_copy_of_the_logs_is_bounded(name):
    """v2.8 review #4: json-file without a limit grows for ever; her own logs live in the data root."""
    logging = SERVICES[name]["logging"]
    assert logging["driver"] == "json-file"
    assert logging["options"]["max-size"] == "10m" and int(logging["options"]["max-file"]) >= 1
