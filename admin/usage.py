"""v5.1 (LUMI-342): the panel's usage and tool rollups, and the aggregates the Usage and Tools views read.

The instance's ledgers (``cache-log.jsonl`` for the model calls, ``media-ledger.jsonl`` for the other paid calls,
``tools.jsonl`` for her tool calls; see :mod:`core.ledgers`) are trimmed to 10 days by the instance. The panel rolls
each **closed** local day up into its own folder, so the history outlives the trim:

* ``usage/<instance>.jsonl`` — per closed day, one ``group`` row per provider × service × model × kind × origin ×
  channel (calls, tokens, units, the estimated cost, the calls with no price, the cache saving) and one ``day`` row
  (the owner's turns, the turn latency, the slowest calls);
* ``tools/<instance>.jsonl`` — per closed day, one ``group`` row per tool × outcome × origin (the count and the
  durations) and one ``tool`` row per tool (its last error, its slowest and its failed calls).

A day is closed once, at the first run after its end (``state.json`` keeps the last closed day per instance), so a
re-read changes nothing; rows older than :data:`KEEP_DAYS` are dropped. Days not closed yet (today, and yesterday
until the next run) are computed live from the ledgers. Nothing here writes outside the panel's own folder, and
nothing here makes a call that costs money.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import statistics
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from admin import readers as rd
from core.ledgers import MEDIA_LEDGER, TOOLS_LEDGER
from core.usage import media_cost, pricing_for

log = logging.getLogger("lumi.admin.usage")

KEEP_DAYS = 400
USAGE_DIR = "usage"
TOOLS_DIR = "tools"
STATE_FILE = "state.json"
CACHE_LOG = "cache-log.jsonl"
FIRST_READ = 2_000_000  # the first window read from a ledger's end; it grows until it covers the days asked for
SLOWEST_PER_DAY = 5
TOOL_SLOWEST = 3
TOOL_FAILED = 5
MS_SAMPLES = 500  # the most durations a tool row keeps for one day (evenly thinned beyond that)
RANGES = {"7d": 7, "30d": 30, "90d": 90}

# A provider's key in an instance's secrets (only whether it is set is ever read).
PROVIDER_KEYS = {
    "anthropic": "providers.anthropic_api_key", "google": "providers.gemini_api_key",
    "openai": "providers.openai_api_key", "deepseek": "providers.deepseek_api_key",
    "minimax": "providers.minimax_api_key", "elevenlabs": "voice.elevenlabs_api_key",
    "deepgram": "dictation.deepgram_api_key",
}


def model_provider(model: str) -> str:
    """The provider of a model call from its model id (the model-call record names no provider)."""
    m = (model or "").lower()
    if m.startswith("claude") or any(f in m for f in ("haiku", "sonnet", "opus", "fable")):
        return "anthropic"
    if m.startswith("gemini"):
        return "google"
    if m.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    if m.startswith("deepseek"):
        return "deepseek"
    if "minimax" in m or m.startswith("abab"):
        return "minimax"
    return "other"


# --- reading the ledgers -------------------------------------------------------------------------------------------


def _day(ts: Any, tz) -> str | None:
    try:
        when = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    return when.astimezone(tz).date().isoformat()


def read_since(path: Path, since: str, tz) -> list[dict]:
    """The records of a JSONL ledger dated ``since`` or later. The window read from the file's end grows until its
    first record is older than ``since`` (or the whole file is read), so a long ledger is never read whole each
    minute."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    limit = FIRST_READ
    while True:
        rows = rd.jsonl_tail(path, limit)
        first = next((d for r in rows if (d := _day(r.get("ts"), tz)) is not None), None)
        if limit >= size or (first is not None and first < since):
            break
        limit *= 4
    out = []
    for r in rows:
        d = _day(r.get("ts"), tz)
        if d is not None and d >= since:
            out.append(r | {"_day": d})
    return out


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(q * (len(ordered) - 1))))
    return ordered[k]


def _thin(values: list[float], cap: int = MS_SAMPLES) -> list[float]:
    ordered = sorted(values)
    if len(ordered) <= cap:
        return ordered
    step = len(ordered) / cap
    return [ordered[int(i * step)] for i in range(cap)]


# --- one day's rows ------------------------------------------------------------------------------------------------


def _model_cost(rec: dict, ttl: str) -> tuple[float, float]:
    """``(cost, saved)`` of one model call: the estimate, and what its cache reads saved against the input price."""
    p = pricing_for(str(rec.get("model") or ""))
    read = int(rec.get("cache_read") or 0)
    cost = (int(rec.get("input") or 0) * p.input + int(rec.get("output") or 0) * p.output + read * p.cache_read
            + int(rec.get("cache_write") or 0) * p.cache_write(ttl)) / 1_000_000
    return cost, read * (p.input - p.cache_read) / 1_000_000


def _group() -> dict[str, Any]:
    return {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "units": {}, "cost": 0.0,
            "unpriced": 0, "saved": 0.0}


def usage_rows(day: str, calls: Iterable[dict], media: Iterable[dict], ttl: str) -> list[dict]:
    """A day's usage rows: one ``group`` row per provider × service × model × kind × origin × channel, then one ``day``
    row (the owner's turns, the turn latency, the slowest calls). No row when the day has no record (a gap)."""
    groups: dict[tuple, dict] = {}
    replies: list[dict] = []
    for rec in calls:
        model = str(rec.get("model") or "")
        kind = str(rec.get("kind") or "?")
        key = (model_provider(model), "model", model, kind, str(rec.get("origin") or ""), str(rec.get("channel") or ""))
        g = groups.setdefault(key, _group())
        cost, saved = _model_cost(rec, ttl)
        g["calls"] += 1
        for k in ("input", "output", "cache_read", "cache_write"):
            g[k] += int(rec.get(k) or 0)
        g["cost"] += cost
        g["saved"] += saved
        if kind == "reply":
            replies.append(rec)
    for rec in media:
        service = str(rec.get("service") or "?")
        key = (str(rec.get("provider") or "?"), service, str(rec.get("model") or ""), service,
               str(rec.get("origin") or ""), str(rec.get("channel") or ""))
        g = groups.setdefault(key, _group())
        g["calls"] += 1
        for unit, amount in (rec.get("units") or {}).items():
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                if unit in ("input", "output"):
                    g[unit] += int(amount)
                else:
                    g["units"][unit] = round(g["units"].get(unit, 0) + amount, 3)
        cost = media_cost(rec)
        if cost is None:
            g["unpriced"] += 1
        else:
            g["cost"] += cost
    rows = []
    for (provider, service, model, kind, origin, channel), g in sorted(groups.items()):
        rows.append({"day": day, "row": "group", "provider": provider, "service": service, "model": model,
                     "kind": kind, "origin": origin, "channel": channel, **g,
                     "cost": round(g["cost"], 6), "saved": round(g["saved"], 6)})
    if not rows:
        return []
    rows.append(_day_row(day, replies))
    return rows


def _turn_ms(rec: dict) -> float | None:
    lat = rec.get("latency_ms")
    if not isinstance(lat, (int, float)):
        return None
    return float(lat) + sum(float(rec[k]) for k in ("pre_ms", "post_ms") if isinstance(rec.get(k), (int, float)))


def _day_row(day: str, replies: list[dict]) -> dict:
    owner = [r for r in replies if str(r.get("origin") or "turn") == "turn"]  # legacy records (no origin) were turns
    turn_ms = [t for r in owner if (t := _turn_ms(r)) is not None]
    split = {k: [float(r[k]) for r in owner if isinstance(r.get(k), (int, float))]
             for k in ("pre_ms", "latency_ms", "post_ms")}
    slow = sorted((r for r in replies if isinstance(r.get("latency_ms"), (int, float))),
                  key=lambda r: r["latency_ms"], reverse=True)[:SLOWEST_PER_DAY]
    return {
        "day": day, "row": "day", "turns": len(owner),
        "latency": {"count": len(turn_ms), "p25": _quantile(turn_ms, 0.25), "median": _quantile(turn_ms, 0.5),
                    "p95": _quantile(turn_ms, 0.95),
                    "pre": statistics.median(split["pre_ms"]) if split["pre_ms"] else None,
                    "model": statistics.median(split["latency_ms"]) if split["latency_ms"] else None,
                    "post": statistics.median(split["post_ms"]) if split["post_ms"] else None},
        "slowest": [{"ts": r.get("ts"), "kind": r.get("kind"), "model": r.get("model"), "origin": r.get("origin"),
                     "channel": r.get("channel"), "latency_ms": r.get("latency_ms"), "pre_ms": r.get("pre_ms"),
                     "post_ms": r.get("post_ms")} for r in slow],
    }


def _call_view(rec: dict) -> dict:
    return {"ts": rec.get("ts"), "ms": rec.get("ms"), "outcome": rec.get("outcome"), "origin": rec.get("origin"),
            "channel": rec.get("channel"), "input": rec.get("input")}


def tool_rows(day: str, records: Iterable[dict]) -> list[dict]:
    """A day's tool rows: one ``group`` row per tool × outcome × origin (the count and the durations), one ``tool``
    row per tool (its last error, its slowest and its latest failed calls)."""
    groups: dict[tuple, list[float]] = {}
    counts: dict[tuple, int] = {}
    per_tool: dict[str, list[dict]] = {}
    for rec in records:
        tool = str(rec.get("tool") or "?")
        key = (tool, str(rec.get("outcome") or "?"), str(rec.get("origin") or ""))
        counts[key] = counts.get(key, 0) + 1
        if isinstance(rec.get("ms"), (int, float)):
            groups.setdefault(key, []).append(float(rec["ms"]))
        per_tool.setdefault(tool, []).append(rec)
    rows = [{"day": day, "row": "group", "tool": t, "outcome": o, "origin": org, "count": n,
             "ms": _thin(groups.get((t, o, org), []))} for (t, o, org), n in sorted(counts.items())]
    for tool, recs in sorted(per_tool.items()):
        failed = [r for r in recs if str(r.get("outcome")) != "ok"]
        timed = [r for r in recs if isinstance(r.get("ms"), (int, float))]
        rows.append({"day": day, "row": "tool", "tool": tool,
                     "last_error": _call_view(max(failed, key=lambda r: str(r.get("ts")))) if failed else None,
                     "slowest": [_call_view(r) for r in sorted(timed, key=lambda r: r["ms"], reverse=True)
                                 [:TOOL_SLOWEST]],
                     "failed": [_call_view(r) for r in sorted(failed, key=lambda r: str(r.get("ts")))
                                [-TOOL_FAILED:]]})
    return rows


def _by_day(records: Iterable[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in records:
        out.setdefault(r["_day"], []).append(r)
    return out


# --- the rollups ---------------------------------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class Rollups:
    """Closes each instance's finished days into the panel's folder (see the module doc). :meth:`run` is called after
    every health run; it never raises."""

    def __init__(self, home: Path, instances: Iterable[rd.Instance], *,
                 clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
                 ttl_of: Callable[[rd.Instance], str] | None = None) -> None:
        self.home = Path(home)
        self.instances = list(instances)
        self.clock = clock
        self.ttl_of = ttl_of or (lambda inst: rd.instance_settings(inst).get("LUMI_PROMPT_CACHE_TTL") or "5m")

    def usage_path(self, name: str) -> Path:
        return self.home / USAGE_DIR / f"{name}.jsonl"

    def tools_path(self, name: str) -> Path:
        return self.home / TOOLS_DIR / f"{name}.jsonl"

    def closed_through(self, name: str) -> str | None:
        state = self._state()
        value = state.get(name)
        return value if isinstance(value, str) else None

    def _state(self) -> dict:
        try:
            data = json.loads((self.home / USAGE_DIR / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def run(self) -> dict[str, int]:
        """Close every finished day not closed yet; ``{instance: days closed}``. Never raises."""
        done: dict[str, int] = {}
        for inst in self.instances:
            try:
                done[inst.name] = self._close(inst)
            except Exception:  # noqa: BLE001 — a rollup problem never stops the health loop
                log.warning("the usage rollup of %s failed", inst.name, exc_info=True)
                done[inst.name] = 0
        return done

    def _close(self, inst: rd.Instance) -> int:
        now = self.clock()
        tz = now.tzinfo
        today = now.date()
        yesterday = (today - timedelta(days=1)).isoformat()
        floor = (today - timedelta(days=KEEP_DAYS)).isoformat()
        last = self.closed_through(inst.name)
        start = max(floor, (date.fromisoformat(last) + timedelta(days=1)).isoformat() if last else floor)
        if start > yesterday:
            return 0
        calls = _by_day(r for r in read_since(inst.data / CACHE_LOG, start, tz) if r["_day"] <= yesterday)
        media = _by_day(r for r in read_since(inst.data / MEDIA_LEDGER, start, tz) if r["_day"] <= yesterday)
        tools = _by_day(r for r in read_since(inst.data / TOOLS_LEDGER, start, tz) if r["_day"] <= yesterday)
        ttl = self.ttl_of(inst)
        days = sorted(set(calls) | set(media) | set(tools))
        new_usage = [row for d in days for row in usage_rows(d, calls.get(d, []), media.get(d, []), ttl)]
        new_tools = [row for d in days for row in tool_rows(d, tools.get(d, []))]
        self._append(self.usage_path(inst.name), new_usage, floor)
        self._append(self.tools_path(inst.name), new_tools, floor)
        state = self._state() | {inst.name: yesterday}
        _write_atomic(self.home / USAGE_DIR / STATE_FILE, json.dumps(state, sort_keys=True) + "\n")
        return len(days)

    @staticmethod
    def _append(path: Path, rows: list[dict], floor: str) -> None:
        """Add a closed day's rows and drop the rows older than ``floor``; rewritten atomically, only on a change."""
        old = _read_jsonl(path)
        kept = [r for r in old if str(r.get("day", "")) >= floor]
        if len(kept) != len(old) or rows:
            _write_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept + rows))

    # --- the rows of a range: closed from the rollups, the rest live from the ledgers ------------------------------

    def rows(self, inst: rd.Instance, since: str, until: str) -> tuple[list[dict], list[dict]]:
        """``(usage rows, tool rows)`` of ``since``…``until`` for one instance, each tagged with its instance."""
        now = self.clock()
        tz = now.tzinfo
        last = self.closed_through(inst.name) or ""
        usage = [r for r in _read_jsonl(self.usage_path(inst.name)) if since <= str(r.get("day", "")) <= until]
        tools = [r for r in _read_jsonl(self.tools_path(inst.name)) if since <= str(r.get("day", "")) <= until]
        live_from = max(since, (date.fromisoformat(last) + timedelta(days=1)).isoformat() if last else since)
        if live_from <= until:
            ttl = self.ttl_of(inst)
            calls = _by_day(r for r in read_since(inst.data / CACHE_LOG, live_from, tz) if r["_day"] <= until)
            media = _by_day(r for r in read_since(inst.data / MEDIA_LEDGER, live_from, tz) if r["_day"] <= until)
            trecs = _by_day(r for r in read_since(inst.data / TOOLS_LEDGER, live_from, tz) if r["_day"] <= until)
            for d in sorted(set(calls) | set(media)):
                usage += usage_rows(d, calls.get(d, []), media.get(d, []), ttl)
            for d in sorted(trecs):
                tools += tool_rows(d, trecs[d])
        tag = {"instance": inst.name}
        return [r | tag for r in usage], [r | tag for r in tools]


# --- the aggregates ------------------------------------------------------------------------------------------------


def range_days(name: str, today: date) -> tuple[date, date]:
    """``(first, last)`` day of a range: ``7d`` / ``30d`` / ``90d`` end today; ``month`` is the calendar month."""
    if name == "month":
        first = today.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return first, nxt - timedelta(days=1)
    if name not in RANGES:
        raise ValueError(f"unknown range {name!r} — allowed: {', '.join([*RANGES, 'month'])}")
    return today - timedelta(days=RANGES[name] - 1), today


def _days(first: date, last: date) -> list[str]:
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def _breakdown(groups: list[dict], key: Callable[[dict], str], total: float) -> list[dict]:
    acc: dict[str, dict] = {}
    for g in groups:
        b = acc.setdefault(key(g) or "—", {"cost": 0.0, "calls": 0, "unpriced": 0})
        b["cost"] += g["cost"]
        b["calls"] += g["calls"]
        b["unpriced"] += g.get("unpriced", 0)
    return [{"key": k, "cost": round(v["cost"], 4), "calls": v["calls"], "unpriced": v["unpriced"],
             "share": round(v["cost"] / total, 4) if total else 0.0}
            for k, v in sorted(acc.items(), key=lambda kv: (-kv[1]["cost"], kv[0]))]


def _month_total(groups: list[dict], first: date, last: date) -> float:
    lo, hi = first.isoformat(), last.isoformat()
    return round(sum(g["cost"] for g in groups if lo <= g["day"] <= hi), 4)


def usage_view(rollups: Rollups, instances: list[rd.Instance], *, range_name: str,
               keyed: dict[str, bool] | None = None) -> dict[str, Any]:
    """The Usage view of an agent's instances over a range (see LUMI-342): totals, the month and its forecast, the
    cost per day by provider, the breakdowns, the cache, the turn latency and the slowest calls."""
    now = rollups.clock()
    today = now.date()
    first, last = range_days(range_name, today)
    month_first = today.replace(day=1)
    prev_last = month_first - timedelta(days=1)
    prev_first = prev_last.replace(day=1)
    read_from = min(first, prev_first).isoformat()
    groups: list[dict] = []
    day_rows: list[dict] = []
    for inst in instances:
        rows, _ = rollups.rows(inst, read_from, today.isoformat())
        groups += [r for r in rows if r.get("row") == "group"]
        day_rows += [r for r in rows if r.get("row") == "day"]
    lo, hi = first.isoformat(), min(last, today).isoformat()
    in_range = [g for g in groups if lo <= g["day"] <= hi]
    days_in_range = [d for d in day_rows if lo <= d["day"] <= hi]
    total = round(sum(g["cost"] for g in in_range), 4)
    turns = sum(d["turns"] for d in days_in_range)

    # the month: to date, the forecast to its end, the previous month
    to_date = _month_total(groups, month_first, today)
    elapsed = today.day
    month_days = ((month_first + timedelta(days=32)).replace(day=1) - month_first).days
    per_day_avg = to_date / elapsed if elapsed else 0.0
    month = {"label": month_first.strftime("%B"), "to_date": to_date,
             "forecast": round(to_date + per_day_avg * (month_days - elapsed), 4),
             "previous": _month_total(groups, prev_first, prev_last), "previous_label": prev_first.strftime("%B"),
             "elapsed": elapsed, "days": month_days,
             "by_instance": {i.name: _month_total([g for g in groups if g["instance"] == i.name], month_first, today)
                             for i in instances}}

    # the cost per day, by provider; a day without any row is a gap, a day after today a forecast
    providers = sorted({g["provider"] for g in in_range})
    per_day = []
    with_data = {g["day"] for g in groups}
    for d in _days(first, last):
        if d > today.isoformat():
            per_day.append({"day": d, "future": True, "forecast": round(per_day_avg, 4)})
            continue
        rows = [g for g in in_range if g["day"] == d]
        by_p: dict[str, float] = {}
        for g in rows:
            by_p[g["provider"]] = round(by_p.get(g["provider"], 0.0) + g["cost"], 6)
        per_day.append({"day": d, "gap": d not in with_data, "total": round(sum(by_p.values()), 4),
                        "providers": by_p})

    prompt_in = sum(g["input"] + g["cache_read"] + g["cache_write"] for g in in_range if g["service"] == "model")
    read = sum(g["cache_read"] for g in in_range if g["service"] == "model")
    latency = []
    lat_days = {d["day"]: d for d in days_in_range if d["latency"]["count"]}
    for d in _days(first, min(last, today)):
        same = [x for x in days_in_range if x["day"] == d and x["latency"]["count"]]
        if not same:
            latency.append({"day": d, "gap": True})
            continue
        n = sum(x["latency"]["count"] for x in same)
        latency.append({"day": d, "gap": False, "count": n,
                        **{q: _weighted([(x["latency"][q], x["latency"]["count"]) for x in same])
                           for q in ("p25", "median", "p95")}})
    split = {k: _weighted([(x["latency"][k], x["latency"]["count"]) for x in lat_days.values()])
             for k in ("pre", "model", "post")}
    slowest = sorted((s | {"instance": d["instance"]} for d in days_in_range for s in d["slowest"]),
                     key=lambda s: s.get("latency_ms") or 0, reverse=True)[:10]
    return {
        "range": range_name, "from": first.isoformat(), "to": last.isoformat(),
        "instances": [i.name for i in instances],
        "total": total, "calls": sum(g["calls"] for g in in_range),
        "unpriced": sum(g.get("unpriced", 0) for g in in_range),
        "by_instance": {i.name: round(sum(g["cost"] for g in in_range if g["instance"] == i.name), 4)
                        for i in instances},
        "turns": turns, "per_turn": round(total / turns, 4) if turns else None,
        "month": month,
        "providers": [{"provider": p, "keyed": (keyed or {}).get(p)} for p in providers],
        "per_day": per_day,
        "by_provider": _breakdown(in_range, lambda g: g["provider"], total),
        "by_model": _breakdown(in_range, lambda g: g["model"] or g["service"], total),
        "by_kind": _breakdown(in_range, lambda g: g["kind"], total),
        "by_origin": _breakdown(in_range, lambda g: g["origin"], total),
        "by_channel": _breakdown(in_range, lambda g: g["channel"], total),
        "cache": {"read": read, "prompt_in": prompt_in, "ratio": round(read / prompt_in, 4) if prompt_in else None,
                  "saved": round(sum(g.get("saved", 0.0) for g in in_range), 4)},
        "latency": latency, "latency_split": split, "slowest": slowest,
    }


def _weighted(pairs: list[tuple[Any, int]]) -> float | None:
    """The count-weighted mean of per-day figures (a day's median, a day's p95): the range's figure, approximate."""
    good = [(float(v), n) for v, n in pairs if isinstance(v, (int, float)) and n]
    weight = sum(n for _, n in good)
    return round(sum(v * n for v, n in good) / weight, 1) if weight else None


def tools_view(rollups: Rollups, instances: list[rd.Instance], *, range_name: str,
               tool: str | None = None) -> dict[str, Any]:
    """The Tools view over a range: per tool the calls, the failures and their rate, the median and p95 duration, the
    last error and the origins that use it; for ``tool``, its calls per day and its slowest and failed calls."""
    today = rollups.clock().date()
    first, last = range_days(range_name, today)
    lo, hi = first.isoformat(), min(last, today).isoformat()
    groups: list[dict] = []
    details: list[dict] = []
    for inst in instances:
        _, rows = rollups.rows(inst, lo, hi)
        groups += [r for r in rows if r.get("row") == "group"]
        details += [r for r in rows if r.get("row") == "tool"]
    table: dict[str, dict] = {}
    for g in groups:
        t = table.setdefault(g["tool"], {"tool": g["tool"], "calls": 0, "failed": 0, "_ms": [], "origins": {}})
        t["calls"] += g["count"]
        if g["outcome"] != "ok":
            t["failed"] += g["count"]
        t["_ms"] += g.get("ms") or []
        origin = g["origin"] or "—"
        t["origins"][origin] = t["origins"].get(origin, 0) + g["count"]
    rows = []
    for name, t in table.items():
        errors = [d["last_error"] for d in details if d["tool"] == name and d.get("last_error")]
        rows.append({"tool": name, "calls": t["calls"], "failed": t["failed"],
                     "rate": round(t["failed"] / t["calls"], 4) if t["calls"] else 0.0,
                     "median_ms": _quantile(t["_ms"], 0.5), "p95_ms": _quantile(t["_ms"], 0.95),
                     "last_error": max(errors, key=lambda e: str(e.get("ts"))) if errors else None,
                     "used_by": [o for o, _ in sorted(t["origins"].items(), key=lambda kv: (-kv[1], kv[0]))]})
    rows.sort(key=lambda r: (-r["calls"], r["tool"]))
    out: dict[str, Any] = {"range": range_name, "from": lo, "to": hi, "instances": [i.name for i in instances],
                           "tools": rows}
    if tool is not None:
        per_day = []
        seen = {g["day"] for g in groups}
        for d in _days(first, min(last, today)):
            mine = [g for g in groups if g["tool"] == tool and g["day"] == d]
            ok = sum(g["count"] for g in mine if g["outcome"] == "ok")
            per_day.append({"day": d, "gap": d not in seen, "ok": ok, "failed": sum(g["count"] for g in mine) - ok})
        mine = [d for d in details if d["tool"] == tool]
        out["tool"] = {
            "name": tool, "per_day": per_day,
            "slowest": sorted((s | {"instance": d["instance"]} for d in mine for s in d["slowest"]),
                              key=lambda s: s.get("ms") or 0, reverse=True)[:10],
            "failed": sorted((s | {"instance": d["instance"]} for d in mine for s in d["failed"]),
                             key=lambda s: str(s.get("ts")), reverse=True)[:20],
        }
    return out


CSV_COLUMNS = ("day", "instance", "provider", "service", "model", "kind", "origin", "channel", "calls", "input",
               "output", "cache_read", "cache_write", "units", "cost_usd", "unpriced")


def usage_csv(rollups: Rollups, instances: list[rd.Instance], *, range_name: str) -> str:
    """The range's usage rows as CSV, one line per day × instance × group; ``units`` as ``name=value`` pairs."""
    today = rollups.clock().date()
    first, last = range_days(range_name, today)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for inst in instances:
        rows, _ = rollups.rows(inst, first.isoformat(), min(last, today).isoformat())
        for r in sorted((r for r in rows if r.get("row") == "group"), key=lambda r: (r["day"], r["provider"])):
            units = " ".join(f"{k}={v}" for k, v in sorted((r.get("units") or {}).items()))
            writer.writerow([r["day"], r["instance"], r["provider"], r["service"], r["model"], r["kind"], r["origin"],
                             r["channel"], r["calls"], r["input"], r["output"], r["cache_read"], r["cache_write"],
                             units, f"{r['cost']:.6f}", r.get("unpriced", 0)])
    return buf.getvalue()


def keyed_providers(instances: list[rd.Instance]) -> dict[str, bool]:
    """Whether any of the instances has each provider's key set — never the value."""
    from core import settings as st

    out = {p: False for p in PROVIDER_KEYS}
    for inst in instances:
        try:
            parsed = st.parse_yaml((inst.root / "secrets.yaml").read_text(encoding="utf-8"), "secrets.yaml") or {}
        except Exception:  # noqa: BLE001 — an unreadable secrets file means "not set" here
            continue
        for provider, key in PROVIDER_KEYS.items():
            group, _, name = key.partition(".")
            block = parsed.get(group) if isinstance(parsed, dict) else None
            if isinstance(block, dict) and str(block.get(name) or "").strip():
                out[provider] = True
    return out
