"""The panel's settings editor (v2.11, LUMI-259): an instance's ``settings.yaml`` / ``secrets.yaml`` as a
schema-driven view, and changes to them — validated, previewed as a diff, applied atomically with the previous
files kept in ``config-history/``, rolled back with one call.

**Secrets are write-only.** No secret value leaves this module: a view or a result shows ``set`` / ``unset`` and the
last four characters (:func:`core.settings.last4`), a diff shows a secret as a key-level line, a history manifest
names a secret's key as ``(secret)``. The copies of ``secrets.yaml`` the history keeps (``0600``) are what a rollback
restores — they are read here and never returned.

**An invalid value never reaches a file.** A plan is checked with the same check an instance runs at start
(:func:`core.settings.check_texts`); a plan with a problem can't be applied, and an apply re-reads the files under a
lock and refuses when they changed since the preview (the hashes the preview handed out).
"""

from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from admin.containers import PROD_CONFIRM
from core import settings as st

SETTINGS = st.SETTINGS_FILE
SECRETS = st.SECRETS_FILE
FILES = (SETTINGS, SECRETS)
HISTORY_DIR = "config-history"
HISTORY_KEEP = 200  # the newest entries kept; an older one's manifest + copies are removed
SECRET_SHOWN = "(secret)"
REDACTED = "•••"
# Keys the instance's compose `environment:` sets — a value in settings.yaml would have no effect there.
CONTAINER_SET = {"instance.home": "LUMI_HOME", "server.host": "LUMI_SERVER_HOST", "server.port": "LUMI_SERVER_PORT"}
SERVER_TOKEN_NOTE = ("server.token changed — every client of this instance needs the new value in its own "
                     "secrets.yaml (the Mac's TUI / lumi-cli tree for this instance); the deploy agent and this panel "
                     "read it from the instance folder")
_HEADER = {SETTINGS: "# Lumi settings — edited in the admin panel. Every setting is described in "
                     "settings.example.yaml.\n",
           SECRETS: "# Lumi secrets — edited in the admin panel (write-only). Keep this file 0600.\n"}
_TS = re.compile(r"^(\d{8}-\d{6}Z)(?:-(\d+))?$")
_HASH_KEY = os.urandom(32)  # the hashes a preview hands out are keyed per process: they identify, never reveal
_LOCK = threading.Lock()


class EditError(Exception):
    """A refused edit; the message is readable and never holds a secret value."""


class Stale(EditError):
    """The files changed since the preview."""


def utc_now() -> datetime:
    return datetime.now(UTC)


# --- the files ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Files:
    """The text of an instance's two settings files (``""`` for a missing one)."""

    settings: str
    secrets: str

    def text(self, name: str) -> str:
        return self.settings if name == SETTINGS else self.secrets

    def replace(self, name: str, text: str) -> Files:
        return Files(text, self.secrets) if name == SETTINGS else Files(self.settings, text)


def read_files(root: Path) -> Files:
    def read(name: str) -> str:
        try:
            return (Path(root) / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    return Files(read(SETTINGS), read(SECRETS))


def hashes(files: Files) -> dict[str, str]:
    """A keyed digest per file: equal for equal text, useless for guessing a secret."""
    return {name: hmac.new(_HASH_KEY, files.text(name).encode("utf-8"), hashlib.sha256).hexdigest()
            for name in FILES}


def _parsed(text: str, name: str) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """``(flat settings, structured sections, error)`` of a file's text."""
    try:
        data = st.parse_mapping(text, name)
    except st.SettingsError as exc:
        return {}, {}, str(exc).removeprefix("Lumi settings — ")
    sections = {k: data.pop(k) for k in st.STRUCTURED if k in data}
    return st.flatten(data), sections, None


def _secret_values(*all_files: Files) -> list[str]:
    """Every secret value in the given files (the secrets file, and a secret misplaced in settings.yaml)."""
    out: set[str] = set()
    for files in all_files:
        for name in FILES:
            flat, _, _ = _parsed(files.text(name), name)
            for key, value in flat.items():
                s = st.BY_KEY.get(key)
                if (name == SECRETS or (s is not None and s.secret)) and value not in (None, ""):
                    out.add(str(value))
    return sorted((v for v in out if len(v) >= 4), key=len, reverse=True)


def _redact(text: str, values: list[str]) -> str:
    for value in values:
        text = text.replace(value, REDACTED)
    return text


def _mask(value: Any) -> str:
    if value in (None, ""):
        return "unset"
    tail = st.last4(str(value))
    return f"set {REDACTED}{tail}" if tail else "set"


# --- the view ----------------------------------------------------------------------------------------------------


def view(root: Path) -> dict[str, Any]:
    """Every schema setting by group — its rules, its current value and where it comes from; a secret as
    ``set`` / ``unset`` + last4 only. Plus the files' hashes (for an apply) and their problems."""
    files = read_files(root)
    plain, sections, _ = _parsed(files.settings, SETTINGS)
    secret, _, _ = _parsed(files.secrets, SECRETS)
    problems, warnings = st.check_texts(files.settings, files.secrets)
    values = _secret_values(files)
    groups = []
    for group in st.GROUPS:
        items = []
        for s in st.SETTINGS:
            if s.group != group:
                continue
            item: dict[str, Any] = {"key": s.key, "env": s.alias, "type": s.type, "description": s.description,
                                    "restart": s.restart, "secret": s.secret, "choices": list(s.choices),
                                    "min": s.min, "max": s.max}
            if s.secret:
                value = secret.get(s.key)
                item.update({"set": value not in (None, ""), "last4": st.last4(str(value or "")) or None,
                             "source": SECRETS if value not in (None, "") else "unset"})
            else:
                value = plain.get(s.key)
                default = list(s.default) if isinstance(s.default, tuple) else s.default
                item.update({"default": default, "value": default if value is None else value,
                             "source": "default" if value is None else SETTINGS})
            if s.key in CONTAINER_SET:
                item["container"] = f"set by the container ({CONTAINER_SET[s.key]}) — a file value has no effect"
            items.append(item)
        groups.append({"name": group, "settings": items})
    return {"groups": groups, "sections": {name: sections.get(name) for name in st.STRUCTURED},
            "hashes": hashes(files), "problems": [_redact(p, values) for p in problems],
            "warnings": [_redact(w, values) for w in warnings]}


# --- plans -------------------------------------------------------------------------------------------------------


def changed(old: Files, new: Files) -> dict[str, Any]:
    """What a change does, safe to show and to log: ``{key: [old, new]}`` for a plain setting, ``{key: "(secret)"}``
    for a secret (or anything in secrets.yaml), ``{section: "(section edited)"}`` for ``schedule`` / ``models``."""
    out: dict[str, Any] = {}
    for name in FILES:
        a, sa, _ = _parsed(old.text(name), name)
        b, sb, _ = _parsed(new.text(name), name)
        for key in sorted(set(a) | set(b)):
            if a.get(key) == b.get(key):
                continue
            s = st.BY_KEY.get(key)
            if name == SECRETS or s is None or s.secret:
                out[key] = SECRET_SHOWN if (name == SECRETS or (s is not None and s.secret)) else "(not in the schema)"
            else:
                out[key] = [a.get(key), b.get(key)]
        for sec in st.STRUCTURED:
            if sa.get(sec) != sb.get(sec):
                out[sec] = "(section edited)"
    return out


def diff(old: Files, new: Files) -> str:
    """A unified diff of ``settings.yaml``, then one key-level line per changed secret — never a secret value."""
    lines: list[str] = []
    if old.settings != new.settings:
        lines += difflib.unified_diff(old.settings.splitlines(), new.settings.splitlines(),
                                      f"{SETTINGS} (now)", f"{SETTINGS} (after)", lineterm="")
    a, _, _ = _parsed(old.secrets, SECRETS)
    b, _, _ = _parsed(new.secrets, SECRETS)
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            lines.append(f"{SECRETS}  {key}: {_mask(a.get(key))} → {_mask(b.get(key))}")
    return _redact("\n".join(lines), _secret_values(old, new))


@dataclass
class Plan:
    """A change to one instance's files, not yet written: ``old`` (as read) → ``new``."""

    action: str
    root: Path
    old: Files
    new: Files
    target: str = ""
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    of: str | None = None  # the history entry a rollback restores

    @property
    def changes(self) -> dict[str, Any]:
        return changed(self.old, self.new)

    @property
    def ok(self) -> bool:
        return not self.problems

    def public(self) -> dict[str, Any]:
        """The preview: safe to return (no secret value)."""
        values = _secret_values(self.old, self.new)
        changes = self.changes
        return {"ok": self.ok, "action": self.action, "target": self.target,
                "problems": [_redact(p, values) for p in self.problems], "notes": self.notes,
                "diff": diff(self.old, self.new), "changes": changes, "hashes": hashes(self.old),
                "restart": bool(changes)}


def _finish(plan: Plan) -> Plan:
    """Refuse what would stop the instance at start, and a plan that changes nothing."""
    if not plan.problems:
        problems, _ = st.check_texts(plan.new.settings, plan.new.secrets)
        plan.problems += problems
    if not plan.problems and not plan.changes:
        plan.problems.append("nothing changes — every value is already as asked")
    return plan


def _set(text: str, name: str, key: str, value: Any) -> str:
    return st.set_in_text(text or _HEADER[name], key, value)


def plan_settings(root: Path, changes: Mapping[str, Any]) -> Plan:
    """A plan for plain settings ``{key or env name: text}``; ``None`` takes a key back to its default (its line is
    removed). A text is parsed like ``lumi-cli config set`` and validated by the schema; a refusal names the key and
    the allowed values."""
    old = read_files(root)
    plan = Plan("settings", Path(root), old, old, target=", ".join(changes))
    text = old.settings
    for name, raw in changes.items():
        s = st.lookup(str(name))
        if s is None:
            plan.problems.append(f"unknown setting '{name}'")
            continue
        if s.secret:
            plan.problems.append(f"{s.key} is a secret — set it in the Secrets editor")
            continue
        if s.key in CONTAINER_SET:
            plan.problems.append(f"{s.key} is {CONTAINER_SET[s.key]}, set by the instance's container — a value in "
                                 f"{SETTINGS} would have no effect")
            continue
        try:
            if raw is None:
                text = st.unset_in_text(text, s.key)
                continue
            value = st.parse_cli_value(s, raw) if isinstance(raw, str) else raw
            if (p := st.problem(s, raw if value is None else value, where=SETTINGS)) is not None:
                plan.problems.append(p)
                continue
            text = _set(text, SETTINGS, s.key, value)
        except st.SettingsError as exc:
            plan.problems.append(f"{s.key}: {exc}")
    plan.new = old.replace(SETTINGS, text)
    return _finish(plan)


def plan_secret(root: Path, key: str, value: str | None) -> Plan:
    """A plan that sets / rotates (``value``) or clears (``None``) one secret. The value is one line, without spaces
    at its ends; it is never part of a returned structure or a message."""
    old = read_files(root)
    s = st.lookup(str(key))
    plan = Plan("secret", Path(root), old, old, target=s.key if s is not None else str(key))
    if s is None or not s.secret:
        plan.problems.append(f"'{key}' is not a secret — change it in the Settings editor")
        return plan
    try:
        if value is None:
            text = st.unset_in_text(old.secrets, s.key)
        else:
            if not isinstance(value, str) or not value or value != value.strip() or "\n" in value or "\r" in value:
                plan.problems.append(f"{s.key}: a secret is one non-empty line without spaces at its ends "
                                     "(clear it to remove it)")
                return plan
            text = _set(old.secrets, SECRETS, s.key, value)
    except st.SettingsError as exc:
        plan.problems.append(f"{s.key}: {exc}")
        return plan
    plan.new = old.replace(SECRETS, text)
    if s.key == "server.token":
        plan.notes.append(SERVER_TOKEN_NOTE)
    return _finish(plan)


# --- apply, history, rollback --------------------------------------------------------------------------------------


def _ts_key(ts: str) -> tuple[str, int]:
    m = _TS.match(ts)
    return (m.group(1), int(m.group(2) or 1)) if m else ("", 0)


def _new_ts(hist: Path, clock: Callable[[], datetime]) -> str:
    base = clock().astimezone(UTC).strftime("%Y%m%d-%H%M%SZ")
    ts, n = base, 1
    while (hist / f"{ts}.json").exists():
        n += 1
        ts = f"{base}-{n}"
    return ts


def _entries(hist: Path) -> list[str]:
    return sorted((p.stem for p in hist.glob("*.json") if _TS.match(p.stem)), key=_ts_key, reverse=True)


def _prune(hist: Path, keep: int) -> None:
    for ts in _entries(hist)[keep:]:
        for p in hist.glob(f"{ts}*"):
            if p.name == f"{ts}.json" or p.name.startswith(f"{ts}-") and p.name[len(ts) + 1:] in FILES:
                p.unlink(missing_ok=True)


def apply(plan: Plan, expect: Mapping[str, str], *, clock: Callable[[], datetime] = utc_now,
          keep: int = HISTORY_KEEP) -> dict[str, Any]:
    """Write ``plan``: refused when it has a problem or when the files changed since the preview whose ``expect``
    hashes the caller hands back. The files it changes are first copied into ``config-history/<ts>-<file>`` (``0600``)
    with a ``<ts>.json`` manifest (the changes, secrets as ``(secret)``), then written atomically. Returns
    ``{ts, changes, restart, notes}``."""
    if plan.problems:
        raise EditError("; ".join(plan.public()["problems"]))
    with _LOCK:
        current = read_files(plan.root)
        if dict(expect) != hashes(plan.old) or current != plan.old:
            raise Stale("the settings changed since this preview — reload and preview again; nothing was written")
        hist = plan.root / HISTORY_DIR
        hist.mkdir(mode=0o700, exist_ok=True)
        ts = _new_ts(hist, clock)
        names = [name for name in FILES if plan.old.text(name) != plan.new.text(name)]
        changes = plan.changes
        for name in names:
            st.write_private(hist / f"{ts}-{name}", plan.old.text(name))
        manifest = {"ts": ts, "action": plan.action, "target": plan.target, "files": names, "changes": changes}
        if plan.of:
            manifest["of"] = plan.of
        st.write_private(hist / f"{ts}.json", json.dumps(manifest, ensure_ascii=False))
        for name in names:
            st.write_atomic(plan.root / name, plan.new.text(name), private=name == SECRETS)
        _prune(hist, keep)
    return {"ts": ts, "changes": changes, "restart": bool(changes), "notes": plan.notes}


def history(root: Path, limit: int = 50) -> list[dict[str, Any]]:
    """The newest history entries first: ``{ts, action, target, files, changes, of?}`` (secrets as ``(secret)``)."""
    hist = Path(root) / HISTORY_DIR
    out = []
    for ts in _entries(hist)[:limit]:
        try:
            entry = json.loads((hist / f"{ts}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict):
            out.append({k: entry.get(k) for k in ("ts", "action", "target", "files", "changes", "of")
                        if k in entry})
    return out


def plan_rollback(root: Path, ts: str) -> Plan:
    """A plan that restores the files as they were before history entry ``ts`` (the files it changed). Applied like
    any plan, so the current files go to history first and a rollback can itself be rolled back."""
    root = Path(root)
    if not _TS.match(ts or ""):
        raise EditError(f"no history entry {ts!r}")
    hist = root / HISTORY_DIR
    try:
        manifest = json.loads((hist / f"{ts}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EditError(f"no history entry {ts!r}") from None
    old = read_files(root)
    new = old
    for name in manifest.get("files") or []:
        if name not in FILES:
            continue
        try:
            new = new.replace(name, (hist / f"{ts}-{name}").read_text(encoding="utf-8"))
        except OSError:
            raise EditError(f"history entry {ts} is missing its copy of {name}") from None
    plan = Plan("rollback", root, old, new, target=ts, of=ts)
    return _finish(plan)


# --- the structured sections: schedule + model profiles (LUMI-260) -----------------------------------------------


def section_text(root: Path, name: str) -> str:
    """An instance's ``schedule:`` / ``models:`` section as YAML text for the editor (``""`` when it has none)."""
    import yaml

    _, sections, _ = _parsed(read_files(root).settings, SETTINGS)
    data = sections.get(name)
    if data in (None, [], {}):
        return ""
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10**6)


def merged_section(root: Path, name: str) -> Any:
    """What the instance runs once its section is merged over the authored file (the panel image's copy):
    the schedule rows, or ``{aliases, profiles}``."""
    from core import config, schedule

    _, sections, _ = _parsed(read_files(root).settings, SETTINGS)
    data = sections.get(name)
    if name == "schedule":
        rows = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        return schedule.merged_rows(config.DEFAULT_SCHEDULE_PATH, rows)
    aliases, profiles = config.merged_models(data if isinstance(data, dict) else {})
    return {"aliases": {k: f"{p}:{m}" for k, (p, m) in aliases.items()},
            "profiles": {k: v._asdict() for k, v in profiles.items()}}


def plan_section(root: Path, name: str, text: str | None) -> Plan:
    """A plan that replaces the instance's ``name`` section with the YAML ``text`` (its body — a list of rows for
    ``schedule``, a mapping for ``models``; a pasted ``schedule:`` header is accepted). Empty text removes the
    section. Checked like every plan: an unquoted ``19:00`` or a profile that isn't a mapping is refused."""
    old = read_files(root)
    plan = Plan("section", Path(root), old, old, target=name)
    if name not in st.STRUCTURED:
        plan.problems.append(f"unknown section {name!r} — one of {', '.join(st.STRUCTURED)}")
        return plan
    try:
        data = st.parse_yaml(text or "", f"the {name} section")
        if isinstance(data, dict) and set(data) == {name}:
            data = data[name]
        plan.new = old.replace(SETTINGS, st.set_section(old.settings, name, data))
    except st.SettingsError as exc:
        plan.problems.append(str(exc).removeprefix("Lumi settings — "))
        return plan
    return _finish(plan)


# --- promote dev → prod (LUMI-260) ---------------------------------------------------------------------------------

# Never promoted: what names the instance itself (a secret never leaves its own instance either).
PER_INSTANCE = ("instance.env", "instance.home", "instance.allow_dirty_prod", "server.host", "server.port",
                "server.client_mode")


def check_confirm(instance: str, confirm: str | None, what: str = "a change") -> None:
    """A write to prod needs the typed confirmation ``prod`` (the v2.10 rule for the service actions)."""
    if instance == "prod" and (confirm or "").strip() != PROD_CONFIRM:
        raise EditError(f"{what} on prod needs the typed confirmation — type {PROD_CONFIRM!r}; nothing was written")


def promotable(source: Path, target: Path) -> list[dict[str, Any]]:
    """The plain settings and sections whose value differs between ``source`` (dev) and ``target`` (prod):
    ``[{key, source, target}]`` — never a secret or a per-instance key."""
    a, sa, _ = _parsed(read_files(source).settings, SETTINGS)
    b, sb, _ = _parsed(read_files(target).settings, SETTINGS)
    out = []
    for s in st.SETTINGS:
        if s.secret or s.key in PER_INSTANCE or s.key in CONTAINER_SET:
            continue
        if a.get(s.key) != b.get(s.key):
            out.append({"key": s.key, "source": a.get(s.key), "target": b.get(s.key)})
    for name in st.STRUCTURED:
        if sa.get(name) != sb.get(name):
            out.append({"key": name, "source": "(section)", "target": "(section)"})
    return out


def plan_promote(source: Path, target: Path, keys: list[str]) -> Plan:
    """A plan for ``target`` (prod) that copies ``keys`` from ``source`` (dev): a plain setting's value (or its
    absence — back to the default), or a whole ``schedule`` / ``models`` section. A secret or a per-instance key is
    refused with the reason."""
    src = read_files(source)
    old = read_files(target)
    plain, sections, _ = _parsed(src.settings, SETTINGS)
    plan = Plan("promote", Path(target), old, old, target=", ".join(keys))
    text = old.settings
    if not keys:
        plan.problems.append("choose the settings to promote")
    for key in keys:
        try:
            if key in st.STRUCTURED:
                text = st.set_section(text, key, sections.get(key))
                continue
            s = st.lookup(str(key))
            if s is None:
                plan.problems.append(f"unknown setting '{key}'")
            elif s.secret:
                plan.problems.append(f"{s.key} is a secret — never promoted; set prod's own in the Secrets editor")
            elif s.key in PER_INSTANCE or s.key in CONTAINER_SET:
                plan.problems.append(f"{s.key} belongs to each instance — never promoted")
            elif plain.get(s.key) is None:
                text = st.unset_in_text(text, s.key)
            else:
                text = _set(text, SETTINGS, s.key, plain[s.key])
        except st.SettingsError as exc:
            plan.problems.append(f"{key}: {exc}")
    plan.new = old.replace(SETTINGS, text)
    return _finish(plan)
