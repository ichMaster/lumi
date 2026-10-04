"""The v2.1 env guard — the dev tree can never open her real memory; prod runs only released code.

A data root carries an ``env-marker`` naming the instance that owns it (written on the first run with
``LUMI_ENV`` set, never rewritten). Every process entry point that opens the root calls
:func:`guard_entry` before touching it, which refuses — with a readable message, never silently — when:

- the root is marked ``prod`` and ``LUMI_ENV`` is not ``prod`` (incl. unset): dev can't open prod data;
- ``LUMI_ENV=prod`` and the root is marked with another env: prod can't run on dev data;
- ``LUMI_ENV=prod`` and the checkout is dirty or HEAD is not exactly at a tag: prod runs released code.

``LUMI_ALLOW_DIRTY_PROD=1`` bypasses only the clean/tagged check (an emergency hatch, warned on every
start) — never the marker checks. A checkout without git (a future tarball install) counts as released,
with a note. ``LUMI_ENV`` unset + an unmarked root → no effect at all (no marker, no git call).

:func:`check` is the pure decision (plain data in, a :class:`Verdict` out); the rest is thin I/O glue.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MARKER_NAME = "env-marker"
OVERRIDE_ENV = "LUMI_ALLOW_DIRTY_PROD"
_TRUTHY = {"1", "true", "on", "yes"}
_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAX_LISTED = 10  # dirty files named in a refusal before "…"


class EnvGuardRefusal(RuntimeError):
    """The guard refused to open the data root; ``str(exc)`` is the readable, multi-line reason."""


@dataclass(frozen=True)
class GitState:
    """The checkout as the guard sees it. ``available=False`` → no repo / no git (treated as released)."""

    available: bool
    tag: str | None = None  # the tag exactly at HEAD, or None
    dirty: tuple[str, ...] = ()  # tracked files with modifications


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reasons: tuple[str, ...] = ()  # why it refuses (empty when ok)
    notes: tuple[str, ...] = ()  # non-blocking warnings (no git, the override in use)


def check(env: str | None, marker: str | None, git: GitState, *, allow_dirty: bool = False) -> Verdict:
    """The pure decision: may an instance named ``env`` open a root marked ``marker`` from this checkout?"""
    reasons: list[str] = []
    notes: list[str] = []
    if marker == "prod" and env != "prod":
        reasons.append(
            f"this data root belongs to prod, but LUMI_ENV is {env or 'unset'} — "
            "the dev tree must never open her real memory"
        )
    if env == "prod" and marker is not None and marker != "prod":
        reasons.append(f"LUMI_ENV=prod, but this data root is marked '{marker}' — prod must not run on {marker} data")
    if env == "prod":
        if allow_dirty:
            notes.append(f"{OVERRIDE_ENV} is set — the clean/tagged check for prod is bypassed")
        if not git.available:
            notes.append("no git checkout found — the clean/tagged check was skipped")
        else:
            problems: list[str] = []
            if git.tag is None:
                problems.append("HEAD is not at a release tag")
            if git.dirty:
                listed = ", ".join(git.dirty[:_MAX_LISTED]) + (" …" if len(git.dirty) > _MAX_LISTED else "")
                problems.append(f"modified tracked files: {listed}")
            if problems and not allow_dirty:
                reasons.extend(f"prod runs only released code — {p}" for p in problems)
    return Verdict(ok=not reasons, reasons=tuple(reasons), notes=tuple(notes))


def read_marker(root: Path) -> str | None:
    """The env that owns ``root`` (lower-cased), or ``None`` when the root is unmarked/unreadable."""
    try:
        return (root / MARKER_NAME).read_text(encoding="utf-8").strip().lower() or None
    except OSError:
        return None


def write_marker(root: Path, env: str) -> bool:
    """Mark ``root`` as owned by ``env`` — only when no marker exists yet. Returns True if it wrote."""
    path = root / MARKER_NAME
    if path.exists():
        return False
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(env + "\n", encoding="utf-8")
    return True


Runner = Callable[..., Any]


def git_state(repo: Path = _REPO_ROOT, run: Runner = subprocess.run) -> GitState:
    """Ask git whether ``repo`` is clean and exactly at a tag. No repo / no git / git error → unavailable."""
    if not (repo / ".git").exists():
        return GitState(available=False)
    try:
        status = run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=10,
        )
        if status.returncode != 0:
            return GitState(available=False)
        tag = run(
            ["git", "-C", str(repo), "describe", "--tags", "--exact-match", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return GitState(available=False)
    dirty = tuple(line[3:].strip() for line in status.stdout.splitlines() if line.strip())
    exact = tag.stdout.strip() if tag.returncode == 0 else ""
    return GitState(available=True, tag=exact or None, dirty=dirty)


def format_refusal(verdict: Verdict, root: Path) -> str:
    lines = [f"Lumi env guard — refusing to start (data root: {root})"]
    lines += [f"  • {r}" for r in verdict.reasons]
    lines.append(
        "Fix the mismatch (see docs/PROD_SETUP.md). For a prod emergency only, "
        f"{OVERRIDE_ENV}=1 bypasses the clean/tagged check — never the marker checks."
    )
    return "\n".join(lines)


def enforce(
    cfg: Any,
    *,
    repo: Path = _REPO_ROOT,
    run: Runner = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Check ``cfg.env`` against the root's marker (+ git for prod); mark an unmarked root on success.

    Raises :class:`EnvGuardRefusal` on a refusal; returns the non-blocking notes otherwise.
    ``LUMI_ENV`` unset + an unmarked root → returns ``()`` without touching git or the disk.
    """
    env: str | None = getattr(cfg, "env", None)
    root = Path(cfg.data_root)
    marker = read_marker(root)
    if env is None and marker is None:
        return ()
    environ = os.environ if environ is None else environ
    allow_dirty = (environ.get(OVERRIDE_ENV) or "").strip().lower() in _TRUTHY
    git = git_state(repo, run) if env == "prod" else GitState(available=False)
    verdict = check(env, marker, git, allow_dirty=allow_dirty)
    if not verdict.ok:
        raise EnvGuardRefusal(format_refusal(verdict, root))
    if env is not None and marker is None:
        write_marker(root, env)
    return verdict.notes


def guard_entry(cfg: Any) -> tuple[str, ...]:
    """For process entry points: a refusal becomes a readable ``SystemExit``; notes go to stderr."""
    try:
        notes = enforce(cfg)
    except EnvGuardRefusal as exc:
        raise SystemExit(str(exc)) from None
    for note in notes:
        print(f"WARNING (env guard): {note}", file=sys.stderr)
    return notes
