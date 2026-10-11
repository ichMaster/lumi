"""The brain host (v2.5 LUMI-224/225) — her time inside ``./lumi-server``.

What the in-process TUI runs on its timers, the server runs on one background thread: the v0.42 scheduler
(+ its startup catch-up), the fast tick, — when the scheduler is off — the legacy idle think / nudge, and
the v0.13 file bus: inbox lines (Telegram, dictation) become turns, her replies go to the outbox (the
Telegram daemons run beside the server on the same data root; the server is the single outbox writer).
Every act takes the service's lock as **the server's own act** (``_Service.background``): it waits for a
client turn, and a client request arriving meanwhile waits for it (never interleaved, never a ``409``).

This module is only the glue — the decisions are the host-neutral ones the TUI uses too
(:mod:`core.scheduler`, :mod:`core.nudge`, ``Core.run_directive`` / ``tick_think``). ``tick(now)`` is one
beat; tests drive it (and the cadence-free ``scheduler_tick`` / ``fast_tick`` / ``idle_tick``) with an
injected clock and a mock model. Quiet hours and day caps are the scheduler's own, unchanged.
"""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from core.nudge import load_nudges, pick_nudge_index, proactive_due
from core.schedule import load_schedule
from core.scheduler import Scheduler, TickService, graduates, is_loud, scheduled_text, spoken_seed
from core.thoughts import parse_directive
from state.bus import drain_inbox_records, mirror_reply, mirror_thought

log = logging.getLogger("lumi.brain")

BEAT_S = 1.0  # the host's heartbeat; each mechanism keeps its own cadence on top of it
IDLE_EVERY_S = 30  # the legacy idle think / nudge cadence (the TUI's 30 s timers)


class BrainHost:
    """Runs her scheduled and idle acts in the server process — see the module doc."""

    def __init__(self, service: Any, cfg: Any, *, clock: Callable[[], datetime] | None = None,
                 rng: Callable[[int, int], int] = random.randint) -> None:
        self._service = service
        self._core = service.core
        self._cfg = cfg
        self._clock = clock or self._core.clock
        self._rng = rng
        now = self._clock()
        self._scheduler: Scheduler | None = None
        self._ticks: TickService | None = None
        if cfg.scheduler:
            self._scheduler = Scheduler(load_schedule(cfg.schedule_path), cfg.schedule_state_path,
                                        day_cap=cfg.sched_day_cap, quiet_hours=cfg.thoughts_quiet_hours)
            self._ticks = TickService()  # the v3 needs register their %update_state here
        # When the scheduler is on it owns the idle rule (as in-process); off → the legacy timers run.
        self._nudges = load_nudges(cfg.nudge_path) if (cfg.idle_nudge and not cfg.scheduler) else []
        self._think = bool(cfg.thoughts and not cfg.scheduler)
        self._think_seeds = load_nudges(cfg.think_seeds_path) if self._think else []
        self._seed_idx: dict[str, int] = {}
        self._nudge_idx = self._think_seed_idx = -1
        self._think_n = 0
        self._last_nudge = self._last_think = now
        # The inbox is drained when the Telegram bridge or dictation feeds it (as in-process).
        self._inbox = cfg.inbox_path if (cfg.bridge or cfg.dictation) else None
        self._due = {"sched": now, "fast": now, "idle": now}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        """Whether anything is hosted (all proactive flags off → the host does nothing)."""
        return bool(self._scheduler or self._nudges or self._think or self._inbox)

    # --- the beat --------------------------------------------------------------------------------------
    def tick(self, now: datetime) -> None:
        """One beat: whatever is due at ``now`` runs (each mechanism on its own cadence)."""
        if self._scheduler is not None:
            if now >= self._due["sched"]:
                self._due["sched"] = now + timedelta(seconds=max(1.0, self._cfg.sched_tick_ms / 1000))
                self.scheduler_tick(now)
            if now >= self._due["fast"]:
                self._due["fast"] = now + timedelta(seconds=max(1.0, self._cfg.sched_tick_fast_ms / 1000))
                self.fast_tick()
        elif now >= self._due["idle"] and (self._nudges or self._think):
            self._due["idle"] = now + timedelta(seconds=IDLE_EVERY_S)
            self.idle_tick(now)
        if self._inbox is not None:
            self.inbox_tick(now)  # every beat (the TUI polls each second)

    def scheduler_tick(self, now: datetime) -> None:
        """Fire every due schedule entry — never while a turn holds the lock (the next beat re-checks, as the
        TUI skips while busy); ``due_now`` stamps only what it returns."""
        if self._scheduler is None or self._service.lock.locked():
            return
        for entry in self._scheduler.due_now(now, last_input=self._service.last_input):
            self._run_scheduled(entry)

    def catch_up(self, now: datetime) -> None:
        """The startup catch-up: a fixed-time entry missed while the brain was down fires once."""
        if self._scheduler is None:
            return
        for entry in self._scheduler.catch_up(now, catchup_h=self._cfg.sched_catchup_h):
            self._run_scheduled(entry)

    def fast_tick(self) -> None:
        if self._ticks is not None:
            self._ticks.tick()  # ephemeral code handlers; a failing one is logged and swallowed

    def idle_tick(self, now: datetime) -> None:
        """The legacy idle mechanisms (the scheduler off): the v0.4 nudge, then the v0.12 think."""
        if self._service.lock.locked():
            return
        self._maybe_nudge(now)
        self._maybe_think(now)

    def inbox_tick(self, now: datetime) -> None:
        """Run each unread inbox line as a turn — never while a turn holds the lock (the next beat
        re-checks). The pointer advances as the lines are read, as in-process."""
        if self._inbox is None or self._service.lock.locked():
            return
        for record in drain_inbox_records(self._inbox, self._inbox.with_suffix(".pos")):
            self._inbox_turn(record)

    # --- the acts ---------------------------------------------------------------------------------------
    def _inbox_turn(self, record: dict) -> None:
        """One inbox line → a turn; her reply → the outbox (never the line itself — no echo)."""
        source = "voice" if record.get("source") == "voice" else "telegram"
        service = self._service
        with service.background():
            try:
                service.last_input = self._clock()  # a Telegram / dictated line is real input
                service.publish_message("user", record["text"], source)
                state = self._core.reply(record["text"], service.session)
                service.mirror(mirror_reply, state)
                service.bus.publish("turn", {"emotion": state.emotion.value, "intensity": state.intensity,
                                             "theme": self._core.theme, "origin": None})
                service.publish_message("lili", state.reply, source, state=state)
            except Exception:  # noqa: BLE001 — one bad line never stops the bus
                log.exception("inbox turn failed")
            finally:
                service._publish_state_quietly(None)

    def _run_scheduled(self, entry: Any) -> None:
        text = scheduled_text(entry, self._seed_idx)
        if not text:  # a seeds row whose file is empty/missing → nothing to fire
            return
        fired = parse_directive(text)
        name = fired.name if fired else entry.directive
        with self._service.background():
            try:
                outcome = self._core.run_directive(text, self._service.session)
                thought = outcome.thought if outcome.is_directive else None
                if thought is not None and is_loud(entry, outcome.mode):
                    if graduates(name, self._rng(0, 99), self._cfg.thoughts_spoken_ratio):
                        self._spoken_turn(spoken_seed(thought.text), source="thought")
                    else:
                        self._push_thought(thought)
                        if self._cfg.sched_show_to_outbox:  # a shown thought reaches Telegram / the voicer
                            self._service.mirror(mirror_thought, thought.text, thought.emotion)
            except Exception:  # noqa: BLE001 — a scheduled act never takes the host down
                log.exception("scheduled %s failed", name)
            finally:
                self._service._publish_state_quietly(None)  # her acts cost tokens / may move the mood

    def _maybe_nudge(self, now: datetime) -> None:
        if not self._nudges or not proactive_due(self._service.last_input, self._last_nudge, now,
                                                 self._cfg.idle_seconds, self._cfg.quiet_hours):
            return
        self._last_nudge = now
        self._nudge_idx = pick_nudge_index(len(self._nudges), self._nudge_idx)
        opener = self._nudges[self._nudge_idx]
        with self._service.background():
            try:
                self._spoken_turn(opener, source="nudge")
            except Exception:  # noqa: BLE001
                log.exception("idle nudge failed")
            finally:
                self._service._publish_state_quietly(None)

    def _maybe_think(self, now: datetime) -> None:
        """The v0.12 idle think: from her own state, or — every other fire — a seed from the menu. The core's
        ``tick_think`` gates it (the interval, the cap, quiet hours); most are silent, a fraction speaks."""
        if not self._think:
            return
        self._think_n += 1
        kind, topic = "think", None
        if self._think_seeds and self._think_n % 2 == 0:
            self._think_seed_idx = pick_nudge_index(len(self._think_seeds), self._think_seed_idx)
            parsed = parse_directive(self._think_seeds[self._think_seed_idx])
            if parsed is not None:
                kind, topic = parsed.name, parsed.topic
        anchor = max(self._service.last_input, self._last_think)
        with self._service.background():
            try:
                thought = self._core.tick_think(self._service.session, anchor, now, rng_seed=self._think_n,
                                                kind=kind, topic=topic)
                if thought is not None:
                    self._last_think = now
                    if thought.spoken:
                        self._spoken_turn(spoken_seed(thought.text), source="thought")
            except Exception:  # noqa: BLE001
                log.exception("idle think failed")
            finally:
                self._service._publish_state_quietly(None)

    def _spoken_turn(self, seed: str, *, source: str) -> None:
        """She speaks first: a hidden self-turn (the seed is never shown), her reply pushed as a ``message``
        with the face event. The caller holds the lock."""
        state = self._core.reply(seed, self._service.session)
        self._service.mirror(mirror_reply, state)  # she speaks first in Telegram too
        self._service.bus.publish("turn", {"emotion": state.emotion.value, "intensity": state.intensity,
                                           "theme": self._core.theme, "origin": None})
        self._service.publish_message("lili", state.reply, source, state=state)

    def _push_thought(self, thought: Any) -> None:
        self._service.bus.publish("thought", {"kind": thought.kind, "text": thought.text,
                                              "emotion": thought.emotion, "when": thought.when, "origin": None})

    # --- the thread -------------------------------------------------------------------------------------
    def start(self) -> bool:
        """Run the catch-up, then beat every ``BEAT_S`` on a daemon thread. ``False`` when nothing is hosted."""
        if not self.active or self._thread is not None:
            return False

        def run() -> None:
            try:
                self.catch_up(self._clock())
            except Exception:  # noqa: BLE001
                log.exception("startup catch-up failed")
            while not self._stop.wait(BEAT_S):
                try:
                    self.tick(self._clock())
                except Exception:  # noqa: BLE001 — a beat never kills her clock
                    log.exception("brain beat failed")

        self._thread = threading.Thread(target=run, daemon=True, name="lumi-brain")
        self._thread.start()
        log.info("brain host on (scheduler=%s idle_think=%s nudge=%s inbox=%s)",
                 self._scheduler is not None, self._think, bool(self._nudges), self._inbox is not None)
        return True

    def stop(self, wait_s: float = 2.0) -> None:
        """Stop beating (an act in flight finishes on its own; the session close waits for the lock)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(wait_s)
