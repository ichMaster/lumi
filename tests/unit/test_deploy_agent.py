"""The pull-based deploy agent (v2.7, LUMI-235) — a green ``main`` reaches dev by itself, a release reaches prod
only after the owner's ``confirm`` (her data archived first — owner decision 2026-10-20), a failed smoke rolls
back, a rollback is one command, and a deploy moves code only (never data or settings).

The decisions are pure and tested over fixtures; the runner is tested over a fake world (a fake command runner
standing in for git/uv/systemctl and a fake HTTP standing in for GitHub and the instance's server) — no network,
no systemd, no paid API.
"""

import fcntl
import hashlib
import time
from pathlib import Path

import pytest

from scripts import deploy_agent as da
from scripts.deploy_agent import (
    Agent,
    ci_verdict,
    dev_action,
    errors_since,
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


# --- a fake world: git / uv / systemctl / GitHub / the instance servers -----------------------------------------
class World:
    """What the box would do, in memory: checkouts move on ``git checkout``, the server answers for whatever its
    checkout runs, ``broken`` refs never come up."""

    def __init__(self, home: Path, *, main="m2", dev_head="m1", prod_tag="v2.6.0", tags=("v2.6.0",),
                 ci="success", ancestors=("m1",), broken=(), versions=None, tar_fails=False,
                 late_crash=()) -> None:
        self.home, self.main, self.tags, self.ci, self.tar_fails = home, main, list(tags), ci, tar_fails
        self.ancestors, self.broken, self.late_crash = set(ancestors), set(broken), set(late_crash)
        self.invocations: dict[str, int] = {}  # unit → how many times it was (re)started
        self.head = {"dev": dev_head, "prod": prod_tag}
        self.running = {"dev": True, "prod": True}
        self.moved_while_running: list[str] = []  # code that moved under a live server (review #1)
        self.versions = versions or {"m1": "2.6.0", "m2": "2.7.0", "m3": "2.7.1"}
        self.cmds: list[list[str]] = []
        self.urls: list[tuple[str, dict]] = []
        for inst in ("dev", "prod"):
            (home / inst / "app").mkdir(parents=True)
            (home / inst / "data").mkdir()
            (home / inst / "data" / "lumi.log").write_text("2026-10-20 00:00:00,000 INFO lumi: started\n")
            (home / inst / "data" / "store.json").write_text('{"messages": ["her memory"]}')
            (home / inst / ".env").write_text(f"LUMI_ENV={inst}\nLUMI_SERVER_HOST=192.168.1.197\n"
                                              f"LUMI_SERVER_PORT={_PORT[inst]}\nLUMI_SERVER_TOKEN=tok-{inst}\n")

    def run(self, cmd, cwd=None):
        self.cmds.append(list(cmd))
        if cmd[0] == "git":
            inst, args = Path(cmd[2]).parent.name, cmd[3:]
            if args[0] == "fetch":
                return 0, ""
            if args[:2] == ["rev-parse", "HEAD"]:
                return 0, self.head[inst]
            if args[:2] == ["rev-parse", "origin/main"]:
                return 0, self.main
            if args[0] == "describe":
                return (0, self.head[inst]) if self.head[inst].startswith("v") else (128, "no tag")
            if args[0] == "merge-base":
                return (0, "") if args[2] in self.ancestors else (1, "")
            if args[0] == "show":
                sha = args[1].split(":")[0]
                return (0, self.versions[sha] + "\n") if sha in self.versions else (128, "")
            if args[0] == "tag":
                return 0, "\n".join(self.tags)
            if args[0] == "checkout":
                if self.running[inst]:
                    self.moved_while_running.append(inst)
                self.head[inst] = args[-1]
                return 0, ""
        if cmd[1:2] == ["sync"]:
            if self.running[Path(cwd).parent.name]:
                self.moved_while_running.append(Path(cwd).parent.name)
            return 0, ""
        if cmd[0] == "tar":
            assert cmd[1] == "-czf" and cmd[3:] == ["-C", str(self.home / "prod"), "data"]
            if self.tar_fails:
                Path(cmd[2]).write_bytes(b"half")  # a partial archive, as a full disk leaves it
                return 2, "tar: write error: No space left on device"
            Path(cmd[2]).write_bytes(b"an archive")
            return 0, ""
        if cmd[0] == "systemctl":
            verb = cmd[2]
            if verb in ("restart", "stop", "start") and "@" in cmd[3]:
                inst = cmd[3].split("@")[1]
                self.running[inst] = verb != "stop"
                if verb != "stop":  # the server and, PartOf/WantedBy, its daemons get new invocations
                    for unit in self._units(inst):
                        self.invocations[unit] = self.invocations.get(unit, 0) + 1
            if verb == "show":
                return 0, f"inv-{cmd[-1]}-{self.invocations.get(cmd[-1], 0)}"
            if verb in ("restart", "stop", "start", "daemon-reload", "enable"):
                return 0, ""
            if verb == "is-enabled":
                return 0, "enabled"
            if verb == "is-active":
                inst = cmd[3].split("@")[1]
                return (3, "failed") if self.head[inst] in self.broken else (0, "active")
        raise AssertionError(f"unexpected command {cmd}")

    def http(self, url, headers):
        self.urls.append((url, dict(headers)))
        if "api.github.com" in url:
            return 200, {"check_runs": [{"status": "completed", "conclusion": self.ci}]}
        inst = _INST_BY_PORT[url.split(":")[2].split("/")[0]]
        if self.head[inst] in self.broken:
            return 0, None
        if url.endswith("/health"):
            ref = self.head[inst]
            return 200, _health(inst, ref.lstrip("v") if ref.startswith("v") else self.versions.get(ref))
        if url.endswith("/state"):
            return (200, {}) if headers.get("Authorization") == f"Bearer tok-{inst}" else (401, None)
        raise AssertionError(url)

    @staticmethod
    def _units(inst):
        return [f"lumi-server@{inst}", f"lumi-telegram-inbound@{inst}", f"lumi-telegram-outbound@{inst}"]

    def sleep(self, seconds):
        """The stable minute passing: a ``late_crash`` checkout crashes and is restarted by systemd meanwhile."""
        if seconds == da.STABLE_S:
            for inst, ref in self.head.items():
                if ref in self.late_crash:
                    unit = f"lumi-server@{inst}"
                    self.invocations[unit] = self.invocations.get(unit, 0) + 1

    def checkouts(self, inst):
        return [c[-1] for c in self.cmds if c[:1] == ["git"] and c[3] == "checkout" and f"/{inst}/" in c[2]]

    def starts(self):
        return [c[3] for c in self.cmds if c[:2] == ["systemctl", "--user"] and c[2] in ("start", "restart")]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path / "lumi")


def _agent(world: World, **kw) -> tuple[Agent, list[str]]:
    said: list[str] = []
    agent = Agent(home=world.home, run=world.run, http=world.http, sleep=world.sleep, now=lambda: NOW,
                  out=said.append, systemd_dir=world.home.parent / "systemd", **kw)
    return agent, said


# --- dev ---------------------------------------------------------------------------------------------------------
def test_tick_deploys_a_green_main_commit_to_dev_and_keeps_it(world):
    agent, _ = _agent(world)
    agent.tick()
    assert world.checkouts("dev") == ["m2"] and world.starts() == ["lumi-server@dev"]
    sync = [c for c in world.cmds if c[1:2] == ["sync"]]
    assert sync == [[da._uv(), "sync", "--frozen", "--all-extras"]]
    state = agent.load()
    assert state["dev"]["current"] == "m2" and state["dev"]["previous"] == "m1"
    assert state["last"]["dev"]["result"] == "ok"
    assert world.checkouts("prod") == []  # nothing new for prod
    agent.tick()
    assert world.checkouts("dev") == ["m2"]  # already live — no second deploy


@pytest.mark.parametrize("ci", ["failure", "in_progress"])
def test_a_red_or_unfinished_ci_is_never_deployed(world, ci):
    world.ci = ci
    agent, _ = _agent(world)
    agent.tick()
    assert world.checkouts("dev") == [] and world.starts() == []
    assert "m2" in agent.load()["dev"]["waiting"]


def test_main_behind_what_dev_runs_is_never_deployed(world):
    world.ancestors = set()  # dev runs a commit main doesn't contain yet (v2-dev before the merge)
    agent, _ = _agent(world)
    agent.tick()
    assert world.checkouts("dev") == []
    assert "behind what dev runs" in agent.load()["dev"]["waiting"]


def test_a_failed_smoke_rolls_dev_back_and_marks_the_commit(world):
    world.broken = {"m2"}
    agent, said = _agent(world)
    agent.tick()
    assert world.checkouts("dev") == ["m2", "m1"]  # deployed, failed, back
    assert world.starts() == ["lumi-server@dev", "lumi-server@dev"]
    state = agent.load()
    assert state["dev"]["current"] == "m1" and state["dev"]["bad"] == ["m2"]
    assert "rolled back" in state["last"]["dev"]["result"]
    assert any("failed the smoke" in line and "rolling back to m1" in line for line in said)
    agent.tick()
    assert world.checkouts("dev") == ["m2", "m1"]  # the bad commit is not retried
    world.main, world.ancestors = "m3", {"m1"}
    agent.tick()
    assert world.checkouts("dev") == ["m2", "m1", "m3"]  # a new commit is


def test_a_crash_during_the_stable_minute_fails_the_smoke(world):
    """Review #2: systemd restarts a crashed server within seconds — "active" a minute later isn't enough."""
    world.late_crash = {"m2"}
    agent, _ = _agent(world)
    agent.tick()
    assert world.checkouts("dev") == ["m2", "m1"]  # rolled back
    assert "lumi-server@dev (restarted)" in agent.load()["last"]["dev"]["result"]


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


def test_dev_expects_the_version_of_the_commit_it_deployed(world):
    world.versions = {"m1": "2.6.0", "m2": "2.7.0"}
    agent, _ = _agent(world)

    def stale_health(url, headers):  # the server still answers with the old code
        status, body = World.http(world, url, headers)
        return (200, _health("dev", "2.6.0")) if url.endswith("/health") and ":8766" in url else (status, body)

    agent._http = stale_health
    agent.tick()
    assert agent.load()["dev"]["current"] == "m1"
    assert "not '2.7.0'" in agent.load()["last"]["dev"]["result"]


def test_a_new_error_in_the_log_since_the_restart_fails_the_smoke(world):
    agent, _ = _agent(world)
    real_run = world.run

    def run(cmd, cwd=None):
        if cmd[:3] == ["systemctl", "--user", "start"] and world.head["dev"] == "m2":
            with (world.home / "dev" / "data" / "lumi.log").open("a") as f:  # the new server logs a failure
                f.write(f"{STAMP},500 ERROR lumi.server: the store did not load\n")
        return real_run(cmd, cwd)

    agent._run = run
    agent.tick()
    assert agent.load()["dev"]["current"] == "m1"
    assert "1 new ERROR line(s)" in agent.load()["last"]["dev"]["result"]


# --- prod ---------------------------------------------------------------------------------------------------------
def test_a_new_tag_waits_for_confirm_then_deploys(world):
    world.main, world.tags = "m1", ["v2.6.0", "v2.7.0"]
    agent, said = _agent(world)
    agent.tick()
    agent.tick()
    assert world.checkouts("prod") == []
    assert agent.load()["prod"]["waiting"] == "v2.7.0"
    assert sum("waiting for `deploy_agent.py confirm v2.7.0`" in line for line in said) == 1  # said once
    agent.confirm("v2.6.1")  # not the released tag → still nothing
    assert world.checkouts("prod") == []
    agent.confirm("v2.7.0")
    assert world.checkouts("prod") == ["v2.7.0"] and world.starts() == ["lumi-server@prod"]
    state = agent.load()
    assert state["prod"]["current"] == "v2.7.0" and state["prod"]["previous"] == "v2.6.0"
    assert "waiting" not in state["prod"]


def test_a_failed_prod_smoke_rolls_back_to_the_previous_tag(world):
    world.main, world.tags, world.broken = "m1", ["v2.6.0", "v2.7.0"], {"v2.7.0"}
    agent, _ = _agent(world)
    agent.confirm("v2.7.0")
    assert world.checkouts("prod") == ["v2.7.0", "v2.6.0"]
    state = agent.load()
    assert state["prod"]["current"] == "v2.6.0" and state["prod"]["bad"] == ["v2.7.0"]
    agent.tick()
    assert world.checkouts("prod") == ["v2.7.0", "v2.6.0"]  # not retried on its own


def test_a_prod_deploy_stops_her_and_archives_her_data_first(world):
    world.main, world.tags = "m1", ["v2.6.0", "v2.7.0"]
    agent, said = _agent(world)
    agent.confirm("v2.7.0")
    order = [("checkout" if c[0] == "git" else c[0] if c[0] == "tar" else c[2]) for c in world.cmds
             if c[0] == "tar" or (c[0] == "git" and c[3] == "checkout")
             or c[:3] in (["systemctl", "--user", "stop"], ["systemctl", "--user", "start"])]
    assert order == ["stop", "tar", "stop", "checkout", "start"]  # her session closed, data archived, then code
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(NOW))
    archive = world.home / "prod" / "backups" / f"data-{stamp}-v2.6.0.tar.gz"  # named after what ran
    assert archive.is_file() and any(str(archive) in line for line in said)
    assert world.head["prod"] == "v2.7.0"


@pytest.mark.parametrize("step", ["deploy dev", "confirm prod", "failed smoke", "rollback dev", "rollback prod"])
def test_code_never_moves_under_a_running_server(world, step):
    """Review #1: the old server closes her session over its own files — stopped before checkout and sync."""
    world.tags = ["v2.6.0", "v2.7.0"]
    world.broken = {"m2"} if step == "failed smoke" else set()
    agent, _ = _agent(world)
    agent.tick()
    if step == "confirm prod" or step == "rollback prod":
        agent.confirm("v2.7.0")
    if step.startswith("rollback"):
        world.broken = set()
        agent.rollback(step.split()[1])
    assert world.checkouts("dev") or world.checkouts("prod")
    assert world.moved_while_running == []
    assert world.running == {"dev": True, "prod": True}  # and every instance is up again afterwards


def test_a_dev_deploy_archives_nothing(world):
    agent, _ = _agent(world)
    agent.tick()
    assert world.checkouts("dev") == ["m2"]
    assert not any(c[0] == "tar" for c in world.cmds) and not (world.home / "dev" / "backups").exists()


def test_a_failed_archive_leaves_prod_on_its_tag_and_running(world):
    world.main, world.tags, world.tar_fails = "m1", ["v2.6.0", "v2.7.0"], True
    agent, _ = _agent(world)
    agent.confirm("v2.7.0")
    assert world.checkouts("prod") == [] and world.head["prod"] == "v2.6.0"
    assert ["systemctl", "--user", "start", "lumi-server@prod"] in world.cmds  # started again
    assert list((world.home / "prod" / "backups").iterdir()) == []  # the partial archive removed
    state = agent.load()
    assert "confirmed" not in state["prod"] and "archive failed" in state["last"]["prod"]["result"]
    stops = sum(c[:3] == ["systemctl", "--user", "stop"] for c in world.cmds)
    agent.tick()  # the next step only waits — it doesn't stop her again every 5 minutes
    assert sum(c[:3] == ["systemctl", "--user", "stop"] for c in world.cmds) == stops
    assert agent.load()["prod"]["waiting"] == "v2.7.0"


def test_rollback_prod_is_one_command_and_sticks(world):
    world.main, world.tags = "m1", ["v2.6.0", "v2.7.0"]
    agent, _ = _agent(world)
    agent.confirm("v2.7.0")
    assert agent.rollback("prod") is True
    assert world.head["prod"] == "v2.6.0"
    state = agent.load()
    assert state["prod"]["current"] == "v2.6.0" and state["prod"]["previous"] == "v2.7.0"
    agent.tick()
    assert world.head["prod"] == "v2.6.0"  # the next tick does not undo the rollback
    agent.confirm("v2.7.0")  # an explicit re-approval
    assert world.head["prod"] == "v2.7.0"


def test_rollback_dev_sticks_until_a_new_commit(world):
    agent, _ = _agent(world)
    agent.tick()
    assert agent.rollback("dev") is True and world.head["dev"] == "m1"
    agent.tick()
    assert world.head["dev"] == "m1"


def test_rollback_with_nothing_to_go_back_to_says_so(world):
    agent, said = _agent(world)
    assert agent.rollback("prod") is False
    assert world.checkouts("prod") == [] and "nothing to roll back to" in said[-1]


def test_status_says_what_runs_where_and_what_waits(world):
    world.tags = ["v2.6.0", "v2.7.0"]
    world.broken = {"m2"}
    agent, _ = _agent(world)
    agent.tick()
    text = agent.status()
    assert "dev: m1" in text and "bad: m2" in text and "rolled back" in text
    assert "prod: v2.6.0" in text and "waiting: v2.7.0" in text


# --- safety -------------------------------------------------------------------------------------------------------
def test_the_state_probe_carries_the_token_but_nothing_logs_it(world):
    agent, said = _agent(world)
    agent.tick()
    assert ("http://192.168.1.197:8766/v1/state", {"Authorization": "Bearer tok-dev"}) in world.urls
    log = (world.home / "deploy" / "deploy.log").read_text()
    assert "tok-dev" not in log and not any("tok-" in line for line in said)
    assert "tok-" not in (world.home / "deploy" / "state.json").read_text()


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
    world.tags = ["v2.6.0", "v2.7.0"]
    before = _fingerprint(world.home)
    assert any(k.endswith(".env") for k in before) and any("/data/" in k for k in before)
    agent, _ = _agent(world)
    agent.tick()
    agent.confirm("v2.7.0")
    agent.rollback("prod")
    agent.rollback("dev")
    assert world.checkouts("dev") and world.checkouts("prod")  # code did move
    assert _fingerprint(world.home) == before  # data/ and .env untouched, byte for byte
    assert {p.name for p in (world.home / "deploy").iterdir()} <= {"state.json", "deploy.log"}
    assert all(p.name.startswith("data-") and p.name.endswith(".tar.gz")
               for p in (world.home / "prod" / "backups").iterdir())


def test_install_copies_the_agent_out_of_the_checkout_and_starts_the_timer(world):
    agent, _ = _agent(world)
    agent.install(ROOT / "scripts" / "deploy_agent.py")
    copied = world.home / "deploy" / "deploy_agent.py"
    assert copied.read_bytes() == (ROOT / "scripts" / "deploy_agent.py").read_bytes()
    assert {p.name for p in (world.home.parent / "systemd").iterdir()} == {"lumi-deploy.service",
                                                                          "lumi-deploy.timer"}
    systemctl = [c for c in world.cmds if c[0] == "systemctl"]
    assert systemctl == [["systemctl", "--user", "daemon-reload"],
                         ["systemctl", "--user", "enable", "--now", "lumi-deploy.timer"]]
    state = agent.load()
    assert state["dev"]["current"] == "m1" and state["prod"]["current"] == "v2.6.0"  # seeded, not deployed
    assert world.checkouts("dev") == [] and world.checkouts("prod") == []


def test_a_second_step_never_runs_alongside_a_running_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    lock = tmp_path / "lumi" / "deploy" / "agent.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert da.main(["status"]) == 1
    assert "another deploy step is running" in capsys.readouterr().out
