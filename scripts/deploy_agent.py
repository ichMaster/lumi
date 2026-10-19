#!/usr/bin/env python3
"""v2.7 (LUMI-235) — the pull-based deploy agent: code reaches the Linux box by pipeline, not by hand.

The repo is public, so nothing reaches *into* the box (no self-hosted runner, no deploy secret in GitHub): this agent,
run every few minutes by a systemd user timer on the box, looks at GitHub itself.

- **dev** follows ``main``: a new commit whose CI checks are all green (and that is ahead of what dev runs) is
  checked out in ``~/lumi/dev/app``, synced, restarted and smoke-checked; a failed smoke rolls dev back to the
  previous commit and marks the bad one.
- **prod** follows release tags, **only after the owner's confirmation**: the newest ``vA.B.C`` waits until
  ``confirm vA.B.C``; then the same deploy → smoke → roll-back-on-failure.
- **Deploys move code, never data or settings** — the agent writes only its own state under ``~/lumi/deploy/``.

Commands::

    deploy_agent.py tick                 # the timer's step: dev, then prod
    deploy_agent.py confirm v2.7.0       # the owner's approval for prod (deploys it now)
    deploy_agent.py status               # what runs where, what waits, what failed
    deploy_agent.py rollback dev|prod    # back to the previous commit / tag, one command
    deploy_agent.py install              # copy this agent to ~/lumi/deploy/ + install and start the timer

Stdlib only (the box's system Python runs it); every outside effect goes through an injectable runner, so the
decisions and the deploy sequence are tested without git, systemd, uv or the network.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO = "ichMaster/lumi"
INSTANCES = ("dev", "prod")
HEALTH_WAIT_S = 90.0  # how long a restarted server may take to answer /v1/health
STABLE_S = 60.0  # the services must still be active this long after the restart
_RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

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


def ci_verdict(payload: object) -> str:
    """GitHub's check runs for a commit → ``green`` (all done and passing), ``red``, ``pending`` or ``none``."""
    runs = (payload or {}).get("check_runs") if isinstance(payload, dict) else None
    if not runs:
        return "none"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    if all(r.get("conclusion") in ("success", "skipped", "neutral") for r in runs):
        return "green"
    return "red"


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
                  inactive: list[str], new_errors: int) -> list[str]:
    """Why a deploy is not healthy (``[]`` = healthy): the server answers as this instance and version, its state
    is readable with the instance's token, its services stay active, and no new error was logged since the
    restart. No model is ever called (``/v1/health`` and ``/v1/state`` are free)."""
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
    if inactive:
        reasons.append("not active: " + ", ".join(inactive))
    if new_errors:
        reasons.append(f"{new_errors} new ERROR line(s) in lumi.log since the restart")
    return reasons


# --- the outside world (replaced in tests) ----------------------------------------------------------------------
def _uv() -> str:
    return shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")


def _run(cmd: list, cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _http(url: str, headers: dict) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"User-Agent": "lumi-deploy-agent", **headers})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — GitHub API / the LAN server
            return resp.status, json.loads(resp.read() or b"null")
    except Exception:  # noqa: BLE001 — a failed probe is "no answer", decided by the caller
        return 0, None


# --- the agent --------------------------------------------------------------------------------------------------
class Agent:
    def __init__(self, *, home: Path | None = None, run: Run = _run, http: Http = _http,
                 sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.time,
                 out: Callable[[str], None] = print, systemd_dir: Path | None = None) -> None:
        self.home = Path(home or Path.home() / "lumi")
        self.dir = self.home / "deploy"
        self.systemd_dir = Path(systemd_dir or Path.home() / ".config" / "systemd" / "user")
        self._run, self._http, self._sleep, self._now, self._out = run, http, sleep, now, out

    # paths + state
    def app(self, instance: str) -> Path:
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
        """Fill in what each instance runs today the first time the agent sees it (no deploy)."""
        dev = state.setdefault("dev", {})
        if not dev.get("current"):
            rc, sha = self.git("dev", "rev-parse", "HEAD")
            if rc == 0:
                dev["current"] = sha.strip()
        prod = state.setdefault("prod", {})
        if not prod.get("current"):
            rc, tag = self.git("prod", "describe", "--tags", "--exact-match")
            if rc == 0:
                prod["current"] = tag.strip()
        return state

    # the deploy step
    def _switch(self, instance: str, ref: str) -> bool:
        """Check out ``ref`` and restart the instance's server (its Telegram daemons follow, ``PartOf``)."""
        for cmd, cwd in (
            (["git", "-C", str(self.app(instance)), "checkout", "--detach", ref], None),
            ([_uv(), "sync", "--frozen", "--all-extras"], self.app(instance)),
            (["systemctl", "--user", "restart", f"lumi-server@{instance}"], None),
        ):
            rc, out = self._run(cmd, cwd)
            if rc != 0:
                self.log(f"{instance}: `{' '.join(cmd)}` failed: {out[-300:]}")
                return False
        return True

    def _smoke(self, instance: str, expected_version: str, since: str) -> list[str]:
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
        self._sleep(STABLE_S)  # the services must stay up, not just start
        inactive = []
        for unit in (f"lumi-server@{instance}", f"lumi-telegram-inbound@{instance}",
                     f"lumi-telegram-outbound@{instance}"):
            rc, enabled = self._run(["systemctl", "--user", "is-enabled", unit], None)
            if rc != 0 or enabled.strip() not in ("enabled", "static", "linked"):
                continue  # a unit this instance doesn't use
            rc, active = self._run(["systemctl", "--user", "is-active", unit], None)
            if active.strip() != "active":
                inactive.append(unit)
        try:
            log_text = (self.home / instance / "data" / "lumi.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
        return smoke_verdict(instance, health, expected_version, state_status, inactive,
                             errors_since(log_text, since))

    def _expected_version(self, instance: str, ref: str) -> str:
        if instance == "prod":
            return ref.lstrip("v")
        rc, version = self.git(instance, "show", f"{ref}:VERSION")
        return version.strip() if rc == 0 else ""

    def deploy(self, state: dict, instance: str, ref: str) -> bool:
        """Deploy ``ref`` to ``instance`` → smoke → keep it, or roll back to the previous ref and mark it bad."""
        side = state.setdefault(instance, {})
        previous = side.get("current")
        since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._now()))
        self.log(f"{instance}: deploying {ref} (was {previous})")
        reasons = (self._smoke(instance, self._expected_version(instance, ref), since)
                   if self._switch(instance, ref) else ["the switch failed"])
        if not reasons:
            side.update(current=ref, previous=previous)
            state.setdefault("last", {})[instance] = {"ref": ref, "result": "ok", "at": since}
            self.log(f"{instance}: {ref} is live — the smoke is green")
            return True
        self.log(f"{instance}: {ref} failed the smoke — {'; '.join(reasons)} — rolling back to {previous}")
        side.setdefault("bad", []).append(ref)
        state.setdefault("last", {})[instance] = {"ref": ref, "result": "rolled back: " + "; ".join(reasons),
                                                  "at": since}
        if previous:
            self._switch(instance, previous)
        return False

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
        self._prod(state)
        self.save(state)

    def rollback(self, instance: str) -> bool:
        state = self._seed(self.load())
        side = state.setdefault(instance, {})
        previous, current = side.get("previous"), side.get("current")
        if not previous:
            self.log(f"{instance}: nothing to roll back to")
            return False
        since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._now()))
        self.log(f"{instance}: rolling back {current} → {previous} (by hand)")
        reasons = (self._smoke(instance, self._expected_version(instance, previous), since)
                   if self._switch(instance, previous) else ["the switch failed"])
        side.update(current=previous, previous=current)
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
            lines.append(f"{instance}: {side.get('current') or '?'} (previous {side.get('previous') or '—'})"
                         + (f" · waiting: {side['waiting']}" if side.get("waiting") else "")
                         + (f" · bad: {', '.join(side['bad'])}" if side.get("bad") else ""))
            if (last := state.get("last", {}).get(instance)):
                lines.append(f"  last deploy {last['ref']} at {last['at']}: {last['result']}")
        return "\n".join(lines)

    def install(self, source: Path) -> None:
        """Copy this agent out of the checkout it will update, install the timer, start it, seed the state."""
        self.dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, self.dir / "deploy_agent.py")
        units = source.resolve().parents[1] / "deploy" / "systemd"
        self.systemd_dir.mkdir(parents=True, exist_ok=True)
        for name in ("lumi-deploy.service", "lumi-deploy.timer"):
            shutil.copy2(units / name, self.systemd_dir / name)
        for cmd in (["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", "--now", "lumi-deploy.timer"]):
            rc, out = self._run(cmd, None)
            if rc != 0:
                raise SystemExit(f"`{' '.join(cmd)}` failed: {out}")
        self.save(self._seed(self.load()))
        self.log("installed — the timer ticks every 5 minutes; `deploy_agent.py status` shows what runs")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="deploy_agent.py", description="Lumi's pull-based deploy agent (v2.7).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tick", help="the timer's step: dev from main, prod from a confirmed tag")
    confirm = sub.add_parser("confirm", help="approve a release tag for prod (deploys it now)")
    confirm.add_argument("tag")
    sub.add_parser("status", help="what runs where, what waits, what failed")
    rollback = sub.add_parser("rollback", help="back to the previous commit / tag")
    rollback.add_argument("instance", choices=INSTANCES)
    sub.add_parser("install", help="copy the agent to ~/lumi/deploy/ and start its timer")
    args = p.parse_args(argv)
    agent = Agent()
    lock = agent.dir / "agent.lock"
    agent.dir.mkdir(parents=True, exist_ok=True)
    import fcntl  # POSIX only — the agent runs on the Linux box

    with lock.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("another deploy step is running — try again in a minute")
            return 1
        if args.cmd == "tick":
            agent.tick()
        elif args.cmd == "confirm":
            agent.confirm(args.tag)
        elif args.cmd == "status":
            print(agent.status())
        elif args.cmd == "rollback":
            return 0 if agent.rollback(args.instance) else 1
        elif args.cmd == "install":
            agent.install(Path(__file__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
