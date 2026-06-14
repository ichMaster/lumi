"""Token-usage ledger + estimated-cost report (per session → ``.lumi/``).

The core accumulates token usage across every model call (replies + background thinks/mood/
summaries) in :class:`~core.agent.UsageTotals`. This module turns the **per-session delta** of those
totals into a durable ledger (``.lumi/usage-ledger.jsonl``, one row per closed session) and renders a
human-readable markdown report (``.lumi/usage-report.md``) aggregated by **month / week / day /
session**, with an **estimated cost** from Anthropic list prices.

Costs are estimates: list prices, cache-read at 10 % of input, cache-write at 1.25× (5m TTL) or 2×
(1h TTL). They track real spend closely but are not a billing source of truth.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

# --- pricing (USD per 1,000,000 tokens; Anthropic list prices) -----------------------------------


@dataclass(frozen=True)
class ModelPricing:
    """Per-1M-token list prices for one model. Cache prices are derived from ``input``."""

    input: float
    output: float

    @property
    def cache_read(self) -> float:
        return self.input * 0.10  # cached input is billed at 10 % of the input rate

    def cache_write(self, ttl: str) -> float:
        return self.input * (2.0 if ttl == "1h" else 1.25)  # 1h write 2×, 5m write 1.25×


PRICING: dict[str, ModelPricing] = {
    "claude-opus-4-8": ModelPricing(5.0, 25.0),
    "claude-opus-4-7": ModelPricing(5.0, 25.0),
    "claude-opus-4-6": ModelPricing(5.0, 25.0),
    "claude-opus-4-5": ModelPricing(5.0, 25.0),
    "claude-sonnet-4-6": ModelPricing(3.0, 15.0),
    "claude-sonnet-4-5": ModelPricing(3.0, 15.0),
    "claude-haiku-4-5": ModelPricing(1.0, 5.0),
}
_DEFAULT_PRICING = ModelPricing(5.0, 25.0)  # unknown model → an opus-tier estimate


def pricing_for(model: str) -> ModelPricing:
    """Best-effort price lookup: exact id, then prefix (handles date/``[1m]`` suffixes), then family."""
    m = (model or "").strip().lower()
    if m in PRICING:
        return PRICING[m]
    for key, price in PRICING.items():
        if m.startswith(key):
            return price
    if "haiku" in m:
        return PRICING["claude-haiku-4-5"]
    if "sonnet" in m:
        return PRICING["claude-sonnet-4-6"]
    return _DEFAULT_PRICING


# --- the per-session record ----------------------------------------------------------------------


@dataclass
class UsageRecord:
    """One closed session's token usage (the delta of the running totals over that session)."""

    session_id: str
    user_id: str
    model: str
    started_at: str
    ended_at: str
    turns: int
    input: int
    output: int
    cache_read: int
    cache_write: int
    cache_ttl: str = "5m"

    @property
    def total_tokens(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    @property
    def cost_usd(self) -> float:
        p = pricing_for(self.model)
        return (
            self.input * p.input
            + self.output * p.output
            + self.cache_read * p.cache_read
            + self.cache_write * p.cache_write(self.cache_ttl)
        ) / 1_000_000


# --- ledger I/O (append-only JSONL) --------------------------------------------------------------


def append_record(path: str | Path, record: UsageRecord) -> None:
    """Append one record to the JSONL ledger (creates the file/dir if needed)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def load_records(path: str | Path) -> list[UsageRecord]:
    """Read every record from the ledger (skips blank/corrupt lines; missing file → ``[]``)."""
    p = Path(path)
    if not p.is_file():
        return []
    out: list[UsageRecord] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            out.append(UsageRecord(**{k: data[k] for k in _FIELDS if k in data}))
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
    return out


_FIELDS = (
    "session_id", "user_id", "model", "started_at", "ended_at",
    "turns", "input", "output", "cache_read", "cache_write", "cache_ttl",
)


# --- report rendering ----------------------------------------------------------------------------


def _period_keys(iso_ts: str) -> tuple[str, str, str]:
    """(month ``YYYY-MM``, ISO week ``YYYY-Www``, day ``YYYY-MM-DD``) for a record's start time."""
    try:
        d = datetime.fromisoformat(iso_ts).date()
    except (ValueError, TypeError):
        return ("unknown", "unknown", "unknown")
    iso = d.isocalendar()
    return (f"{d.year:04d}-{d.month:02d}", f"{iso[0]:04d}-W{iso[1]:02d}", d.isoformat())


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_cost(c: float) -> str:
    return f"${c:,.4f}" if c < 1 else f"${c:,.2f}"


@dataclass
class _Agg:
    sessions: int = 0
    turns: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0
    cost: float = 0.0

    def add(self, r: UsageRecord) -> None:
        self.sessions += 1
        self.turns += r.turns
        self.input += r.input
        self.output += r.output
        self.cache_read += r.cache_read
        self.cache_write += r.cache_write
        self.total += r.total_tokens
        self.cost += r.cost_usd


def _group(records: list[UsageRecord], which: int) -> dict[str, _Agg]:
    out: dict[str, _Agg] = {}
    for r in records:
        key = _period_keys(r.started_at)[which]
        out.setdefault(key, _Agg()).add(r)
    return out


_PERIOD_HEADER = (
    "| {label} | Sessions | Turns | Input | Output | Cache read | Cache write | Total tokens | Est. cost |\n"
    "|---|--:|--:|--:|--:|--:|--:|--:|--:|"
)


def _period_table(label: str, groups: dict[str, _Agg]) -> str:
    rows = [_PERIOD_HEADER.format(label=label)]
    for key in sorted(groups, reverse=True):  # most recent first
        a = groups[key]
        rows.append(
            f"| {key} | {a.sessions} | {_fmt_int(a.turns)} | {_fmt_int(a.input)} | "
            f"{_fmt_int(a.output)} | {_fmt_int(a.cache_read)} | {_fmt_int(a.cache_write)} | "
            f"{_fmt_int(a.total)} | {_fmt_cost(a.cost)} |"
        )
    return "\n".join(rows)


def render_report(records: list[UsageRecord], *, generated_at: str, recent_sessions: int = 50) -> str:
    """Render the full markdown usage report from the ledger records."""
    total = _Agg()
    for r in records:
        total.add(r)

    out: list[str] = []
    out.append("# Lumi — token usage & estimated cost\n")
    out.append(f"_Generated {generated_at} · {len(records)} sessions logged._\n")

    out.append("## Overall\n")
    out.append(
        f"- **Estimated cost:** **{_fmt_cost(total.cost)}**\n"
        f"- **Total tokens:** {_fmt_int(total.total)} "
        f"(input {_fmt_int(total.input)} · output {_fmt_int(total.output)} · "
        f"cache read {_fmt_int(total.cache_read)} · cache write {_fmt_int(total.cache_write)})\n"
        f"- **Sessions:** {total.sessions} · **User turns:** {_fmt_int(total.turns)}\n"
    )
    out.append(
        "> Costs are **estimates** from Anthropic list prices "
        "(cache read = 10% of input; cache write = 1.25× at 5m TTL, 2× at 1h TTL). "
        "Not a billing source of truth.\n"
    )

    out.append("## By month\n" + _period_table("Month", _group(records, 0)) + "\n")
    out.append("## By week (ISO)\n" + _period_table("Week", _group(records, 1)) + "\n")
    out.append("## By day\n" + _period_table("Day", _group(records, 2)) + "\n")

    out.append(f"## Recent sessions (last {recent_sessions})\n")
    out.append(
        "| Started | Session | Model | Turns | Input | Output | Cache read | Cache write | "
        "Total | Est. cost |\n|---|---|---|--:|--:|--:|--:|--:|--:|--:|"
    )
    for r in sorted(records, key=lambda r: r.started_at, reverse=True)[:recent_sessions]:
        started = r.started_at[:16].replace("T", " ")
        out.append(
            f"| {started} | `{r.session_id[:8]}` | {r.model} | {r.turns} | {_fmt_int(r.input)} | "
            f"{_fmt_int(r.output)} | {_fmt_int(r.cache_read)} | {_fmt_int(r.cache_write)} | "
            f"{_fmt_int(r.total_tokens)} | {_fmt_cost(r.cost_usd)} |"
        )

    return "\n".join(out) + "\n"


def write_report(records: list[UsageRecord], path: str | Path, *, generated_at: str) -> None:
    """Render and write the markdown report (creates the dir if needed)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_report(records, generated_at=generated_at), encoding="utf-8")
