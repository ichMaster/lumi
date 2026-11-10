"""v2.1 (LUMI-210) — update the prod instance to a release tag. Moves code, never data or config.

The prod home (see docs/PROD_SETUP.md)::

    ~/lumi/prod/
      app/        a git checkout at a release tag (its uv venv at app/.venv)
      settings.yaml + secrets.yaml   (v2.9; .env before) instance.home, instance.env, the keys — never edited here
      data/       her memory — only ever READ here (into the backup tarball)
      backups/    data-<YYYYmmdd-HHMM>-<old-tag>.tar.gz, one per update

Steps: refuse while prod processes run (the SQLite store is in WAL mode — a live backup isn't safe) →
fetch tags + validate the target → tar-backup data/ → checkout the tag → ``uv sync --frozen
--all-extras`` → print the running tag → warn about settings that are NEW in this release's example
files (``settings.example.yaml`` / ``secrets.example.yaml``; ``.env.example`` before v2.9) and not set by the
instance (its ``settings.yaml`` / ``secrets.yaml`` / ``.env`` — names only, the files are never touched).

Usage:
    python3 scripts/prod_update.py --tag v2.1.0 [--home ~/lumi/prod] [--dry-run]

Stdlib only, so any ``python3`` runs it (the venv may be mid-rebuild). Rollback = run it again with the
previous tag, then restore the matching backup (docs/PROD_SETUP.md §Rollback).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

DEFAULT_HOME = Path("~/lumi/prod")
_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")
_KEY_RE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=")
_ENV_TOKEN_RE = re.compile(r"·\s*env\s+([A-Z][A-Z0-9_]*)")  # the generated YAML examples' `· env NAME` comments
_GROUP_RE = re.compile(r"^([a-z][a-z0-9_]*):\s*$")
_EXAMPLE_KEY_RE = re.compile(r"^\s+#\s+([a-z][a-z0-9_]*):")
_YAML_KEY_RE = re.compile(r"^\s+([a-z][a-z0-9_]*):")
EXAMPLE_FILES = ("settings.example.yaml", "secrets.example.yaml")
INSTANCE_YAML = ("settings.yaml", "secrets.yaml")


# --- pure helpers (unit-tested) ---------------------------------------------------------------------


def valid_tag(tag: str) -> bool:
    """A release tag: ``vA.B.C`` with an optional pre-release suffix (``v2.1.0-rc1``)."""
    return bool(_TAG_RE.match(tag or ""))


def backup_name(now: datetime, old_tag: str | None) -> str:
    """``data-<YYYYmmdd-HHMM>-<old-tag>.tar.gz`` — sortable by time, labelled with what it was taken from."""
    return f"data-{now:%Y%m%d-%H%M}-{old_tag or 'untagged'}.tar.gz"


def env_keys(text: str) -> set[str]:
    """Every variable NAME an example documents: a dotenv's ``KEY=`` / ``# KEY=`` lines, or (v2.9) the
    ``· env NAME`` comments of the generated YAML examples."""
    names = {m.group(1) for line in text.splitlines() if (m := _KEY_RE.match(line))}
    return names | set(_ENV_TOKEN_RE.findall(text))


def example_aliases(text: str) -> dict[str, str]:
    """``group.key`` → env NAME, read from a generated YAML example (each key line follows its comment)."""
    out: dict[str, str] = {}
    group, alias = "", None
    for line in text.splitlines():
        if m := _GROUP_RE.match(line):
            group, alias = m.group(1), None
        elif m := _ENV_TOKEN_RE.search(line):
            alias = m.group(1)
        elif (m := _EXAMPLE_KEY_RE.match(line)) and alias and group:
            out[f"{group}.{m.group(1)}"] = alias
            alias = None
    return out


def yaml_set_keys(text: str, aliases: dict[str, str]) -> set[str]:
    """The env NAMEs an instance's ``settings.yaml`` / ``secrets.yaml`` sets (uncommented ``group:`` → ``key:``)."""
    out: set[str] = set()
    group = ""
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if m := _GROUP_RE.match(line):
            group = m.group(1)
        elif (m := _YAML_KEY_RE.match(line)) and group and f"{group}.{m.group(1)}" in aliases:
            out.add(aliases[f"{group}.{m.group(1)}"])
    return out


def set_keys(text: str) -> set[str]:
    """Only the keys actually SET (uncommented) in a dotenv text — what the prod ``.env`` defines."""
    return {m.group(1) for line in text.splitlines() if (m := _KEY_RE.match(line)) and not line.lstrip().startswith("#")}


def new_unset_keys(old_example: str, new_example: str, prod_env: str) -> list[str]:
    """Names this release ADDS to its examples that the instance doesn't set — the "check these" list.
    ``prod_env`` is the instance's set names as text (its ``.env``) or already-known names joined by newlines."""
    return sorted(env_keys(new_example) - env_keys(old_example) - set_keys(prod_env))


def prod_processes(ps_lines: list[str], home: Path, cwd_pids: set[str] = frozenset()) -> list[str]:
    """The ``ps`` lines of prod processes: running prod's venv python by path, OR working inside
    ``<home>/app`` (``cwd_pids``). The cwd signal is the one that matters — under ``uv run`` the child
    python shows up in ``ps`` as the resolved framework interpreter (``…/Python.app/…``), not the venv path.
    """
    needles = {f"{home}/app/.venv/bin/python", f"{home.resolve()}/app/.venv/bin/python"}
    hits = []
    for line in ps_lines:
        pid, _, command = line.strip().partition(" ")
        in_app = pid in cwd_pids and _is_lumi_command(command)  # a shell sitting in app/ isn't prod
        if in_app or any(n in line for n in needles):
            hits.append(line.strip())
    return hits


def _is_lumi_command(command: str) -> bool:
    """A process that can hold her data: a python interpreter or ``uv`` — not a shell/editor in app/."""
    tokens = command.split()
    if not tokens:
        return False
    first = tokens[0].rsplit("/", 1)[-1]
    return first in {"uv", "uvx"} or "python" in first.lower() or first == "Python"


def pids_working_in(lsof_out: str, app: Path) -> set[str]:
    """PIDs whose current directory is ``app`` or below, from ``lsof -d cwd -Fpn`` output."""
    roots = {str(app), str(app.resolve())}
    pids: set[str] = set()
    pid = None
    for line in lsof_out.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("n") and pid is not None:
            path = line[1:]
            if any(path == r or path.startswith(r + "/") for r in roots):
                pids.add(pid)
    return pids


# --- glue -----------------------------------------------------------------------------------------


def _git(app: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(app), *args], capture_output=True, text=True, check=check)


def _current_tag(app: Path) -> str | None:
    r = _git(app, "describe", "--tags", "--exact-match", "HEAD", check=False)
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _die(msg: str) -> None:
    raise SystemExit(f"prod_update: {msg}")


def _find_uv() -> str | None:
    """``uv`` on PATH, else the official installer's location (``~/.local/bin/uv``)."""
    found = shutil.which("uv")
    if found:
        return found
    fallback = Path("~/.local/bin/uv").expanduser()
    return str(fallback) if fallback.exists() else None


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Update the Lumi prod instance to a release tag (code only).")
    ap.add_argument("--tag", required=True, help="the release tag to run, e.g. v2.1.0")
    ap.add_argument("--home", default=str(DEFAULT_HOME), help="the prod home (default ~/lumi/prod)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    args = ap.parse_args(argv)

    home = Path(args.home).expanduser()
    app, data, backups, env_file = home / "app", home / "data", home / "backups", home / ".env"
    if not valid_tag(args.tag):
        _die(f"'{args.tag}' is not a release tag (expected vA.B.C or vA.B.C-suffix)")
    if not (app / ".git").exists():
        _die(f"{app} is not a git checkout — see docs/PROD_SETUP.md §Install")
    uv = _find_uv()
    if uv is None and not args.dry_run:  # checked BEFORE any change, never after a checkout
        _die("uv not found (PATH or ~/.local/bin/uv) — install it first; nothing changed")

    # 1. never back up a live WAL store
    ps = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout.splitlines()
    lsof = subprocess.run(["lsof", "-d", "cwd", "-Fpn"], capture_output=True, text=True).stdout
    running = prod_processes(ps, home, pids_working_in(lsof, app))
    if running:
        _die("prod is running — stop the TUI and the daemons first:\n  " + "\n  ".join(running))

    old_tag = _current_tag(app)
    plan = [
        f"fetch tags into {app} and verify {args.tag}",
        f"backup {data} → {backups / backup_name(datetime.now(), old_tag)}",
        f"checkout {args.tag} (was {old_tag or 'untagged'})",
        "uv sync --frozen --all-extras",
        "warn about settings new in this release",
    ]
    if args.dry_run:
        print("prod_update (dry run):\n  " + "\n  ".join(f"{i}. {s}" for i, s in enumerate(plan, 1)))
        return

    # 2. the target tag must exist before anything changes
    _git(app, "fetch", "--tags", "--quiet")
    if _git(app, "rev-parse", "--verify", "--quiet", f"refs/tags/{args.tag}", check=False).returncode != 0:
        _die(f"unknown tag {args.tag} — nothing changed")
    old_example = "\n".join(_git(app, "show", f"HEAD:{name}", check=False).stdout
                            for name in (*EXAMPLE_FILES, ".env.example"))

    # 3. backup (data/ is only read)
    if data.exists():
        backups.mkdir(parents=True, exist_ok=True)
        target = backups / backup_name(datetime.now(), old_tag)
        print(f"backing up {data} → {target} …")
        with tarfile.open(target, "w:gz") as tar:
            tar.add(data, arcname="data")
    else:
        print(f"no {data} yet — nothing to back up")

    # 4–5. code
    r = _git(app, "checkout", "--quiet", args.tag, check=False)
    if r.returncode != 0:
        _die(f"checkout {args.tag} failed (data untouched, backup kept):\n{r.stderr.strip()}")
    subprocess.run([uv, "sync", "--frozen", "--all-extras"], cwd=app, check=True)

    # 6. report
    print(f"prod now runs {_current_tag(app) or 'an UNTAGGED commit (?)'}")

    # 7. config is the owner's: only point at what's new
    new_example = "\n".join((app / name).read_text(encoding="utf-8")
                            for name in (*EXAMPLE_FILES, ".env.example") if (app / name).exists())
    prod_env = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    aliases = example_aliases(new_example)
    for name in INSTANCE_YAML:  # v2.9: the instance's YAML files, as KEY= lines for set_keys
        if (home / name).exists():
            prod_env += "".join(f"\n{n}=" for n in yaml_set_keys((home / name).read_text(encoding="utf-8"), aliases))
    if not env_file.exists() and not any((home / name).exists() for name in INSTANCE_YAML):
        print(f"WARNING: no settings in {home} — prod needs instance.home, instance.env=prod and the keys")
    fresh = new_unset_keys(old_example, new_example, prod_env)
    if fresh:
        print("New in this release's settings examples and not set by prod (add the ones you need):")
        print("  " + ", ".join(fresh))


if __name__ == "__main__":
    sys.exit(main())
