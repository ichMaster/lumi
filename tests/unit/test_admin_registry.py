"""v2.10 LUMI-256 — the panel's registry (admin.yaml) + its token (secrets.yaml): validated, refused readably."""

from __future__ import annotations

import pytest

from admin import registry as rg

TOKEN = "t" * 40
GOOD = """instances:
  prod: {root: /instances/prod, url: "http://192.168.1.197:8765", project: lumi-prod}
  dev: {root: /instances/dev, url: "http://192.168.1.197:8766/", project: lumi-dev}
server:
  host: 0.0.0.0
  port: 8767
"""


def _home(tmp_path, admin_yaml=GOOD, token=TOKEN):
    home = tmp_path / "admin"
    home.mkdir()
    (home / "admin.yaml").write_text(admin_yaml, encoding="utf-8")
    if token is not None:
        (home / "secrets.yaml").write_text(f"admin:\n  token: {token}\n", encoding="utf-8")
    return home


def test_a_good_registry_loads(tmp_path):
    cfg = rg.load(_home(tmp_path), environ={})
    assert [i.name for i in cfg.instances] == ["prod", "dev"]
    assert cfg.instance("dev").url == "http://192.168.1.197:8766" and cfg.instance("dev").project == "lumi-dev"
    assert str(cfg.instance("prod").data) == "/instances/prod/data"
    assert (cfg.host, cfg.port, cfg.token) == ("0.0.0.0", 8767, TOKEN)


@pytest.mark.parametrize("token, why", [(None, "missing"), ("short", "too short")])
def test_no_or_a_short_token_refuses(tmp_path, token, why):
    with pytest.raises(rg.AdminConfigError) as err:
        rg.load(_home(tmp_path, token=token), environ={})
    assert "admin.token" in str(err.value) and why in str(err.value)


@pytest.mark.parametrize("text, key", [
    ("instances: {}\n", "instances must name prod"),
    ("instances:\n  staging: {root: /x, url: 'http://x', project: p}\n", "instances.staging"),
    ("instances:\n  dev: {url: 'http://x', project: p}\n", "instances.dev.root"),
    ("instances:\n  dev: {root: /x, url: 'ftp://x', project: p}\n", "instances.dev.url"),
    ("instances:\n  dev: {root: /x, url: 'http://x'}\n", "instances.dev.project"),
    (GOOD.replace("port: 8767", "port: 99999"), "server.port"),
    ("instances: [unclosed\n", "not valid YAML"),
])
def test_an_invalid_registry_refuses_naming_the_key(tmp_path, text, key):
    with pytest.raises(rg.AdminConfigError) as err:
        rg.load(_home(tmp_path, admin_yaml=text), environ={})
    assert key in str(err.value)


def test_the_environment_wins(tmp_path):
    cfg = rg.load(_home(tmp_path, token=None),
                  environ={"LUMI_ADMIN_TOKEN": "e" * 40, "LUMI_ADMIN_HOST": "192.168.1.197", "LUMI_ADMIN_PORT": "9000"})
    assert (cfg.token, cfg.host, cfg.port) == ("e" * 40, "192.168.1.197", 9000)


# --- v5.1 (LUMI-342): the agents map ---------------------------------------------------------------------------------


def test_v5_1_without_an_agents_map_one_agent_lili_runs_every_instance(tmp_path):
    cfg = rg.load(_home(tmp_path), environ={})
    agents = cfg.agent_list()
    assert [(a.id, a.name, a.instances) for a in agents] == [("lili", "Lili", ("prod", "dev"))]
    assert cfg.agent("lili") == agents[0] and cfg.agent("ada") is None


def test_v5_1_an_agents_map_is_read(tmp_path):
    cfg = rg.load(_home(tmp_path, GOOD + "agents:\n  lili: {name: Lili, instances: [prod]}\n"
                        "  ada: {name: Ada, instances: [dev]}\n"), environ={})
    assert [(a.id, a.name, a.instances) for a in cfg.agent_list()] == [("lili", "Lili", ("prod",)),
                                                                        ("ada", "Ada", ("dev",))]


@pytest.mark.parametrize("block, why", [
    ("agents: []\n", "agents must be a mapping"),
    ("agents:\n  Lili: {name: Lili, instances: [prod]}\n", "an id is lowercase"),
    ("agents:\n  lili: {instances: [prod]}\n", "agents.lili.name"),
    ("agents:\n  lili: {name: Lili, instances: [staging]}\n", "staging is not a configured instance"),
    ("agents:\n  lili: {name: Lili, instances: [prod]}\n  ada: {name: Ada, instances: [prod]}\n",
     "prod already runs lili"),
])
def test_v5_1_a_bad_agents_map_refuses_naming_the_key(tmp_path, block, why):
    with pytest.raises(rg.AdminConfigError) as err:
        rg.load(_home(tmp_path, GOOD + block), environ={})
    assert why in str(err.value)
