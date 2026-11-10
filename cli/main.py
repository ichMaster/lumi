"""The CLI's commands (v2.2, LUMI-214) — argument parsing, the API calls, the masked config.

v2.9 (LUMI-248): ``config get | set | validate`` work on the instance's settings files directly (no server) —
``set`` validates against the schema and writes ``settings.yaml`` / ``secrets.yaml`` (0600); every output masks
every secret.

``main(argv, client=…)`` takes an injected API client so tests drive it against a ``TestClient`` server;
the real run builds a ``RemoteCore`` from ``LUMI_SERVER_HOST/PORT/TOKEN``. Every failure is one readable
line on stderr and a non-zero exit — never a traceback.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core import settings as st
from core.config import load_config
from core.llm import LLMError

_SECRET_HINTS = ("key", "token", "secret", "password")
SET = "•••set•••"
UNSET = "(unset)"


def _secret_values() -> set[str]:
    """Every non-empty value a schema ``secret`` setting has in this process — masked wherever it shows up."""
    out = set()
    for s in st.SETTINGS:
        if s.secret:
            for name in (s.alias, *s.old):
                if value := (os.environ.get(name) or "").strip():
                    out.add(value)
    return out


def mask_config(cfg: Any) -> dict[str, Any]:
    """The effective config as plain values, every secret masked (``set`` / ``unset``): a field named like a
    secret, or (v2.9) any field holding the value of a schema ``secret`` setting."""
    secrets = _secret_values() | {str(getattr(cfg, f.name)) for f in dataclasses.fields(cfg)
                                  if any(h in f.name.lower() for h in _SECRET_HINTS) and getattr(cfg, f.name)}
    out: dict[str, Any] = {}
    for f in dataclasses.fields(cfg):
        value = getattr(cfg, f.name)
        if any(hint in f.name.lower() for hint in _SECRET_HINTS) or (isinstance(value, str) and value in secrets):
            out[f.name] = SET if value else UNSET
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[f.name] = value
        else:
            out[f.name] = str(value)
    return out


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m cli", description="Run, inspect and manage the Lumi server.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the server (= python -m server)")
    sub.add_parser("status", help="the server's health + state")
    mem = sub.add_parser("memory", help="her memory of you")
    mem.add_argument("action", choices=["show", "clear"])
    model = sub.add_parser("model", help="show / switch the engine (/model)")
    model.add_argument("name", nargs="?", default="")
    profile = sub.add_parser("model-set", help="show / switch the model profile (/model-set)")
    profile.add_argument("name", nargs="?", default="")
    cfg_p = sub.add_parser("config", help="the instance's settings: show | get | set | validate (secrets masked)")
    folder = argparse.ArgumentParser(add_help=False)
    folder.add_argument("--dir", help="the settings folder (default: LUMI_SETTINGS_DIR, else the checkout's)")
    cfg_sub = cfg_p.add_subparsers(dest="config_cmd")
    cfg_sub.add_parser("show", parents=[folder], help="the effective config (the default)")
    get = cfg_sub.add_parser("get", parents=[folder], help="one setting's value and where it comes from")
    get.add_argument("key", help="group.key (e.g. memory.window) or its env name (LUMI_MEMORY_WINDOW)")
    put = cfg_sub.add_parser("set", parents=[folder], help="validate and write one setting (restart to apply)")
    put.add_argument("key", help="group.key or its env name")
    put.add_argument("value", help="the new value (a list as a,b or [a, b])")
    cfg_sub.add_parser("validate", parents=[folder], help="check settings.yaml + secrets.yaml completely")
    cfg_p.add_argument("--dir", dest="dir_top", help=argparse.SUPPRESS)
    think = sub.add_parser("think", help="she thinks out loud (%%think!) — every connected client sees it")
    think.add_argument("topic", nargs="*", help="what about (optional)")
    return p


def _client_from_config(cfg: Any) -> Any:
    from tui.remote import (  # the v1 API client (shared with the TUI client mode)
        RemoteCore,
        server_url,
    )

    if not cfg.server_token:
        raise SystemExit("server.token is not set (secrets.yaml, or LUMI_SERVER_TOKEN) — the CLI uses the server's token.")
    return RemoteCore(server_url(cfg), cfg.server_token)


def _print_result(result: Any, out) -> None:
    if result is None:
        print("(not a command)", file=out)
    else:
        print(result.text, file=out)


def main(argv: list[str] | None = None, *, client: Any = None,
         ask: Callable[[str], str] = input, out=None, err=None) -> int:
    """Run one CLI command; returns the exit code."""
    out = out or sys.stdout
    err = err or sys.stderr
    args = _parser().parse_args(argv)
    if args.cmd == "config":  # v2.9: the files directly — before load_config, so validate can name a broken file
        return _config(args, out, err)
    cfg = load_config()
    if args.cmd == "serve":
        # prints a refusal, then exits 78 (code review v2.6 #2)
        from server.__main__ import run as serve

        serve()
        return 0
    api = client or _client_from_config(cfg)
    try:
        if args.cmd == "status":
            api.connect()
            health = api.health()
            totals = api.totals
            print(f"server: {api.base_url} · {health.get('env') or 'no env'} v{health.get('version') or '?'}", file=out)
            print(f"model: {api.provider or '?'} / {api.model}" + (f" · profile {api.profile}" if api.profile else ""),
                  file=out)
            print(f"session: {api.session_id} · turns {totals.turns} · tokens {totals.total_tokens}",
                  file=out)
            print(f"mood: {api.mood or '—'} · theme: {api.theme or '—'}", file=out)
            return 0
        if args.cmd == "memory" and args.action == "show":
            _print_result(api.command("/memory"), out)
            return 0
        if args.cmd == "memory" and args.action == "clear":
            asked = api.command("/forget")
            if asked is None or asked.confirm is None:
                _print_result(asked, out)
                return 0
            try:
                answer = ask(f"{asked.confirm} [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):  # no terminal / Ctrl+C at the prompt = "no"
                answer = ""
            if answer not in ("y", "yes", "т", "так"):
                print("Cancelled.", file=out)
                return 0
            _print_result(api.command("/forget", confirmed=True), out)
            return 0
        if args.cmd == "model":
            _print_result(api.command(f"/model {args.name}".strip()), out)
            return 0
        if args.cmd == "model-set":
            _print_result(api.command(f"/model-set {args.name}".strip()), out)
            return 0
        if args.cmd == "think":  # v2.4: the thought is also pushed to every listening client
            outcome = api.run_directive(" ".join(["%think!", *args.topic]))
            if outcome.thought is None:
                print("(no thought came this time — the model returned nothing, or the thought-stream is off on "
                      "the server: LUMI_THOUGHTS; the server log says which)", file=out)
            else:
                print(f"💭 {outcome.thought.text}", file=out)
            return 0
    except LLMError as exc:  # down / rejected / busy / a model error — one readable line
        print(f"lumi: {exc}", file=err)
        return 1
    return 2  # pragma: no cover - argparse rejects unknown subcommands first


def _folder(args: Any) -> Path:
    given = getattr(args, "dir", None) or getattr(args, "dir_top", None)
    return Path(given).expanduser() if given else st.settings_dir()


def _config(args: Any, out, err) -> int:
    """``config [show] | get KEY | set KEY VALUE | validate`` — every secret masked on every output."""
    cmd = args.config_cmd or "show"
    folder = _folder(args)
    try:
        if cmd == "show":
            cfg = load_config(settings_dir=folder) if (getattr(args, "dir", None) or getattr(args, "dir_top", None)) \
                else load_config()
            for name, value in mask_config(cfg).items():
                print(f"{name} = {value}", file=out)
            return 0
        if cmd == "validate":
            problems, warnings = st.check_folder(folder)
            for w in warnings:
                print(f"warning: {w}", file=out)
            for p in problems:
                print(f"error: {p}", file=err)
            if problems:
                return 1
            print(f"{folder}: settings.yaml + secrets.yaml are valid", file=out)
            return 0
        s = st.lookup(args.key)
        if s is None:
            print(f"lumi: unknown setting '{args.key}' — see settings.example.yaml / secrets.example.yaml", file=err)
            return 1
        if cmd == "get":
            res = st.resolve(os.environ, folder=folder)
            raw = res.values.get(s.alias)
            source = res.source(s.alias)
            if s.secret:
                shown = SET if raw else UNSET
            elif raw is None:
                shown = st.yaml_scalar(s.default) if s.default is not None else UNSET
            else:
                shown = raw
            print(f"{s.key} = {shown}  ({source}; env {s.alias})", file=out)
            return 0
        if cmd == "set":
            s, path = st.set_value(folder, args.key, args.value)
            shown = SET if s.secret else st.yaml_scalar(st.parse_cli_value(s, args.value))
            print(f"{s.key} = {shown} → {path}" + (" (0600)" if s.secret else ""), file=out)
            if s.restart:
                print("restart needed — the running processes read their settings at start", file=out)
            if s.alias in os.environ:
                print(f"note: {s.alias} is set in this environment and still wins over the file", file=out)
            return 0
    except st.SettingsError as exc:
        print(f"lumi: {exc}", file=err)
        return 1
    return 2  # pragma: no cover - argparse rejects unknown subcommands first
