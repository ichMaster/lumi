"""The Textual terminal client — Лілі in the terminal (v0).

A thin client: it imports ``core`` and renders. On submit it calls
``core.reply(...)`` **off the UI thread** (``asyncio.to_thread``) so the UI never
freezes during the model call, and a failed call surfaces as a readable line
rather than crashing the loop. No model/SDK or storage logic lives here — in
v1.1 this is refactored into a server client calling the same contract.
"""

from __future__ import annotations

import asyncio

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, RichLog

from core.agent import Core
from core.repository import Session

USER_LABEL = "Ти"
LILI_LABEL = "Лілі"
ERROR_LINE = "⚠ Лілі зараз недоступна. Спробуй ще раз за мить."

# Speaker colors — so your lines and Лілі's read apart at a glance.
USER_COLOR = "cyan"
LILI_COLOR = "green"
ERROR_COLOR = "red"


class LumiApp(App[None]):
    """A minimal chat loop: scrollable history, an input line, clean exit."""

    TITLE = "Lumi — Лілі"
    BINDINGS = [
        ("ctrl+q", "quit", "Вийти"),
        ("ctrl+c", "quit", "Вийти"),
        ("ctrl+y", "copy_reply", "Копіювати відповідь"),
        ("ctrl+o", "copy_all", "Копіювати все"),
    ]
    CSS = """
    #history {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
        margin: 1 1 1 1;
    }

    #prompt {
        border: round $accent;
        margin: 1 1 1 1;
    }
    """

    def __init__(self, core: Core, session: Session | None = None) -> None:
        super().__init__()
        self._core = core
        self._session = session
        # Plain-text mirror of the conversation (for tests + simplicity).
        self.transcript: list[str] = []
        # Лілі's most recent reply, for one-key copy.
        self._last_reply: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield RichLog(id="history", wrap=True, markup=False)
            yield Input(
                id="prompt",
                placeholder="Напиши Лілі…  (Ctrl+Y — копіювати відповідь · Ctrl+Q — вийти)",
            )
        yield Footer()

    def on_mount(self) -> None:
        if self._session is None:
            self._session = self._core.start_session()
        self.query_one("#prompt", Input).focus()

    @staticmethod
    def _styled(label: str, message: str, color: str) -> Text:
        """A colored ``label: message`` line (bold label, tinted message)."""
        line = Text()
        line.append(f"{label}: ", style=f"bold {color}")
        line.append(message, style=color)
        return line

    @staticmethod
    def _markdown_block(label: str, message: str, color: str) -> list[RenderableType]:
        """A colored speaker label, then the message rendered as Markdown.

        Лілі writes Markdown (``**bold**``, italics, lists, code); render it so
        the formatting shows instead of literal asterisks. The label keeps the
        speaker color; the body uses Markdown's own styling.
        """
        return [Text(f"{label}:", style=f"bold {color}"), Markdown(message)]

    def _emit(self, plain: str, *renderables: RenderableType) -> None:
        # ``transcript`` stays plain text (tests + simplicity); the widget is rich.
        self.transcript.append(plain)
        log = self.query_one("#history", RichLog)
        if renderables:
            for renderable in renderables:
                log.write(renderable)
        else:
            log.write(plain)

    def _say(self, label: str, message: str, color: str) -> None:
        self._emit(f"{label}: {message}", self._styled(label, message, color))

    def _say_markdown(self, label: str, message: str, color: str) -> None:
        self._emit(f"{label}: {message}", *self._markdown_block(label, message, color))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        prompt = self.query_one("#prompt", Input)
        prompt.value = ""
        prompt.disabled = True
        self._say(USER_LABEL, text, USER_COLOR)

        try:
            assert self._session is not None
            reply = await asyncio.to_thread(self._core.reply, text, self._session)
            self._last_reply = reply
            self._say_markdown(LILI_LABEL, reply, LILI_COLOR)
        except Exception:  # noqa: BLE001 — never crash the loop on a model error
            self._emit(ERROR_LINE, Text(ERROR_LINE, style=f"bold {ERROR_COLOR}"))
        finally:
            prompt.disabled = False
            prompt.focus()

    # --- clipboard actions ----------------------------------------------
    def action_copy_reply(self) -> None:
        """Copy Лілі's last reply to the system clipboard (OSC-52)."""
        if not self._last_reply:
            self.notify("Ще немає відповіді Лілі для копіювання.", severity="warning")
            return
        self.copy_to_clipboard(self._last_reply)
        self.notify("Скопійовано останню відповідь Лілі.")

    def action_copy_all(self) -> None:
        """Copy the whole conversation (plain text) to the system clipboard."""
        if not self.transcript:
            self.notify("Розмова поки порожня.", severity="warning")
            return
        self.copy_to_clipboard("\n".join(self.transcript))
        self.notify(f"Скопійовано всю розмову ({len(self.transcript)} рядків).")
