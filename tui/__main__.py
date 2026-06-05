"""Run the TUI: ``python -m tui`` (or ``uv run python -m tui``).

Wires the real core (Claude Haiku via the Anthropic backend) from config and
launches the Textual app. Needs ``ANTHROPIC_API_KEY`` in ``.env``.
"""

from __future__ import annotations

from core.agent import build_core
from core.llm import LLMError
from tui.app import LumiApp


def main() -> None:
    try:
        core = build_core()
    except LLMError as exc:
        raise SystemExit(f"{exc}\nSet ANTHROPIC_API_KEY in .env (see .env.example).") from exc
    LumiApp(core).run()


if __name__ == "__main__":
    main()
