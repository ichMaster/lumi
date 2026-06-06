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

from core.config import DEFAULT_MEMORY_WINDOW, Config, load_config
from core.llm import AnthropicClient, LLMClient, Message
from core.memory import summary_request, trim_history
from core.prompt import build_system_prompt, load_canon
from core.repository import Repository, Session, ShortSummary, make_message, now_iso
from core.user import DEFAULT_USER_ID

# Map stored roles → the model's chat roles (Лілі speaks as the assistant).
_ROLE_TO_LLM = {"user": "user", "lili": "assistant"}


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
    ) -> None:
        self._llm = llm
        self._repo = repository
        self._canon = canon
        self._model = model
        self._user_id = user_id
        self._memory_window = memory_window

    @property
    def user_id(self) -> str:
        return self._user_id

    def start_session(self) -> Session:
        """Open a fresh session for the active user (persisted)."""
        return self._repo.create_session(self._user_id)

    def _system_prompt(self) -> str:
        """Assemble the system prompt for this turn.

        v0.2: the canon verbatim. LUMI-011 folds the user's recent summaries +
        long-term facts in here (still canon at the base).
        """
        return build_system_prompt(self._canon)

    def reply(self, user_text: str, session: Session) -> str:
        """Run one turn and return Лілі's reply.

        Loads prior history (trimmed to the rolling window), calls the model with
        the system prompt + windowed history + the new line, then persists both
        the user and Лілі messages (user-scoped). The full history stays stored;
        only the in-context window is trimmed.
        """
        history = trim_history(self._repo.load_messages(session.id), self._memory_window)
        messages: list[Message] = [
            {"role": _ROLE_TO_LLM[m.role], "content": m.text} for m in history
        ]
        messages.append({"role": "user", "content": user_text})

        reply_text = self._llm.reply(
            system=self._system_prompt(),
            messages=messages,
            model=self._model,
        )

        self._repo.append_message(make_message(session.id, self._user_id, "user", user_text))
        self._repo.append_message(make_message(session.id, self._user_id, "lili", reply_text))
        return reply_text

    def end_session(self, session: Session) -> ShortSummary | None:
        """Close a session: mark it ended and write a short summary.

        Summarizes the session's history via the model and stores a per-user
        ``ShortSummary`` (injected at startup by LUMI-011). An empty session
        produces none; a model failure degrades to no summary — ending a
        session never raises (ARCHITECTURE §Error handling).
        """
        history = self._repo.load_messages(session.id)
        self._repo.end_session(session.id)
        if not history:
            return None
        try:
            system, msgs = summary_request(history)
            summary_text = self._llm.reply(system=system, messages=msgs, model=self._model).strip()
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
        llm = AnthropicClient(cfg.api_key)

    canon = load_canon(cfg.canon_path)
    return Core(
        llm=llm,
        repository=repository,
        canon=canon,
        model=cfg.model,
        user_id=user_id,
        memory_window=cfg.memory_window,
    )
