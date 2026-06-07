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

from dataclasses import dataclass

from core.config import DEFAULT_COMPACTION_BATCH, DEFAULT_MEMORY_WINDOW, Config, load_config
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
from core.prompt import build_system_prompt, load_canon
from core.repository import (
    LongTermFact,
    Repository,
    Session,
    SessionDigest,
    ShortSummary,
    make_message,
    now_iso,
)
from core.styles import DEFAULT_STYLE, load_styles
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
    ) -> None:
        self._llm = llm
        self._repo = repository
        self._canon = canon
        self._model = model
        self._user_id = user_id
        self._memory_window = memory_window
        self._compaction_batch = compaction_batch
        # Answer styles (overlays) + the active one (per-session).
        self._styles = styles or {}
        self._style = DEFAULT_STYLE
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
    def style(self) -> str:
        """The active answer style (per-session)."""
        return self._style

    def style_names(self) -> list[str]:
        """All selectable style names (``normal`` + the authored overlays)."""
        return [DEFAULT_STYLE, *sorted(self._styles)]

    def set_style(self, name: str) -> bool:
        """Switch the active answer style; returns ``False`` for an unknown style."""
        name = name.strip().lower()
        if name == DEFAULT_STYLE or name in self._styles:
            self._style = name
            return True
        return False

    def start_session(self) -> Session:
        """Open a fresh session for the active user (persisted).

        The answer style is per-session — it resets to ``normal`` here.
        """
        self._style = DEFAULT_STYLE
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
        return build_system_prompt(
            self._canon,
            summaries=summaries,
            facts=facts,
            digest=digest.summary if digest else None,
            style=self._styles.get(self._style) or None,
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

    def reply(self, user_text: str, session: Session) -> str:
        """Run one turn and return Лілі's reply.

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
        reply_text = self._llm.reply(system=system, messages=messages, model=self._model)
        self.last_thinking = getattr(self._llm, "last_thinking", None)
        self.last_stats = getattr(self._llm, "last_stats", None)
        if self.last_stats is not None:
            self.totals.turns += 1
            self.totals.input_tokens += self.last_stats.input_tokens or 0
            self.totals.output_tokens += self.last_stats.output_tokens or 0
            self.totals.latency_ms += self.last_stats.latency_ms

        self._repo.append_message(make_message(session.id, self._user_id, "user", user_text))
        self._repo.append_message(make_message(session.id, self._user_id, "lili", reply_text))
        return reply_text

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
    )
