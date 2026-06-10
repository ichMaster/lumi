"""Monitoring utility for the Telegram bridge (v0.13).

A snapshot of the file bus + daemon logs — queue depths, a health verdict, recent traffic, and the
tail of each daemon log. **Local-only** (no network), so it's cheap to poll.

    uv run python -m telegram.monitor            # one-shot snapshot
    uv run python -m telegram.monitor --watch    # refresh every 2s (Ctrl+C to stop)
    uv run python -m telegram.monitor --watch 5  # every 5s
    uv run python -m telegram.monitor --lines 8  # show more recent records / log lines
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from core.config import Config, load_config
from state import fifo


def _records(jsonl: Path) -> list[dict]:
    """All records in a FIFO file, in order (empty if missing/unreadable)."""
    if not jsonl.is_file():
        return []
    out: list[dict] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def queue_status(jsonl: Path, pos: Path) -> tuple[int, int, int]:
    """``(total records, pointer id, pending)`` for a queue file + its pointer file."""
    records = _records(jsonl)
    pointer = fifo.load_pointer(pos)
    pending = sum(1 for r in records if r.get("id", 0) > pointer)
    return len(records), pointer, pending


def health(inbox_pending: int, outbox_pending: int, bridge: bool) -> str:
    """A one-line verdict from the two pending counts."""
    if not bridge:
        return "⚠ bridge OFF (LUMI_BRIDGE not on — the TUI isn't on the bus)"
    issues: list[str] = []
    if inbox_pending:
        issues.append(f"inbox not draining ({inbox_pending} unread — TUI down / busy?)")
    if outbox_pending:
        issues.append(f"outbox not sending ({outbox_pending} unsent — daemon 2 down?)")
    return "✓ healthy (both queues drained)" if not issues else "⚠ " + "; ".join(issues)


def _tail(path: Path, n: int) -> list[str]:
    """The last ``n`` non-empty lines of a text file (empty if missing)."""
    if not path.is_file():
        return ["    (no log yet)"]
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [f"    {ln}" for ln in lines[-n:]] or ["    (empty)"]


def _fmt_records(records: list[dict], n: int) -> list[str]:
    """Render the last ``n`` queue records compactly: ``HH:MM id=… <emotion> "text"``."""
    out = []
    for r in records[-n:]:
        ts = str(r.get("ts", ""))[11:16]  # HH:MM from an iso ts
        emo = f" {r['emotion']:9}" if r.get("emotion") else ""
        text = str(r.get("text", "")).replace("\n", " ⏎ ")
        out.append(f"    {ts} id={r.get('id', '?')}{emo}  {text[:64]!r}")
    return out or ["    (none)"]


def render(cfg: Config, lines: int, *, now: str = "") -> str:
    """Build the snapshot report (pure — easy to test; no I/O beyond reading the bus/log files)."""
    inbox_pos = cfg.inbox_path.with_suffix(".pos")
    outbox_sent = cfg.outbox_path.with_suffix(".sent")
    in_total, in_ptr, in_pend = queue_status(cfg.inbox_path, inbox_pos)
    out_total, out_ptr, out_pend = queue_status(cfg.outbox_path, outbox_sent)
    log_dir = cfg.inbox_path.parent

    lines_out = [
        f"Lumi Telegram bridge{('  ' + now) if now else ''}",
        f"  bridge: {'ON' if cfg.bridge else 'OFF'}   inbox={cfg.inbox_path}   outbox={cfg.outbox_path}",
        "",
        "  queue    total  pointer  pending",
        f"  inbox  {in_total:7}{in_ptr:8} {in_pend:8}  {'✓' if in_pend == 0 else '⚠'}",
        f"  outbox {out_total:7}{out_ptr:8} {out_pend:8}  {'✓' if out_pend == 0 else '⚠'}",
        "",
        f"  health: {health(in_pend, out_pend, cfg.bridge)}",
        "",
        "  recent inbox (Telegram → you):",
        *_fmt_records(_records(cfg.inbox_path), lines),
        "  recent outbox (Лілі → Telegram):",
        *_fmt_records(_records(cfg.outbox_path), lines),
        "",
        f"  inbound log ({log_dir / 'telegram-inbound.log'}):",
        *_tail(log_dir / "telegram-inbound.log", lines),
        f"  outbound log ({log_dir / 'telegram-outbound.log'}):",
        *_tail(log_dir / "telegram-outbound.log", lines),
    ]
    return "\n".join(lines_out)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI glue (clock/loop/print)
    parser = argparse.ArgumentParser(prog="telegram.monitor", description="Telegram bridge monitor")
    parser.add_argument("--watch", nargs="?", type=float, const=2.0, default=None,
                        help="refresh every N seconds (default 2); omit for a one-shot snapshot")
    parser.add_argument("--lines", type=int, default=4, help="recent records / log lines to show")
    args = parser.parse_args(argv)

    def snapshot() -> str:
        return render(load_config(), args.lines, now=time.strftime("%Y-%m-%d %H:%M:%S"))

    if args.watch is None:
        print(snapshot())
        return 0
    try:
        while True:
            sys.stdout.write("\033[2J\033[H")  # clear screen + home
            print(snapshot())
            print("\n  (--watch: refreshing every", args.watch, "s — Ctrl+C to stop)")
            time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
