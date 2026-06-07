"""Ambient world context (v0.4) — *now / here* fetched once at startup.

A small **interface-independent** provider: ``fetch_world_context(clock, …)``
returns a :class:`WorldContext` snapshot — date/time + weekday from the **injected
clock**, and location / weather / short news from **config-gated** sources reached
over **thin HTTP** (no SDK, no tool loop). It is **graceful**: any source off or
erroring yields ``None``/``()`` — `fetch` never raises. Fetched text is **data,
not instructions** (truncated/sanitized). The v0.6 mood reads this layer; the
ambient block is injected into the prompt in LUMI-021.

The HTTP call is an injected ``http_get`` so tests run with no network.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from core.clock import Clock

HttpGet = Callable[[str], str]

# Ukrainian calendar bits + a small WMO weather-code lexicon (data, not exhaustive).
_WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя"]
_MONTHS = [
    "", "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
]
_WEATHER_CODES = {
    0: "ясно", 1: "переважно ясно", 2: "мінлива хмарність", 3: "хмарно",
    45: "туман", 48: "паморозь", 51: "мряка", 61: "дощ", 63: "дощ",
    71: "сніг", 73: "сніг", 80: "злива", 95: "гроза",
}


@dataclass(frozen=True)
class WorldContext:
    """A startup snapshot of Лілі's *now / here* (each field optional)."""

    now: str | None = None          # "2026-06-07 14:30"
    calendar: str | None = None     # "субота, 7 червня"
    location: str | None = None     # "Львів"
    weather: str | None = None      # "15°C, ясно"
    news: tuple[str, ...] = field(default_factory=tuple)


def _http_get(url: str, timeout: float = 4.0) -> str:
    # A browser-ish User-Agent — many sites/feeds reject the default urllib one (403).
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; Lumi/0.4; +ambient-context)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _calendar(weekday_idx: int, day: int, month: int) -> str:
    return f"{_WEEKDAYS[weekday_idx]}, {day} {_MONTHS[month]}"


def _clean(text: str, limit: int = 120) -> str:
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text).strip()
    return text[:limit]


def _weather(http_get: HttpGet, lat: float, lon: float) -> str | None:
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code"
        )
        cur = json.loads(http_get(url))["current"]
        desc = _WEATHER_CODES.get(cur.get("weather_code"))
        line = f"{round(cur['temperature_2m'])}°C"
        return f"{line}, {desc}" if desc else line
    except Exception:  # noqa: BLE001 — best-effort source; degrade to None
        return None


def _news(http_get: HttpGet, url: str, cap: int) -> tuple[str, ...]:
    try:
        text = http_get(url)
        # Titles INSIDE <item>/<entry> only (RSS/Atom) — skips the channel/image title.
        titles: list[str] = []
        for block in re.findall(r"<(?:item|entry)\b.*?</(?:item|entry)>", text, re.IGNORECASE | re.DOTALL):
            m = re.search(r"<title\b[^>]*>(.*?)</title>", block, re.IGNORECASE | re.DOTALL)
            if m and (title := _clean(m.group(1))):
                titles.append(title)
            if len(titles) >= max(0, cap):
                break
        return tuple(titles)
    except Exception:  # noqa: BLE001 — best-effort source; degrade to ()
        return ()


def ambient_line(wc: WorldContext | None, clock: Clock) -> str | None:
    """Render the ambient "now / here" block for the system prompt, or ``None``.

    The **date-time is recomputed per turn** from the clock (the snapshot's
    location/weather/news are held from startup). Framed as **background that
    colors tone, never competence**, and fetched text is quoted as **data**.
    """
    if wc is None:
        return None
    dt = clock()
    bits = [f"час: {dt.strftime('%Y-%m-%d %H:%M')}, {_calendar(dt.weekday(), dt.day, dt.month)}"]
    if wc.location:
        bits.append(f"місце: {wc.location}")
    if wc.weather:
        bits.append(f"погода: {wc.weather}")
    if wc.news:
        bits.append("новини: " + " | ".join(wc.news))
    return "Зараз і тут (фон, що лише фарбує тон — не змінює суті):\n" + "; ".join(bits) + "."


def fetch_world_context(
    clock: Clock,
    *,
    location: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    news_url: str | None = None,
    news_cap: int = 3,
    http_get: HttpGet = _http_get,
) -> WorldContext:
    """Build the ambient snapshot. Time/calendar from the clock; the rest config-gated."""
    dt = clock()
    weather = _weather(http_get, lat, lon) if (lat is not None and lon is not None) else None
    news = _news(http_get, news_url, news_cap) if news_url else ()
    return WorldContext(
        now=dt.strftime("%Y-%m-%d %H:%M"),
        calendar=_calendar(dt.weekday(), dt.day, dt.month),
        location=location or None,
        weather=weather,
        news=news,
    )
