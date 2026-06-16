"""Cost analysis report — ``.lumi/cost-report.md``: tokens + cost by **session × activity × operation**.

A cost-focused view of the same per-call log the cache monitor writes (``core/cache_log.py``). For each
session, and accumulated across all, it decomposes spend by **activity** (reply / tool / think / mood /
session-start / session-close / …) and by **operation** (input / output / cache-read / cache-write),
with each row's **share** of the scope's cost. The diagnostic twin of the cache report: that one
explains *why the cache writes*, this one explains *where the money goes*.

Rendered from the cache-log events at session close (whenever the cache monitor is on — same data), so
it needs no separate ledger. Cost uses the same list prices + cache multipliers as ``core/usage.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.cache_log import CacheEvent


@dataclass
class _Cost:
    """A cost accumulator for one bucket (a session-activity, or a total)."""

    calls: int = 0
    tokens: int = 0
    c_in: float = 0.0
    c_out: float = 0.0
    c_rd: float = 0.0
    c_wr: float = 0.0

    @property
    def cost(self) -> float:
        return self.c_in + self.c_out + self.c_rd + self.c_wr

    def add(self, e: CacheEvent, p, write_rate: float) -> None:
        self.calls += 1
        self.tokens += (e.input or 0) + (e.output or 0) + (e.cache_read or 0) + (e.cache_write or 0)
        self.c_in += (e.input or 0) * p.input / 1_000_000
        self.c_out += (e.output or 0) * p.output / 1_000_000
        self.c_rd += (e.cache_read or 0) * p.cache_read / 1_000_000
        self.c_wr += (e.cache_write or 0) * write_rate / 1_000_000

    def fold(self, o: _Cost) -> None:
        self.calls += o.calls
        self.tokens += o.tokens
        self.c_in += o.c_in
        self.c_out += o.c_out
        self.c_rd += o.c_rd
        self.c_wr += o.c_wr


def _fmt(n: int) -> str:
    return f"{n:,}"


def _usd(x: float) -> str:
    return f"${x:,.4f}"


def _share(x: float, total: float) -> str:
    return f"{(x / total * 100) if total else 0:.0f}%"


_HEADER = (
    "| Activity | Calls | Input | Output | Cache read | Cache write | Tokens | Cost | Share |\n"
    "|---|--:|--:|--:|--:|--:|--:|--:|--:|"
)


def _activity_table(by_act: dict[str, _Cost], scope_total: float) -> tuple[list[str], _Cost]:
    """A by-activity table (operation columns are **cost $**); returns the lines + the TOTAL row."""
    lines = [_HEADER]
    total = _Cost()
    for kind in sorted(by_act, key=lambda k: -by_act[k].cost):
        c = by_act[kind]
        total.fold(c)
        lines.append(
            f"| {kind} | {c.calls} | {_usd(c.c_in)} | {_usd(c.c_out)} | {_usd(c.c_rd)} | "
            f"{_usd(c.c_wr)} | {_fmt(c.tokens)} | {_usd(c.cost)} | {_share(c.cost, scope_total)} |"
        )
    lines.append(
        f"| **TOTAL** | {total.calls} | {_usd(total.c_in)} | {_usd(total.c_out)} | {_usd(total.c_rd)} | "
        f"{_usd(total.c_wr)} | {_fmt(total.tokens)} | {_usd(total.cost)} | 100% |"
    )
    return lines, total


def render_cost_report(events: list[CacheEvent], *, generated_at: str, ttl: str = "5m") -> str:
    """Render the cost report: an accumulated by-activity×operation table + one per session."""
    from core.usage import pricing_for  # local import — avoids a module-load cycle

    grand: dict[str, _Cost] = {}
    sessions: dict[str, dict] = {}
    order: list[str] = []
    for e in events:
        p = pricing_for(e.model)
        write_rate = p.cache_write(ttl)
        grand.setdefault(e.kind, _Cost()).add(e, p, write_rate)
        sid = e.session_id or "(legacy)"
        if sid not in sessions:
            sessions[sid] = {"first": e.ts, "by_act": {}}
            order.append(sid)
        sessions[sid]["by_act"].setdefault(e.kind, _Cost()).add(e, p, write_rate)

    grand_total = sum(c.cost for c in grand.values()) or 1.0
    op = _Cost()
    for c in grand.values():
        op.fold(c)

    out: list[str] = []
    out.append("# Lumi — cost analysis\n")
    out.append(
        f"_Generated {generated_at} · {len(events)} model calls · {len(order)} sessions · TTL {ttl}._\n"
    )
    out.append(
        f"- **Total cost:** {_usd(op.cost)}\n"
        f"- **By operation:** input {_usd(op.c_in)} ({_share(op.c_in, grand_total)}) · "
        f"output {_usd(op.c_out)} ({_share(op.c_out, grand_total)}) · "
        f"cache read {_usd(op.c_rd)} ({_share(op.c_rd, grand_total)}) · "
        f"cache write {_usd(op.c_wr)} ({_share(op.c_wr, grand_total)})\n"
    )
    out.append(
        "> Each row is an **activity**; the operation columns show that activity's **cost** by operation "
        "(input / output / cache-read / cache-write). **Tokens** = total tokens (all operations). "
        "**Share** = % of the scope's total cost. The **TOTAL** row gives the by-operation cost totals.\n"
    )

    out.append("## All sessions\n")
    lines, _ = _activity_table(grand, grand_total)
    out.extend(lines)
    out.append("")

    out.append("## Per session\n")
    for sid in order:
        s = sessions[sid]
        short = sid if sid == "(legacy)" else sid[:8]
        started = s["first"][:16].replace("T", " ")
        sess_total = sum(c.cost for c in s["by_act"].values()) or 1.0
        out.append(
            f"### Session `{short}` — started {started} · {_usd(sess_total)} "
            f"({_share(sess_total, grand_total)} of total)\n"
        )
        lines, _ = _activity_table(s["by_act"], sess_total)
        out.extend(lines)
        out.append("")

    return "\n".join(out) + "\n"


def write_cost_report(events: list[CacheEvent], path: str | Path, *, generated_at: str, ttl: str = "5m") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_cost_report(events, generated_at=generated_at, ttl=ttl), encoding="utf-8")
