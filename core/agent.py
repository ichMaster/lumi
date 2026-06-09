"""The core turn — the single ``reply(...)`` contract every interface calls.

``Core.reply(user_text, session)`` ties **canon + memory + LLMClient +
Repository** into one turn: assemble the system prompt and the session's history,
call the model through the :class:`~core.llm.LLMClient` seam, persist the user
and Лілі messages, and return the reply. No interface logic lives here.

The core is **user-scoped** (v0.2): it carries an active ``user_id`` (default
``owner``) and every record it writes is keyed by it. It holds the **canon** and
builds the system prompt **per turn** (``_system_prompt``) — the seam LUMI-011
extends to fold in the user's summaries + facts. v0.1 returns ``str``; v0.3 turns
the return into a validated ``EmotionState``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from core.biorhythm import (
    Biorhythms,
    format_biorhythms,
    parse_birth_date,
)
from core.biorhythm import (
    biorhythms as biorhythm_cycles,
)
from core.clock import Clock, format_date, format_stamp, strip_leading_stamp, system_clock
from core.closeness import (
    ClosenessTuning,
    RelationRead,
    closeness_block,
    level_name,
    load_levels,
    naive_level,
    update_closeness,
    validate_relation,
)
from core.config import DEFAULT_COMPACTION_BATCH, DEFAULT_MEMORY_WINDOW, Config, load_config
from core.cycle import CyclePhase, format_cycle, menstrual_phase, parse_cycle_anchor
from core.emotion import DEFAULT_EMOTION, DEFAULT_INTENSITY, EmotionState, validate
from core.llm import AnthropicClient, LLMClient, Message, ResponseStats
from core.memory import (
    DAY_DAYS,
    MAX_DAY_ROWS,
    MAX_WEEK_ROWS,
    RECENT_SUMMARIES,
    SESSION_DAYS,
    WEEK_DAYS,
    clamp_rows,
    compaction_plan,
    day_summary_request,
    digest_request,
    facts_request,
    parse_facts,
    parse_summary,
    summary_request,
    trim_history,
    week_summary_request,
)
from core.mood import MoodState, load_natal, mood_request, split_resolution
from core.prompt import (
    REASONING_DIRECTIVE,
    build_system_prompt,
    load_canon,
    split_emotion,
    split_reasoning,
    split_style,
)
from core.repository import (
    Closeness,
    DaySummary,
    LongTermFact,
    Repository,
    Session,
    SessionDigest,
    ShortSummary,
    WeekSummary,
    make_message,
    now_iso,
)
from core.styles import load_meta_descriptions, load_meta_styles, load_styles
from core.user import DEFAULT_USER_ID
from core.worldcontext import WorldContext, ambient_line

# The full daily mood reading is logged here (only the resolution rides in the prompt).
_mood_log = logging.getLogger("lumi.mood")

# Map stored roles → the model's chat roles (Лілі speaks as the assistant).
_ROLE_TO_LLM = {"user": "user", "lili": "assistant"}


def _monday_of(date_str: str) -> str:
    """The Monday ("YYYY-MM-DD") of the Mon–Sun week containing ``date_str`` (date-based recall weeks)."""
    d = date.fromisoformat(date_str)
    return (d - timedelta(days=d.weekday())).isoformat()


@dataclass(frozen=True)
class MemoryView:
    """A read-only snapshot of a user's relationship memory (for the TUI)."""

    summaries: list[str]
    facts: list[str]


@dataclass
class UsageTotals:
    """Running totals across the session (for the TUI status line)."""

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    @property
    def avg_latency_ms(self) -> int:
        return self.latency_ms // self.turns if self.turns else 0


class Core:
    """Лілі's interface-independent, user-scoped turn engine."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        repository: Repository,
        canon: str,
        model: str,
        user_id: str = DEFAULT_USER_ID,
        memory_window: int = DEFAULT_MEMORY_WINDOW,
        compaction_batch: int = DEFAULT_COMPACTION_BATCH,
        recent_summaries: int = RECENT_SUMMARIES,
        session_days: int = SESSION_DAYS,
        day_days: int = DAY_DAYS,
        week_days: int = WEEK_DAYS,
        max_day_rows: int = MAX_DAY_ROWS,
        max_week_rows: int = MAX_WEEK_ROWS,
        styles: dict[str, str] | None = None,
        meta_styles: dict[str, list[str]] | None = None,
        meta_descriptions: dict[str, str] | None = None,
        closeness_levels: dict[int, tuple[str, str]] | None = None,
        closeness_enabled: bool = True,
        closeness_tuning: ClosenessTuning | None = None,
        clock: Clock = system_clock,
        natal: str = "",
        mood_enabled: bool = True,
        mood_log_path: Path | None = None,
        biorhythms_enabled: bool = True,
        cycle_enabled: bool = True,
        face_signal: Path | None = None,
    ) -> None:
        self._llm = llm
        self._repo = repository
        self._canon = canon
        self._model = model
        self._user_id = user_id
        self._clock = clock  # injectable: real time by default, fixed in tests
        # Ambient "now / here" snapshot (v0.4), set by the client at startup/refresh.
        self._world: WorldContext | None = None
        # v0.6 mood of the day: the fixed natal seed + the cached daily MoodState.
        self._natal = natal.strip()
        self._mood_enabled = mood_enabled
        self._mood_log_path = mood_log_path  # the full reading is appended here (readable)
        self._mood: MoodState | None = None
        # v0.8 biorhythms: computed cycles merged into the daily mood + the cached state.
        self._biorhythms_enabled = biorhythms_enabled
        self._biorhythms: Biorhythms | None = None
        # v0.8 hormonal cycle: the phased body rhythm merged into the mood + the cached phase.
        self._cycle_enabled = cycle_enabled
        self._cycle: CyclePhase | None = None
        # v0.7 emotion-face signal: a one-word file the local viewer polls each turn.
        self._face_signal = face_signal
        self._memory_window = memory_window
        self._compaction_batch = compaction_batch
        # date-based recall date-based short-memory windows (config/env-tunable): session/day/week spans + caps.
        self._recent_summaries = recent_summaries  # /memory quick-view count
        self._session_days = session_days  # tier 1: detailed session summaries window
        self._day_days = day_days  # tier 2: per-day digests window
        self._week_days = week_days  # tier 3: per-week digests window
        self._max_day_rows = max_day_rows
        self._max_week_rows = max_week_rows
        # Answer styles + meta-styles (presets → several base styles). Лілі picks her
        # own style each turn from this palette (preferring meta-styles) and declares
        # it; `/style <name>` sets a soft per-session *recommendation*, not a switch.
        self._styles = styles or {}
        self._meta = meta_styles or {}
        self._meta_desc = meta_descriptions or {}  # concise per-mega description for the palette
        # v0.10 closeness: authored level → (name, behavior) blocks, injected by active level.
        self._closeness_levels = closeness_levels or {}
        self._closeness_enabled = closeness_enabled
        self._closeness_tuning = closeness_tuning or ClosenessTuning()
        # The level a fresh user (no record) sits at — derived from the configured baseline.
        self._default_level = naive_level(self._closeness_tuning.baseline)
        self._recommendation: list[str] = []  # the user's soft style suggestion (or none)
        self.last_style: str | None = None  # the style Лілі declared last turn (<style>…)
        # The validated EmotionState from the last turn (for a renderer / status line).
        self.last_emotion: EmotionState | None = None
        # The relational read of the user's last message (v0.10; internal, feeds closeness).
        self.last_relation: RelationRead = RelationRead()
        # The model's reasoning summary from the last turn (None when thinking is
        # off or absent), for a client to render alongside the reply.
        self.last_thinking: str | None = None
        # Stats for the last reply + running totals, for the TUI status line.
        self.last_stats: ResponseStats | None = None
        self.totals = UsageTotals()
        # The exact prompt sent on the last turn, for inspection ({system, messages}).
        self.last_prompt: dict | None = None
        # How many messages the last turn folded into the session digest (0 if none).
        self.last_compaction: int = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def clock(self) -> Clock:
        """The injected clock (so a client fetches ambient context with the same time)."""
        return self._clock

    def set_world_context(self, world: WorldContext | None) -> None:
        """Set the ambient *now / here* snapshot (the client fetches it at startup/refresh)."""
        self._world = world

    @property
    def mood(self) -> str | None:
        """Today's mood **resolution** (v0.6), or ``None`` when off / not yet computed."""
        return self._mood.resolution if self._mood else None

    @property
    def closeness(self) -> Closeness | None:
        """The active user's relationship-closeness record (v0.10), or ``None`` if none yet."""
        return self._repo.get_closeness(self._user_id)

    def closeness_status(self) -> tuple[int, str | None]:
        """The active relationship **level** (1–5) + its authored **name** (for ``/closeness``).

        A fresh user (no record yet) sits at the default level. The raw value / dimension
        scores stay internal — only the level + its name are surfaced.
        """
        existing = self._repo.get_closeness(self._user_id)
        level = existing.level if existing else self._default_level
        return level, level_name(self._closeness_levels, level)

    @property
    def biorhythms(self) -> Biorhythms | None:
        """Today's computed biorhythm cycles (v0.8), cached with the mood; ``None`` when off."""
        return self._biorhythms

    @property
    def cycle(self) -> CyclePhase | None:
        """Today's hormonal-cycle phase (v0.8), cached with the mood; ``None`` when off."""
        return self._cycle

    def ensure_mood(self) -> None:
        """Compute today's mood now (idempotent / cached) — a client may call at startup."""
        self._ensure_mood()

    def ensure_day_summaries(self) -> None:
        """Bring each day in the recall window (last ``day_days``) up to date — lazily, at prompt
        time. A day's ≤``max_day_rows``-row digest is (re)built from that day's **session
        summaries** **only when stale** (no digest yet, or the day gained sessions — its summary
        count changed, incl. today). Best-effort; a model error on one day never blocks the turn.
        """
        since = (self._clock().date() - timedelta(days=self._day_days)).isoformat()
        by_day: dict[str, list[str]] = {}
        for s in self._repo.summaries_since(self._user_id, since):
            if s.summary.strip():
                by_day.setdefault(s.ts[:10], []).append(s.summary)
        for day, texts in by_day.items():
            existing = self._repo.get_day_summary(self._user_id, day)
            if existing is not None and existing.count == len(texts):
                continue  # count matches the day's sessions → up to date
            try:
                system, msgs = day_summary_request(texts)
                summary = clamp_rows(self._housekeeping_reply(system, msgs), self._max_day_rows)
                if summary:
                    self._repo.set_day_summary(
                        DaySummary(self._user_id, day, summary, len(texts), self._clock().isoformat())
                    )
            except Exception:  # noqa: BLE001 — best-effort; never block the turn
                continue

    def ensure_week_summaries(self) -> None:
        """Bring each Mon–Sun week in the recall window (last ``week_days``) up to date — lazily.
        A week's ≤``max_week_rows``-row digest is (re)built from that week's **session summaries**
        only when its summary count changed. Weeks are keyed by their Monday. Best-effort.
        """
        since = (self._clock().date() - timedelta(days=self._week_days)).isoformat()
        by_week: dict[str, list[str]] = {}
        for s in self._repo.summaries_since(self._user_id, since):
            if s.summary.strip():
                by_week.setdefault(_monday_of(s.ts[:10]), []).append(s.summary)
        for week_start, texts in by_week.items():
            existing = self._repo.get_week_summary(self._user_id, week_start)
            if existing is not None and existing.count == len(texts):
                continue
            try:
                system, msgs = week_summary_request(texts)
                summary = clamp_rows(self._housekeeping_reply(system, msgs), self._max_week_rows)
                if summary:
                    self._repo.set_week_summary(
                        WeekSummary(self._user_id, week_start, summary, len(texts),
                                    self._clock().isoformat())
                    )
            except Exception:  # noqa: BLE001 — best-effort; never block the turn
                continue

    @property
    def thinking(self) -> bool:
        """Whether extended thinking is enabled on the model (for a status indicator)."""
        return bool(getattr(self._llm, "_thinking", False))

    @property
    def style(self) -> str:
        """The style for the status line: Лілі's last choice + **who** picked it.

        ``"<name> (Лілі)"`` when she chose it herself; ``"<name> (ти)"`` when her
        choice matches your standing recommendation (so you can see whether she
        followed it). Before her first reply: ``"авто"`` (+ your recommendation, if any).
        """
        if self.last_style is None:
            return f"авто · радиш: {'+'.join(self._recommendation)}" if self._recommendation else "авто"
        who = "ти" if (self._recommendation and self.last_style in self._recommendation) else "Лілі"
        return f"{self.last_style} ({who})"

    @property
    def recommendation(self) -> str:
        """The active style recommendation for display ('' when none)."""
        return "+".join(self._recommendation)

    def base_names(self) -> list[str]:
        """The authored base style names."""
        return sorted(self._styles)

    def meta_names(self) -> list[str]:
        """The meta-style (preset) names."""
        return sorted(self._meta)

    def style_names(self) -> list[str]:
        """All names a recommendation may use (``auto`` + meta-styles + base styles)."""
        return ["auto", *sorted(self._meta), *sorted(self._styles)]

    def set_style(self, spec: str) -> bool:
        """Set a soft style **recommendation** (not a switch) — Лілі still chooses.

        Names are separated by spaces/commas/``+``. ``auto``/``normal``/empty clears
        the recommendation (she chooses freely). Returns ``False`` (changing nothing)
        if any name is unknown.
        """
        names = [n for n in re.split(r"[\s,+]+", spec.strip().lower()) if n]
        if not names or names == ["auto"] or names == ["normal"]:
            self._recommendation = []
            return True
        valid = {*self._styles, *self._meta}
        if any(n not in valid for n in names):
            return False
        self._recommendation = list(dict.fromkeys(names))  # dedupe, keep order
        return True

    def _style_directive(self) -> str | None:
        """The auto-style palette — just the **mega-styles** with a concise description each
        (so the prompt stays short), plus the user's soft recommendation if set. ``None`` when
        no mega-styles are authored. Base styles are no longer dumped into the prompt."""
        if not self._meta:
            return None
        lines = ["Мега-стилі — обери один, що найкраще пасує:"]
        for name in sorted(self._meta):
            desc = self._meta_desc.get(name) or ", ".join(self._meta[name])
            lines.append(f"- {name}: {desc}")
        if self._recommendation:
            lines.append(
                f"(Користувач радить: {', '.join(self._recommendation)} — "
                "врахуй, якщо доречно; ти все одно вирішуєш.)"
            )
        return "\n".join(lines)

    def start_session(self) -> Session:
        """Open a fresh session for the active user (persisted).

        The style recommendation + Лілі's last choice are per-session — reset here.
        """
        self._recommendation = []
        self.last_style = None
        self._write_face_signal(DEFAULT_EMOTION.value, DEFAULT_INTENSITY)  # calm before the first turn
        return self._repo.create_session(self._user_id)

    def _write_face_signal(self, emotion: str, intensity: float) -> None:
        """Write the current emotion (one word + intensity) to the viewer signal (v0.7).

        Best-effort: a separate viewer process polls this file. A write failure never
        affects the turn.
        """
        if self._face_signal is None:
            return
        try:
            self._face_signal.parent.mkdir(parents=True, exist_ok=True)
            stamp = self._clock().strftime("%Y-%m-%d %H:%M:%S")  # makes every turn's line unique
            self._face_signal.write_text(f"{emotion} {intensity:.2f} {stamp}", encoding="utf-8")
        except OSError:
            pass  # best-effort; the viewer falls back to calm

    def _system_prompt(self, session: Session) -> str:
        """Assemble the system prompt for this turn, rehydrated for the user.

        Composes the canon with the user's recent summaries + long-term facts and
        — if the current session has been compacted — its running digest. Loaded
        per turn, so a restart recalls prior context and new memory takes effect.
        Isolation holds — only this ``user_id``'s records are read.
        """
        # date-based recall three date-based layers (cumulative): per-WEEK digests (last week_days) →
        # per-DAY digests (last day_days) → per-SESSION detail (last session_days). Coarse → fine.
        today = self._clock().date()
        week_since = _monday_of((today - timedelta(days=self._week_days)).isoformat())
        week_summaries = []
        for ws in self._repo.week_summaries_since(self._user_id, week_since):
            body = " ".join(ln.strip() for ln in ws.summary.splitlines() if ln.strip())
            if body:
                week_summaries.append(f"[тиждень з {ws.week_start}] {body}")
        day_since = (today - timedelta(days=self._day_days)).isoformat()
        day_summaries = []
        for ds in self._repo.day_summaries_since(self._user_id, day_since):
            body = " ".join(ln.strip() for ln in ds.summary.splitlines() if ln.strip())
            if body:
                day_summaries.append(f"[{ds.date}] {body}")
        session_since = (today - timedelta(days=self._session_days)).isoformat()
        summaries = [
            f"[{format_date(s.ts)}] {s.summary}"
            for s in self._repo.summaries_since(self._user_id, session_since)
        ]
        facts = [f.fact for f in self._repo.facts(self._user_id)]
        digest = self._repo.get_digest(session.id)
        # v0.10: inject the active relationship level's authored block (warmth/openness, never
        # competence). Use the prior turn's level (a fresh user sits at the default level). Off → none.
        closeness = None
        if self._closeness_enabled:
            existing = self._repo.get_closeness(self._user_id)
            closeness = closeness_block(
                self._closeness_levels, existing.level if existing else self._default_level
            )
        # Append the reasoning directive to the canon so any pre-answer reasoning is
        # wrapped in <think>…</think> (parsed out in reply()); the style rides last.
        return build_system_prompt(
            f"{self._canon}\n\n{REASONING_DIRECTIVE}",
            summaries=summaries,
            day_summaries=day_summaries,
            week_summaries=week_summaries,
            facts=facts,
            digest=digest.summary if digest else None,
            style=self._style_directive(),
            emotion=True,
            relation=self._closeness_enabled,  # v0.10: ask for the relational read (off → skip)
            ambient=ambient_line(self._world, self._clock),
            mood=self.mood,  # only the resolution rides in the prompt (v0.6)
            closeness=closeness,  # the active relationship level's block (v0.10)
        )

    def _housekeeping_reply(self, system: str, messages: list[Message]) -> str:
        """An internal model call with extended thinking forced off.

        Used for summaries / facts / compaction — internal extraction, not
        user-facing reasoning, so it stays fast and cheap.
        """
        llm = self._llm
        prev_thinking = getattr(llm, "_thinking", None)
        if prev_thinking:
            llm._thinking = False
        try:
            return llm.reply(system=system, messages=messages, model=self._model)
        finally:
            if prev_thinking:
                llm._thinking = prev_thinking

    def _maybe_compact(self, session: Session, history: list) -> SessionDigest | None:
        """Fold older-than-window messages into the session digest, in batches.

        Returns the current digest (possibly updated). Sets ``last_compaction`` to
        how many messages were folded this turn (0 if none). Best-effort — a model
        failure keeps the prior digest and never breaks the turn.
        """
        self.last_compaction = 0
        digest = self._repo.get_digest(session.id)
        compacted = digest.compacted_count if digest else 0
        new_compacted = compaction_plan(
            len(history), compacted, self._memory_window, self._compaction_batch
        )
        if new_compacted <= compacted:
            return digest
        chunk = history[compacted:new_compacted]
        try:
            system, msgs = digest_request(digest.summary if digest else None, chunk)
            summary = self._housekeeping_reply(system, msgs).strip()
        except Exception:  # noqa: BLE001 — best-effort; keep the prior digest
            return digest
        if not summary:
            return digest
        updated = SessionDigest(
            session_id=session.id,
            summary=summary,
            compacted_count=new_compacted,
            ts=now_iso(),
        )
        self._repo.set_digest(updated)
        self.last_compaction = len(chunk)
        return updated

    @staticmethod
    def _history_content(m) -> str:
        """The content to replay to the model for a stored message.

        Prefixed with the message's **date-time** (so Лілі perceives the rhythm of
        the conversation and the gap since the last turn — v0.4). For Лілі's turns,
        also re-append the ``<emotion>…</emotion>`` tag reconstructed from the
        persisted ``emotion``/``intensity`` — the stored text is clean (tag-stripped),
        so without this the model only sees tag-less past replies and drifts to stop
        emitting the tag over a long conversation.
        """
        body = m.text
        if m.role == "lili" and m.emotion:
            intensity = m.intensity if m.intensity is not None else 0.5
            body = f"{m.text} <emotion>{m.emotion} {intensity:.1f}</emotion>"
        return f"[{format_stamp(m.ts)}] {body}"

    def _ensure_mood(self) -> None:
        """Compute today's mood once per local day (cached); **log the full reading**.

        Best-effort: off, no natal, or a model failure → no mood, never blocks a turn.
        Only the **resolution** is held (``Core.mood``); the full reading goes to the log.
        Runs through the housekeeping path (extended thinking off), like summaries.
        """
        if not self._mood_enabled or not self._natal:
            return
        today = self._clock().strftime("%Y-%m-%d")
        if self._mood and self._mood.date == today:
            return  # already computed for this local day; a turn keeps its mood
        # v0.8: compute today's body rhythms (exact, deterministic) and merge them in.
        bio_line: str | None = None
        cycle_line: str | None = None
        self._biorhythms = None
        self._cycle = None
        today_date = self._clock().date()
        if self._biorhythms_enabled:
            birth = parse_birth_date(self._natal)
            if birth is not None:
                self._biorhythms = biorhythm_cycles(birth, today_date)
                bio_line = format_biorhythms(self._biorhythms)
        if self._cycle_enabled:
            anchor = parse_cycle_anchor(self._natal)
            if anchor is not None:
                self._cycle = menstrual_phase(anchor[0], today_date, anchor[1])
                cycle_line = format_cycle(self._cycle)
        try:
            system, msgs = mood_request(self._natal, today, biorhythms=bio_line, cycle=cycle_line)
            reading = self._housekeeping_reply(system, msgs).strip()
        except Exception:  # noqa: BLE001 — mood is best-effort; never block a turn
            return
        if not reading:
            return
        self._mood = MoodState(date=today, resolution=split_resolution(reading), reading=reading)
        _mood_log.info("mood %s:\n%s", today, reading, extra={"date": today})
        if self._mood_log_path is not None:  # also persist the full reading, readable
            try:
                self._mood_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._mood_log_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n\n===== {today} =====\n{reading}\n")
            except OSError:
                pass  # best-effort; never block a turn on logging

    def reply(self, user_text: str, session: Session) -> EmotionState:
        """Run one turn and return Лілі's validated :class:`EmotionState` (v0.3).

        Compacts older-than-window messages into the session digest (in batches),
        then calls the model with the system prompt (+ digest) + the verbatim
        **live tail** (between ``memory_window`` and ``+ batch`` messages) + the
        new line, and persists both messages. The full history stays stored.
        """
        self._ensure_mood()  # compute today's mood once per local day (v0.6)
        self.ensure_day_summaries()  # refresh the day digests the prompt will inject (date-based recall)
        self.ensure_week_summaries()  # refresh the week digests (date-based recall)
        history = self._repo.load_messages(session.id)
        digest = self._maybe_compact(session, history)
        compacted = digest.compacted_count if digest else 0
        # The verbatim tail: messages not yet folded into the digest, capped for
        # safety (in case compaction repeatedly failed).
        live = trim_history(history[compacted:], self._memory_window + self._compaction_batch)
        turn_ts = self._clock().isoformat()  # one stamp for this turn's stored messages
        messages: list[Message] = [
            {"role": _ROLE_TO_LLM[m.role], "content": self._history_content(m)} for m in live
        ]
        messages.append({"role": "user", "content": f"[{format_stamp(turn_ts)}] {user_text}"})

        system = self._system_prompt(session)
        self.last_prompt = {"system": system, "messages": list(messages)}
        raw = self._llm.reply_structured(system=system, messages=messages, model=self._model)
        # Split any <think>…</think> reasoning, then the inline <emotion> tag, out of
        # the reply field; the clean text is shown/stored. Prefer the model's tagged
        # inline reasoning; fall back to the provider's summarized thinking channel.
        inline_thinking, reply_text = split_reasoning(str(raw.get("reply") or ""))
        tag_emotion, reply_text = split_emotion(reply_text)
        # Лілі's self-chosen answer style — record it (for the status "who") and strip it.
        tag_style, reply_text = split_style(reply_text)
        if tag_style:
            self.last_style = tag_style
        # Strip a leading [date-time] the model may echo from the timestamped history.
        reply_text = strip_leading_stamp(reply_text)
        self.last_thinking = inline_thinking or getattr(self._llm, "last_thinking", None)
        self.last_stats = getattr(self._llm, "last_stats", None)
        if self.last_stats is not None:
            self.totals.turns += 1
            self.totals.input_tokens += self.last_stats.input_tokens or 0
            self.totals.output_tokens += self.last_stats.output_tokens or 0
            self.totals.latency_ms += self.last_stats.latency_ms

        # Merge emotion sources: the structured tool wins when present; otherwise the
        # inline <emotion> tag (the reliable path when the tool can't be forced —
        # extended thinking on); else the validation gate's fallback (calm).
        tag = tag_emotion or {}
        emotion = raw.get("emotion") or tag.get("emotion")
        intensity = raw.get("intensity")
        if intensity is None:
            intensity = tag.get("intensity")
        # Validate/repair into a valid EmotionState (raises EmotionError if no reply).
        state = validate(
            {"reply": reply_text, "emotion": emotion, "intensity": intensity},
            session_id=session.id,
            turn=self.totals.turns,
        )
        self.last_emotion = state
        # v0.10: the additive relational read of the user's message (internal; feeds closeness).
        self.last_relation = validate_relation(raw.get("relation"))
        # Advance the per-user closeness: decay over silence + this turn's relational delta.
        if self._closeness_enabled:
            self._repo.set_closeness(
                update_closeness(
                    self._repo.get_closeness(self._user_id),
                    self.last_relation,
                    self._clock(),
                    self._user_id,
                    self._closeness_tuning,
                )
            )
        self._write_face_signal(state.emotion.value, state.intensity)  # update the viewer (v0.7)

        self._repo.append_message(
            make_message(session.id, self._user_id, "user", user_text, ts=turn_ts)
        )
        self._repo.append_message(
            make_message(
                session.id,
                self._user_id,
                "lili",
                state.reply,
                ts=turn_ts,
                emotion=state.emotion.value,
                intensity=state.intensity,
            )
        )
        return state

    def end_session(self, session: Session) -> ShortSummary | None:
        """Close a session: mark it ended, write a short summary, accumulate facts.

        Summarizes the session and extracts durable facts via the model, storing
        a per-user ``ShortSummary`` and any new ``LongTermFact``s (injected at
        startup by LUMI-011). An empty session produces none; a model failure on
        either step degrades to nothing — ending a session never raises
        (ARCHITECTURE §Error handling).
        """
        history = self._repo.load_messages(session.id)
        self._repo.end_session(session.id)
        if not history:
            return None
        # Summary + facts run via _housekeeping_reply (extended thinking off) so
        # this internal extraction stays fast and quitting is snappy.
        summary = self._write_summary(session, history)
        self._accumulate_facts(history)
        return summary

    def _write_summary(self, session: Session, history: list) -> ShortSummary | None:
        try:
            system, msgs = summary_request(history)
            summary_text = self._housekeeping_reply(system, msgs).strip()
        except Exception:  # noqa: BLE001 — never block session end on a model error
            return None
        if not summary_text:
            return None
        # v0.9: one call → both the detailed summary and a one-line gist.
        detailed, gist = parse_summary(summary_text)
        summary = ShortSummary(
            user_id=self._user_id,
            session_id=session.id,
            summary=detailed,
            gist=gist,
            ts=self._clock().isoformat(),
        )
        self._repo.add_summary(summary)
        return summary

    # --- memory commands (memory.view / memory.clear) -------------------
    def view_memory(self, user_id: str | None = None) -> MemoryView:
        """Snapshot the user's relationship memory (summaries + facts)."""
        uid = user_id or self._user_id
        return MemoryView(
            summaries=[s.summary for s in self._repo.recent_summaries(uid, self._recent_summaries)],
            facts=[f.fact for f in self._repo.facts(uid)],
        )

    def clear_memory(self, user_id: str | None = None) -> None:
        """Wipe the user's short + long-term memory (only this user)."""
        self._repo.clear_memory(user_id or self._user_id)

    def _accumulate_facts(self, history: list) -> None:
        try:
            system, msgs = facts_request(history)
            text = self._housekeeping_reply(system, msgs)
        except Exception:  # noqa: BLE001 — facts are best-effort
            return
        existing = {f.fact for f in self._repo.facts(self._user_id)}
        for fact in parse_facts(text):
            if fact in existing:
                continue  # dedup against what's already stored
            self._repo.add_fact(
                LongTermFact(
                    user_id=self._user_id, fact=fact, meta="", confidence=0.5, ts=now_iso()
                )
            )
            existing.add(fact)


def build_core(
    *,
    config: Config | None = None,
    llm: LLMClient | None = None,
    repository: Repository | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> Core:
    """Wire a :class:`Core` from config.

    Defaults to the real Anthropic backend and the local JSON store; tests inject
    a ``MockLLMClient`` and a temp-file ``JsonRepository``. The Anthropic SDK is
    only reached when ``llm`` is not supplied.
    """
    cfg = config or load_config()

    if repository is None:
        from state.local_store import JsonRepository  # imported here to keep core/ light

        repository = JsonRepository(cfg.store_path)

    if llm is None:
        llm = AnthropicClient(
            cfg.api_key,
            max_tokens=cfg.max_tokens,
            thinking=cfg.thinking,
            effort=cfg.effort,
        )

    canon = load_canon(cfg.canon_path)
    return Core(
        llm=llm,
        repository=repository,
        canon=canon,
        model=cfg.model,
        user_id=user_id,
        memory_window=cfg.memory_window,
        compaction_batch=cfg.compaction_batch,
        recent_summaries=cfg.recent_summaries,
        session_days=cfg.session_days,
        day_days=cfg.day_days,
        week_days=cfg.week_days,
        max_day_rows=cfg.max_day_rows,
        max_week_rows=cfg.max_week_rows,
        styles=load_styles(cfg.styles_path),
        meta_styles=load_meta_styles(cfg.styles_path),
        meta_descriptions=load_meta_descriptions(cfg.styles_path),
        closeness_levels=load_levels(cfg.closeness_path),
        closeness_enabled=cfg.closeness,
        closeness_tuning=cfg.closeness_tuning,
        natal=load_natal(cfg.natal_path),
        mood_enabled=cfg.mood,
        mood_log_path=cfg.store_path.parent / "mood.log",
        biorhythms_enabled=cfg.biorhythms,
        cycle_enabled=cfg.cycle,
        face_signal=cfg.face_signal or cfg.store_path.parent / "face.txt",
    )
