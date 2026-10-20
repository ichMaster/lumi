"""The pull-based deploy agent (v2.7, LUMI-235; images v2.8, LUMI-241) — a green ``main`` reaches dev by itself,
a release reaches prod only after the owner's ``confirm`` (her data archived first), a failed smoke goes back, a
rollback is one command, the v2.6 units are retired by the first container deploy (and are the way back from it),
and a deploy moves code only (never data or settings).

The decisions are pure and tested over fixtures; the runner is tested over a fake world (a fake command runner
standing in for git / docker compose / docker inspect / systemctl, a fake HTTP standing in for GitHub and the
instance's server) — no network, no Docker, no systemd, no paid API.
"""

import fcntl
import hashlib
import signal
import time
from pathlib import Path

import pytest

from scripts import deploy_agent as da
from scripts.deploy_agent import (
    Agent,
    ci_verdict,
    dev_action,
    errors_since,
    image_tag,
    parse_env,
    prod_action,
    release_tags,
    smoke_verdict,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = 1_800_000_000.0
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW))
_PORT = {"dev": "8766", "prod": "8765"}
_INST_BY_PORT = {v: k for k, v in _PORT.items()}


# --- pure decisions ---------------------------------------------------------------------------------------------
def test_parse_env_reads_the_instance_settings():
    env = parse_env("# a comment\n\nLUMI_SERVER_HOST=192.168.1.197\nLUMI_SERVER_PORT='8766'  # dev\n"
                    'LUMI_SERVER_TOKEN="t0k"\nnot a line\n')
    assert env == {"LUMI_SERVER_HOST": "192.168.1.197", "LUMI_SERVER_PORT": "8766", "LUMI_SERVER_TOKEN": "t0k"}


@pytest.mark.parametrize(("runs", "verdict"), [
    (None, "none"),
    ([], "none"),
    ([{"status": "completed", "conclusion": "success"}], "green"),
    ([{"status": "completed", "conclusion": "success"}, {"status": "completed", "conclusion": "skipped"}], "green"),
    ([{"status": "completed", "conclusion": "success"}, {"status": "in_progress", "conclusion": None}], "pending"),
    ([{"status": "completed", "conclusion": "failure"}], "red"),
    ([{"status": "completed", "conclusion": "cancelled"}], "red"),
])
def test_ci_verdict(runs, verdict):
    assert ci_verdict({"check_runs": runs} if runs is not None else None) == verdict


def _run(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion if status == "completed" else None}


@pytest.mark.parametrize(("runs", "verdict"), [
    # merged into main, then v2-dev pushed again: the commit's v2-dev runs cancelled, its main runs passed
    ([_run("gates", conclusion="cancelled"), _run("image", conclusion="cancelled"), _run("gates"), _run("image")],
     "green"),
    ([_run("gates"), _run("image", conclusion="cancelled")], "red"),  # the image never finished anywhere
    ([_run("gates", conclusion="failure"), _run("gates")], "red"),  # a real failure on any ref stays red
    ([_run("gates", conclusion="cancelled"), _run("gates", status="in_progress")], "pending"),
    ([_run("gates"), _run("image", status="queued"), _run("image", conclusion="cancelled")], "pending"),
    ([_run("gates", conclusion="timed_out"), _run("image", status="in_progress")], "red"),
])
def test_a_cancelled_twin_never_marks_a_commit_red(runs, verdict):
    """Review #1: each check is judged by name; a cancelled run of one ref doesn't outvote a passed one."""
    assert ci_verdict({"check_runs": runs}) == verdict


def test_a_ref_runs_as_cis_image_tag():
    assert image_tag("v2.8.0") == "v2.8.0"  # a release is its own tag
    assert image_tag("b6a4ca692fe299014e0db805394ccb72f30a3e49") == "sha-b6a4ca6"  # metadata-action's type=sha
    assert image_tag("v2.8.0-rc1") == "sha-v2.8.0-"  # not a release: never mistaken for one


def test_release_tags_are_versions_in_version_order():
    tags = ["v2.9.0", "v2.10.0", "v2.6.0", "v2.7.0-rc1", "latest", "v2.6.1"]
    assert release_tags(tags) == ["v2.6.0", "v2.6.1", "v2.9.0", "v2.10.0"]


def test_dev_deploys_only_a_new_green_commit_that_is_ahead():
    state = {"dev": {"current": "m1", "bad": ["m9"]}}
    assert dev_action(state, "m2", "green", ahead=True) == "m2"
    assert dev_action(state, "m2", "red", ahead=True) is None  # a red CI is never deployed
    assert dev_action(state, "m2", "pending", ahead=True) is None
    assert dev_action(state, "m2", "none", ahead=True) is None
    assert dev_action(state, "m2", "green", ahead=False) is None  # main behind what dev runs (before the merge)
    assert dev_action(state, "m1", "green", ahead=True) is None  # already live
    assert dev_action(state, "m9", "green", ahead=True) is None  # rolled back once — not again
    assert dev_action(state, "", "green", ahead=True) is None


def test_prod_deploys_only_the_exact_confirmed_newest_tag():
    tags = ["v2.6.0", "v2.7.0"]
    assert prod_action({"prod": {"current": "v2.6.0"}}, tags) == ("wait", "v2.7.0")
    assert prod_action({"prod": {"current": "v2.6.0", "confirmed": "v2.6.1"}}, tags) == ("wait", "v2.7.0")
    assert prod_action({"prod": {"current": "v2.6.0", "confirmed": "v2.7.0"}}, tags) == ("deploy", "v2.7.0")
    assert prod_action({"prod": {"current": "v2.7.0", "confirmed": "v2.7.0"}}, tags) == ("none", None)
    assert prod_action({"prod": {"current": "v2.6.0", "confirmed": "v2.7.0", "bad": ["v2.7.0"]}}, tags) \
        == ("none", None)
    assert prod_action({"prod": {"current": "v2.8.0", "confirmed": "v2.7.0"}}, tags) == ("none", None)  # never older
    assert prod_action({"prod": {}}, []) == ("none", None)


def test_errors_since_counts_only_new_error_lines():
    log = ("2026-10-20 00:58:05,087 ERROR lumi.server: old\n"
           f"{STAMP},100 WARNING lumi.llm: Gemini returned no answer\n"
           f"{STAMP},200 ERROR lumi.agent: boom\n"
           "  a traceback line ERROR without a stamp\n")
    assert errors_since(log, STAMP) == 1
    assert errors_since("", STAMP) == 0


def _health(inst="dev", version="2.7.0"):
    return {"ok": True, "env": inst, "version": version}


def test_smoke_verdict_is_green_only_when_everything_holds():
    assert smoke_verdict("dev", _health(), "2.7.0", 200, [], 0) == []
    assert smoke_verdict("dev", None, "2.7.0", 0, [], 0) == ["the server does not answer /v1/health"]
    assert "env='prod'" in smoke_verdict("dev", _health("prod"), "2.7.0", 200, [], 0)[0]
    assert "'2.6.0'" in smoke_verdict("dev", _health(version="2.6.0"), "2.7.0", 200, [], 0)[0]
    assert smoke_verdict("dev", _health(), "2.7.0", 401, [], 0) == ["/v1/state answered 401"]
    assert smoke_verdict("dev", _health(), "2.7.0", 200, ["lumi-server@dev (restarted)"], 0) \
        == ["not up the whole minute: lumi-server@dev (restarted)"]
    assert smoke_verdict("dev", _health(), "2.7.0", 200, [], 2) == ["2 new ERROR line(s) in lumi.log since the restart"]


# --- a fake world: git / docker compose / docker inspect / systemctl / GitHub / the servers ----------------------
class World:
    """What the box would do, in memory. Each instance runs either as its v2.6 units (on its checkout's ref) or as
    compose containers (on an image tag); the server answers for whatever runs. ``broken`` refs never come up,
    ``late_crash`` ones crash and are restarted during the stable minute, ``images`` are what GHCR can serve."""

    def __init__(self, home: Path, *, main="m2", dev_head="m1", prod_tag="v2.7.0", tags=("v2.7.0",),
                 ci="success", ancestors=("m1",), broken=(), versions=None, tar_fails=False, late_crash=(),
                 images=("sha-m2", "sha-m3", "v2.8.0", "v2.9.0")) -> None:
        self.home, self.main, self.tags, self.ci, self.tar_fails = home, main, list(tags), ci, tar_fails
        self.ancestors, self.broken, self.late_crash = set(ancestors), set(broken), set(late_crash)
        self.images = set(images)  # no image for a commit or tag older than the image workflow (m1, v2.7.0)
        self.versions = versions or {"m1": "2.7.0", "m2": "2.8.0", "m3": "2.8.1"}
        self.checkout = {"dev": dev_head, "prod": prod_tag}  # the v2.6 units run this; the agent never moves it
        self.enabled = {"dev": True, "prod": True}  # the v2.6 units
        self.runs: dict[str, tuple[str, str] | None] = {"dev": ("units", dev_head), "prod": ("units", prod_tag)}
        self.server_up = {"dev": True, "prod": True}
        self.containers: dict[str, dict[str, list]] = {"dev": {}, "prod": {}}  # service → [id, restarts]
        self._ids = 0
        self.invocations: dict[str, int] = {}
        self.cmds: list[list[str]] = []
        self.urls: list[tuple[str, dict]] = []
        for inst in ("dev", "prod"):
            (home / inst / "app").mkdir(parents=True)
            (home / inst / "data").mkdir()
            (home / inst / "data" / "lumi.log").write_text("2026-10-20 00:00:00,000 INFO lumi: started\n")
            (home / inst / "data" / "store.json").write_text('{"messages": ["her memory"]}')
            (home / inst / ".env").write_text(f"LUMI_ENV={inst}\nLUMI_SERVER_HOST=192.168.1.197\n"
                                              f"LUMI_SERVER_PORT={_PORT[inst]}\nLUMI_SERVER_TOKEN=tok-{inst}\n")

    # the commands the agent runs
    def run(self, cmd, cwd=None):
        cmd = list(cmd)
        self.cmds.append(cmd)
        if cmd[0] == "env" and cmd[2:4] == ["docker", "compose"]:
            return self._compose(cmd[1].split("=", 1)[1], cmd)
        if cmd[:2] == ["docker", "inspect"]:
            return self._inspect(cmd[4:])
        if cmd[0] == "git":
            return self._git(Path(cmd[2]).parent.name, cmd[3:])
        if cmd[0] == "tar":
            assert cmd[1] == "-czf" and cmd[3:] == ["-C", str(self.home / "prod"), "data"]
            assert not self.server_up["prod"], "her data archived while the server still writes it"
            Path(cmd[2]).write_bytes(b"half" if self.tar_fails else b"an archive")
            return (2, "tar: write error: No space left on device") if self.tar_fails else (0, "")
        if cmd[:2] == ["systemctl", "--user"]:
            return self._systemctl(cmd[2:])
        raise AssertionError(f"unexpected command {cmd}")

    def _git(self, inst, args):
        if args[0] == "fetch":
            return 0, ""
        if args[:2] == ["rev-parse", "HEAD"]:
            return 0, self.checkout[inst]
        if args[:2] == ["rev-parse", "origin/main"]:
            return 0, self.main
        if args[0] == "describe":
            return (0, self.checkout[inst]) if self.checkout[inst].startswith("v") else (128, "no tag")
        if args[0] == "merge-base":
            return (0, "") if args[2] in self.ancestors else (1, "")
        if args[0] == "show":
            sha = args[1].split(":")[0]
            return (0, self.versions[sha] + "\n") if sha in self.versions else (128, "")
        if args[0] == "tag":
            return 0, "\n".join(self.tags)
        raise AssertionError(f"the agent must not move a checkout: git {args}")

    def _compose(self, tag, cmd):
        inst = Path(cmd[cmd.index("--env-file") + 1]).stem
        verb = cmd[cmd.index("-f") + 2:]
        if verb[0] == "pull":
            return (0, "") if tag in self.images else (1, f"manifest for ghcr.io/ichmaster/lumi:{tag} not found")
        if verb[:2] == ["up", "-d"]:
            if tag not in self.images:
                return 1, f"pull access denied for ghcr.io/ichmaster/lumi:{tag}"
            if self.enabled[inst] or self.runs[inst] and self.runs[inst][0] == "units" and self.server_up[inst]:
                return 1, f"Bind for 192.168.1.197:{_PORT[inst]} failed: port is already allocated"
            self.runs[inst], self.server_up[inst] = ("containers", tag), True
            self.containers[inst] = {s: [self._new_id(), 0] for s in da.SERVICES}
            return 0, ""
        if verb[0] == "down":
            self.containers[inst], self.runs[inst], self.server_up[inst] = {}, None, False
            return 0, ""
        if verb[:2] in (["stop", "server"], ["start", "server"]):
            self.server_up[inst] = verb[0] == "start"
            return 0, ""
        if verb[:3] == ["ps", "-a", "-q"]:
            return 0, "\n".join(c[0] for c in self.containers[inst].values())
        raise AssertionError(f"unexpected compose {verb}")

    def _inspect(self, ids):
        lines = [f"{s} {c[0]} running {c[1]}" for inst in self.containers.values() for s, c in inst.items()
                 if c[0] in ids]
        return 0, "\n".join(lines)

    def _systemctl(self, args):
        verb, units = args[0], [a for a in args[1:] if "@" in a]
        if verb in ("disable", "enable"):
            assert "--now" in args
            for unit in units:
                inst = unit.split("@")[1]
                self.enabled[inst] = verb == "enable"
                if verb == "disable":
                    self.server_up[inst], self.runs[inst] = False, None
                else:
                    assert not self.containers[inst], "units started while the containers still run"
                    self.runs[inst], self.server_up[inst] = ("units", self.checkout[inst]), True
                    self._invoke(unit)
            return 0, ""
        if verb in ("stop", "start"):
            inst = units[0].split("@")[1]
            self.server_up[inst] = verb == "start"
            return 0, ""
        if verb in ("daemon-reload",) or args[:2] == ["enable", "--now"]:
            return 0, ""
        if verb == "is-enabled":
            inst = units[0].split("@")[1]
            return (0, "enabled") if self.enabled[inst] else (1, "disabled")
        if verb == "is-active":
            inst = units[0].split("@")[1]
            up = self.runs[inst] and self.runs[inst][0] == "units" and self.server_up[inst]
            return (0, "active") if up else (3, "inactive")
        if verb == "show":
            return 0, f"inv-{units[0]}-{self.invocations.get(units[0], 0)}"
        raise AssertionError(f"unexpected systemctl {args}")

    def _new_id(self):
        self._ids += 1
        return f"c{self._ids:04d}"

    def _invoke(self, unit):
        self.invocations[unit] = self.invocations.get(unit, 0) + 1

    def sleep(self, seconds):
        """The stable minute passing: a ``late_crash`` image crashes and is restarted by Docker meanwhile."""
        if seconds == da.STABLE_S:
            for inst, running in self.runs.items():
                if running and running[0] == "containers" and running[1] in self.late_crash:
                    self.containers[inst]["server"][1] += 1  # same id, RestartCount + 1

    # the instance's server
    def http(self, url, headers):
        self.urls.append((url, dict(headers)))
        if "api.github.com" in url:
            return 200, {"check_runs": [{"status": "completed", "conclusion": self.ci}]}
        inst = _INST_BY_PORT[url.split(":")[2].split("/")[0]]
        running = self.runs[inst]
        if not running or not self.server_up[inst] or running[1] in self.broken:
            return 0, None
        if url.endswith("/health"):
            return 200, _health(inst, self._version(running))
        if url.endswith("/state"):
            return (200, {}) if headers.get("Authorization") == f"Bearer tok-{inst}" else (401, None)
        raise AssertionError(url)

    def _version(self, running):
        how, ref = running
        if ref.startswith("v"):
            return ref.lstrip("v")
        return self.versions.get(ref.removeprefix("sha-"))

    # what the tests read
    def ups(self, inst):
        """The image tags `compose up` ran for an instance, in order."""
        return [c[1].split("=", 1)[1] for c in self.cmds
                if c[0] == "env" and "up" in c and Path(c[c.index("--env-file") + 1]).stem == inst]

    def unit_calls(self, verb):
        return [c for c in self.cmds if c[:3] == ["systemctl", "--user", verb]]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "lumi")


def _agent(world: World, **kw) -> tuple[Agent, list[str]]:
    said: list[str] = []
    agent = Agent(home=world.home, run=world.run, http=world.http, sleep=world.sleep, now=lambda: NOW,
                  out=said.append, systemd_dir=world.home.parent / "systemd", **kw)
    return agent, said


# --- dev ---------------------------------------------------------------------------------------------------------
def test_the_first_container_deploy_retires_devs_units_and_keeps_the_image(world):
    agent, said = _agent(world)
    agent.tick()
    assert world.ups("dev") == ["sha-m2"] and world.runs["dev"] == ("containers", "sha-m2")
    assert world.unit_calls("disable") == [["systemctl", "--user", "disable", "--now", "lumi-server@dev",
                                            "lumi-telegram-inbound@dev", "lumi-telegram-outbound@dev"]]
    state = agent.load()
    assert state["dev"]["current"] == "m2" and state["dev"]["previous"] == "m1"
    assert state["dev"]["runs"] == "containers" and state["dev"]["previous_runs"] == "units"
    assert state["last"]["dev"]["result"] == "ok"
    assert world.runs["prod"] == ("units", "v2.7.0") and world.ups("prod") == []  # nothing new for prod
    assert any("the v2.6 units are stopped and disabled" in line for line in said)
    agent.tick()
    assert world.ups("dev") == ["sha-m2"]  # already live — no second deploy


def test_a_later_deploy_recreates_the_containers_and_never_touches_the_units(world):
    agent, _ = _agent(world)
    agent.tick()
    world.main, world.ancestors = "m3", {"m1", "m2"}
    agent.tick()
    assert world.ups("dev") == ["sha-m2", "sha-m3"]
    assert len(world.unit_calls("disable")) == 1 and world.unit_calls("enable") == []  # retired once
    assert agent.load()["dev"]["previous_runs"] == "containers"


def test_a_way_back_by_hand_is_noticed_before_the_next_deploy(world):
    """Review #2: the owner went back to the units by hand (`compose down` + `enable --now`) and restarted the timer;
    the state still says containers — the next deploy must retire the units again, not run into their port."""
    agent, _ = _agent(world)
    agent.tick()  # dev on sha-m2, units retired
    world.run(agent._compose("dev", "m2", "down"))  # the guide's way back, by hand
    world.run(["systemctl", "--user", "enable", "--now", *da._units("dev")])
    assert world.runs["dev"] == ("units", "m1") and agent.load()["dev"]["runs"] == "containers"  # stale record
    world.main, world.ancestors = "m3", {"m1", "m2"}
    agent.tick()
    assert world.runs["dev"] == ("containers", "sha-m3")
    assert len(world.unit_calls("disable")) == 2  # retired again, because systemd said so
    assert agent.load()["last"]["dev"]["result"] == "ok"


@pytest.mark.parametrize("ci", ["failure", "in_progress"])
def test_a_red_or_unfinished_ci_is_never_deployed(world, ci):
    world.ci = ci
    agent, _ = _agent(world)
    agent.tick()
    assert world.ups("dev") == [] and world.runs["dev"] == ("units", "m1") and world.unit_calls("disable") == []
    assert "m2" in agent.load()["dev"]["waiting"]


def test_main_behind_what_dev_runs_is_never_deployed(world):
    world.ancestors = set()
    agent, _ = _agent(world)
    agent.tick()
    assert world.ups("dev") == []
    assert "behind what dev runs" in agent.load()["dev"]["waiting"]


def test_a_missing_image_deploys_nothing_and_stops_nothing(world):
    world.images.discard("sha-m2")  # CI hasn't pushed it, or the package isn't public yet
    agent, _ = _agent(world)
    agent.tick()
    assert world.ups("dev") == [] and world.unit_calls("disable") == [] and world.runs["dev"] == ("units", "m1")
    state = agent.load()
    assert "pull failed" in state["last"]["dev"]["result"] and "m2" not in state["dev"].get("bad", [])


def test_a_failed_first_container_deploy_goes_back_to_the_units(world):
    world.broken = {"sha-m2"}
    agent, said = _agent(world)
    agent.tick()
    assert world.ups("dev") == ["sha-m2"]  # no image exists for m1 — back to the units, not to an image
    assert world.unit_calls("enable") == [["systemctl", "--user", "enable", "--now", "lumi-server@dev",
                                           "lumi-telegram-inbound@dev", "lumi-telegram-outbound@dev"]]
    assert world.runs["dev"] == ("units", "m1") and world.containers["dev"] == {}
    state = agent.load()
    assert state["dev"]["current"] == "m1" and state["dev"]["bad"] == ["m2"] and state["dev"]["runs"] == "units"
    assert any("failed the smoke" in line and "back to m1 (units)" in line for line in said)
    agent.tick()
    assert world.ups("dev") == ["sha-m2"]  # the bad commit is not retried
    world.main, world.ancestors = "m3", {"m1"}
    agent.tick()
    assert world.ups("dev") == ["sha-m2", "sha-m3"] and world.runs["dev"] == ("containers", "sha-m3")


def test_a_failed_later_deploy_goes_back_to_the_previous_image(world):
    agent, _ = _agent(world)
    agent.tick()  # m2, containers
    world.main, world.ancestors, world.broken = "m3", {"m1", "m2"}, {"sha-m3"}
    agent.tick()
    assert world.ups("dev") == ["sha-m2", "sha-m3", "sha-m2"] and world.runs["dev"] == ("containers", "sha-m2")
    assert world.unit_calls("enable") == []
    assert agent.load()["dev"]["current"] == "m2" and agent.load()["dev"]["bad"] == ["m3"]


def test_dev_expects_the_version_of_the_commit_it_deployed(world):
    agent, _ = _agent(world)

    def stale_health(url, headers):  # the server still answers with the old code
        status, body = World.http(world, url, headers)
        return (200, _health("dev", "2.7.0")) if url.endswith("/health") and ":8766" in url else (status, body)

    agent._http = stale_health
    agent.tick()
    assert agent.load()["dev"]["current"] == "m1"
    assert "not '2.8.0'" in agent.load()["last"]["dev"]["result"]


def test_a_new_error_in_the_log_since_the_start_fails_the_smoke(world):
    agent, _ = _agent(world)
    real_run = world.run

    def run(cmd, cwd=None):
        out = real_run(cmd, cwd)
        if cmd[0] == "env" and "up" in cmd:
            with (world.home / "dev" / "data" / "lumi.log").open("a") as f:  # the new server logs a failure
                f.write(f"{STAMP},500 ERROR lumi.server: the store did not load\n")
        return out

    agent._run = run
    agent.tick()
    assert agent.load()["dev"]["current"] == "m1"
    assert "1 new ERROR line(s)" in agent.load()["last"]["dev"]["result"]


def test_a_crash_during_the_stable_minute_fails_the_smoke(world):
    """Review #2 for containers: Docker restarts a crashed server within seconds — same id, RestartCount + 1."""
    world.late_crash = {"sha-m2"}
    agent, _ = _agent(world)
    agent.tick()
    assert world.runs["dev"] == ("units", "m1")  # back
    assert "server (restarted)" in agent.load()["last"]["dev"]["result"]


def test_a_server_gone_by_the_end_of_the_minute_fails_the_smoke(world):
    agent, _ = _agent(world)
    answered: list[str] = []

    def health_then_gone(url, headers):
        if url.endswith("/health") and ":8766" in url:
            answered.append(url)
            if len(answered) > 1:
                return 0, None  # answered at the start, silent a minute later
        return World.http(world, url, headers)

    agent._http = health_then_gone
    agent.tick()
    assert agent.load()["dev"]["current"] == "m1"
    assert "does not answer /v1/health" in agent.load()["last"]["dev"]["result"]


# --- prod ---------------------------------------------------------------------------------------------------------
def test_a_new_tag_waits_for_confirm_then_deploys_its_image(world):
    world.main, world.tags = "m1", ["v2.7.0", "v2.8.0"]
    agent, said = _agent(world)
    agent.tick()
    agent.tick()
    assert world.ups("prod") == [] and agent.load()["prod"]["waiting"] == "v2.8.0"
    assert sum("waiting for `deploy_agent.py confirm v2.8.0`" in line for line in said) == 1  # said once
    agent.confirm("v2.7.1")  # not the released tag → still nothing
    assert world.ups("prod") == []
    agent.confirm("v2.8.0")
    assert world.ups("prod") == ["v2.8.0"] and world.runs["prod"] == ("containers", "v2.8.0")
    state = agent.load()
    assert state["prod"]["current"] == "v2.8.0" and state["prod"]["previous"] == "v2.7.0"
    assert state["prod"]["previous_runs"] == "units" and "waiting" not in state["prod"]


def test_a_prod_deploy_pulls_then_stops_her_and_archives_her_data_first(world):
    world.main, world.tags = "m1", ["v2.7.0", "v2.8.0"]
    agent, said = _agent(world)
    agent.confirm("v2.8.0")
    order = [("pull" if "pull" in c else "up" if "up" in c else "tar" if c[0] == "tar" else c[2])
             for c in world.cmds if "pull" in c or ("up" in c and c[0] == "env") or c[0] == "tar"
             or c[:3] in (["systemctl", "--user", "stop"], ["systemctl", "--user", "disable"])]
    assert order == ["pull", "stop", "tar", "disable", "up"]  # nothing stops if the pull fails
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(NOW))
    archive = world.home / "prod" / "backups" / f"data-{stamp}-v2.7.0.tar.gz"  # named after what ran
    assert archive.is_file() and any(str(archive) in line for line in said)


def test_a_later_prod_deploy_stops_the_server_container_for_the_archive(world):
    world.main, world.tags = "m1", ["v2.7.0", "v2.8.0"]
    agent, _ = _agent(world)
    agent.confirm("v2.8.0")
    world.tags.append("v2.9.0")
    agent.confirm("v2.9.0")
    stops = [c for c in world.cmds if c[0] == "env" and c[-2:] == ["stop", "server"]]
    assert len(stops) == 1 and stops[0][1] == "LUMI_IMAGE_TAG=v2.8.0"  # the running project, by its tag
    assert world.ups("prod") == ["v2.8.0", "v2.9.0"] and len(world.unit_calls("disable")) == 1


def test_a_failed_first_prod_deploy_goes_back_to_the_units_on_her_tag(world):
    world.main, world.tags, world.broken = "m1", ["v2.7.0", "v2.8.0"], {"v2.8.0"}
    agent, _ = _agent(world)
    agent.confirm("v2.8.0")
    assert world.runs["prod"] == ("units", "v2.7.0")
    state = agent.load()
    assert state["prod"]["current"] == "v2.7.0" and state["prod"]["bad"] == ["v2.8.0"]
    agent.tick()
    assert world.ups("prod") == ["v2.8.0"]  # not retried on its own


def test_a_failed_archive_leaves_prod_running_on_its_tag(world):
    world.main, world.tags, world.tar_fails = "m1", ["v2.7.0", "v2.8.0"], True
    agent, _ = _agent(world)
    agent.confirm("v2.8.0")
    assert world.ups("prod") == [] and world.runs["prod"] == ("units", "v2.7.0") and world.server_up["prod"]
    assert ["systemctl", "--user", "start", "lumi-server@prod"] in world.cmds  # started again
    assert list((world.home / "prod" / "backups").iterdir()) == []  # the partial archive removed
    state = agent.load()
    assert "confirmed" not in state["prod"] and "archive failed" in state["last"]["prod"]["result"]
    stops = len(world.unit_calls("stop"))
    agent.tick()  # the next step only waits — it doesn't stop her again every 5 minutes
    assert len(world.unit_calls("stop")) == stops and agent.load()["prod"]["waiting"] == "v2.8.0"


def test_rollback_prod_after_the_first_container_deploy_is_the_units(world):
    world.main, world.tags = "m1", ["v2.7.0", "v2.8.0"]
    agent, _ = _agent(world)
    agent.confirm("v2.8.0")
    assert agent.rollback("prod") is True
    assert world.runs["prod"] == ("units", "v2.7.0") and world.containers["prod"] == {}
    state = agent.load()
    assert state["prod"]["current"] == "v2.7.0" and state["prod"]["previous"] == "v2.8.0"
    assert state["prod"]["runs"] == "units" and state["prod"]["previous_runs"] == "containers"
    agent.tick()
    assert world.runs["prod"] == ("units", "v2.7.0")  # the next tick does not undo the rollback
    agent.confirm("v2.8.0")  # an explicit re-approval
    assert world.runs["prod"] == ("containers", "v2.8.0")


def test_rollback_between_two_images_stays_in_containers(world):
    world.main, world.tags = "m1", ["v2.7.0", "v2.8.0"]
    agent, _ = _agent(world)
    agent.confirm("v2.8.0")
    world.tags.append("v2.9.0")
    agent.confirm("v2.9.0")
    assert agent.rollback("prod") is True
    assert world.runs["prod"] == ("containers", "v2.8.0") and world.unit_calls("enable") == []


def test_rollback_of_a_v27_record_says_why_and_moves_nothing(world):
    """Before any container deploy both refs ran as units, and the v2.8 agent no longer moves a checkout."""
    agent, said = _agent(world)
    agent.save({"prod": {"current": "v2.7.0", "previous": "v2.6.0", "runs": "units"}})
    assert agent.rollback("prod") is False
    assert world.cmds and not any(c[0] == "env" or c[2:3] in (["enable"], ["disable"]) for c in world.cmds)
    assert "move the units' checkout by hand" in said[-1]


def test_rollback_dev_sticks_until_a_new_commit(world):
    agent, _ = _agent(world)
    agent.tick()
    assert agent.rollback("dev") is True and world.runs["dev"] == ("units", "m1")
    agent.tick()
    assert world.runs["dev"] == ("units", "m1")


def test_rollback_with_nothing_to_go_back_to_says_so(world):
    agent, said = _agent(world)
    assert agent.rollback("prod") is False
    assert world.ups("prod") == [] and "nothing to roll back to" in said[-1]


def test_status_says_what_runs_where_and_what_waits(world):
    world.tags = ["v2.7.0", "v2.8.0"]
    world.broken = {"sha-m2"}
    agent, _ = _agent(world)
    agent.tick()
    text = agent.status()
    assert "dev: m1 (v2.6 units" in text and "bad: m2" in text and "rolled back" in text
    assert "prod: v2.7.0 (v2.6 units" in text and "waiting: v2.8.0" in text
    world.broken = set()
    world.main, world.ancestors = "m3", {"m1"}
    agent.tick()
    assert "dev: m3 (image sha-m3" in agent.status()


# --- safety -------------------------------------------------------------------------------------------------------
def test_the_containers_never_start_beside_the_units(world):
    """The units are disabled before `up`: the port and the brain lock are free, two brains never share her root."""
    world.tags = ["v2.7.0", "v2.8.0"]
    agent, _ = _agent(world)
    agent.tick()
    agent.confirm("v2.8.0")
    for inst in ("dev", "prod"):
        up = next(i for i, c in enumerate(world.cmds) if c[0] == "env" and "up" in c and f"/{inst}.env" in " ".join(c))
        off = next(i for i, c in enumerate(world.cmds) if c[:3] == ["systemctl", "--user", "disable"]
                   and f"lumi-server@{inst}" in c)
        assert off < up


def test_every_compose_call_names_the_instance_and_a_tag(world):
    world.tags = ["v2.7.0", "v2.8.0"]
    agent, _ = _agent(world)
    agent.tick()
    agent.confirm("v2.8.0")
    agent.rollback("prod")
    compose = [c for c in world.cmds if c[0] == "env"]
    assert compose and all(c[1].startswith("LUMI_IMAGE_TAG=") and len(c[1]) > len("LUMI_IMAGE_TAG=") for c in compose)
    assert all(str(world.home / "deploy" / "compose" / "compose.yaml") in c for c in compose)


def test_the_state_probe_carries_the_token_but_nothing_logs_it(world):
    agent, said = _agent(world)
    agent.tick()
    assert ("http://192.168.1.197:8766/v1/state", {"Authorization": "Bearer tok-dev"}) in world.urls
    log = (world.home / "deploy" / "deploy.log").read_text()
    assert "tok-dev" not in log and not any("tok-" in line for line in said)
    assert "tok-" not in (world.home / "deploy" / "state.json").read_text()
    assert not any("tok-" in " ".join(c) for c in world.cmds)  # never on a command line either


def test_the_github_probe_is_unauthenticated(world):
    agent, _ = _agent(world)
    agent.tick()
    gh = [h for url, h in world.urls if "api.github.com" in url]
    assert gh and all("Authorization" not in h for h in gh)


def _agents_own(rel: Path) -> bool:
    """The agent's own files: its state dir and the prod archives beside the data root."""
    return rel.parts[0] == "deploy" or rel.parts[:2] == ("prod", "backups")


def _fingerprint(home: Path) -> dict[str, str]:
    """Every file but the agent's own → its hash."""
    return {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(home.rglob("*")) if p.is_file() and not _agents_own(p.relative_to(home))}


def test_contract_a_deploy_and_a_rollback_move_code_only(world):
    world.tags = ["v2.7.0", "v2.8.0"]
    before = _fingerprint(world.home)
    assert any(k.endswith(".env") for k in before) and any("/data/" in k for k in before)
    agent, _ = _agent(world)
    agent.tick()
    agent.confirm("v2.8.0")
    agent.rollback("prod")
    agent.rollback("dev")
    assert world.ups("dev") and world.ups("prod")  # code did move
    assert _fingerprint(world.home) == before  # data/ and .env untouched, byte for byte
    assert {p.name for p in (world.home / "deploy").iterdir()} <= {"state.json", "deploy.log"}
    assert all(p.name.startswith("data-") and p.name.endswith(".tar.gz")
               for p in (world.home / "prod" / "backups").iterdir())


def test_install_copies_the_agent_and_the_compose_files_out_of_the_checkout(world):
    agent, _ = _agent(world)
    agent.install(ROOT / "scripts" / "deploy_agent.py")
    deploy = world.home / "deploy"
    assert (deploy / "deploy_agent.py").read_bytes() == (ROOT / "scripts" / "deploy_agent.py").read_bytes()
    for name in ("compose.yaml", "prod.env", "dev.env"):
        assert (deploy / "compose" / name).read_bytes() == (ROOT / "deploy" / "compose" / name).read_bytes()
    assert {p.name for p in (world.home.parent / "systemd").iterdir()} == {"lumi-deploy.service",
                                                                          "lumi-deploy.timer"}
    assert world.unit_calls("daemon-reload") and ["systemctl", "--user", "enable", "--now",
                                                  "lumi-deploy.timer"] in world.cmds
    state = agent.load()
    assert state["dev"]["current"] == "m1" and state["prod"]["current"] == "v2.7.0"  # seeded, not deployed
    assert state["dev"]["runs"] == state["prod"]["runs"] == "units"
    assert world.ups("dev") == world.ups("prod") == []


def test_a_dropped_session_never_breaks_the_output(monkeypatch):
    """Review #3: the ssh session that ran `confirm` is gone — its stdout is a broken pipe."""
    class Gone:
        def write(self, _):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr("sys.stdout", Gone())
    da._say("prod: deploying v2.8.0")  # no exception


def test_the_confirmation_is_saved_before_the_deploy_starts(world):
    world.main, world.tags = "m1", ["v2.7.0", "v2.8.0"]
    agent, _ = _agent(world)
    real_run = world.run

    def killed_at_up(cmd, cwd=None):
        if cmd[0] == "env" and "up" in cmd:
            raise KeyboardInterrupt  # killed mid-deploy
        return real_run(cmd, cwd)

    agent._run = killed_at_up
    with pytest.raises(KeyboardInterrupt):
        agent.confirm("v2.8.0")
    assert agent.load()["prod"]["confirmed"] == "v2.8.0"  # the next tick finishes the job


def test_main_survives_a_hangup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    before = signal.getsignal(signal.SIGHUP)
    monkeypatch.setattr(da.Agent, "status", lambda self: "dev: m1")
    try:
        assert da.main(["status"]) == 0
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGHUP, before)


def test_an_error_status_is_kept_not_reported_as_silence(monkeypatch):
    """Review #5: urllib raises on a 401 — the smoke must say "answered 401", not "answered nothing"."""
    import io
    import urllib.error

    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"detail":"unauthorized"}'))

    def unreachable(req, timeout):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(da.urllib.request, "urlopen", refuse)
    assert da._http("http://192.168.1.197:8766/v1/state", {}) == (401, None)
    monkeypatch.setattr(da.urllib.request, "urlopen", unreachable)
    assert da._http("http://192.168.1.197:8766/v1/state", {}) == (0, None)


def test_a_second_step_never_runs_alongside_a_running_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    lock = tmp_path / "lumi" / "deploy" / "agent.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert da.main(["status"]) == 1
    assert "another deploy step is running" in capsys.readouterr().out
