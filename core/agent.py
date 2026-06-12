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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    mood_shift,
    naive_level,
    shifted_level,
    update_closeness,
    validate_relation,
)
from core.config import DEFAULT_COMPACTION_BATCH, DEFAULT_MEMORY_WINDOW, Config, load_config
from core.cycle import CyclePhase, format_cycle, menstrual_phase, parse_cycle_anchor
from core.emotion import DEFAULT_EMOTION, DEFAULT_INTENSITY, Emotion, EmotionState, validate
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
    facts_digest_request,
    facts_request,
    parse_facts,
    parse_summary,
    summary_request,
    trim_history,
    week_summary_request,
)
from core.mood import (
    MoodState,
    load_natal,
    mood_request,
    split_resolution,
    split_theme,
    strip_theme,
)
from core.nudge import should_nudge
from core.placeholders import resolve_placeholders
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
    FactsDigest,
    LongTermFact,
    Repository,
    Session,
    SessionDigest,
    ShortSummary,
    Thought,
    WeekSummary,
    make_message,
    make_thought,
    now_iso,
)
from core.styles import load_meta_descriptions, load_meta_styles, load_styles
from core.thoughts import (
    REGISTRY,
    THOUGHT_FULL_HEADER,
    THOUGHTS_CAP,
    THOUGHTS_INTERVAL_S,
    THOUGHTS_MAX_LINES,
    THOUGHTS_SPOKEN_RATIO,
    THOUGHTS_WINDOW_H,
    directive_mode,
    parse_directive,
    parse_thought,
    should_graduate,
    thought_full_seed,
    thought_request,
    thoughts_diary_block,
)
from core.user import DEFAULT_USER_ID
from core.worldcontext import WorldContext, ambient_line

# The locked base-9 emotion set, used to validate a generated thought's emotion (v0.12).
_EMOTION_VALUES = frozenset(e.value for e in Emotion)

# The full daily mood reading is logged here (only the resolution rides in the prompt).
_mood_log = logging.getLogger("lumi.mood")

# Every recorded thought is logged here (the v0.3 logged tier) — never persisted to long-term memory.
_thoughts_log = logging.getLogger("lumi.thoughts")

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


@dataclass(frozen=True)
class DirectiveOutcome:
    """The result of routing a ``%directive`` input (v0.12).

    ``is_directive`` is ``False`` when the input was **not** a known ``%directive`` (the client
    treats it as a normal chat message). When ``True``: ``mode`` is ``"silent"``/``"open"`` and
    ``thought`` is the recorded :class:`Thought` (``None`` if the model produced nothing). The
    client shows the raw thought (``💭``) only for ``mode == "open"``.
    """

    is_directive: bool
    mode: str | None = None
    thought: Thought | None = None


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
        facts_digest_enabled: bool = False,
        facts_digest_max: int = 150,
        facts_digest_refresh: int = 20,
        prompt_cache: bool = False,
        clock: Clock = system_clock,
        natal: str = "",
        mood_enabled: bool = True,
        mood_log_path: Path | None = None,
        theme_descriptions: dict[str, str] | None = None,
        default_theme: str | None = None,
        biorhythms_enabled: bool = True,
        cycle_enabled: bool = True,
        face_signal: Path | None = None,
        thoughts_enabled: bool = True,
        thoughts_window_h: int = THOUGHTS_WINDOW_H,
        thoughts_max_lines: int = THOUGHTS_MAX_LINES,
        thoughts_interval_s: int = THOUGHTS_INTERVAL_S,
        thoughts_cap: int = THOUGHTS_CAP,
        thoughts_spoken_ratio: float = THOUGHTS_SPOKEN_RATIO,
        thoughts_show: str = "hidden",
        thoughts_context: str = "lean",
        quiet_hours: tuple[int, int] | None = None,
        thoughts_quiet_hours: tuple[int, int] | None = None,
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
        # v0.11 face themes: the mood picks one per day; the signal carries it. Off → default/None.
        self._theme_descriptions = theme_descriptions or {}
        self._default_theme = default_theme
        self._force_theme: str | None = None  # /theme override — beats the mood's pick (session)
        self._last_emotion = DEFAULT_EMOTION.value  # last face state (re-emitted on a theme change)
        self._last_intensity = DEFAULT_INTENSITY
        # v0.8 biorhythms: computed cycles merged into the daily mood + the cached state.
        self._biorhythms_enabled = biorhythms_enabled
        self._biorhythms: Biorhythms | None = None
        # v0.8 hormonal cycle: the phased body rhythm merged into the mood + the cached phase.
        self._cycle_enabled = cycle_enabled
        self._cycle: CyclePhase | None = None
        # v0.7 emotion-face signal: a one-word file the local viewer polls each turn.
        self._face_signal = face_signal
        # v0.12 thought-stream: her mind acts on its own (%think/%wonder) into the global diary;
        # the last `thoughts_window_h` hours feed back into the prompt.
        self._thoughts_enabled = thoughts_enabled
        self._thoughts_window_h = thoughts_window_h
        self._thoughts_max_lines = thoughts_max_lines
        # v0.12 proactive nudge: idle interval, a per-session cap, and the spoken fraction.
        self._thoughts_interval_s = thoughts_interval_s
        self._thoughts_cap = thoughts_cap
        self._thoughts_spoken_ratio = thoughts_spoken_ratio
        self._thoughts_show = thoughts_show  # hidden (default) / admin / off — the /thoughts policy
        self._thoughts_context = thoughts_context  # lean (seeds) / full (the whole reply backdrop)
        self._quiet_hours = quiet_hours
        # The proactive-think's quiet window is independent of the nudge's (falls back to it in config).
        self._thoughts_quiet_hours = thoughts_quiet_hours
        self._think_count = 0  # proactive thinks this session (reset in start_session)
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
        # Facts digest: a consolidated, compact view of the long-term facts injected instead of
        # all raw facts (rebuilt only when the facts grow by `refresh`; non-destructive).
        self._facts_digest_enabled = facts_digest_enabled
        self._facts_digest_max = facts_digest_max
        self._facts_digest_refresh = facts_digest_refresh
        self._prompt_cache = prompt_cache  # v0.15: pass the cache_prefix to the LLM on the reply turn
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
    def theme(self) -> str | None:
        """The active face **theme** (v0.11): a ``/theme`` override, else the mood's pick, else the
        default, else ``None``. The override wins so the daily mood can't clobber a manual choice."""
        if self._force_theme is not None:
            return self._force_theme
        if self._mood and self._mood.theme:
            return self._mood.theme
        return self._default_theme

    @property
    def themes(self) -> list[str]:
        """The known face themes (the manifest-described ones the mood / ``/theme`` choose from)."""
        return sorted(self._theme_descriptions)

    def set_theme(self, name: str | None) -> bool:
        """Manually override the face theme (``/theme``). ``None``/``"auto"`` clears it (back to the
        mood). Returns ``False`` for an unknown theme (override unchanged); on success re-emits the
        face signal so the viewer updates at once."""
        if not name or name.strip().lower() == "auto":
            self._force_theme = None
        elif name in self._theme_descriptions:
            self._force_theme = name
        else:
            return False
        self._write_face_signal(self._last_emotion, self._last_intensity)
        return True

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
        self._think_count = 0  # v0.12: the proactive-think cap is per session
        self._write_face_signal(DEFAULT_EMOTION.value, DEFAULT_INTENSITY)  # calm before the first turn
        return self._repo.create_session(self._user_id)

    def _write_face_signal(self, emotion: str, intensity: float) -> None:
        """Write the viewer signal (v0.7 + v0.11): ``[<theme>] <emotion> <intensity> <stamp>``.

        The day's face **theme** (v0.11) rides in front when set; with no theme it's the bare
        v0.7 line. Best-effort: a separate viewer process polls this file; a write failure never
        affects the turn.
        """
        self._last_emotion, self._last_intensity = emotion, intensity  # for a /theme re-emit
        if self._face_signal is None:
            return
        try:
            self._face_signal.parent.mkdir(parents=True, exist_ok=True)
            stamp = self._clock().strftime("%Y-%m-%d %H:%M:%S")  # makes every turn's line unique
            prefix = f"{self.theme} " if self.theme else ""  # v0.11 theme, when set
            self._face_signal.write_text(
                f"{prefix}{emotion} {intensity:.2f} {stamp}", encoding="utf-8"
            )
        except OSError:
            pass  # best-effort; the viewer falls back to calm

    # --- Thought-stream (v0.12) — the mental-act engine ------------------
    def think(
        self,
        kind: str = "think",
        *,
        topic: str | None = None,
        session: Session | None = None,
        rng_seed: int = 0,
        spoken: bool = False,
    ) -> Thought | None:
        """Run one ``%directive`` — seed → generate → validate → record — into the dated diary.

        Returns the recorded :class:`Thought`, or ``None`` (off / unknown directive / malformed
        output). **Best-effort**: a model failure or an empty thought records nothing, never raises.
        The model call is **thinking-off** housekeeping (mocked in tests); the diary stamp comes
        from the injected clock; the emotion is validated to the locked base-9 set.
        """
        if not self._thoughts_enabled:
            return None
        directive = REGISTRY.get(kind)
        if directive is None:
            return None
        if topic:  # a topic may carry {placeholders} (e.g. %think about {last_thought})
            topic = self.resolve(topic, session=session)
        try:
            if self._thoughts_context == "full" and session is not None:
                system, msgs, seeds = self._thought_call_full(directive, session, topic, rng_seed)
            else:
                system, msgs, seeds = self._thought_call_lean(directive, session, topic, rng_seed)
            raw = self._housekeeping_reply(system, msgs).strip()
        except Exception:  # noqa: BLE001 — thoughts are best-effort; never block
            return None
        _, raw = split_reasoning(raw)  # strip any <think>…</think> (the full backdrop's directive)
        parsed = parse_thought(raw)
        if parsed is None:
            return None  # empty / malformed → record nothing (never corrupt the stream)
        text, emo = parsed
        emotion = emo if emo in _EMOTION_VALUES else DEFAULT_EMOTION.value
        thought = make_thought(
            when=self._clock().strftime("%Y-%m-%dT%H:%M"),
            kind=kind, text=text, emotion=emotion, seeds=seeds,
            user_id=self._user_id, spoken=spoken,
        )
        self._repo.add_thought(thought)
        _thoughts_log.info("%s [%s] %s", thought.when, thought.kind, thought.text)  # logged tier
        return thought

    def _thought_call_lean(
        self, directive, session: Session | None, topic: str | None, rng_seed: int,
    ) -> tuple[str, list[dict[str, str]], list[str]]:
        """The default **lean** thought call: a dedicated prompt seeded from her live state (cheap)."""
        seeds: list[str] = []
        mood = self.mood
        if mood:
            seeds.append("mood")
        _, closeness = self.closeness_status()
        if closeness:
            seeds.append("closeness")
        recent = self._recent_tail(session) if session is not None else None
        if recent:
            seeds.append("recent")
        last = self._recent_thoughts_text()
        if last:
            seeds.append("last_thoughts")
        if topic:
            seeds.append("topic")
        system, msgs = thought_request(
            directive, mood=mood, closeness=closeness, recent=recent,
            last_thoughts=last, topic=topic, rng_seed=rng_seed,
        )
        return system, msgs, seeds

    def _thought_call_full(
        self, directive, session: Session, topic: str | None, rng_seed: int,
    ) -> tuple[str, list[dict[str, str]], list[str]]:
        """The **full-context** thought call (``LUMI_THOUGHTS_CONTEXT=full``): the same backdrop a
        reply gets — canon + memory + mood + closeness + the diary block + the conversation window —
        with the reply task swapped for a thought task. Richer, but a mini-reply in tokens."""
        live = trim_history(self._repo.load_messages(session.id), self._memory_window)
        messages = [
            {"role": _ROLE_TO_LLM[m.role], "content": self._history_content(m)} for m in live
        ]
        messages.append({"role": "user", "content": thought_full_seed(topic=topic, rng_seed=rng_seed)})
        system = self._system_prompt(session)[0] + THOUGHT_FULL_HEADER.format(
            instruction=directive.instruction
        )
        seeds = ["context", *(["topic"] if topic else [])]
        return system, messages, seeds

    def _recent_tail(self, session: Session, n: int = 6) -> str | None:
        """The last ``n`` messages of the session, compact — a seed for a thought."""
        msgs = self._repo.load_messages(session.id)[-n:]
        if not msgs:
            return None
        speaker = {"user": "Він", "lili": "Я"}
        return "\n".join(f"{speaker.get(m.role, m.role)}: {m.text}" for m in msgs)

    def _recent_thoughts_text(self, n: int = 5) -> str | None:
        """The last ``n`` of her own (surfaceable) thoughts — continuity seed."""
        recent = self._repo.thoughts_for(self._user_id, "")[-n:]
        if not recent:
            return None
        return "\n".join(f"- {t.text}" for t in recent)

    def recent_thoughts(self, *, window_h: int | None = None) -> list[Thought]:
        """This user's surfaceable thoughts within the last ``window_h`` hours (dated, oldest first)."""
        hours = self._thoughts_window_h if window_h is None else window_h
        since = (self._clock() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")
        return self._repo.thoughts_for(self._user_id, since)

    def _thoughts_block(self) -> str | None:
        """The last-24h **dated diary** slice for the prompt (per-user, capped). ``None`` when off/empty."""
        if not self._thoughts_enabled:
            return None
        return thoughts_diary_block(self.recent_thoughts(), max_lines=self._thoughts_max_lines)

    @property
    def thoughts_show(self) -> str:
        """The ``/thoughts`` view policy: ``hidden`` (default) / ``admin`` / ``off`` (v0.12)."""
        return self._thoughts_show

    def thoughts_view(self, *, days: int = 7, max_lines: int = 20) -> str | None:
        """The recent dated diary for ``/thoughts`` — this user's surfaceable thoughts over the
        last ``days``, dated, capped. **Per-user filtered** (never the cross-user stream)."""
        return thoughts_diary_block(self.recent_thoughts(window_h=days * 24), max_lines=max_lines)

    def run_directive(
        self,
        raw: str,
        session: Session,
        *,
        is_owner: bool = True,
        rng_seed: int = 0,
    ) -> DirectiveOutcome:
        """Route a typed ``%directive`` (v0.12): parse → access-gate → fire → record.

        Returns a :class:`DirectiveOutcome`. ``is_directive=False`` means the input wasn't a known
        ``%directive`` (the client treats it as **plain chat**). Otherwise the thought is recorded
        (mode ``silent``/``open``); a **non-owner** can never fire silent (forced to ``open``). The
        topic may carry ``{placeholders}`` (resolved by ``think``).
        """
        parsed = parse_directive(raw)
        if parsed is None:
            return DirectiveOutcome(is_directive=False)
        mode = directive_mode(parsed, is_owner=is_owner)
        thought = self.think(parsed.name, topic=parsed.topic, session=session, rng_seed=rng_seed)
        return DirectiveOutcome(is_directive=True, mode=mode, thought=thought)

    def tick_think(
        self,
        session: Session,
        last_activity: datetime,
        now: datetime,
        *,
        rng_seed: int = 0,
        kind: str = "think",
        topic: str | None = None,
        ratio: float | None = None,
    ) -> Thought | None:
        """The **proactive nudge** (B + A): after the idle interval (and not in quiet hours / over
        the per-session cap), fire a directive **silently** into the diary; a configurable fraction
        **graduate** to a spoken turn (``Thought.spoken`` — the client delivers those via the hidden
        self-turn). ``kind``/``topic`` let the client **free-muse** (B, no topic) or fire a **seed**
        from a menu (A, e.g. ``%think {recent}``). Returns the Thought, or ``None`` (not due /
        capped / off / nothing made)."""
        if not self._thoughts_enabled:
            return None
        if not should_nudge(last_activity, now, self._thoughts_interval_s, self._thoughts_quiet_hours):
            return None
        if self._think_count >= self._thoughts_cap:
            return None
        spoken = should_graduate(
            rng_seed, self._thoughts_spoken_ratio if ratio is None else ratio
        )
        thought = self.think(kind, topic=topic, session=session, rng_seed=rng_seed, spoken=spoken)
        if thought is None:
            return None
        self._think_count += 1
        return thought

    # --- Prompt placeholders (v0.12) ------------------------------------
    def resolve(self, text: str, *, session: Session | None = None) -> str:
        """Expand ``{name}`` placeholders in ``text`` from live state (ARCHITECTURE §Prompt
        placeholders) — unknown tokens stay literal; ``{last_thought}``/``{thoughts}`` are
        isolation-aware (this user's surfacing read)."""
        return resolve_placeholders(text, self._placeholder_resolvers(session))

    def _placeholder_resolvers(self, session: Session | None) -> dict[str, Callable[[], str]]:
        """The fixed registry of token → live-value getters (lazy, isolation-aware)."""
        def last_thought() -> str:
            mine = self._repo.thoughts_for(self._user_id, "")
            return mine[-1].text if mine else ""

        return {
            "last_thought": last_thought,
            "thoughts": lambda: self._recent_thoughts_text() or "",
            "mood": lambda: self.mood or "",
            "closeness": lambda: self.closeness_status()[1] or "",
            "plan": lambda: "",  # v0.13 inner life
            "need": lambda: "",  # v0.13 needs
            "recent": lambda: (self._recent_tail(session) if session is not None else "") or "",
            "now": lambda: self._clock().strftime("%Y-%m-%d %H:%M"),
            "today": lambda: self._clock().strftime("%Y-%m-%d"),
            "user": lambda: self._user_id,
        }

    def _system_prompt(self, session: Session) -> tuple[str, str]:
        """Assemble the system prompt for this turn — returns ``(system, cache_prefix)``,
        the cacheable stable head (v0.15). Rehydrated for the user.

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
        # Long-term facts: inject the consolidated digest + any facts added since it was built
        # (verbatim tail), instead of all raw facts. Falls back to raw when no digest exists.
        raw_facts = self._repo.facts(self._user_id)
        digest = self._repo.get_facts_digest(self._user_id) if self._facts_digest_enabled else None
        if digest is not None:
            tail = [f.fact for f in raw_facts[digest.count:]]  # facts newer than the digest
            facts = [ln for ln in digest.summary.split("\n") if ln.strip()] + tail
        else:
            facts = [f.fact for f in raw_facts]
        digest = self._repo.get_digest(session.id)
        # v0.10: inject the active relationship level's authored block (warmth/openness, never
        # competence). The persisted level is the prior turn's (a fresh user sits at the default).
        # Refinement: today's ephemeral mood-shift (emotional biorhythm + cycle phase) colors the
        # EFFECTIVE level for THIS prompt only — the stored value/level are untouched. Off → none.
        closeness = None
        if self._closeness_enabled:
            existing = self._repo.get_closeness(self._user_id)
            base_level = existing.level if existing else self._default_level
            emotional = self._biorhythms.emotional.value if self._biorhythms else None
            phase = self._cycle.phase if self._cycle else None
            shift = mood_shift(emotional, phase, self._closeness_tuning.mood_shift_scale)
            base_value = existing.value if existing else self._closeness_tuning.baseline
            # with a shift, re-bucket the effective value (transient, no inertia); without one,
            # keep the inertia-stabilized persisted level (no behavior change when mood is off).
            level = shifted_level(base_value, shift) if shift else base_level
            closeness = closeness_block(self._closeness_levels, level)
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
            thoughts=self._thoughts_block(),  # the last-24h dated diary (v0.12)
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
            system, msgs = mood_request(
                self._natal, today, biorhythms=bio_line, cycle=cycle_line,
                themes=self._theme_descriptions or None,  # v0.11: also pick a face theme
                thoughts=self._recent_thoughts_text() if self._thoughts_enabled else None,  # v0.12
            )
            reading = self._housekeeping_reply(system, msgs).strip()
        except Exception:  # noqa: BLE001 — mood is best-effort; never block a turn
            return
        if not reading:
            return
        # v0.11: pull the «ТЕМА: …» pick (validated against the manifest, else default/None) and
        # keep it off the resolution.
        known = {n.lower(): n for n in self._theme_descriptions}
        theme = known.get(split_theme(reading) or "")
        self._mood = MoodState(
            date=today, resolution=split_resolution(strip_theme(reading)), reading=reading,
            theme=theme,
        )
        _mood_log.info("mood %s:\n%s", today, reading, extra={"date": today})
        if self._mood_log_path is not None:  # also persist the full reading, readable
            try:
                self._mood_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._mood_log_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n\n===== {today} =====\n{reading}\n")
            except OSError:
                pass  # best-effort; never block a turn on logging

    def _ensure_facts_digest(self) -> None:
        """Consolidate the accumulated facts into a compact digest, **rebuilt only when** the raw
        facts have grown past the last digest by ``facts_digest_refresh``.

        One housekeeping call (extended thinking off, like summaries) per refresh — far cheaper
        than injecting all raw facts every turn. **Non-destructive** (the raw facts are kept) and
        best-effort: disabled, too few facts, or a model failure → no digest (the prompt falls
        back to raw facts), never blocks a turn. Deterministic ``ts`` from the injected clock.
        """
        if not self._facts_digest_enabled:
            return
        raw = self._repo.facts(self._user_id)
        if len(raw) <= self._facts_digest_max:
            return  # already small enough — inject raw, no point digesting
        existing = self._repo.get_facts_digest(self._user_id)
        if existing is not None and len(raw) - existing.count < self._facts_digest_refresh:
            return  # fresh enough — reuse (recent facts ride as a verbatim tail in the prompt)
        try:
            system, msgs = facts_digest_request([f.fact for f in raw], self._facts_digest_max)
            digest_facts = parse_facts(self._housekeeping_reply(system, msgs).strip())
            if digest_facts:
                self._repo.set_facts_digest(
                    FactsDigest(self._user_id, "\n".join(digest_facts), len(raw), self._clock().isoformat())
                )
        except Exception:  # noqa: BLE001 — degrade to raw facts; never break a turn
            pass

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
        self._ensure_facts_digest()  # consolidate facts into a compact digest (rebuild only when they grow)
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

        system, cache_prefix = self._system_prompt(session)
        self.last_prompt = {"system": system, "messages": list(messages)}
        raw = self._llm.reply_structured(
            system=system, messages=messages, model=self._model,
            cache_prefix=cache_prefix if self._prompt_cache else None,  # v0.15 cache breakpoint
        )
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
    # v0.11 face themes: load the manifest here (the composition root), so the Core class
    # itself stays interface-independent — it only receives the theme data.
    from viewer.themes import load_themes  # local import: keep core/ free of a viewer dependency

    _faces_themes = load_themes(cfg.faces_dir)
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
        facts_digest_enabled=cfg.facts_digest,
        prompt_cache=cfg.prompt_cache,
        facts_digest_max=cfg.facts_digest_max,
        natal=load_natal(cfg.natal_path),
        mood_enabled=cfg.mood,
        mood_log_path=cfg.store_path.parent / "mood.log",
        theme_descriptions={n: d for n, d in _faces_themes.descriptions.items() if d},
        default_theme=_faces_themes.default,
        biorhythms_enabled=cfg.biorhythms,
        cycle_enabled=cfg.cycle,
        face_signal=cfg.face_signal or cfg.store_path.parent / "face.txt",
        thoughts_enabled=cfg.thoughts,
        thoughts_window_h=cfg.thoughts_window_h,
        thoughts_max_lines=cfg.thoughts_max_lines,
        thoughts_interval_s=cfg.thoughts_interval_s,
        thoughts_cap=cfg.thoughts_cap,
        thoughts_spoken_ratio=cfg.thoughts_spoken_ratio,
        thoughts_show=cfg.thoughts_show,
        thoughts_context=cfg.thoughts_context,
        quiet_hours=cfg.quiet_hours,
        thoughts_quiet_hours=cfg.thoughts_quiet_hours,
    )
