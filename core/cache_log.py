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
also sliced **per session** (each event carries its ``session_id``). It renders ``.lumi/cache-report.md``
(by-channel + by-cause + by-activity + by-session) at session close, the diagnostic twin of the v0.17
usage report. Off-by-default-friendly; an event is a cheap JSONL append.
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


def render_cache_report(events: list[CacheEvent], *, generated_at: str, ttl: str = "5m") -> str:
    by_kind: dict[str, _ChannelAgg] = {}
    total = _ChannelAgg()
    causes = {"first": 0, "expired": 0, "moved": 0, "evicted": 0}
    for e in events:
        by_kind.setdefault(e.kind, _ChannelAgg()).add(e)
        total.add(e)
        if e.cause in causes:
            causes[e.cause] += 1

    out: list[str] = []
    out.append("# Lumi — prompt-cache behaviour\n")
    out.append(f"_Generated {generated_at} · {len(events)} model calls logged · TTL {ttl}._\n")

    out.append(
        f"- **Cache writes:** {_fmt(total.writes)}  ·  **read:write ratio:** {_ratio(total.cache_read, total.cache_write)}\n"
        f"- **Cache read:** {_fmt(total.cache_read)} tokens  ·  **cache write:** {_fmt(total.cache_write)} tokens\n"
    )
    out.append(
        "> A write is a cache **miss**. Its cause is **measured** (the prefix is fingerprinted each call): "
        "**first** (first call of that channel), **expired** (idle > TTL), **moved** (the prefix really "
        "changed — see which section below), or **evicted** (the prefix was *identical* but the cache was "
        "dropped early — Anthropic doesn't guarantee a prefix survives its TTL; **nothing in the prompt to "
        "fix**). A high *evicted* count means the cache is being dropped, not that your prompt churns.\n"
    )

    out.append("## By channel\n")
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

    out.append(_activity_table(events, ttl))
    out.append(_session_table(events, ttl))
    out.append(_session_activity_tables(events, ttl))
    return "\n".join(out) + "\n"


def _activity_table(events: list[CacheEvent], ttl: str) -> str:
    """Tokens + estimated cost per activity (reply / tool / think / housekeeping)."""
    from core.usage import pricing_for  # local import — avoids a module-load cycle

    write_mult = 2.0 if ttl == "1h" else 1.25
    agg: dict[str, dict] = {}
    grand = 0.0
    for e in events:
        p = pricing_for(e.model)
        cost = (
            e.input * p.input + e.output * p.output
            + e.cache_read * p.cache_read + e.cache_write * (p.input * write_mult)
        ) / 1_000_000
        grand += cost
        a = agg.setdefault(e.kind, {"calls": 0, "input": 0, "output": 0, "cr": 0, "cw": 0, "cost": 0.0})
        a["calls"] += 1
        a["input"] += e.input
        a["output"] += e.output
        a["cr"] += e.cache_read
        a["cw"] += e.cache_write
        a["cost"] += cost

    rows = ["## By activity (tokens & cost)\n"]
    rows.append(
        "| Activity | Calls | Input | Output | Cache read | Cache write | Est. cost | Share |\n"
        "|---|--:|--:|--:|--:|--:|--:|--:|"
    )
    grand = grand or 1.0
    for kind in sorted(agg, key=lambda k: -agg[k]["cost"]):
        a = agg[kind]
        rows.append(
            f"| {kind} | {a['calls']} | {_fmt(a['input'])} | {_fmt(a['output'])} | {_fmt(a['cr'])} | "
            f"{_fmt(a['cw'])} | ${a['cost']:,.4f} | {a['cost'] / grand * 100:.0f}% |"
        )
    return "\n".join(rows) + "\n"


def _session_table(events: list[CacheEvent], ttl: str) -> str:
    """Tokens + estimated cost per **session** — the same all-time log, sliced by conversation.

    Events are in chronological append order, so first-seen order = session start order. Calls logged
    before the ``session_id`` field existed group under ``(legacy)``.
    """
    from core.usage import pricing_for  # local import — avoids a module-load cycle

    write_mult = 2.0 if ttl == "1h" else 1.25
    agg: dict[str, dict] = {}
    order: list[str] = []
    for e in events:
        sid = e.session_id or "(legacy)"
        if sid not in agg:
            agg[sid] = {"calls": 0, "cr": 0, "cw": 0, "writes": 0, "cost": 0.0, "first": e.ts, "last": e.ts}
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
        a["last"] = e.ts
        if e.cause != "none":
            a["writes"] += 1

    rows = ["## By session (tokens & cost)\n"]
    rows.append(
        "| Session | Calls | Cache read | Cache write | Writes | Est. cost | Started |\n"
        "|---|--:|--:|--:|--:|--:|---|"
    )
    for sid in order:
        a = agg[sid]
        short = sid if sid == "(legacy)" else sid[:8]
        started = a["first"][:16].replace("T", " ")
        rows.append(
            f"| {short} | {a['calls']} | {_fmt(a['cr'])} | {_fmt(a['cw'])} | "
            f"{a['writes']} | ${a['cost']:,.4f} | {started} |"
        )
    return "\n".join(rows) + "\n"


def _session_activity_tables(events: list[CacheEvent], ttl: str) -> str:
    """**One table per session**, tokens (+ cost) by activity — the per-activity breakdown sliced per
    conversation. Each row is a channel (reply / tool / think / mood / …) inside that session."""
    from core.usage import pricing_for  # local import — avoids a module-load cycle

    write_mult = 2.0 if ttl == "1h" else 1.25
    sessions: dict[str, dict] = {}
    order: list[str] = []
    for e in events:
        sid = e.session_id or "(legacy)"
        if sid not in sessions:
            sessions[sid] = {"first": e.ts, "agg": {}}
            order.append(sid)
        p = pricing_for(e.model)
        cost = (
            e.input * p.input + e.output * p.output
            + e.cache_read * p.cache_read + e.cache_write * (p.input * write_mult)
        ) / 1_000_000
        a = sessions[sid]["agg"].setdefault(
            e.kind, {"calls": 0, "input": 0, "output": 0, "cr": 0, "cw": 0, "cost": 0.0}
        )
        a["calls"] += 1
        a["input"] += e.input
        a["output"] += e.output
        a["cr"] += e.cache_read
        a["cw"] += e.cache_write
        a["cost"] += cost

    rows = ["## Per session — tokens by activity\n"]
    for sid in order:
        s = sessions[sid]
        short = sid if sid == "(legacy)" else sid[:8]
        started = s["first"][:16].replace("T", " ")
        rows.append(f"### Session `{short}` — started {started}\n")
        rows.append(
            "| Activity | Calls | Input | Output | Cache read | Cache write | Est. cost |\n"
            "|---|--:|--:|--:|--:|--:|--:|"
        )
        for kind in sorted(s["agg"], key=lambda k: -s["agg"][k]["cost"]):
            a = s["agg"][kind]
            rows.append(
                f"| {kind} | {a['calls']} | {_fmt(a['input'])} | {_fmt(a['output'])} | "
                f"{_fmt(a['cr'])} | {_fmt(a['cw'])} | ${a['cost']:,.4f} |"
            )
        rows.append("")  # blank line between sessions
    return "\n".join(rows) + "\n"


def write_cache_report(events: list[CacheEvent], path: str | Path, *, generated_at: str, ttl: str = "5m") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_cache_report(events, generated_at=generated_at, ttl=ttl), encoding="utf-8")
