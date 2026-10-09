"""The CLI's commands (v2.2, LUMI-214) — argument parsing, the API calls, the masked config.

``main(argv, client=…)`` takes an injected API client so tests drive it against a ``TestClient`` server;
the real run builds a ``RemoteCore`` from ``LUMI_SERVER_HOST/PORT/TOKEN``. Every failure is one readable
line on stderr and a non-zero exit — never a traceback.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Callable
from typing import Any

from core.config import load_config
from core.llm import LLMError

_SECRET_HINTS = ("key", "token", "secret", "password")


def mask_config(cfg: Any) -> dict[str, Any]:
    """The effective config as plain values, every secret-ish field masked (``set`` / ``unset``)."""
    out: dict[str, Any] = {}
    for f in dataclasses.fields(cfg):
        value = getattr(cfg, f.name)
        if any(hint in f.name.lower() for hint in _SECRET_HINTS):
            out[f.name] = "•••set•••" if value else "(unset)"
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
    sub.add_parser("config", help="the instance's effective config (secrets masked)")
    return p


def _client_from_config(cfg: Any) -> Any:
    from tui.remote import RemoteCore  # the v1 API client (shared with the TUI client mode)

    if not cfg.server_token:
        raise SystemExit("LUMI_SERVER_TOKEN is not set — the CLI uses the server's token from .env.")
    return RemoteCore(f"http://{cfg.server_host}:{cfg.server_port}", cfg.server_token)


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
    cfg = load_config()
    if args.cmd == "serve":
        from server.__main__ import main as serve

        serve()
        return 0
    if args.cmd == "config":
        for name, value in mask_config(cfg).items():
            print(f"{name} = {value}", file=out)
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
    except LLMError as exc:  # down / rejected / busy / a model error — one readable line
        print(f"lumi: {exc}", file=err)
        return 1
    return 2  # pragma: no cover - argparse rejects unknown subcommands first
