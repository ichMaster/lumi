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
from pathlib import Path

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
from core.biorhythm import format_biorhythms
from core.config import load_config
from core.cycle import format_cycle
from core.emoji import EmojiRenderer, load_emoji_map
from core.emotion import LogRenderer
from core.nudge import load_nudges, pick_nudge_index, proactive_due
from core.prompt import mark_cache_breakpoint
from core.repository import Session
from core.thoughts import parse_directive
from core.worldcontext import fetch_world_context
from tui.bridge import drain_inbox, mirror_reply, mirror_user
from tui.sound import SoundPlayer

USER_LABEL = "You"
LILI_LABEL = "Лілі"  # her name (the persona is Ukrainian); UI chrome is English
ERROR_LINE = "Лілі is unavailable right now. Try again in a moment."
MEMORY_EMPTY = "_Memory is empty so far._"
MOOD_PENDING = "_Лілі ще не визначила настрій сьогодні — напиши їй, і він складеться._"
BIORHYTHM_OFF = "_Біоритми вимкнені або немає дати народження — напиши їй, щоб порахувати._"
CLEARED_LINE = "Memory cleared (short- and long-term)."
CANCELLED_LINE = "Cancelled."

# Speaker colors — so your lines and Лілі's read apart at a glance.
USER_COLOR = "cyan"
TELEGRAM_LABEL = "📱 Telegram"  # v0.13: an incoming line from the Telegram bridge
TELEGRAM_COLOR = "blue"
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
        Binding("ctrl+s", "toggle_sound", "Sound", priority=True),
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
        /* No border: terminal mouse-selection (Ctrl+T) would otherwise copy the
           side │ chars along with the text. */
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
        # The v0.3 emotion renderer — the "logged" tier (IEmotionRenderer).
        self._renderer = LogRenderer()
        # The v0.5 emoji tier — its glyph goes next to Лілі's reply (built-in map;
        # the authored core/emoji.md is loaded in on_mount).
        self._emoji = EmojiRenderer()
        # v0.4 idle nudge state (configured in on_mount; off by default).
        self._nudge_enabled: bool = False
        self._idle_seconds: int = 240
        self._quiet_hours: tuple[int, int] | None = None
        self._nudges: list[str] = []
        self._nudge_idx: int = -1
        # `_last_activity` is reset ONLY by real input (keyboard / Telegram). The nudge and the
        # think each keep their OWN last-fired stamp so they pace independently and never reset
        # each other's idle clock (both run together).
        self._last_activity = self._core.clock()
        self._last_nudge_ts = self._core.clock()
        # v0.12 proactive thinking state (configured in on_mount; on with the thought-stream).
        self._think_seeds: list[str] = []  # `%`-directive seeds from think_seeds.md (the A menu)
        self._seed_idx: int = -1
        self._think_n: int = 0  # a varying rng_seed + the A/B alternation counter
        self._last_think_ts = self._core.clock()
        self._think_busy: bool = False  # avoid overlapping proactive thinks
        # v0.13 bridge state (configured in on_mount; off by default). The TUI reads inbox / writes outbox.
        self._bridge: bool = False
        self._voice: bool = False  # v0.14: the local voicer also consumes the outbox
        self._inbox_path: Path | None = None
        self._inbox_pos: Path | None = None
        self._outbox_path: Path | None = None
        self._inbox_busy: bool = False  # avoid overlapping inbox drains
        # When True the app releases the mouse so the terminal can select text.
        self._mouse_selection: bool = False
        # Connection state for the status line (False after a failed turn).
        self._connected: bool = True
        # True while a turn (or session save) is in flight — the input box is locked
        # (disabled) until it's your turn again. Toggled via _set_busy.
        self._busy: bool = False
        # v0.7.x send/receive sound — off by default, toggled with Ctrl+S (lazy init).
        self._sound = SoundPlayer()
        self._sound_on: bool = False

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
            prompt.border_subtitle = "Enter — send · Shift+Enter — newline · /style /mood /biorhythm /closeness /recall /new /prompt /memory /forget"
            yield prompt
        yield Footer()

    def on_mount(self) -> None:
        if self._session is None:
            self._session = self._core.start_session()
        self._render_status()
        self._render_stats()
        self.query_one("#prompt", ChatInput).focus()
        self.run_worker(self._refresh_world(), exclusive=False)  # ambient fetch (v0.4)
        # mood (v0.6) — render the bar after, so the startup mood call's tokens show up
        self.run_worker(self._render_after(asyncio.to_thread(self._core.ensure_mood)), exclusive=False)
        # v0.16 semantic recall: index the existing history once, off the UI thread, so /recall
        # works on a fresh store and the first search never blocks (no-op when recall is off).
        self.run_worker(asyncio.to_thread(self._core.ensure_backfill), exclusive=False)
        # v0.4 idle nudge: load config + openers, then poll on a coarse interval.
        cfg = load_config()
        self._emoji = EmojiRenderer(load_emoji_map(cfg.emoji_path))  # authored map (v0.5)
        self._nudge_enabled = cfg.idle_nudge
        self._idle_seconds = cfg.idle_seconds
        self._quiet_hours = cfg.quiet_hours
        self._sound_on = cfg.sound  # start on only if LUMI_SOUND=on; else toggle with F2
        now = self._core.clock()
        self._last_activity = self._last_nudge_ts = self._last_think_ts = now
        # Two separate menus, two independent timers — the nudge (openers) and the think (seeds)
        # run together and pace on their own stamps.
        if self._nudge_enabled:  # v0.4: random opener from nudges.md after idle_seconds
            self._nudges = load_nudges(cfg.nudge_path)
            self.set_interval(30, self._maybe_nudge)
        if cfg.thoughts:  # v0.12: random %think seed from think_seeds.md (A) or free musing (B)
            self._think_seeds = load_nudges(cfg.think_seeds_path)
            self.set_interval(30, self._maybe_think)
        # v0.13 bridge: the TUI consumes the inbox FIFO (Telegram → file) on a fast poll, and
        # mirrors Лілі's replies to the outbox FIFO (→ Telegram). Off unless LUMI_BRIDGE=on.
        self._bridge = cfg.bridge
        self._voice = cfg.voice  # v0.14: the local voicer also consumes the outbox
        # The outbox is written when EITHER the Telegram bridge or the local voicer wants it.
        if self._bridge or self._voice:
            self._outbox_path = cfg.outbox_path
        if self._bridge:  # the inbox poller is Telegram-only
            self._inbox_path = cfg.inbox_path
            self._inbox_pos = cfg.inbox_path.with_suffix(".pos")
            self.set_interval(1, self._poll_inbox)

    async def _refresh_world(self) -> None:
        """Fetch the ambient *now / here* snapshot off-thread and hand it to the core.

        Only when configured (a place / coords / news feed); otherwise no ambient
        block. Best-effort — never blocks the UI, never raises.
        """
        cfg = load_config()
        if not (cfg.location or (cfg.lat is not None and cfg.lon is not None) or cfg.news_url):
            return
        try:
            world = await asyncio.to_thread(
                fetch_world_context,
                self._core.clock,
                location=cfg.location,
                lat=cfg.lat,
                lon=cfg.lon,
                weather_url=cfg.weather_url,
                news_url=cfg.news_url,
                news_cap=cfg.news_cap,
            )
            self._core.set_world_context(world)
        except Exception:  # noqa: BLE001 — ambient context is best-effort
            pass

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
            self._set_busy(True)
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

    def _show_tool_trace(self) -> None:
        """Show the file tools Лілі used this turn — dim lines above her reply (v0.19; off unless
        `LUMI_FILE_TOOL_TRACE`). The full detail also streams to `.lumi/tool-log.jsonl`."""
        for name, args, result in getattr(self._core, "last_tool_calls", ()):
            arg_str = " ".join(f"{k}={v}" for k, v in args.items())
            preview = " ".join((result or "").split())[:90]
            line = f"🔧 {name}({arg_str}) → {preview}"
            self._emit(line, Text(line, style="grey50"))

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
        think = f" · thinking:{'on' if self._core.thinking else 'off'}"
        snd = f" · sound:{'on' if self._sound_on else 'off'}"
        style_part = f" · style: {self._core.style}"  # always show (incl. 'normal')
        emo = self._core.last_emotion
        emo_part = f" · {emo.emotion.value} {emo.intensity:.1f}" if emo else ""
        meta = f"{model}{think}{snd}{style_part}{emo_part}"
        if busy:
            return f"status: [yellow]{busy}[/] · {meta}"
        if not self._connected:
            return f"status: [red]{STATUS_OFFLINE}[/] · {model} · no connection"
        return f"status: [green]{STATUS_READY}[/] · {meta}"

    def _stats_text(self) -> str:
        """The statistics line — last response + running totals (total tokens only)."""
        stats = self._core.last_stats
        totals = self._core.totals
        if stats is None:  # show as soon as ANY model call ran (e.g. the startup mood), not just replies
            return "stats: —"
        last_tok = (stats.input_tokens or 0) + (stats.output_tokens or 0)
        total_tok = totals.total_tokens  # all tokens: fresh input + cache read/write + output
        last = f"last {self._fmt_tokens(last_tok)} tok · {self._fmt_latency(stats.latency_ms)}"
        if stats.cache_read_tokens:  # v0.15: prefix served from the prompt cache
            last += f" · cache {self._fmt_tokens(stats.cache_read_tokens)}↩"
        if stats.cache_write_tokens:  # the prefix was (re)written to the cache this turn
            last += f" · wrote {self._fmt_tokens(stats.cache_write_tokens)}↑"
        total = (
            f"total {totals.turns} turns · {self._fmt_tokens(total_tok)} tok"
            f" · avg {self._fmt_latency(totals.avg_latency_ms)}"
        )
        return f"stats: {last}   ·   {total}"

    def _render_status(self, busy: str | None = None) -> None:
        self.query_one("#status", Static).update(self._status_text(busy))

    def _render_stats(self) -> None:
        self.query_one("#stats", Static).update(self._stats_text())

    async def _render_after(self, coro) -> None:
        """Await a background model-consuming task, then refresh the stats bar — so the startup
        mood call (and other off-thread housekeeping) shows its real token cost, not just replies."""
        try:
            await coro
        finally:
            self._render_stats()

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        prompt = self.query_one("#prompt", ChatInput)

        # The input box is locked while Лілі replies (see _set_busy); this guard is a
        # safety net for any submit that slips through before the lock applies — it
        # keeps the draft (no clear, no send).
        if self._busy:
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
        if text == "/mood":
            self._show_mood()
            prompt.focus()
            return
        if text == "/biorhythm":
            self._show_biorhythm()
            prompt.focus()
            return
        if text == "/closeness":
            self._show_closeness()
            prompt.focus()
            return
        if text == "/thoughts":
            self._show_thoughts()
            prompt.focus()
            return
        if text == "/recall" or text.startswith("/recall "):
            self._recall_command(text)
            prompt.focus()
            return
        if text == "/theme" or text.startswith("/theme "):
            self._theme_command(text)
            prompt.focus()
            return
        if text == "/new":
            await self._new_session()
            prompt.focus()
            return

        # %directive (v0.12) — her mind acts, not a chat message. Unknown %name → falls through.
        if text.startswith("%") and self._session is not None:
            outcome = self._core.run_directive(text, self._session)
            if outcome.is_directive:
                if outcome.mode == "open" and outcome.thought is not None:
                    body = f"💭 {outcome.thought.text}"
                    self._emit(body, Markdown(body))  # silent → nothing shown
                self._last_activity = self._core.clock()
                prompt.focus()
                return

        self._last_activity = self._core.clock()  # real input resets the idle timer
        await self._run_turn(text, mirror_input=True)  # keyboard turn → also mirror your line to Telegram

    def action_toggle_sound(self) -> None:
        """Toggle the send/receive sound (Ctrl+S). Turning it on probes the audio device."""
        if not self._sound_on:
            if not self._sound.ensure():  # lazily init the mixer; False → no audio device
                self.notify("No audio device — sound unavailable.",
                            severity="warning", timeout=2)
                return
            self._sound_on = True
        else:
            self._sound_on = False
        self.notify(f"Sound {'on' if self._sound_on else 'off'}.", timeout=1)
        self._render_status()

    def _set_busy(self, busy: bool) -> None:
        """Toggle the working state and **lock the input box** while Лілі replies — the
        box is disabled until it's your turn, then re-enabled and refocused."""
        self._busy = busy
        prompt = self.query_one("#prompt", ChatInput)
        prompt.disabled = busy
        if not busy:
            prompt.focus()

    async def _run_turn(self, text: str, *, hidden: bool = False, mirror_input: bool = False) -> None:
        """Run one model turn. A ``hidden`` turn (the idle nudge) suppresses the user
        line entirely — only Лілі's reply is shown — so she appears to speak first.
        ``mirror_input`` (keyboard turns only) also sends your typed line to Telegram."""
        self._set_busy(True)
        if not hidden:
            self._say(USER_LABEL, text, USER_COLOR)
            if mirror_input and self._bridge:  # v0.13: your keyboard line → Telegram (not echoed inbox lines)
                mirror_user(self._outbox_path, text)
            if self._sound_on:
                self._sound.send()  # your message went out (never on a hidden nudge)
        self._render_status(busy=STATUS_BUSY)  # live tech status: working, not frozen
        try:
            assert self._session is not None
            state = await asyncio.to_thread(self._core.reply, text, self._session)
            self._connected = True
            self._last_reply = state.reply
            # Route the validated state through the renderer (logs the field) — the
            # status line then shows her current emotion + intensity.
            self._renderer.session_id = self._session.id
            self._renderer.turn = self._core.totals.turns
            self._renderer.render(state)
            compacted = getattr(self._core, "last_compaction", 0)
            if compacted:
                note = f"Compacted {compacted} earlier messages into a running summary."
                self._emit(note, Text(note, style=SYSTEM_COLOR))
            # Лілі's reasoning goes to the Thinking box only (not the chat);
            # the box shows just this turn's thinking, or clears if there was none.
            self._render_thinking(getattr(self._core, "last_thinking", None))
            self._show_tool_trace()  # v0.19: the file tools she used this turn (dim, above the reply)
            # Her emotion shows as an emoji next to her name (v0.5), e.g. "Лілі 😄✨:".
            self._say_markdown(f"{LILI_LABEL} {self._emoji.glyph(state)}", state.reply, LILI_COLOR)
            if self._bridge or self._voice:  # mirror ONLY Лілі's reply to the outbox (→ Telegram / voicer)
                mirror_reply(self._outbox_path, state)
            if not hidden and self._sound_on:
                self._sound.receive()  # her reply arrived (suppressed for the idle nudge)
        except Exception:  # noqa: BLE001 — never crash the loop on a model error
            self._render_thinking(None)  # the failed turn has no thinking
            self._connected = False
            self._emit(ERROR_LINE, Text(ERROR_LINE, style=f"bold {ERROR_COLOR}"))
        finally:
            self._set_busy(False)  # unlock + refocus the input — your turn again
            self._render_status()
            self._render_stats()

    def _maybe_nudge(self) -> None:
        """Idle-timer tick: after a long silence, run a hidden nudge so Лілі speaks first.

        Off by default; gated on enablement, not-busy, an open session, and quiet
        hours; rate-limited to one nudge per idle gap (resets the activity stamp).
        """
        if not self._nudge_enabled or self._busy or self._session is None or not self._nudges:
            return
        now = self._core.clock()
        # Due on the later of real-input idle AND the nudge's own last fire — never writes
        # _last_activity, so the think can't starve it (and vice-versa).
        if not proactive_due(self._last_activity, self._last_nudge_ts, now,
                             self._idle_seconds, self._quiet_hours):
            return
        self._last_nudge_ts = now
        self._nudge_idx = pick_nudge_index(len(self._nudges), self._nudge_idx)
        opener = self._nudges[self._nudge_idx]
        self.run_worker(self._run_turn(opener, hidden=True), exclusive=False)

    def _maybe_think(self) -> None:
        """Idle-timer tick (v0.12): fire a proactive ``%think`` from her own state (B), or — every
        other fire, when ``nudges.md`` has ``%``-seeds — about a seed from that menu (A). The core
        ``tick_think`` gates on the idle interval / per-session cap / quiet hours; most thoughts are
        **silent** (recorded only), a fraction **graduate to spoken** and she speaks first."""
        if self._busy or self._session is None or self._think_busy:
            return
        self._think_n += 1
        kind, topic = "think", None
        if self._think_seeds and self._think_n % 2 == 0:  # A: seed from the menu, alternating
            self._seed_idx = pick_nudge_index(len(self._think_seeds), self._seed_idx)
            parsed = parse_directive(self._think_seeds[self._seed_idx])
            if parsed is not None:
                kind, topic = parsed.name, parsed.topic
        self.run_worker(self._think_tick(kind, topic, self._think_n), exclusive=False)

    async def _think_tick(self, kind: str, topic: str | None, seed_n: int) -> None:
        """Run one proactive think off-thread; deliver it aloud only if it graduated to spoken."""
        self._think_busy = True
        try:
            now = self._core.clock()
            # Gate (inside tick_think) on the later of: real-input idle AND the think's own last
            # fire — so the think paces on its OWN interval and never touches _last_activity.
            anchor = max(self._last_activity, self._last_think_ts)
            thought = await asyncio.to_thread(
                self._core.tick_think, self._session, anchor, now,
                rng_seed=seed_n, kind=kind, topic=topic,
            )
            self._render_stats()  # a think consumes tokens too — reflect its cost in the bar
            if thought is None:
                return  # not due / capped / off / nothing made — silent ones end here
            self._last_think_ts = now  # rate-limit the think only (never resets the nudge's idle)
            if thought.spoken:  # she speaks first, grounded in the thought (the hidden self-turn)
                seed = (f"(ти щойно подумала: «{thought.text}» — напиши йому перша, "
                        "коротко поділись цим або почни з цього розмову)")
                await self._run_turn(seed, hidden=True)
        finally:
            self._think_busy = False

    def _poll_inbox(self) -> None:
        """Bridge tick (v0.13): drain the `inbox` FIFO (Telegram → file) on idle and run each line as a
        turn. Gated on the bridge being on, not-busy, an open session, and no overlapping drain."""
        if not self._bridge or self._busy or self._session is None or self._inbox_busy:
            return
        self.run_worker(self._drain_inbox(), exclusive=False)

    async def _drain_inbox(self) -> None:
        """Run each unread `inbox` line as a turn (shown tagged), advancing the pointer."""
        self._inbox_busy = True
        try:
            for text in drain_inbox(self._inbox_path, self._inbox_pos):
                self._say(TELEGRAM_LABEL, text, TELEGRAM_COLOR)  # show what arrived
                self._last_activity = self._core.clock()  # a Telegram line is real activity
                await self._run_turn(text, hidden=True)  # reply (the line was already shown)
        finally:
            self._inbox_busy = False

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

    def _show_mood(self) -> None:
        """Show Лілі's mood of the day — the `/mood` command (v0.6)."""
        resolution = self._core.mood
        body = f"**Настрій Лілі сьогодні:**\n\n{resolution}" if resolution else MOOD_PENDING
        self._emit(body, Markdown(body))

    def _theme_command(self, text: str) -> None:
        """Manually set / clear the face theme — `/theme <name>` and `/theme auto` (v0.11)."""
        arg = text[len("/theme"):].strip()
        available = ", ".join(self._core.themes) or "(жодної)"
        if not arg:
            current = self._core.theme or "(плоский v0.7)"
            body = (f"**Тема обличчя:** {current}\nДоступні: {available}\n"
                    "`/theme <назва>` — поставити, `/theme auto` — за настроєм дня.")
        elif arg.lower() == "auto":
            self._core.set_theme(None)
            body = "Тема обличчя: **авто** (за настроєм дня)."
        elif self._core.set_theme(arg):
            body = f"Тема обличчя: **{arg}**."
        else:
            body = f"Невідома тема «{arg}». Доступні: {available}"
        self._emit(body, Markdown(body))

    def _show_thoughts(self) -> None:
        """Show the recent dated thought-stream — the `/thoughts` command (v0.12)."""
        if self._core.thoughts_show == "off":
            body = "Перегляд думок вимкнено."
        else:
            view = self._core.thoughts_view()
            body = f"**Що в мене на думці:**\n\n{view}" if view else "Поки що жодних думок."
        self._emit(body, Markdown(body))

    def _recall_command(self, text: str) -> None:
        """Explicit semantic search — the `/recall <query>` command (v0.16; v0.17 context expansion).

        Renders each hit as a dated dialogue **snippet** (the matched line with its session
        neighbours, anchor + score marked), so search results read as moments, not orphan lines.
        Empty query / no results → a friendly note; off (LUMI_RECALL) → says so.
        """
        query = text[len("/recall"):].strip()
        if not self._core.recall_enabled:
            body = "Семантичний пошук вимкнено (увімкни `LUMI_RECALL=on`)."
            self._emit(body, Markdown(body))
            return
        if not query:
            body = "Що згадати? `/recall <запит>`"
            self._emit(body, Markdown(body))
            return
        moments = self._core.recall_moments(query)  # v0.17: dated snippets (hit + neighbours)
        if not moments:
            body = f"Нічого не згадалося про «{query}»."
        else:
            body = f"**Згадую про «{query}»:**\n\n" + "\n\n".join(moments)
        self._emit(body, Markdown(body))

    def _show_closeness(self) -> None:
        """Show the current relationship level by name — the `/closeness` command (v0.10).

        Only the level + its name; the raw value / dimension scores stay internal.
        """
        level, name = self._core.closeness_status()
        label = name or f"рівень {level}"
        body = f"**Близькість:** {label} (рівень {level} з 5)"
        self._emit(body, Markdown(body))

    def _show_biorhythm(self) -> None:
        """Show today's computed body rhythms — biorhythms + cycle — the `/biorhythm` command (v0.8)."""
        b = self._core.biorhythms
        c = self._core.cycle
        if not b and not c:
            self._emit(BIORHYTHM_OFF, Markdown(BIORHYTHM_OFF))
            return
        parts: list[str] = []
        if b:
            parts.append(f"**Біоритми Лілі сьогодні:**\n\n{format_biorhythms(b)}")
        if c:
            parts.append(f"**Цикл:** {format_cycle(c)}")
        body = "\n\n".join(parts)
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
            await self._refresh_world()  # re-snapshot ambient context for the new session
        finally:
            self._busy = False

    def _style_command(self, text: str) -> None:
        """Лілі picks her own style each turn. `/style` lists the palette + who chose;
        `/style <name>` *recommends* a style (a soft hint, she still decides);
        `/style auto` clears the recommendation.
        """
        arg = text[len("/style"):].strip()
        if not arg:
            metas = ", ".join(self._core.meta_names()) or "—"
            bases = ", ".join(self._core.base_names())
            rec = self._core.recommendation or "—"
            line = (
                f"Лілі обирає стиль сама (зараз: {self._core.style} · рекомендація: {rec}).\n"
                f"Мега-стилі: {metas}\nБазові: {bases}\n"
                "/style <назва> — порадити · /style auto — без поради"
            )
            self._emit(line, Text(line, style=SYSTEM_COLOR))
            return
        if self._core.set_style(arg):
            rec = self._core.recommendation
            line = (
                f"Рекомендація стилю → {rec}. Лілі врахує (вирішує сама)."
                if rec else "Стиль → авто. Лілі обирає сама."
            )
            self._emit(line, Text(line, style=f"bold {SYSTEM_COLOR}"))
            self._render_status()
        else:
            names = ", ".join(self._core.style_names())
            line = f"Unknown style in '{arg}'. Available: {names}"
            self._emit(line, Text(line, style=ERROR_COLOR))

    def _last_tokens_line(self) -> str | None:
        """The last turn's token usage for the `/prompt` dump — in/out + cache + latency, or None."""
        stats = self._core.last_stats
        if stats is None:
            return None
        bits = [
            f"in {self._fmt_tokens(stats.input_tokens or 0)}",
            f"out {self._fmt_tokens(stats.output_tokens or 0)}",
        ]
        if stats.cache_read_tokens:
            bits.append(f"cache {self._fmt_tokens(stats.cache_read_tokens)}↩")
        if stats.cache_write_tokens:
            bits.append(f"wrote {self._fmt_tokens(stats.cache_write_tokens)}↑")
        bits.append(self._fmt_latency(stats.latency_ms))
        return "[TOKENS] " + " · ".join(bits)

    def _show_prompt(self) -> None:
        """Show the exact prompt sent on the last turn (+ its token cost) — `/prompt`."""
        p = getattr(self._core, "last_prompt", None)
        if not p:
            msg = "No prompt yet — make a turn first."
            self._emit(msg, Text(msg, style=SYSTEM_COLOR))
            return
        system = mark_cache_breakpoint(p["system"], p.get("cache_prefix"))  # show the cache split
        head = ["── last turn's prompt ──"]
        if (tokens := self._last_tokens_line()) is not None:
            head.append(tokens)
        parts = [*head, "", "[SYSTEM]", system, "", "[MESSAGES]"]
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
