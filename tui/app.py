"""The Textual terminal client — Лілі in the terminal (v0).

A thin client: it imports ``core`` and renders. On submit it calls
``core.reply(...)`` **off the UI thread** (``asyncio.to_thread``) so the UI never
freezes during the model call, and a failed call surfaces as a readable line
rather than crashing the loop. No model/SDK or storage logic lives here — in
v1.1 this is refactored into a server client calling the same contract.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, RichLog

from core.agent import Core
from core.repository import Session

USER_LABEL = "Ти"
LILI_LABEL = "Лілі"
ERROR_LINE = "⚠ Лілі зараз недоступна. Спробуй ще раз за мить."


class LumiApp(App[None]):
    """A minimal chat loop: scrollable history, an input line, clean exit."""

    TITLE = "Lumi — Лілі"
    BINDINGS = [
        ("ctrl+q", "quit", "Вийти"),
        ("ctrl+c", "quit", "Вийти"),
    ]

    def __init__(self, core: Core, session: Session | None = None) -> None:
        super().__init__()
        self._core = core
        self._session = session
        # Plain-text mirror of the conversation (for tests + simplicity).
        self.transcript: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield RichLog(id="history", wrap=True, markup=False)
            yield Input(id="prompt", placeholder="Напиши Лілі…  (Ctrl+Q — вийти)")
        yield Footer()

    def on_mount(self) -> None:
        if self._session is None:
            self._session = self._core.start_session()
        self.query_one("#prompt", Input).focus()

    def _emit(self, line: str) -> None:
        self.transcript.append(line)
        self.query_one("#history", RichLog).write(line)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        prompt = self.query_one("#prompt", Input)
        prompt.value = ""
        prompt.disabled = True
        self._emit(f"{USER_LABEL}: {text}")

        try:
            assert self._session is not None
            reply = await asyncio.to_thread(self._core.reply, text, self._session)
            self._emit(f"{LILI_LABEL}: {reply}")
        except Exception:  # noqa: BLE001 — never crash the loop on a model error
            self._emit(ERROR_LINE)
        finally:
            prompt.disabled = False
            prompt.focus()
