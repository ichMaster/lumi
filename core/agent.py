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

import re
from dataclasses import dataclass

from core.config import DEFAULT_COMPACTION_BATCH, DEFAULT_MEMORY_WINDOW, Config, load_config
from core.emotion import EmotionState, validate
from core.llm import AnthropicClient, LLMClient, Message, ResponseStats
from core.memory import (
    RECENT_SUMMARIES,
    compaction_plan,
    digest_request,
    facts_request,
    parse_facts,
    summary_request,
    trim_history,
)
from core.prompt import (
    REASONING_DIRECTIVE,
    build_system_prompt,
    load_canon,
    split_emotion,
    split_reasoning,
)
from core.repository import (
    LongTermFact,
    Repository,
    Session,
    SessionDigest,
    ShortSummary,
    make_message,
    now_iso,
)
from core.styles import DEFAULT_STYLE, load_meta_styles, load_styles
from core.user import DEFAULT_USER_ID

# Map stored roles → the model's chat roles (Лілі speaks as the assistant).
_ROLE_TO_LLM = {"user": "user", "lili": "assistant"}


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
        styles: dict[str, str] | None = None,
        meta_styles: dict[str, list[str]] | None = None,
    ) -> None:
        self._llm = llm
        self._repo = repository
        self._canon = canon
        self._model = model
        self._user_id = user_id
        self._memory_window = memory_window
        self._compaction_batch = compaction_batch
        # Answer styles (overlays) + meta-styles (presets → several base styles) +
        # the active selection (per-session; combinable). Empty = "normal".
        self._styles = styles or {}
        self._meta = meta_styles or {}
        self._active: list[str] = []
        # The validated EmotionState from the last turn (for a renderer / status line).
        self.last_emotion: EmotionState | None = None
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
    def thinking(self) -> bool:
        """Whether extended thinking is enabled on the model (for a status indicator)."""
        return bool(getattr(self._llm, "_thinking", False))

    @property
    def style(self) -> str:
        """The active answer style(s), combined for display ('short+formal')."""
        return "+".join(self._active) if self._active else DEFAULT_STYLE

    def base_names(self) -> list[str]:
        """The authored base style names."""
        return sorted(self._styles)

    def meta_names(self) -> list[str]:
        """The meta-style (preset) names."""
        return sorted(self._meta)

    def style_names(self) -> list[str]:
        """All selectable names (``normal`` + meta-styles + base styles)."""
        return [DEFAULT_STYLE, *sorted(self._meta), *sorted(self._styles)]

    def set_style(self, spec: str) -> bool:
        """Set the active answer style(s) from a spec — base styles and/or meta-styles.

        Names are separated by spaces, commas, or ``+`` (e.g. ``"коротко офіційно"``
        or a meta-style like ``"лагідна"``); they stack (each overlay is appended, in
        order). A **meta-style** expands to its base styles. ``normal`` clears the
        overlay. Returns ``False`` (and changes nothing) if **any** name is
        unknown — the switch is all-or-nothing.
        """
        names = [n for n in re.split(r"[\s,+]+", spec.strip().lower()) if n]
        if not names:
            return False
        valid = {DEFAULT_STYLE, *self._styles, *self._meta}
        if any(n not in valid for n in names):
            return False
        active: list[str] = []
        for n in names:  # drop 'normal' (no overlay) and dedupe, keep order
            if n != DEFAULT_STYLE and n not in active:
                active.append(n)
        self._active = active
        return True

    def _expand(self) -> list[str]:
        """Resolve the active selection to an ordered, deduped list of base styles.

        Meta-styles expand to their base styles; base styles map to themselves.
        Unknown base references (e.g. a typo in a meta alias) are skipped.
        """
        out: list[str] = []
        for token in self._active:
            for base in self._meta.get(token, [token]):
                if base in self._styles and base not in out:
                    out.append(base)
        return out

    def _style_overlay(self) -> str | None:
        """The combined overlay text for the active (expanded) style(s), or ``None``."""
        return "\n\n".join(self._styles[b] for b in self._expand()) or None

    def start_session(self) -> Session:
        """Open a fresh session for the active user (persisted).

        The answer style is per-session — it resets to ``normal`` here.
        """
        self._active = []
        return self._repo.create_session(self._user_id)

    def _system_prompt(self, session: Session) -> str:
        """Assemble the system prompt for this turn, rehydrated for the user.

        Composes the canon with the user's recent summaries + long-term facts and
        — if the current session has been compacted — its running digest. Loaded
        per turn, so a restart recalls prior context and new memory takes effect.
        Isolation holds — only this ``user_id``'s records are read.
        """
        summaries = [
            s.summary for s in self._repo.recent_summaries(self._user_id, RECENT_SUMMARIES)
        ]
        facts = [f.fact for f in self._repo.facts(self._user_id)]
        digest = self._repo.get_digest(session.id)
        # Append the reasoning directive to the canon so any pre-answer reasoning is
        # wrapped in <think>…</think> (parsed out in reply()); the style rides last.
        return build_system_prompt(
            f"{self._canon}\n\n{REASONING_DIRECTIVE}",
            summaries=summaries,
            facts=facts,
            digest=digest.summary if digest else None,
            style=self._style_overlay(),
            emotion=True,
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

    def reply(self, user_text: str, session: Session) -> EmotionState:
        """Run one turn and return Лілі's validated :class:`EmotionState` (v0.3).

        Compacts older-than-window messages into the session digest (in batches),
        then calls the model with the system prompt (+ digest) + the verbatim
        **live tail** (between ``memory_window`` and ``+ batch`` messages) + the
        new line, and persists both messages. The full history stays stored.
        """
        history = self._repo.load_messages(session.id)
        digest = self._maybe_compact(session, history)
        compacted = digest.compacted_count if digest else 0
        # The verbatim tail: messages not yet folded into the digest, capped for
        # safety (in case compaction repeatedly failed).
        live = trim_history(history[compacted:], self._memory_window + self._compaction_batch)
        messages: list[Message] = [
            {"role": _ROLE_TO_LLM[m.role], "content": m.text} for m in live
        ]
        messages.append({"role": "user", "content": user_text})

        system = self._system_prompt(session)
        self.last_prompt = {"system": system, "messages": list(messages)}
        raw = self._llm.reply_structured(system=system, messages=messages, model=self._model)
        # Split any <think>…</think> reasoning, then the inline <emotion> tag, out of
        # the reply field; the clean text is shown/stored. Prefer the model's tagged
        # inline reasoning; fall back to the provider's summarized thinking channel.
        inline_thinking, reply_text = split_reasoning(str(raw.get("reply") or ""))
        tag_emotion, reply_text = split_emotion(reply_text)
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

        self._repo.append_message(make_message(session.id, self._user_id, "user", user_text))
        self._repo.append_message(
            make_message(
                session.id,
                self._user_id,
                "lili",
                state.reply,
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
        summary = ShortSummary(
            user_id=self._user_id,
            session_id=session.id,
            summary=summary_text,
            ts=now_iso(),
        )
        self._repo.add_summary(summary)
        return summary

    # --- memory commands (memory.view / memory.clear) -------------------
    def view_memory(self, user_id: str | None = None) -> MemoryView:
        """Snapshot the user's relationship memory (summaries + facts)."""
        uid = user_id or self._user_id
        return MemoryView(
            summaries=[s.summary for s in self._repo.recent_summaries(uid, RECENT_SUMMARIES)],
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
        styles=load_styles(cfg.styles_path),
        meta_styles=load_meta_styles(cfg.styles_path),
    )
