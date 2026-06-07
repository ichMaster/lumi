"""The Textual terminal client — Лілі in the terminal (v0).

A thin client: it imports ``core`` and renders. On submit it calls
``core.reply(...)`` **off the UI thread** (``asyncio.to_thread``) so the UI never
freezes during the model call, and a failed call surfaces as a readable line
rather than crashing the loop. No model/SDK or storage logic lives here — in
v1.1 this is refactored into a server client calling the same contract.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Label, RichLog, Static, TextArea

from core.agent import Core
from core.repository import Session
from core.styles import DEFAULT_STYLE

USER_LABEL = "You"
LILI_LABEL = "Лілі"  # her name (the persona is Ukrainian); UI chrome is English
ERROR_LINE = "Лілі is unavailable right now. Try again in a moment."
MEMORY_EMPTY = "_Memory is empty so far._"
CLEARED_LINE = "Memory cleared (short- and long-term)."
CANCELLED_LINE = "Cancelled."

# Speaker colors — so your lines and Лілі's read apart at a glance.
USER_COLOR = "cyan"
LILI_COLOR = "green"
ERROR_COLOR = "red"
SYSTEM_COLOR = "yellow"
THINKING_COLOR = "grey50"  # Лілі's reasoning, dimmed apart from her reply

# Technical connection status (no icons) — same vocabulary across states.
STATUS_READY = "online"
STATUS_BUSY = "requesting…"
STATUS_OFFLINE = "offline"


class ConfirmScreen(ModalScreen[bool]):
    """A tiny yes/no modal — returns ``True`` on confirm, ``False`` otherwise."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("т", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "Cancel"),
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
            yield Label("[y] yes   ·   [n] no")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ChatInput(TextArea):
    """A multi-line chat input: Enter submits, Shift+Enter inserts a newline.

    Sized to ~3 lines and scrolls when the message is longer (or pasted).
    """

    class Submitted(Message):
        """Posted when the user submits the input (Enter)."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def on_focus(self) -> None:
        # Blink the cursor only while the box is focused.
        self.cursor_blink = True

    def on_blur(self) -> None:
        self.cursor_blink = False

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)


class LumiApp(App[None]):
    """A minimal chat loop: scrollable history, an input line, clean exit."""

    TITLE = "Lumi — Лілі"
    # priority=True so these app shortcuts win over the multi-line input's own
    # key bindings (TextArea otherwise captures ctrl+y/o/l/t while focused).
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+y", "copy_reply", "Copy reply", priority=True),
        Binding("ctrl+o", "copy_all", "Copy all", priority=True),
        Binding("ctrl+l", "clear", "Clear screen", priority=True),
        Binding("ctrl+t", "toggle_mouse", "Mouse select", priority=True),
    ]
    CSS = """
    #thinking {
        height: 6;                 /* a small fixed peek at Лілі's reasoning */
        border: round $primary-darken-3;
        padding: 0 1;
        margin: 1 1 0 1;
        color: $text-muted;
    }

    #history {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
        margin: 1 1 1 1;
    }

    #prompt {
        height: 5;          /* border (2) + ~3 text lines; scrolls if longer */
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
        # What's currently in the Thinking box (last turn's reasoning, or None).
        self._thinking_shown: str | None = None
        # When True the app releases the mouse so the terminal can select text.
        self._mouse_selection: bool = False
        # Connection state for the status line (False after a failed turn).
        self._connected: bool = True
        # True while a turn (or session save) is in flight — you can type, but
        # can only send when it's your turn (not busy).
        self._busy: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="status")
        yield Static(id="stats")
        with Vertical():
            thinking = RichLog(id="thinking", wrap=True, markup=False)
            thinking.border_title = "Thinking"
            thinking.border_subtitle = "Лілі's reasoning — last turn only"
            yield thinking
            yield RichLog(id="history", wrap=True, markup=False)
            prompt = ChatInput(id="prompt", show_line_numbers=False, soft_wrap=True)
            prompt.border_title = "You"
            prompt.border_subtitle = "Enter — send · Shift+Enter — newline · /style /new /prompt /memory /forget"
            yield prompt
        yield Footer()

    def on_mount(self) -> None:
        if self._session is None:
            self._session = self._core.start_session()
        self._render_status()
        self._render_stats()
        self.query_one("#prompt", ChatInput).focus()

    def on_unmount(self) -> None:
        # Fallback for non-quit teardown (e.g. a crash): summarize if action_quit
        # didn't already (it nulls self._session once it has processed).
        if self._session is not None:
            try:
                self._core.end_session(self._session)
            except Exception:  # noqa: BLE001
                pass

    async def action_quit(self) -> None:
        """Quit — but summarize the current session first, then exit when done."""
        if self._session is not None:
            self._busy = True
            note = "Saving session before exit (summary + facts)…"
            self._emit(note, Text(note, style=f"bold {SYSTEM_COLOR}"))
            await self._process_current_session()
            self._session = None  # so on_unmount won't re-process it
        self.exit()

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

    def _render_thinking(self, thinking: str | None) -> None:
        """Show the last turn's reasoning in the Thinking box only (empty if none).

        Always replaces the box contents — never the chat — so the box holds just
        the most recent response's thinking, and is empty when there was none.
        """
        self._thinking_shown = thinking or None
        box = self.query_one("#thinking", RichLog)
        box.clear()
        if thinking:
            box.write(Text(thinking, style=f"italic {THINKING_COLOR}"))

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
        style = self._core.style
        style_part = f" · style: {style}" if style != DEFAULT_STYLE else ""
        meta = f"{model}{think}{style_part}"
        if busy:
            return f"status: [yellow]{busy}[/] · {meta}"
        if not self._connected:
            return f"status: [red]{STATUS_OFFLINE}[/] · {model} · no connection"
        return f"status: [green]{STATUS_READY}[/] · {meta}"

    def _stats_text(self) -> str:
        """The statistics line — last response + running totals (total tokens only)."""
        stats = self._core.last_stats
        totals = self._core.totals
        if stats is None or totals.turns == 0:
            return "stats: —"
        last_tok = (stats.input_tokens or 0) + (stats.output_tokens or 0)
        total_tok = totals.input_tokens + totals.output_tokens
        last = f"last {self._fmt_tokens(last_tok)} tok · {self._fmt_latency(stats.latency_ms)}"
        total = (
            f"total {totals.turns} turns · {self._fmt_tokens(total_tok)} tok"
            f" · avg {self._fmt_latency(totals.avg_latency_ms)}"
        )
        return f"stats: {last}   ·   {total}"

    def _render_status(self, busy: str | None = None) -> None:
        self.query_one("#status", Static).update(self._status_text(busy))

    def _render_stats(self) -> None:
        self.query_one("#stats", Static).update(self._stats_text())

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        prompt = self.query_one("#prompt", ChatInput)

        # You can keep typing while Лілі responds, but can only *send* on your
        # turn. A premature submit keeps the draft (no clear, no send).
        if self._busy:
            self.notify("Лілі is still replying — send when it's your turn.",
                        severity="warning", timeout=2)
            return

        text = event.value.strip()
        prompt.text = ""
        if not text:
            prompt.focus()
            return

        # Commands — handled here, not sent to the model or persisted as a turn.
        if text == "/memory":
            self._show_memory()
            prompt.focus()
            return
        if text == "/forget":
            self._forget()
            prompt.focus()
            return
        if text == "/prompt":
            self._show_prompt()
            prompt.focus()
            return
        if text == "/style" or text.startswith("/style "):
            self._style_command(text)
            prompt.focus()
            return
        if text == "/new":
            await self._new_session()
            prompt.focus()
            return

        self._busy = True
        self._say(USER_LABEL, text, USER_COLOR)
        self._render_status(busy=STATUS_BUSY)  # live tech status: working, not frozen

        try:
            assert self._session is not None
            reply = await asyncio.to_thread(self._core.reply, text, self._session)
            self._connected = True
            self._last_reply = reply
            compacted = getattr(self._core, "last_compaction", 0)
            if compacted:
                note = f"Compacted {compacted} earlier messages into a running summary."
                self._emit(note, Text(note, style=SYSTEM_COLOR))
            # Лілі's reasoning goes to the Thinking box only (not the chat);
            # the box shows just this turn's thinking, or clears if there was none.
            self._render_thinking(getattr(self._core, "last_thinking", None))
            self._say_markdown(LILI_LABEL, reply, LILI_COLOR)
        except Exception:  # noqa: BLE001 — never crash the loop on a model error
            self._render_thinking(None)  # the failed turn has no thinking
            self._connected = False
            self._emit(ERROR_LINE, Text(ERROR_LINE, style=f"bold {ERROR_COLOR}"))
        finally:
            self._busy = False
            self._render_status()
            self._render_stats()
            prompt.focus()

    # --- memory commands -------------------------------------------------
    def _show_memory(self) -> None:
        """Render the user's memory (facts + summaries) — the `/memory` command."""
        mem = self._core.view_memory()
        lines: list[str] = []
        if mem.facts:
            lines.append("**What Лілі remembers about you:**")
            lines += [f"- {f}" for f in mem.facts]
        if mem.summaries:
            lines.append("**Memory of past conversations:**")
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
            ConfirmScreen("Clear Лілі's memory about you? This can't be undone."),
            _on_confirm,
        )

    # --- session + prompt commands --------------------------------------
    async def _process_current_session(self) -> None:
        """End + summarize the active session (writes its summary + facts).

        Best-effort and run off the UI thread so it never blocks; shared by the
        `/new` command and quit. Shows the technical "saving" status while it runs.
        """
        if self._session is None:
            return
        self._render_status(busy="saving session…")
        try:
            await asyncio.to_thread(self._core.end_session, self._session)
        except Exception:  # noqa: BLE001 — never block on housekeeping
            pass

    async def _new_session(self) -> None:
        """Start a fresh session — `/new`. The previous session is summarized first."""
        self._busy = True
        try:
            await self._process_current_session()
            self._session = self._core.start_session()
            # Fresh session → fresh screen (memory is kept in the store).
            self.query_one("#history", RichLog).clear()
            self._render_thinking(None)  # clear the Thinking box too
            self.transcript.clear()
            self._last_reply = None
            line = "── new session (previous saved) ──"
            self._emit(line, Text(line, style=f"bold {SYSTEM_COLOR}"))
            self._render_status()
            self._render_stats()
        finally:
            self._busy = False

    def _style_command(self, text: str) -> None:
        """`/style` lists styles + current; `/style <name> [<name>…]` switches (combinable).

        A name can be a meta-style (expands to several) or a base style; they stack.
        """
        arg = text[len("/style"):].strip()
        if not arg:
            metas = ", ".join(self._core.meta_names()) or "—"
            bases = ", ".join(self._core.base_names())
            line = (
                f"Meta-styles: {metas}  (current: {self._core.style})\n"
                f"Base: normal, {bases}  ·  combine: /style коротко офіційно"
            )
            self._emit(line, Text(line, style=SYSTEM_COLOR))
            return
        if self._core.set_style(arg):
            line = f"Style → {self._core.style}"
            self._emit(line, Text(line, style=f"bold {SYSTEM_COLOR}"))
            self._render_status()
        else:
            names = ", ".join(self._core.style_names())
            line = f"Unknown style in '{arg}'. Available: {names}"
            self._emit(line, Text(line, style=ERROR_COLOR))

    def _show_prompt(self) -> None:
        """Show the exact prompt sent on the last turn — `/prompt`."""
        p = getattr(self._core, "last_prompt", None)
        if not p:
            msg = "No prompt yet — make a turn first."
            self._emit(msg, Text(msg, style=SYSTEM_COLOR))
            return
        parts = ["── last turn's prompt ──", "", "[SYSTEM]", p["system"], "", "[MESSAGES]"]
        parts += [f"{m['role']}: {m['content']}" for m in p["messages"]]
        body = "\n".join(parts)
        self._emit(body, Text(body, style=THINKING_COLOR))  # dim, like a meta block

    # --- clipboard actions ----------------------------------------------
    def _copy(self, text: str) -> None:
        """Copy to the system clipboard — via pbcopy on macOS (reliable) and
        OSC-52 (for remote/other terminals)."""
        pbcopy = shutil.which("pbcopy")
        if pbcopy:
            try:
                subprocess.run([pbcopy], input=text.encode("utf-8"), check=False, timeout=5)
            except Exception:  # noqa: BLE001 — fall back to OSC-52
                pass
        self.copy_to_clipboard(text)

    def action_copy_reply(self) -> None:
        """Copy Лілі's last reply to the system clipboard."""
        if not self._last_reply:
            self.notify("No Лілі reply to copy yet.", severity="warning")
            return
        self._copy(self._last_reply)
        self.notify("Copied Лілі's last reply.")

    def action_copy_all(self) -> None:
        """Copy the whole conversation (plain text) to the system clipboard."""
        if not self.transcript:
            self.notify("Conversation is empty.", severity="warning")
            return
        self._copy("\n".join(self.transcript))
        self.notify(f"Copied the whole conversation ({len(self.transcript)} lines).")

    def action_clear(self) -> None:
        """Clear the on-screen history. Лілі still remembers (the store is kept)."""
        self.query_one("#history", RichLog).clear()
        self.transcript.clear()
        self._last_reply = None
        self.notify("Screen cleared. Лілі still remembers — history is kept.")

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
            self.notify("Mouse select on — drag to select & copy. Ctrl+T to toggle back.")
        else:
            if driver is not None and hasattr(driver, "_enable_mouse_support"):
                driver._enable_mouse_support()
            self.notify("Mouse select off (normal mode).")
