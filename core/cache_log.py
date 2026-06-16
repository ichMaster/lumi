"""Per-call prompt-cache monitor — log every model call's cache behaviour and attribute the writes.

The reply turn and the proactive thoughts use **separate** prompt caches (different prompt shapes →
different cache entries), so a write to one never affects the other. To explain "why is cache write
big?", this logs **one event per model call** with its **channel** — ``reply`` (the answer), ``tool``
(file-tool loop rounds), ``think`` (proactive %think), ``session-start`` (building the prompt sections:
day/week/facts digests), ``session-close`` (the wrap-up summary + facts), ``mood``, ``compaction`` —
the cache read/write, and the **gap since the last call of the same channel**. It also **fingerprints
the cached prefix** each call, so a write is classified by **measurement**, not a guess: ``first`` /
``expired`` (gap > TTL) / ``moved`` (the prefix actually changed — and *which section*) / ``evicted``
(the prefix was identical but the cache was dropped early — nothing in the prompt to fix).

The log is **append-only across all sessions**, so the report is always **all-time cumulative**; it is
also sliced **per session** (each event carries its ``session_id``). It renders the unified
``.lumi/cache-report.md`` at session close — **cache behaviour** (by-channel writes + write cause) **and
cost** (by activity × operation — input/output/cache-read/cache-write — per session + accumulated, with
share). One report: the cache view explains *why it writes*, the cost view *where the money goes*.
Off-by-default-friendly; an event is a cheap JSONL append.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

# Cache TTL → seconds, for the "expired" threshold.
TTL_SECONDS = {"1h": 3600, "5m": 300}


def ttl_seconds(ttl: str) -> int:
    return TTL_SECONDS.get(ttl, 300)


def classify(cache_write: int, gap_s: float | None, ttl_s: int, prefix_changed: bool = True) -> str:
    """Why a call wrote cache, **measured** (not guessed): ``none`` (no write), ``first`` (no prior call
    of this kind), ``expired`` (gap > TTL → the entry timed out), ``moved`` (warm and the cached prefix
    **actually changed** — see ``changed_section``), or ``evicted`` (warm, the prefix is **identical**,
    but the cache was dropped early — Anthropic doesn't guarantee a prefix survives its full TTL; nothing
    in the prompt to fix). ``prefix_changed`` comes from fingerprinting the prefix; when unknown it
    defaults to ``True`` (the old optimistic "it moved")."""
    if cache_write <= 0:
        return "none"
    if gap_s is None:
        return "first"
    if gap_s > ttl_s:
        return "expired"
    return "moved" if prefix_changed else "evicted"


def prefix_sections(prefix: str) -> dict[str, str]:
    """Fingerprint a cached prefix → ``{section-label: short-hash}`` so a write can name **which** part
    moved. Splits on top-level ``# `` headers (the prompt's section markers); the head (canon) is
    ``(canon)``. Empty prefix → ``{}``."""
    if not prefix:
        return {}
    out: dict[str, str] = {}
    for part in re.split(r"\n\n(?=# )", prefix):
        label = part.split("\n", 1)[0].strip() if part.startswith("# ") else "(canon)"
        out[label] = hashlib.sha256(part.encode("utf-8")).hexdigest()[:8]
    return out


def diff_sections(prev: dict[str, str] | None, cur: dict[str, str]) -> tuple[bool, str]:
    """Compare two prefix fingerprints → ``(changed?, "label, …")``. ``prev is None`` (first call of the
    group) → ``(True, "")`` (the ``first`` cause already covers it). A dropped section shows as ``-label``."""
    if prev is None:
        return True, ""
    changed = [label for label, h in cur.items() if prev.get(label) != h]
    changed += [f"-{label}" for label in prev if label not in cur]
    return bool(changed), ", ".join(changed)


@dataclass
class CacheEvent:
    ts: str
    kind: str            # reply / tool / think / mood / session-start / session-close / compaction
    cache_read: int
    cache_write: int
    input: int
    output: int
    gap_s: float | None  # seconds since the previous call of the SAME kind (None = first)
    cause: str           # none / first / expired / moved / evicted
    model: str = ""      # the model for this call (for the per-activity cost)
    session_id: str = "" # the session this call belongs to (for the per-session breakdown; "" = legacy)
    changed_section: str = ""  # for a `moved` write: which prefix section(s) changed (canon/memory/mood)


_FIELDS = ("ts", "kind", "cache_read", "cache_write", "input", "output", "gap_s", "cause", "model",
           "session_id", "changed_section")


def append_event(path: str | Path, event: CacheEvent) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


def load_events(path: str | Path) -> list[CacheEvent]:
    p = Path(path)
    if not p.is_file():
        return []
    out: list[CacheEvent] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            out.append(CacheEvent(**{k: data[k] for k in _FIELDS if k in data}))
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
    return out


# --- report --------------------------------------------------------------------------------------
@dataclass
class _ChannelAgg:
    calls: int = 0
    cache_read: int = 0
    cache_write: int = 0
    writes: int = 0
    first: int = 0
    expired: int = 0
    moved: int = 0
    evicted: int = 0

    def add(self, e: CacheEvent) -> None:
        self.calls += 1
        self.cache_read += e.cache_read
        self.cache_write += e.cache_write
        if e.cause != "none":
            self.writes += 1
            if hasattr(self, e.cause):
                setattr(self, e.cause, getattr(self, e.cause) + 1)


def _ratio(read: int, write: int) -> str:
    if write <= 0:
        return "—" if read == 0 else "∞"
    return f"{read / write:.0f}:1"


def _fmt(n: int) -> str:
    return f"{n:,}"


def _usd(x: float) -> str:
    return f"${x:,.4f}"


def _share(x: float, total: float) -> str:
    return f"{(x / total * 100) if total else 0:.0f}%"


@dataclass
class _Cost:
    """Tokens **and** cost per operation for one bucket (a session-activity, an activity total, …)."""

    calls: int = 0
    t_in: int = 0
    t_out: int = 0
    t_rd: int = 0
    t_wr: int = 0
    c_in: float = 0.0
    c_out: float = 0.0
    c_rd: float = 0.0
    c_wr: float = 0.0

    @property
    def tokens(self) -> int:
        return self.t_in + self.t_out + self.t_rd + self.t_wr

    @property
    def cost(self) -> float:
        return self.c_in + self.c_out + self.c_rd + self.c_wr

    def add(self, e: CacheEvent, p, write_rate: float) -> None:
        self.calls += 1
        self.t_in += e.input or 0
        self.t_out += e.output or 0
        self.t_rd += e.cache_read or 0
        self.t_wr += e.cache_write or 0
        self.c_in += (e.input or 0) * p.input / 1_000_000
        self.c_out += (e.output or 0) * p.output / 1_000_000
        self.c_rd += (e.cache_read or 0) * p.cache_read / 1_000_000
        self.c_wr += (e.cache_write or 0) * write_rate / 1_000_000

    def fold(self, o: _Cost) -> None:
        for f in ("calls", "t_in", "t_out", "t_rd", "t_wr", "c_in", "c_out", "c_rd", "c_wr"):
            setattr(self, f, getattr(self, f) + getattr(o, f))


_TOKENS_HEADER = (
    "| Activity | Calls | Input | Output | Cache read | Cache write | Read:Write | Cost | Share |\n"
    "|---|--:|--:|--:|--:|--:|--:|--:|--:|"
)
_COST_HEADER = (
    "| Activity | Calls | Input | Output | Cache read | Cache write | Tokens | Cost | Share |\n"
    "|---|--:|--:|--:|--:|--:|--:|--:|--:|"
)


def _tokens_table(by_act: dict[str, _Cost], scope_total: float) -> list[str]:
    """A by-activity **tokens** table (operation columns are token counts) + read:write, cost, share."""
    lines = [_TOKENS_HEADER]
    total = _Cost()
    for kind in sorted(by_act, key=lambda k: -by_act[k].cost):
        c = by_act[kind]
        total.fold(c)
        lines.append(
            f"| {kind} | {c.calls} | {_fmt(c.t_in)} | {_fmt(c.t_out)} | {_fmt(c.t_rd)} | {_fmt(c.t_wr)} | "
            f"{_ratio(c.t_rd, c.t_wr)} | {_usd(c.cost)} | {_share(c.cost, scope_total)} |"
        )
    lines.append(
        f"| **TOTAL** | {total.calls} | {_fmt(total.t_in)} | {_fmt(total.t_out)} | {_fmt(total.t_rd)} | "
        f"{_fmt(total.t_wr)} | {_ratio(total.t_rd, total.t_wr)} | {_usd(total.cost)} | 100% |"
    )
    return lines


def _cost_table(by_act: dict[str, _Cost], scope_total: float) -> list[str]:
    """A by-activity **cost** table (operation columns are cost $) + total tokens, cost, share."""
    lines = [_COST_HEADER]
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
    return lines


def render_cache_report(events: list[CacheEvent], *, generated_at: str, ttl: str = "5m") -> str:
    """The unified **prompt-cache & cost** report: cache behaviour (by channel + write cause) and, by
    activity × operation, **two** breakdowns — **tokens** and **cost $** — per session and accumulated,
    each with share. One report; the cache view explains *why it writes*, the cost view *where the money
    goes*."""
    from core.usage import pricing_for  # local import — avoids a module-load cycle

    by_kind: dict[str, _ChannelAgg] = {}
    total = _ChannelAgg()
    causes = {"first": 0, "expired": 0, "moved": 0, "evicted": 0}
    by_act: dict[str, _Cost] = {}
    op = _Cost()
    sessions: dict[str, dict] = {}
    order: list[str] = []
    for e in events:
        by_kind.setdefault(e.kind, _ChannelAgg()).add(e)
        total.add(e)
        if e.cause in causes:
            causes[e.cause] += 1
        p = pricing_for(e.model)
        write_rate = p.cache_write(ttl)
        by_act.setdefault(e.kind, _Cost()).add(e, p, write_rate)
        op.add(e, p, write_rate)
        sid = e.session_id or "(legacy)"
        if sid not in sessions:
            sessions[sid] = {"first": e.ts, "by_act": {}}
            order.append(sid)
        sessions[sid]["by_act"].setdefault(e.kind, _Cost()).add(e, p, write_rate)
    grand_total = op.cost or 1.0

    out: list[str] = []
    out.append("# Lumi — prompt-cache & cost\n")
    out.append(
        f"_Generated {generated_at} · {len(events)} model calls · {len(order)} sessions · TTL {ttl}._\n"
    )
    out.append(
        f"- **Cache writes:** {_fmt(total.writes)} · **read:write ratio:** "
        f"{_ratio(total.cache_read, total.cache_write)} · cache read {_fmt(total.cache_read)} / "
        f"write {_fmt(total.cache_write)} tokens\n"
        f"- **Total cost:** {_usd(op.cost)} · input {_usd(op.c_in)} ({_share(op.c_in, grand_total)}) · "
        f"output {_usd(op.c_out)} ({_share(op.c_out, grand_total)}) · cache read {_usd(op.c_rd)} "
        f"({_share(op.c_rd, grand_total)}) · cache write {_usd(op.c_wr)} ({_share(op.c_wr, grand_total)})\n"
    )
    out.append(
        "> A write is a cache **miss**, classified by **measurement** (the prefix is fingerprinted each "
        "call): **first** (first call of that channel), **expired** (idle > TTL), **moved** (the prefix "
        "really changed), or **evicted** (prefix *identical* but the cache was dropped early — Anthropic "
        "doesn't guarantee a prefix survives its TTL; **nothing in the prompt to fix**). A high *evicted* "
        "count means the cache is being dropped, not that your prompt churns.\n"
    )

    out.append("## By channel (cache behaviour)\n")
    out.append(
        "| Channel | Calls | Cache read | Cache write | Read:Write | Writes (first / expired / moved / evicted) |\n"
        "|---|--:|--:|--:|--:|--:|"
    )
    for kind in sorted(by_kind, key=lambda k: -by_kind[k].cache_write):
        a = by_kind[kind]
        out.append(
            f"| {kind} | {a.calls} | {_fmt(a.cache_read)} | {_fmt(a.cache_write)} | "
            f"{_ratio(a.cache_read, a.cache_write)} | {a.writes} ({a.first} / {a.expired} / {a.moved} / {a.evicted}) |"
        )
    out.append(
        f"| **TOTAL** | {total.calls} | {_fmt(total.cache_read)} | {_fmt(total.cache_write)} | "
        f"{_ratio(total.cache_read, total.cache_write)} | "
        f"{total.writes} ({total.first} / {total.expired} / {total.moved} / {total.evicted}) |\n"
    )

    out.append("## Writes by cause\n")
    out.append(
        f"- **first**-of-channel: {causes['first']}\n"
        f"- **expired** (idle > TTL): {causes['expired']}\n"
        f"- **moved** (the prefix actually changed): {causes['moved']}\n"
        f"- **evicted** (prefix identical, cache dropped early — nothing to fix): {causes['evicted']}\n"
    )

    out.append("## By activity — tokens\n")
    out.append("> Operation columns are **token counts**. **Read:Write** = cache read:write ratio; "
               "**Share** = % of total cost.\n")
    out.extend(_tokens_table(by_act, grand_total))
    out.append("")

    out.append("## By activity — cost\n")
    out.append("> Operation columns are **cost $** (input / output / cache-read / cache-write). "
               "**Tokens** = total tokens; **Share** = % of total cost.\n")
    out.extend(_cost_table(by_act, grand_total))
    out.append("")

    out.append(_session_table(events, ttl, grand_total))

    out.append("## Per session — by activity (tokens & cost)\n")
    for sid in order:
        s = sessions[sid]
        short = sid if sid == "(legacy)" else sid[:8]
        started = s["first"][:16].replace("T", " ")
        sess_total = sum(c.cost for c in s["by_act"].values()) or 1.0
        out.append(
            f"### Session `{short}` — started {started} · {_usd(sess_total)} "
            f"({_share(sess_total, grand_total)} of total)\n"
        )
        out.append("**Tokens**\n")
        out.extend(_tokens_table(s["by_act"], sess_total))
        out.append("")
        out.append("**Cost**\n")
        out.extend(_cost_table(s["by_act"], sess_total))
        out.append("")

    return "\n".join(out) + "\n"


def _session_table(events: list[CacheEvent], ttl: str, grand_total: float) -> str:
    """Per-session **overview** — one row per conversation: cache read/write tokens + read:write ratio +
    cost + share. The cache-token companion to the per-session breakdowns below. Calls logged before the
    ``session_id`` field existed group under ``(legacy)``."""
    from core.usage import pricing_for  # local import — avoids a module-load cycle

    write_mult = 2.0 if ttl == "1h" else 1.25
    agg: dict[str, dict] = {}
    order: list[str] = []
    for e in events:
        sid = e.session_id or "(legacy)"
        if sid not in agg:
            agg[sid] = {"calls": 0, "cr": 0, "cw": 0, "writes": 0, "cost": 0.0, "first": e.ts}
            order.append(sid)
        p = pricing_for(e.model)
        cost = (
            e.input * p.input + e.output * p.output
            + e.cache_read * p.cache_read + e.cache_write * (p.input * write_mult)
        ) / 1_000_000
        a = agg[sid]
        a["calls"] += 1
        a["cr"] += e.cache_read
        a["cw"] += e.cache_write
        a["cost"] += cost
        if e.cause != "none":
            a["writes"] += 1

    rows = ["## By session (overview)\n"]
    rows.append(
        "| Session | Calls | Cache read | Cache write | Read:Write | Writes | Cost | Share | Started |\n"
        "|---|--:|--:|--:|--:|--:|--:|--:|---|"
    )
    for sid in order:
        a = agg[sid]
        short = sid if sid == "(legacy)" else sid[:8]
        started = a["first"][:16].replace("T", " ")
        rows.append(
            f"| {short} | {a['calls']} | {_fmt(a['cr'])} | {_fmt(a['cw'])} | "
            f"{_ratio(a['cr'], a['cw'])} | {a['writes']} | {_usd(a['cost'])} | "
            f"{_share(a['cost'], grand_total)} | {started} |"
        )
    return "\n".join(rows) + "\n"


def write_cache_report(events: list[CacheEvent], path: str | Path, *, generated_at: str, ttl: str = "5m") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_cache_report(events, generated_at=generated_at, ttl=ttl), encoding="utf-8")
