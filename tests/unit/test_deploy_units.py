"""The box's systemd units (v2.6, LUMI-230) — templated per instance, restartable, secret-free.

The units are copied to the Linux box by hand (docs/LINUX_DEPLOY.md); this pins what the guide relies on:
each one is an ``%i``-templated user unit under ``~/lumi/%i/``, runs a repo script that exists, restarts on a
failure but never loops on a configuration refusal (exit 78), gives a stop time to close her session, and
carries no secret. No systemd is needed — the files are parsed as INI.
"""

import configparser
import os
import re
from pathlib import Path

import pytest

from server.__main__ import EX_CONFIG

ROOT = Path(__file__).resolve().parents[2]
UNITS = ROOT / "deploy" / "systemd"
NAMES = ("lumi-server@.service", "lumi-telegram-inbound@.service", "lumi-telegram-outbound@.service")
_SECRET = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD)\s*=|[A-Za-z0-9_-]{32,}", re.IGNORECASE)


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # systemd keys are case-sensitive
    parser.read_string((UNITS / name).read_text(encoding="utf-8"))
    return parser


def test_the_unit_set_is_exactly_the_three():
    assert sorted(p.name for p in UNITS.glob("*.service")) == sorted(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_each_unit_is_templated_and_runs_a_repo_script(name):
    unit = _unit(name)
    assert {"Unit", "Service", "Install"} <= set(unit.sections())
    svc = unit["Service"]
    assert svc["WorkingDirectory"] == "%h/lumi/%i/app"
    script = svc["ExecStart"].removeprefix("%h/lumi/%i/app/")
    assert "/" not in script and (ROOT / script).is_file() and os.access(ROOT / script, os.X_OK)
    assert svc["Restart"] == "on-failure" and int(svc["RestartSec"]) > 0
    assert unit["Unit"]["StartLimitIntervalSec"] == "0"  # keeps retrying while the box's address comes up


@pytest.mark.parametrize("name", NAMES)
def test_no_unit_carries_a_secret_or_a_machine_path(name):
    text = (UNITS / name).read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert not _SECRET.search(line), f"secret-shaped value in {name}: {line!r}"
        assert "/home/" not in line and "/Users/" not in line, f"a machine path in {name}: {line!r}"


def test_the_server_never_loops_on_a_refusal_and_has_time_to_close_her_session():
    svc = _unit("lumi-server@.service")["Service"]
    assert str(EX_CONFIG) in svc["RestartPreventExitStatus"].split()
    assert int(svc["TimeoutStopSec"]) >= 120  # the session close makes model calls
    assert _unit("lumi-server@.service")["Install"]["WantedBy"] == "default.target"


@pytest.mark.parametrize("name", ["lumi-telegram-inbound@.service", "lumi-telegram-outbound@.service"])
def test_the_bridge_daemons_live_and_stop_with_their_server(name):
    unit = _unit(name)
    assert unit["Unit"]["PartOf"] == "lumi-server@%i.service"
    assert unit["Unit"]["After"] == "lumi-server@%i.service"
    assert unit["Install"]["WantedBy"] == "lumi-server@%i.service"
