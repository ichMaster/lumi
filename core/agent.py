"""The core turn — the single ``reply(...)`` contract every interface calls.

``Core.reply(user_text, session)`` ties **canon + LLMClient + Repository** into
one turn: assemble the system prompt and the session's history, call the model
through the :class:`~core.llm.LLMClient` seam, persist the user and Лілі
messages, and return the reply. No interface logic lives here — the TUI (v0),
and the server (v1.1) call exactly this.

v0.1 returns ``str`` and includes the **full** session history (rolling-window
trimming arrives in v0.2). v0.3 turns the return into a validated ``EmotionState``.
"""

from __future__ import annotations

from core.config import Config, load_config
from core.llm import AnthropicClient, LLMClient, Message
from core.prompt import build_system_prompt, load_canon
from core.repository import Repository, Session, make_message

# Map stored roles → the model's chat roles (Лілі speaks as the assistant).
_ROLE_TO_LLM = {"user": "user", "lili": "assistant"}


class Core:
    """Лілі's interface-independent turn engine."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        repository: Repository,
        system_prompt: str,
        model: str,
    ) -> None:
        self._llm = llm
        self._repo = repository
        self._system_prompt = system_prompt
        self._model = model

    def start_session(self) -> Session:
        """Open a fresh session (persisted)."""
        return self._repo.create_session()

    def reply(self, user_text: str, session: Session) -> str:
        """Run one turn and return Лілі's reply.

        Loads prior history, calls the model with canon + history + the new line,
        then persists both the user and Лілі messages.
        """
        history = self._repo.load_messages(session.id)
        messages: list[Message] = [
            {"role": _ROLE_TO_LLM[m.role], "content": m.text} for m in history
        ]
        messages.append({"role": "user", "content": user_text})

        reply_text = self._llm.reply(
            system=self._system_prompt,
            messages=messages,
            model=self._model,
        )

        self._repo.append_message(make_message(session.id, "user", user_text))
        self._repo.append_message(make_message(session.id, "lili", reply_text))
        return reply_text


def build_core(
    *,
    config: Config | None = None,
    llm: LLMClient | None = None,
    repository: Repository | None = None,
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
    system_prompt = build_system_prompt(canon)
    return Core(llm=llm, repository=repository, system_prompt=system_prompt, model=cfg.model)
