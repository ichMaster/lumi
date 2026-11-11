"""Read-only views of an instance folder and of the box (v2.10, LUMI-253).

An instance folder is ``~/lumi/<instance>`` on the box (mounted read-only in the panel's container): its
``settings.yaml`` / ``secrets.yaml`` and its data root ``data/``. Every reader opens files for **reading only**,
reads at most the end of a large file, returns plain data, and turns a missing or malformed source into ``None`` /
an empty result — never an exception to the caller. Any secret value of the instance is replaced with ``•••`` in
every line a reader returns; the server token is only ever handed to the HTTP client.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.usage import pricing_for

REDACTED = "•••"
TAIL_BYTES = 4_000_000  # the most of a ledger / log a reader ever reads (its end)
SIZE_CACHE_S = 600  # the data-root size is walked at most every 10 minutes
_LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:,\d+)? (DEBUG|INFO|WARNING|ERROR|CRITICAL) (\S+?): ?(.*)$")
_MOOD_HEADER = re.compile(r"^===== (\d{4}-\d{2}-\d{2}) =====$", re.M)


@dataclass(frozen=True)
class Instance:
    """One panel instance: its name, its folder (settings + data root), its server URL and compose project."""

    name: str
    root: Path
    url: str
    project: str

    @property
    def data(self) -> Path:
        return self.root / "data"


# --- low-level reads --------------------------------------------------------------------------------------------


def tail_bytes(path: Path, limit: int = TAIL_BYTES) -> bytes:
    """The last ``limit`` bytes of ``path`` (from the start of a line), or ``b""`` when it can't be read."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - limit)
            f.seek(start)
            data = f.read()
    except OSError:
        return b""
    if start:
        cut = data.find(b"\n")
        data = data[cut + 1:] if cut >= 0 else b""
    return data


def tail_lines(path: Path, limit: int = TAIL_BYTES) -> list[str]:
    return tail_bytes(path, limit).decode("utf-8", errors="replace").splitlines()


def jsonl_tail(path: Path, limit: int = TAIL_BYTES) -> list[dict]:
    """The records at the end of a JSONL file; a malformed line is skipped."""
    out = []
    for line in tail_lines(path, limit):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _when(ts: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.astimezone()


# --- secrets: the server token + the values to redact ----------------------------------------------------------


def _secret_values(root: Path) -> dict[str, str]:
    """``group.key → value`` of the instance's ``secrets.yaml`` (empty when missing or unreadable)."""
    try:
        import yaml

        data = yaml.safe_load((root / "secrets.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — unreadable or malformed: nothing to hand out, nothing to redact
        return {}
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for group, block in data.items():
            if isinstance(block, dict):
                for key, value in block.items():
                    if value not in (None, ""):
                        out[f"{group}.{key}"] = str(value)
    return out


def server_token(inst: Instance) -> str:
    """The instance's client token, for ``GET /v1/state`` only — never part of a returned structure."""
    return _secret_values(inst.root).get("server.token", "")


def redactor(inst: Instance) -> Callable[[str], str]:
    """A function that replaces every secret value of the instance with ``•••`` (the longest first)."""
    values = sorted({v for v in _secret_values(inst.root).values() if len(v) >= 4}, key=len, reverse=True)

    def redact(text: str) -> str:
        for value in values:
            if value in text:
                text = text.replace(value, REDACTED)
        return text

    return redact


# --- settings as the instance sees them (its files, not the panel's environment) --------------------------------


def instance_settings(inst: Instance) -> dict[str, str]:
    """The instance's merged settings (``core.settings.resolve`` over its folder, no process environment); empty
    when its files are invalid."""
    from core import settings as st

    try:
        return st.resolve({}, folder=inst.root).values
    except st.SettingsError:
        return {}


def _on(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "on", "yes", "y"}


# --- usage, cost and latency ------------------------------------------------------------------------------------


def _cost(rec: dict, ttl: str) -> float:
    p = pricing_for(str(rec.get("model") or ""))
    return (int(rec.get("input") or 0) * p.input + int(rec.get("output") or 0) * p.output
            + int(rec.get("cache_read") or 0) * p.cache_read
            + int(rec.get("cache_write") or 0) * p.cache_write(ttl)) / 1_000_000


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(q * (len(ordered) - 1))))
    return ordered[k]


def _bucket() -> dict[str, Any]:
    return {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}


def _add(b: dict, rec: dict, cost: float) -> None:
    b["calls"] += 1
    for k in ("input", "output", "cache_read", "cache_write"):
        b[k] += int(rec.get(k) or 0)
    b["cost"] = round(b["cost"] + cost, 6)


def usage(inst: Instance, *, now: datetime, days: int = 7, slowest: int = 5) -> dict[str, Any]:
    """Tokens and estimated cost per day / model / kind, the cache hit ratio, turn latency and the slowest calls,
    from ``cache-log.jsonl`` (one row per model call); sessions per day from ``usage-ledger.jsonl``."""
    ttl = instance_settings(inst).get("LUMI_PROMPT_CACHE_TTL") or "5m"
    since = now - timedelta(days=days)
    today = now.date().isoformat()
    per_day: dict[str, dict] = {}
    per_model: dict[str, dict] = {}
    per_kind: dict[str, dict] = {}
    total = _bucket()
    replies: list[dict] = []
    last_reply: datetime | None = None
    turns_today = 0
    for rec in jsonl_tail(inst.data / "cache-log.jsonl"):
        when = _when(rec.get("ts"))
        if when is None or when < since:
            continue
        cost = _cost(rec, ttl)
        day = when.astimezone(now.tzinfo).date().isoformat()
        for table, key in ((per_day, day), (per_model, str(rec.get("model") or "?")),
                           (per_kind, str(rec.get("kind") or "?"))):
            _add(table.setdefault(key, _bucket()), rec, cost)
        _add(total, rec, cost)
        if rec.get("kind") == "reply":
            replies.append(rec | {"_when": when})
            if day == today:
                turns_today += 1
            if last_reply is None or when > last_reply:
                last_reply = when
    prompt_in = total["input"] + total["cache_read"] + total["cache_write"]
    model_ms = [float(r["latency_ms"]) for r in replies if isinstance(r.get("latency_ms"), (int, float))]
    timed = [r for r in replies if all(isinstance(r.get(k), (int, float)) for k in ("pre_ms", "latency_ms", "post_ms"))]
    turn_ms = [float(r["pre_ms"] + r["latency_ms"] + r["post_ms"]) for r in timed]
    sessions: dict[str, int] = {}
    for rec in jsonl_tail(inst.data / "usage-ledger.jsonl"):
        when = _when(rec.get("ended_at") or rec.get("started_at"))
        if when is not None and when >= since:
            day = when.astimezone(now.tzinfo).date().isoformat()
            sessions[day] = sessions.get(day, 0) + 1
    slow = sorted((r for r in replies if isinstance(r.get("latency_ms"), (int, float))),
                  key=lambda r: r["latency_ms"], reverse=True)[:slowest]
    return {
        "days": days,
        "per_day": dict(sorted(per_day.items())),
        "per_model": per_model,
        "per_kind": per_kind,
        "total": total,
        "today": per_day.get(today, _bucket()),
        "cache_hit_ratio": round(total["cache_read"] / prompt_in, 4) if prompt_in else None,
        "latency": {
            "turns": len(replies),
            "model_median_ms": _pct(model_ms, 0.5), "model_p95_ms": _pct(model_ms, 0.95),
            "turn_median_ms": _pct(turn_ms, 0.5), "turn_p95_ms": _pct(turn_ms, 0.95),
            "pre_median_ms": statistics.median([r["pre_ms"] for r in timed]) if timed else None,
            "post_median_ms": statistics.median([r["post_ms"] for r in timed]) if timed else None,
        },
        "slowest": [{"ts": r.get("ts"), "kind": r.get("kind"), "model": r.get("model"),
                     "latency_ms": r.get("latency_ms"), "pre_ms": r.get("pre_ms"), "post_ms": r.get("post_ms")}
                    for r in slow],
        "turns_today": turns_today,
        "last_turn_age_s": int((now - last_reply).total_seconds()) if last_reply else None,
        "last_call_age_s": _last_call_age(inst, now),
        "sessions_per_day": dict(sorted(sessions.items())),
    }


def _last_call_age(inst: Instance, now: datetime) -> int | None:
    rows = jsonl_tail(inst.data / "cache-log.jsonl", 64_000)
    whens = [w for r in rows if (w := _when(r.get("ts"))) is not None]
    return int((now - max(whens)).total_seconds()) if whens else None


# --- logs -------------------------------------------------------------------------------------------------------

LOG_SOURCES = {"lumi": "lumi.log", "telegram-inbound": "telegram-inbound.log",
               "telegram-outbound": "telegram-outbound.log"}


def parse_log_line(raw: str) -> dict | None:
    """``{date, time, level, source, message}`` from a Lumi log line; ``None`` for a continuation line."""
    m = _LOG_LINE.match(raw.rstrip())
    if not m:
        return None
    return {"date": m[1], "time": m[2], "level": m[3], "source": m[4], "message": m[5]}


def _line_when(rec: dict, tz) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{rec['date']}T{rec['time']}").replace(tzinfo=tz)
    except ValueError:
        return None


def _model_failure(rec: dict) -> bool:
    """A ``lumi.llm`` line that means the call did NOT answer: an ERROR, or a warning that a retry failed too
    (``… again``). A retry or a fallback that answered (``asking once more``, ``forcing``, ``→ blocking loop``) is not
    one (code review #2: those kept the model check amber on a recovering Gemini)."""
    return rec["level"] in ("ERROR", "CRITICAL") or " again" in rec["message"]


def errors(inst: Instance, *, now: datetime) -> dict[str, Any]:
    """``ERROR`` lines in the last 24 h; model failures (a call that didn't answer) and model retries (a call that
    recovered) in the last hour, from ``lumi.llm``; and the last error line (redacted)."""
    redact = redactor(inst)
    day_ago, hour_ago = now - timedelta(hours=24), now - timedelta(hours=1)
    errors_24h = failures_1h = retries_1h = 0
    last_error = None
    for raw in tail_lines(inst.data / "lumi.log"):
        rec = parse_log_line(raw)
        if rec is None:
            continue
        when = _line_when(rec, now.tzinfo)
        if when is None:
            continue
        if rec["level"] in ("ERROR", "CRITICAL") and when >= day_ago:
            errors_24h += 1
            last_error = f"{rec['date']} {rec['time']} {rec['source']}: {redact(rec['message'])}"
        if rec["source"].startswith("lumi.llm") and rec["level"] in ("WARNING", "ERROR") and when >= hour_ago:
            if _model_failure(rec):
                failures_1h += 1
            else:
                retries_1h += 1
    return {"errors_24h": errors_24h, "model_failures_1h": failures_1h, "model_retries_1h": retries_1h,
            "last_error": last_error}


def log_tail(inst: Instance, source: str = "lumi", *, level: str = "", q: str = "", tail: int = 200
             ) -> list[dict]:
    """The last ``tail`` lines of one of the instance's logs, filtered by level (that level and above) and text,
    every secret value redacted. An unknown source → ``[]``."""
    name = LOG_SOURCES.get(source)
    if name is None:
        return []
    order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    floor = order.index(level.upper()) if level.upper() in order else 0
    redact = redactor(inst)
    out: list[dict] = []
    for raw in tail_lines(inst.data / name, 1_000_000):
        rec = parse_log_line(raw)
        if rec is None:
            if out:  # a continuation (a traceback / a multi-line message) belongs to the line before
                out[-1]["message"] += "\n" + redact(raw)
            continue
        if order.index(rec["level"]) < floor:
            continue
        rec["message"] = redact(rec["message"])
        out.append(rec)
    if q:
        needle = q.lower()
        out = [r for r in out if needle in r["message"].lower() or needle in r["source"].lower()]
    return out[-max(1, min(tail, 2000)):]


# --- mood, scheduler, bus, marker -------------------------------------------------------------------------------


def mood_today(inst: Instance, *, today: str) -> dict[str, Any]:
    """Today's block of ``mood.log``: present, complete (``core.mood.reading_is_complete``), its theme."""
    from core.mood import reading_is_complete, split_theme

    text = tail_bytes(inst.data / "mood.log", 400_000).decode("utf-8", errors="replace")
    headers = list(_MOOD_HEADER.finditer(text))
    mine = [h for h in headers if h.group(1) == today]
    if not mine:
        return {"present": False, "complete": False, "theme": None}
    start = mine[-1].end()
    later = [h.start() for h in headers if h.start() > start]
    reading = text[start:later[0] if later else len(text)].strip()
    theme = split_theme(reading)
    return {"present": True, "complete": reading_is_complete(reading, want_theme=theme is not None) and bool(reading),
            "theme": theme}


def scheduler(inst: Instance, *, now: datetime) -> dict[str, Any]:
    """Whether the instance's scheduler is on, and its newest firing (``schedule.state``)."""
    enabled = _on(instance_settings(inst).get("LUMI_SCHEDULER"))
    try:
        state = json.loads((inst.data / "schedule.state").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    whens = [w for v in (state.values() if isinstance(state, dict) else []) if (w := _when(v)) is not None]
    newest = max(whens) if whens else None
    return {"enabled": enabled, "newest": newest.isoformat() if newest else None,
            "age_s": int((now - newest).total_seconds()) if newest else None, "rows": len(whens)}


def pending(jsonl: Path, pointer_path: Path, *, scan_bytes: int = 2_000_000) -> int | None:
    """Records after the consumer's pointer — read from the file's end (the ids only grow), so a large outbox
    costs only its tail. ``None`` when the queue file is missing."""
    from state import fifo

    if not jsonl.is_file():
        return None
    pointer = fifo.load_pointer(pointer_path)
    count = 0
    for line in reversed(tail_lines(jsonl, scan_bytes)):
        try:
            rec_id = int(json.loads(line).get("id", 0))
        except (ValueError, AttributeError, TypeError):
            continue
        if rec_id <= pointer:
            break
        count += 1
    return count


def bus(inst: Instance) -> dict[str, Any]:
    d = inst.data
    return {"inbox_pending": pending(d / "inbox.jsonl", d / "inbox.pos"),
            "outbox_pending": pending(d / "outbox.jsonl", d / "outbox.sent"),
            "bridge": _on(instance_settings(inst).get("LUMI_BRIDGE"))}


def marker(inst: Instance) -> str | None:
    from core.envguard import read_marker

    return read_marker(inst.data)


_SIZE_CACHE: dict[str, tuple[float, int]] = {}


def data_size(inst: Instance, *, clock: Callable[[], float] = time.monotonic) -> int | None:
    """The data root's size in bytes (walked; cached for 10 minutes)."""
    key = str(inst.data)
    cached = _SIZE_CACHE.get(key)
    if cached and clock() - cached[0] < SIZE_CACHE_S:
        return cached[1]
    if not inst.data.is_dir():
        return None
    total = 0
    for dirpath, _dirs, files in os.walk(inst.data):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    _SIZE_CACHE[key] = (clock(), total)
    return total


# --- the box ----------------------------------------------------------------------------------------------------


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def box(paths: Iterable[Path], *, proc: Path = Path("/proc")) -> dict[str, Any]:
    """The box: uptime, load, memory (``/proc`` — the host's, also inside a container) and disk free under each
    given path. A missing source → ``None``."""
    uptime = _read(proc / "uptime")
    load = _read(proc / "loadavg")
    mem: dict[str, int] = {}
    for line in (_read(proc / "meminfo") or "").splitlines():
        name, _, rest = line.partition(":")
        if name in ("MemTotal", "MemAvailable") and rest.split():
            mem[name] = int(rest.split()[0]) * 1024
    disks = {}
    for p in paths:
        try:
            st = os.statvfs(p)
        except OSError:
            continue
        disks[str(p)] = {"total": st.f_blocks * st.f_frsize, "free": st.f_bavail * st.f_frsize}
    return {
        "uptime_s": int(float(uptime.split()[0])) if uptime else None,
        "load": [float(x) for x in load.split()[:3]] if load else None,
        "memory": {"total": mem.get("MemTotal"), "available": mem.get("MemAvailable")} if mem else None,
        "disks": disks,
    }
