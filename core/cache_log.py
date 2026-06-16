"""Per-call prompt-cache monitor — log every model call's cache behaviour and attribute the writes.

The reply turn and the proactive thoughts use **separate** prompt caches (different prompt shapes →
different cache entries), so a write to one never affects the other. To explain "why is cache write
big?", this logs **one event per model call** with its **channel** — ``reply`` (the answer), ``tool``
(file-tool loop rounds), ``think`` (proactive %think), ``session-start`` (building the prompt sections:
day/week/facts digests), ``session-close`` (the wrap-up summary + facts), ``mood``, ``compaction`` —
the cache read/write, and the **gap since the last call of the same channel** — then classifies each
write as ``first`` / ``expired`` (gap > TTL) / ``changed`` (warm but the prefix moved).

It renders ``.lumi/cache-report.md`` (per-channel + by-cause) at session close, the diagnostic twin of
the v0.17 usage report. Off-by-default-friendly; an event is a cheap JSONL append.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

# Cache TTL → seconds, for the "expired" threshold.
TTL_SECONDS = {"1h": 3600, "5m": 300}


def ttl_seconds(ttl: str) -> int:
    return TTL_SECONDS.get(ttl, 300)


def classify(cache_write: int, gap_s: float | None, ttl_s: int) -> str:
    """Why a call wrote cache: ``none`` (no write), ``first`` (no prior call of this kind), ``expired``
    (gap > TTL → the entry timed out), or ``changed`` (warm, but the cached prefix moved)."""
    if cache_write <= 0:
        return "none"
    if gap_s is None:
        return "first"
    if gap_s > ttl_s:
        return "expired"
    return "changed"


@dataclass
class CacheEvent:
    ts: str
    kind: str            # reply / tool / think / mood / session-start / session-close / compaction
    cache_read: int
    cache_write: int
    input: int
    output: int
    gap_s: float | None  # seconds since the previous call of the SAME kind (None = first)
    cause: str           # none / first / expired / changed
    model: str = ""      # the model for this call (for the per-activity cost)


_FIELDS = ("ts", "kind", "cache_read", "cache_write", "input", "output", "gap_s", "cause", "model")


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
    expired: int = 0
    first: int = 0
    changed: int = 0

    def add(self, e: CacheEvent) -> None:
        self.calls += 1
        self.cache_read += e.cache_read
        self.cache_write += e.cache_write
        if e.cause != "none":
            self.writes += 1
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
    causes = {"expired": 0, "first": 0, "changed": 0}
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
        "> A higher read:write ratio = a warmer, cheaper cache. A write is a cache **miss**: "
        "**first** (first call of that channel), **expired** (idle > TTL), or **changed** (the cached "
        "prefix moved while warm).\n"
    )

    out.append("## By channel\n")
    out.append(
        "| Channel | Calls | Cache read | Cache write | Read:Write | Writes (expired / first / changed) |\n"
        "|---|--:|--:|--:|--:|--:|"
    )
    for kind in sorted(by_kind, key=lambda k: -by_kind[k].cache_write):
        a = by_kind[kind]
        out.append(
            f"| {kind} | {a.calls} | {_fmt(a.cache_read)} | {_fmt(a.cache_write)} | "
            f"{_ratio(a.cache_read, a.cache_write)} | {a.writes} ({a.expired} / {a.first} / {a.changed}) |"
        )
    out.append(
        f"| **TOTAL** | {total.calls} | {_fmt(total.cache_read)} | {_fmt(total.cache_write)} | "
        f"{_ratio(total.cache_read, total.cache_write)} | "
        f"{total.writes} ({total.expired} / {total.first} / {total.changed}) |\n"
    )

    out.append("## Writes by cause\n")
    out.append(
        f"- **expired** (idle > TTL): {causes['expired']}\n"
        f"- **first**-of-channel: {causes['first']}\n"
        f"- **changed**-prefix (warm but moved): {causes['changed']}\n"
    )

    out.append(_activity_table(events, ttl))
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


def write_cache_report(events: list[CacheEvent], path: str | Path, *, generated_at: str, ttl: str = "5m") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_cache_report(events, generated_at=generated_at, ttl=ttl), encoding="utf-8")
