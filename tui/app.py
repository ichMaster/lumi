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
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, RichLog, Static

from core.agent import Core
from core.repository import Session

USER_LABEL = "Ти"
LILI_LABEL = "Лілі"
ERROR_LINE = "⚠ Лілі зараз недоступна. Спробуй ще раз за мить."
MEMORY_EMPTY = "_Памʼять поки порожня._"
CLEARED_LINE = "🧠 Памʼять очищено (короткострокову й довгострокову)."
CANCELLED_LINE = "Скасовано."

# Speaker colors — so your lines and Лілі's read apart at a glance.
USER_COLOR = "cyan"
LILI_COLOR = "green"
ERROR_COLOR = "red"
SYSTEM_COLOR = "yellow"
THINKING_COLOR = "grey50"  # Лілі's reasoning, dimmed apart from her reply
THINKING_PREFIX = "💭"

# Technical connection status (no icons) — same vocabulary across states.
STATUS_READY = "online"
STATUS_BUSY = "requesting…"
STATUS_OFFLINE = "offline"


class ConfirmScreen(ModalScreen[bool]):
    """A tiny yes/no modal — returns ``True`` on confirm, ``False`` otherwise."""

    BINDINGS = [
        ("y", "confirm", "Так"),
        ("т", "confirm", "Так"),
        ("n", "cancel", "Ні"),
        ("escape", "cancel", "Скасувати"),
    ]
    CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #dialog {
        width: auto;
        max-width: 70%;
        height: auto;
        border: round $warning;
        padding: 1 2;
        background: $panel;
    }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._question)
            yield Label("[y] так   ·   [n] ні")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class LumiApp(App[None]):
    """A minimal chat loop: scrollable history, an input line, clean exit."""

    TITLE = "Lumi — Лілі"
    BINDINGS = [
        ("ctrl+q", "quit", "Вийти"),
        ("ctrl+c", "quit", "Вийти"),
        ("ctrl+y", "copy_reply", "Копіювати відповідь"),
        ("ctrl+o", "copy_all", "Копіювати все"),
        ("ctrl+l", "clear", "Очистити екран"),
        ("ctrl+t", "toggle_mouse", "Виділення мишею"),
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

    #status, #stats {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $panel;
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
        # When True the app releases the mouse so the terminal can select text.
        self._mouse_selection: bool = False
        # Connection state for the status line (False after a failed turn).
        self._connected: bool = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="status")
        yield Static(id="stats")
        with Vertical():
            yield RichLog(id="history", wrap=True, markup=False)
            yield Input(
                id="prompt",
                placeholder="Напиши Лілі…  (/memory · /forget · Ctrl+Y копіювати · Ctrl+Q вийти)",
            )
        yield Footer()

    def on_mount(self) -> None:
        if self._session is None:
            self._session = self._core.start_session()
        self._render_status()
        self._render_stats()
        self.query_one("#prompt", Input).focus()

    def on_unmount(self) -> None:
        # Session-end hook: summarize on exit (best-effort — never block quitting).
        if self._session is not None:
            try:
                self._core.end_session(self._session)
            except Exception:  # noqa: BLE001
                pass

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

    def _emit_thinking(self, thinking: str) -> None:
        """Render Лілі's reasoning, dimmed in grey above her reply."""
        plain = f"{THINKING_PREFIX} {thinking}"
        self._emit(plain, Text(plain, style=f"italic {THINKING_COLOR}"))

    # --- status line -----------------------------------------------------
    @staticmethod
    def _fmt_tokens(n: int | None) -> str:
        if n is None:
            return "—"
        return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

    @staticmethod
    def _fmt_latency(ms: int) -> str:
        return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"

    @staticmethod
    def _short_model(model: str) -> str:
        return model[len("claude-") :] if model.startswith("claude-") else model

    def _status_text(self, busy: str | None = None) -> str:
        """The technical connection/status line (no icons)."""
        model = self._short_model(self._core.model)
        thinking = self._core.last_stats and self._core.last_stats.thinking
        think = " thinking" if thinking else ""
        if busy:
            return f"status: [yellow]{busy}[/] · {model}{think}"
        if not self._connected:
            return f"status: [red]{STATUS_OFFLINE}[/] · {model} · no connection"
        return f"status: [green]{STATUS_READY}[/] · {model}{think}"

    def _stats_text(self) -> str:
        """The statistics line — last response + running totals (no icons)."""
        stats = self._core.last_stats
        totals = self._core.totals
        if stats is None or totals.turns == 0:
            return "stats: —"
        last = (
            f"last {self._fmt_tokens(stats.input_tokens)}/"
            f"{self._fmt_tokens(stats.output_tokens)} tok · {self._fmt_latency(stats.latency_ms)}"
        )
        total = (
            f"total {totals.turns} turns · "
            f"{self._fmt_tokens(totals.input_tokens)}/{self._fmt_tokens(totals.output_tokens)} tok"
            f" · avg {self._fmt_latency(totals.avg_latency_ms)}"
        )
        return f"stats: {last}   ·   {total}"

    def _render_status(self, busy: str | None = None) -> None:
        self.query_one("#status", Static).update(self._status_text(busy))

    def _render_stats(self) -> None:
        self.query_one("#stats", Static).update(self._stats_text())

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        prompt = self.query_one("#prompt", Input)
        prompt.value = ""

        # Memory commands (memory.view / memory.clear) — handled here, not sent
        # to the model or persisted as a turn.
        if text == "/memory":
            self._show_memory()
            prompt.focus()
            return
        if text == "/forget":
            self._forget()
            prompt.focus()
            return

        prompt.disabled = True
        self._say(USER_LABEL, text, USER_COLOR)
        self._render_status(busy=STATUS_BUSY)  # live tech status: working, not frozen

        try:
            assert self._session is not None
            reply = await asyncio.to_thread(self._core.reply, text, self._session)
            self._connected = True
            self._last_reply = reply
            thinking = getattr(self._core, "last_thinking", None)
            if thinking:
                self._emit_thinking(thinking)
            self._say_markdown(LILI_LABEL, reply, LILI_COLOR)
        except Exception:  # noqa: BLE001 — never crash the loop on a model error
            self._connected = False
            self._emit(ERROR_LINE, Text(ERROR_LINE, style=f"bold {ERROR_COLOR}"))
        finally:
            self._render_status()
            self._render_stats()
            prompt.disabled = False
            prompt.focus()

    # --- memory commands -------------------------------------------------
    def _show_memory(self) -> None:
        """Render the user's memory (facts + summaries) — the `/memory` command."""
        mem = self._core.view_memory()
        lines: list[str] = []
        if mem.facts:
            lines.append("**Що Лілі памʼятає про тебе:**")
            lines += [f"- {f}" for f in mem.facts]
        if mem.summaries:
            lines.append("**Памʼять про попередні розмови:**")
            lines += [f"- {s}" for s in mem.summaries]
        body = "\n".join(lines) if lines else MEMORY_EMPTY
        self._emit(body, Markdown(body))

    def _forget(self) -> None:
        """Clear the user's memory after a confirmation — the `/forget` command."""

        def _on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._core.clear_memory()
                self._emit(CLEARED_LINE, Text(CLEARED_LINE, style=f"bold {SYSTEM_COLOR}"))
            else:
                self._emit(CANCELLED_LINE, Text(CANCELLED_LINE, style=SYSTEM_COLOR))

        self.push_screen(
            ConfirmScreen("Очистити памʼять Лілі про тебе? Це не можна скасувати."),
            _on_confirm,
        )

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

    def action_clear(self) -> None:
        """Clear the on-screen history. Лілі still remembers (the store is kept)."""
        self.query_one("#history", RichLog).clear()
        self.transcript.clear()
        self._last_reply = None
        self.notify("Екран очищено. Лілі памʼятає розмову — історія збережена.")

    def action_toggle_mouse(self) -> None:
        """Release/recapture the mouse so the terminal can select text natively.

        While released, drag-select + your terminal's copy works on the chat;
        Textual's mouse features (scroll, click) pause until you toggle back.
        """
        self._mouse_selection = not self._mouse_selection
        driver = self._driver
        if self._mouse_selection:
            if driver is not None and hasattr(driver, "_disable_mouse_support"):
                driver._disable_mouse_support()
            self.notify("Виділення мишею увімкнено — тягни, щоб виділити й скопіювати. Ctrl+T — назад.")
        else:
            if driver is not None and hasattr(driver, "_enable_mouse_support"):
                driver._enable_mouse_support()
            self.notify("Виділення мишею вимкнено (звичайний режим).")
