"""Alerts (v2.11, LUMI-262): one Telegram message when an instance starts failing, one when it recovers — through a
**dedicated admin bot** (its own BotFather token in the admin ``secrets.yaml``; never her prod or dev bot).

After each health run, :class:`Alerts` looks at the instances it watches (prod by default). An instance **fails**
while any of its checks is red:

- none red → some red: an episode starts and one "failing" message names the red checks;
- inside the episode nothing more is sent, however the red set changes;
- some red → none: one "recovered after N min" message — only when a failing message went out for that episode.

**Rate limit:** at most one failing message per instance per :data:`RATE_S`. A failure that starts again inside that
window (a flapping instance) waits; if it is still failing when the window ends, its message goes then. The sender
never raises into the health runner, and never logs the bot token.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from admin.health import RED, CheckResult
from admin.registry import AlertsConfig

RATE_S = 600
DETAIL_MAX = 120
TELEGRAM_API = "https://api.telegram.org"
_log = logging.getLogger("lumi.admin.alerts")


class Sender(Protocol):
    def send(self, text: str) -> bool: ...


Post = Callable[[str, bytes, float], int]  # (url, JSON body, timeout) → HTTP status


def _urllib_post(url: str, body: bytes, timeout: float) -> int:  # pragma: no cover - the real network
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — a fixed https host
        return resp.status


class TelegramSender:
    """``sendMessage`` to the one chat, as the admin bot. A failure is logged (without the token) and returns
    ``False`` — never raised."""

    def __init__(self, token: str, chat_id: int, *, post: Post = _urllib_post, timeout: float = 10.0) -> None:
        self._token = token
        self.chat_id = chat_id
        self.post = post
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"TelegramSender(chat_id={self.chat_id})"

    def send(self, text: str) -> bool:
        url = f"{TELEGRAM_API}/bot{self._token}/sendMessage"
        body = json.dumps({"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
                          ensure_ascii=False).encode("utf-8")
        try:
            status = self.post(url, body, self.timeout)
        except Exception as exc:  # noqa: BLE001 — an alert never breaks the health runner
            why = f"{type(exc).__name__}: {exc}".replace(self._token, "•••") if self._token else type(exc).__name__
            _log.warning("alert not sent — %s", why)
            return False
        if not 200 <= int(status) < 300:
            _log.warning("alert not sent — Telegram answered HTTP %s", status)
            return False
        return True


class FakeSender:
    """The tests' sender: keeps every text; ``fail`` makes sends return False."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail = False

    def send(self, text: str) -> bool:
        if self.fail:
            return False
        self.sent.append(text)
        return True


@dataclass
class _Episode:
    failing: bool = False
    since: datetime | None = None
    alerted: bool = False
    last_alert: datetime | None = None


def _duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _short(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= DETAIL_MAX else text[:DETAIL_MAX - 1] + "…"


class Alerts:
    """The alert state machine over the health runs (the clock injected; pure but for the sender)."""

    def __init__(self, cfg: AlertsConfig, sender: Sender | None, *,
                 clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
                 redact: Callable[[str, str], str] | None = None, rate_s: float = RATE_S) -> None:
        self.cfg = cfg
        self.sender = sender
        self.clock = clock
        self.redact = redact
        self.rate = timedelta(seconds=rate_s)
        self.episodes: dict[str, _Episode] = {}
        self.last_sent: dict[str, str] = {}

    @property
    def on(self) -> bool:
        return self.sender is not None and self.cfg.off_reason is None

    def _clean(self, instance: str, text: str) -> str:
        return self.redact(instance, text) if self.redact is not None else text

    def _send(self, instance: str, text: str) -> bool:
        if self.redact is not None:
            text = self.redact(instance, text)
        ok = self.sender is not None and self.sender.send(text)
        if ok:
            self.last_sent[instance] = self.clock().isoformat(timespec="seconds")
        return ok

    def observe(self, latest: dict[str, list[CheckResult]]) -> None:
        """Look at one health run's results; send what an episode boundary calls for."""
        if not self.on:
            return
        now = self.clock()
        for name in self.cfg.instances:
            results = latest.get(name)
            if results is None:
                continue
            red = [r for r in results if r.status == RED]
            ep = self.episodes.setdefault(name, _Episode())
            if red and not ep.failing:
                ep.failing, ep.since, ep.alerted = True, now, False
            if red and not ep.alerted and (ep.last_alert is None or now - ep.last_alert >= self.rate):
                checks = ", ".join(f"{r.name} ({_short(self._clean(name, r.detail))})" if r.detail else r.name
                                   for r in red)  # redacted before the cap: a cut secret can't be found (review #3)
                if self._send(name, f"⚠ Lumi {name} failing: {checks}"):
                    ep.alerted, ep.last_alert = True, now
            elif not red and ep.failing:
                if ep.alerted:
                    self._send(name, f"✓ Lumi {name} recovered after {_duration(now - (ep.since or now))}")
                ep.failing, ep.since, ep.alerted = False, None, False

    def status(self) -> dict:
        """For the page: on / off (and why), the instances watched, who's failing, the last message times."""
        return {"on": self.on, "why": None if self.on else (self.cfg.off_reason or "alerts are off"),
                "instances": list(self.cfg.instances),
                "failing": sorted(n for n, ep in self.episodes.items() if ep.failing),
                "last_sent": dict(self.last_sent)}
