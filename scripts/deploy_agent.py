#!/usr/bin/env python3
"""v2.7 (LUMI-235) — the pull-based deploy agent: code reaches the Linux box by pipeline, not by hand.
v2.8 (LUMI-241) — code arrives as **images**: each instance is a compose project (``deploy/compose/``).

The repo is public, so nothing reaches *into* the box (no self-hosted runner, no deploy secret in GitHub): this agent,
run every few minutes by a systemd user timer on the box, looks at GitHub itself.

- **dev** follows ``main``: a new commit whose CI checks are all green (the image build among them) and that is
  ahead of what dev runs is deployed as its image ``sha-<short>`` (``docker compose pull`` + ``up -d``) and
  smoke-checked; a failed smoke goes back to what ran before and marks the bad commit.
- **prod** follows release tags, **only after the owner's confirmation**: the newest ``vA.B.C`` waits until
  ``confirm vA.B.C``; then its image is pulled, the server stopped and her data archived
  (``~/lumi/prod/backups/``, the ``prod_update.py`` naming), and the same deploy → smoke → roll-back-on-failure.
- **The v2.6 units are retired** by an instance's first container deploy (stopped + disabled, never deleted). Until
  that deploy is superseded the way back is those units — their checkout still holds the code they ran, and no
  image exists for a commit or tag older than the image workflow.
- **Deploys move code, never data or settings** — the agent writes only its own state under ``~/lumi/deploy/`` and
  the prod archives beside the data root (``data/`` is only read, into the archive).

Commands::

    deploy_agent.py tick                 # the timer's step: dev, then prod
    deploy_agent.py confirm v2.7.0       # the owner's approval for prod (deploys it now)
    deploy_agent.py status               # what runs where, what waits, what failed
    deploy_agent.py rollback dev|prod    # back to what ran before (the previous image, or the units), one command
    deploy_agent.py install              # copy this agent + the compose files to ~/lumi/deploy/, start the timer

Stdlib only (the box's system Python runs it); every outside effect goes through an injectable runner, so the
decisions and the deploy sequence are tested without git, Docker, systemd or the network.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO = "ichMaster/lumi"
IMAGE = "ghcr.io/ichmaster/lumi"  # deploy/compose/compose.yaml pulls it with the ref's tag
INSTANCES = ("dev", "prod")
HEALTH_WAIT_S = 90.0  # how long a restarted server may take to answer /v1/health
STABLE_S = 60.0  # the services must still be active this long after the restart
_RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
SERVICES = ("server", "telegram-inbound", "telegram-outbound")  # deploy/compose/compose.yaml
# One line per container: its compose service, id, state and restart count (a restart keeps the id, bumps the count).
_INSPECT = '{{index .Config.Labels "com.docker.compose.service"}} {{.Id}} {{.State.Status}} {{.RestartCount}}'

Run = Callable[[list, Path | None], tuple[int, str]]
Http = Callable[[str, dict], tuple[int, object]]


# --- pure decisions ---------------------------------------------------------------------------------------------
def parse_env(text: str) -> dict[str, str]:
    """``KEY=VALUE`` lines of an instance ``.env`` (comments, blanks and inline ``# …`` dropped; quotes stripped)."""
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.sub(r"\s+#.*$", "", value).strip().strip("'\"")
        env[key.strip()] = value
    return env


_PASSED = ("success", "skipped", "neutral")


def ci_verdict(payload: object) -> str:
    """GitHub's check runs for a commit → ``green``, ``red``, ``pending`` or ``none``.

    A commit collects runs from every ref it was pushed to (``v2-dev``, then ``main``, a tag), and a newer push to a
    ref cancels that ref's run in progress — so each check (by name) is judged on its own (v2.8 review #1): any
    failure → red; a run still going → pending; otherwise green if one run of it passed (a cancelled twin doesn't
    count against it); only cancelled → red."""
    runs = (payload or {}).get("check_runs") if isinstance(payload, dict) else None
    if not runs:
        return "none"
    by_name: dict[str, list[dict]] = {}
    for run in runs:
        by_name.setdefault(str(run.get("name")), []).append(run)
    verdicts = set()
    for same in by_name.values():
        done = [r.get("conclusion") for r in same if r.get("status") == "completed"]
        if any(c not in _PASSED and c != "cancelled" for c in done):
            verdicts.add("red")
        elif len(done) < len(same):
            verdicts.add("pending")
        else:
            verdicts.add("green" if any(c in _PASSED for c in done) else "red")
    return "red" if "red" in verdicts else "pending" if "pending" in verdicts else "green"


def image_tag(ref: str) -> str:
    """The image a git ref runs as — CI's tags: a release tag is its own tag, a commit is ``sha-<7 chars>``."""
    return ref if _RELEASE.match(ref) else f"sha-{ref[:7]}"


def _units(instance: str) -> list[str]:
    """The instance's v2.6 systemd units — the server first."""
    return [f"lumi-server@{instance}", f"lumi-telegram-inbound@{instance}", f"lumi-telegram-outbound@{instance}"]


def release_tags(tags: list[str]) -> list[str]:
    """The ``vA.B.C`` release tags (no ``-rc``), oldest first."""
    found = [(tuple(int(n) for n in m.groups()), t) for t in tags if (m := _RELEASE.match(t.strip()))]
    return [t for _, t in sorted(found)]


def dev_action(state: dict, main_sha: str, ci: str, ahead: bool) -> str | None:
    """The ``main`` commit to deploy to dev now, or ``None``: only a new, green commit that is ahead of what dev
    runs (never an older ``main``), and never one already rolled back."""
    dev = state.get("dev", {})
    if not main_sha or main_sha == dev.get("current") or main_sha in dev.get("bad", []):
        return None
    if ci != "green" or not ahead:
        return None
    return main_sha


def prod_action(state: dict, tags: list[str]) -> tuple[str, str | None]:
    """``("deploy", tag)`` when the newest release is confirmed and not yet live, ``("wait", tag)`` when it awaits
    the owner's ``confirm``, ``("none", None)`` otherwise."""
    prod = state.get("prod", {})
    releases = release_tags(tags)
    if not releases:
        return "none", None
    latest = releases[-1]
    if latest == prod.get("current") or latest in prod.get("bad", []):
        return "none", None
    if prod.get("current") and release_tags([prod["current"], latest])[-1] != latest:
        return "none", None  # never "promote" to an older tag
    if prod.get("confirmed") != latest:
        return "wait", latest
    return "deploy", latest


def errors_since(log_text: str, since: str) -> int:
    """``ERROR`` lines in ``lumi.log`` stamped at or after ``since`` (``YYYY-mm-dd HH:MM:SS``)."""
    count = 0
    for line in log_text.splitlines():
        m = _LOG_TS.match(line)
        if m and m.group(1) >= since and " ERROR " in line:
            count += 1
    return count


def smoke_verdict(instance: str, health: object, expected_version: str, state_status: int,
                  unsteady: list[str], new_errors: int) -> list[str]:
    """Why a deploy is not healthy (``[]`` = healthy): the server answers as this instance and version — at the
    start and again a minute later — its state is readable with the instance's token, its services stay up the whole
    minute (``unsteady``: not active, or restarted meanwhile), and no new error was logged since the start. No model
    is ever called (``/v1/health`` and ``/v1/state`` are free)."""
    reasons: list[str] = []
    if not isinstance(health, dict) or not health.get("ok"):
        reasons.append("the server does not answer /v1/health")
    else:
        if health.get("env") != instance:
            reasons.append(f"the server says env={health.get('env')!r}, not {instance!r}")
        if expected_version and health.get("version") != expected_version:
            reasons.append(f"the server runs {health.get('version')!r}, not {expected_version!r}")
        if state_status != 200:
            reasons.append(f"/v1/state answered {state_status or 'nothing'}")
    if unsteady:
        reasons.append("not up the whole minute: " + ", ".join(unsteady))
    if new_errors:
        reasons.append(f"{new_errors} new ERROR line(s) in lumi.log since the restart")
    return reasons


# --- the outside world (replaced in tests) ----------------------------------------------------------------------
def _run(cmd: list, cwd: Path | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after 900 s: {' '.join(map(str, cmd))}"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _say(line: str) -> None:
    """Print for whoever asked — never fail when they're gone (review #3: a dropped ssh session closes stdout
    mid-deploy). ``deploy.log`` stays the record."""
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass


def _http(url: str, headers: dict) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"User-Agent": "lumi-deploy-agent", **headers})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — GitHub API / the LAN server
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:  # an answer, just not a 2xx — keep its status (review #5: a 401)
        return exc.code, None
    except Exception:  # noqa: BLE001 — a failed probe is "no answer", decided by the caller
        return 0, None


# --- the agent --------------------------------------------------------------------------------------------------
class Agent:
    def __init__(self, *, home: Path | None = None, run: Run = _run, http: Http = _http,
                 sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.time,
                 out: Callable[[str], None] = _say, systemd_dir: Path | None = None) -> None:
        self.home = Path(home or Path.home() / "lumi")
        self.dir = self.home / "deploy"
        self.systemd_dir = Path(systemd_dir or Path.home() / ".config" / "systemd" / "user")
        self._run, self._http, self._sleep, self._now, self._out = run, http, sleep, now, out

    # paths + state
    def app(self, instance: str) -> Path:
        """The instance's git checkout — read for refs (``merge-base``, tags, ``VERSION``), run only by the v2.6
        units (the way back from a first container deploy)."""
        return self.home / instance / "app"

    def _state_path(self) -> Path:
        return self.dir / "state.json"

    def load(self) -> dict:
        try:
            return json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self, state: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._state_path())

    def log(self, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._now()))} {message}"
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "deploy.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        self._out(line)

    def git(self, instance: str, *args: str) -> tuple[int, str]:
        return self._run(["git", "-C", str(self.app(instance)), *args], None)

    def _env(self, instance: str) -> dict[str, str]:
        try:
            return parse_env((self.home / instance / ".env").read_text(encoding="utf-8"))
        except OSError:
            return {}

    def _seed(self, state: dict) -> dict:
        """Fill in what each instance runs: its ref the first time the agent sees it (from its checkout — no
        deploy), and on **every** step whether it runs as the v2.6 units or as containers, read from systemd (review
        #2: the owner may go back to the units by hand; a recorded value would then send the next deploy into the
        port the units hold)."""
        for instance in INSTANCES:
            side = state.setdefault(instance, {})
            if not side.get("current"):
                rc, ref = (self.git("dev", "rev-parse", "HEAD") if instance == "dev"
                           else self.git("prod", "describe", "--tags", "--exact-match"))
                if rc == 0:
                    side["current"] = ref.strip()
            side["runs"] = "units" if self._enabled(_units(instance)[0]) else "containers"
        return state

    # the outside world, one command at a time
    def _do(self, instance: str, cmd: list) -> bool:
        rc, out = self._run(cmd, None)
        if rc != 0:
            self.log(f"{instance}: `{' '.join(map(str, cmd))}` failed: {out[-300:]}")
        return rc == 0

    def _compose(self, instance: str, ref: str, *args: str) -> list:
        """``docker compose`` for this instance's project, its image pinned to ``ref``'s tag (the compose file has
        no default tag, so every call names one)."""
        compose = self.dir / "compose"
        return ["env", f"LUMI_IMAGE_TAG={image_tag(ref)}", "docker", "compose",
                "--env-file", str(compose / f"{instance}.env"), "-f", str(compose / "compose.yaml"), *args]

    def _enabled(self, unit: str) -> bool:
        rc, out = self._run(["systemctl", "--user", "is-enabled", unit], None)
        return rc == 0 and out.strip() in ("enabled", "static", "linked")

    def _stamp(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._now()))

    # the switch
    def _to_containers(self, instance: str, side: dict, ref: str) -> bool:
        """Run ``ref``'s image. The first time, the v2.6 units are stopped and disabled before any container starts
        (the port and the brain lock are then free); later, ``up -d`` recreates each container — the old one stops
        (SIGTERM → her session close inside the old image) before the new one starts, so code never changes under
        a running server."""
        if side.get("runs") != "containers":
            if not self._do(instance, ["systemctl", "--user", "disable", "--now", *_units(instance)]):
                return False
            side["runs"] = "containers"
            self.log(f"{instance}: the v2.6 units are stopped and disabled — the containers take over")
        return self._do(instance, self._compose(instance, ref, "up", "-d", "--remove-orphans"))

    def _to_units(self, instance: str, side: dict, leaving: str) -> bool:
        """Back to the v2.6 units — the way back from a first container deploy: the containers go, the units start
        on their checkout, which still holds the code they ran."""
        down = self._do(instance, self._compose(instance, leaving, "down"))
        up = self._do(instance, ["systemctl", "--user", "enable", "--now", *_units(instance)])
        side["runs"] = "units"
        return down and up

    def _go(self, instance: str, side: dict, ref: str, runs: str, leaving: str | None) -> bool:
        return (self._to_units(instance, side, leaving or ref) if runs == "units"
                else self._to_containers(instance, side, ref))

    def _go_and_smoke(self, instance: str, side: dict, ref: str, runs: str, leaving: str | None) -> list[str]:
        if not self._go(instance, side, ref, runs, leaving):
            return ["the switch failed"]
        since = self._stamp()  # after the start: the old server's shutdown lines don't count against the new
        return self._smoke(instance, side, ref, self._expected_version(instance, ref), since)

    # the smoke
    def _smoke(self, instance: str, side: dict, ref: str, expected_version: str, since: str) -> list[str]:
        env = self._env(instance)
        host, port = env.get("LUMI_SERVER_HOST", "127.0.0.1"), env.get("LUMI_SERVER_PORT", "8765")
        base = f"http://{host}:{port}/v1"
        waited, health = 0.0, None
        while waited <= HEALTH_WAIT_S:
            status, body = self._http(f"{base}/health", {})
            if status == 200:
                health = body
                break
            self._sleep(5.0)
            waited += 5.0
        token = env.get("LUMI_SERVER_TOKEN", "")  # read here, sent to the instance only, never logged
        state_status = self._http(f"{base}/state", {"Authorization": f"Bearer {token}"})[0] if health else 0
        # Review #2: a crashed service is back within seconds (`Restart=on-failure` / `restart: unless-stopped`), so
        # "running" a minute later proves nothing — what must not change is its identity: the container (id +
        # restart count) or, for a unit, its InvocationID.
        born = self._identities(instance, side, ref)
        self._sleep(STABLE_S)  # the services must stay up, not just start
        now = self._identities(instance, side, ref)
        unsteady = []
        for name, ident in born.items():
            if not ident or not now.get(name):
                unsteady.append(f"{name} (not running)")
            elif now[name] != ident:
                unsteady.append(f"{name} (restarted)")
        if health is not None and self._http(f"{base}/health", {})[0] != 200:
            health = None  # it answered at the start, not a minute later
        try:
            log_text = (self.home / instance / "data" / "lumi.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
        return smoke_verdict(instance, health, expected_version, state_status, unsteady,
                             errors_since(log_text, since))

    def _identities(self, instance: str, side: dict, ref: str) -> dict[str, str]:
        """Each service that should run → who runs it now (``""`` when nothing does)."""
        if side.get("runs") == "units":
            names = [u for u in _units(instance) if self._enabled(u)]
            found = {}
            for unit in names:
                _, active = self._run(["systemctl", "--user", "is-active", unit], None)
                ident = self._run(["systemctl", "--user", "show", "-p", "InvocationID", "--value", unit], None)[1]
                found[unit] = ident.strip() if active.strip() == "active" else ""
            return found
        found = dict.fromkeys(SERVICES, "")
        rc, ids = self._run(self._compose(instance, ref, "ps", "-a", "-q"), None)
        if rc != 0 or not ids.split():
            return found
        rc, out = self._run(["docker", "inspect", "--format", _INSPECT, *ids.split()], None)
        for line in out.splitlines() if rc == 0 else []:
            parts = line.split()
            if len(parts) == 4 and parts[0] in found and parts[2] == "running":
                found[parts[0]] = f"{parts[1]}/{parts[3]}"  # the container id / its restart count
        return found

    def _expected_version(self, instance: str, ref: str) -> str:
        if instance == "prod":
            return ref.lstrip("v")
        rc, version = self.git(instance, "show", f"{ref}:VERSION")
        return version.strip() if rc == 0 else ""

    # the archive
    def _backup(self, instance: str, side: dict, running: str | None) -> bool:
        """Stop the server (her session closes; nothing writes ``data/`` any more), then archive ``data/`` beside
        it as ``backups/data-<YYYYmmdd-HHMM>-<running tag>.tar.gz`` — the ``prod_update.py`` naming, so a code
        rollback can be paired with her data as it was under that tag. ``data/`` is only read."""
        backups = self.home / instance / "backups"
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(self._now()))
        target = backups / f"data-{stamp}-{running or 'untagged'}.tar.gz"
        backups.mkdir(parents=True, exist_ok=True)
        units = side.get("runs") == "units"
        stop = (["systemctl", "--user", "stop", _units(instance)[0]] if units
                else self._compose(instance, running or "", "stop", "server"))
        start = (["systemctl", "--user", "start", _units(instance)[0]] if units
                 else self._compose(instance, running or "", "start", "server"))
        for cmd in (stop, ["tar", "-czf", str(target), "-C", str(self.home / instance), "data"]):
            rc, out = self._run(cmd, None)
            if rc != 0:
                self.log(f"{instance}: `{' '.join(map(str, cmd))}` failed: {out[-300:]} — not deploying; "
                         "starting it again")
                target.unlink(missing_ok=True)
                self._run(start, None)
                return False
        self.log(f"{instance}: her data is archived → {target}")
        return True

    # a deploy
    def deploy(self, state: dict, instance: str, ref: str) -> bool:
        """Deploy ``ref``'s image to ``instance`` → smoke → keep it, or go back to what ran before (the previous
        image, or the v2.6 units after a first container deploy) and mark ``ref`` bad."""
        side = state.setdefault(instance, {})
        previous, previous_runs = side.get("current"), side.get("runs") or "units"
        started = self._stamp()
        last = state.setdefault("last", {})
        self.log(f"{instance}: deploying {ref} as {image_tag(ref)} (was {previous}, {previous_runs})")
        if not self._do(instance, self._compose(instance, ref, "pull", "--quiet")):
            last[instance] = {"ref": ref, "result": "not deployed: the image pull failed", "at": started}
            return False  # nothing was stopped; the next tick tries again
        if instance == "prod" and not self._backup(instance, side, previous):
            side.pop("confirmed", None)  # never stop prod again every 5 minutes — a fresh `confirm` retries
            last[instance] = {"ref": ref, "result": "not deployed: the data archive failed — fix it, then confirm "
                              "again", "at": started}
            return False
        reasons = self._go_and_smoke(instance, side, ref, "containers", previous)
        if not reasons:
            side.update(current=ref, previous=previous, previous_runs=previous_runs)
            last[instance] = {"ref": ref, "result": "ok", "at": started}
            self.log(f"{instance}: {ref} is live — the smoke is green")
            self._prune(state)
            return True
        self.log(f"{instance}: {ref} failed the smoke — {'; '.join(reasons)} — back to {previous} ({previous_runs})")
        side.setdefault("bad", []).append(ref)
        last[instance] = {"ref": ref, "result": "rolled back: " + "; ".join(reasons), "at": started}
        if previous:
            self._go(instance, side, previous, previous_runs, ref)
        return False

    def _prune(self, state: dict) -> None:
        """Remove the Lumi images no instance runs or would roll back to (v2.8 review #3 — each new commit leaves
        ~240 MB of layers). Kept: each instance's current and previous image. GHCR keeps every tag, and Docker
        refuses to remove an image a container still uses."""
        keep = {image_tag(ref) for instance in INSTANCES for ref in
                (state.get(instance, {}).get("current"), state.get(instance, {}).get("previous")) if ref}
        rc, out = self._run(["docker", "images", IMAGE, "--format", "{{.Tag}}"], None)
        stale = sorted({tag for tag in out.split() if tag != "<none>" and tag not in keep}) if rc == 0 else []
        if stale:
            rc, _ = self._run(["docker", "image", "rm", *[f"{IMAGE}:{tag}" for tag in stale]], None)
            self.log(f"superseded images {'removed' if rc == 0 else 'removed where unused'}: {', '.join(stale)}")

    # the commands
    def tick(self) -> None:
        state = self._seed(self.load())
        self._dev(state)
        self._prod(state)
        self.save(state)

    def _dev(self, state: dict) -> None:
        dev = state.setdefault("dev", {})
        dev.pop("waiting", None)
        if self.git("dev", "fetch", "--quiet", "--tags", "origin")[0] != 0:
            return
        rc, main_sha = self.git("dev", "rev-parse", "origin/main")
        main_sha, current = (main_sha.strip() if rc == 0 else ""), dev.get("current", "")
        if not main_sha or main_sha == current or main_sha in dev.get("bad", []):
            return
        ahead = not current or self.git("dev", "merge-base", "--is-ancestor", current, main_sha)[0] == 0
        _, payload = self._http(f"https://api.github.com/repos/{REPO}/commits/{main_sha}/check-runs",
                                {"Accept": "application/vnd.github+json"})
        ci = ci_verdict(payload)
        target = dev_action(state, main_sha, ci, ahead)
        if target:
            self.deploy(state, "dev", target)
        else:
            dev["waiting"] = f"main {main_sha[:12]} ({'CI ' + ci if ci != 'green' else 'behind what dev runs'})"

    def _prod(self, state: dict) -> None:
        if self.git("prod", "fetch", "--quiet", "--tags", "origin")[0] != 0:
            return
        _, tags = self.git("prod", "tag", "--list", "v*")
        action, tag = prod_action(state, tags.split())
        prod = state.setdefault("prod", {})
        if action == "wait":
            if prod.get("waiting") != tag:
                self.log(f"prod: {tag} is released — waiting for `deploy_agent.py confirm {tag}`")
            prod["waiting"] = tag
            return
        prod.pop("waiting", None)
        if action == "deploy" and tag:
            self.deploy(state, "prod", tag)

    def confirm(self, tag: str) -> None:
        state = self._seed(self.load())
        prod = state.setdefault("prod", {})
        prod["confirmed"] = tag.strip()
        prod["bad"] = [t for t in prod.get("bad", []) if t != tag.strip()]  # an explicit re-approval
        self.log(f"prod: the owner confirmed {tag}")
        self.save(state)  # recorded before the deploy starts — whatever happens next, the approval stands
        self._prod(state)
        self.save(state)

    def rollback(self, instance: str) -> bool:
        """Back to what ran before, in one command — the previous image, or the v2.6 units right after the first
        container deploy. It sticks: the rolled-back ref is marked bad (prod's needs a fresh ``confirm``)."""
        state = self._seed(self.load())
        side = state.setdefault(instance, {})
        previous, current = side.get("previous"), side.get("current")
        if not previous:
            self.log(f"{instance}: nothing to roll back to")
            return False
        runs_now, runs_then = side.get("runs") or "units", side.get("previous_runs")
        if not runs_then:
            # A v2.7 record: both refs ran as units, and the v2.8 agent never moves a checkout any more.
            self.log(f"{instance}: {previous} ran before the containers — move the units' checkout by hand "
                     "(docs/LINUX_DEPLOY.md, the way back)")
            return False
        self.log(f"{instance}: rolling back {current} ({runs_now}) → {previous} ({runs_then}) (by hand)")
        reasons = self._go_and_smoke(instance, side, previous, runs_then, current)
        side.update(current=previous, previous=current, previous_runs=runs_now)
        if current:
            side.setdefault("bad", []).append(current)  # the next tick must not redeploy it
        if instance == "prod":
            side["confirmed"] = previous  # the newer tag needs a fresh `confirm`
        self.save(state)
        self.log(f"{instance}: {previous} is live — " + ("the smoke is green" if not reasons
                                                          else "the smoke says: " + "; ".join(reasons)))
        return not reasons

    def status(self) -> str:
        state = self._seed(self.load())
        lines = []
        for instance in INSTANCES:
            side = state.get(instance, {})
            current = side.get("current")
            how = f"image {image_tag(current)}" if side.get("runs") == "containers" and current else "v2.6 units"
            lines.append(f"{instance}: {current or '?'} ({how}; previous {side.get('previous') or '—'})"
                         + (f" · waiting: {side['waiting']}" if side.get("waiting") else "")
                         + (f" · bad: {', '.join(side['bad'])}" if side.get("bad") else ""))
            if (last := state.get("last", {}).get(instance)):
                lines.append(f"  last deploy {last['ref']} at {last['at']}: {last['result']}")
        return "\n".join(lines)

    def install(self, source: Path) -> None:
        """Copy this agent and the compose files out of the checkout they will update, install the timer, start it,
        seed the state."""
        self.dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, self.dir / "deploy_agent.py")
        deploy = source.resolve().parents[1] / "deploy"
        shutil.copytree(deploy / "compose", self.dir / "compose", dirs_exist_ok=True)
        self.systemd_dir.mkdir(parents=True, exist_ok=True)
        for name in ("lumi-deploy.service", "lumi-deploy.timer"):
            shutil.copy2(deploy / "systemd" / name, self.systemd_dir / name)
        for cmd in (["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", "--now", "lumi-deploy.timer"]):
            rc, out = self._run(cmd, None)
            if rc != 0:
                raise SystemExit(f"`{' '.join(cmd)}` failed: {out}")
        self.save(self._seed(self.load()))
        self.log("installed — the timer ticks every 5 minutes; `deploy_agent.py status` shows what runs")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="deploy_agent.py", description="Lumi's pull-based deploy agent (v2.7–v2.8).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tick", help="the timer's step: dev from main, prod from a confirmed tag")
    confirm = sub.add_parser("confirm", help="approve a release tag for prod (deploys it now)")
    confirm.add_argument("tag")
    sub.add_parser("status", help="what runs where, what waits, what failed")
    rollback = sub.add_parser("rollback", help="back to what ran before: the previous image, or the units")
    rollback.add_argument("instance", choices=INSTANCES)
    sub.add_parser("install", help="copy the agent to ~/lumi/deploy/ and start its timer")
    args = p.parse_args(argv)
    # Review #3: `confirm` / `rollback` run over ssh and take minutes; a dropped session sends SIGHUP — the deploy
    # must finish (or roll back) regardless, not die between the stop and the start.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    agent = Agent()
    lock = agent.dir / "agent.lock"
    agent.dir.mkdir(parents=True, exist_ok=True)
    import fcntl  # POSIX only — the agent runs on the Linux box

    with lock.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _say("another deploy step is running — try again in a minute")
            return 1
        if args.cmd == "tick":
            agent.tick()
        elif args.cmd == "confirm":
            agent.confirm(args.tag)
        elif args.cmd == "status":
            _say(agent.status())
        elif args.cmd == "rollback":
            return 0 if agent.rollback(args.instance) else 1
        elif args.cmd == "install":
            agent.install(Path(__file__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
