"""Run the TUI: ``python -m tui`` (or ``uv run python -m tui``).

Wires the real core (Claude Haiku via the Anthropic backend) from config and
launches the Textual app. Needs ``ANTHROPIC_API_KEY`` in ``.env``.
"""

from __future__ import annotations

import logging

from core.agent import build_core
from core.config import load_config
from core.llm import LLMError
from tui.app import LumiApp
from tui.bridge import make_photo_sink


def _setup_logging(cfg) -> None:
    """Send ``lumi.*`` logs to ``.lumi/lumi.log`` so model-call failures (the cause behind the TUI's
    'unavailable' line) and other diagnostics are captured — Textual owns the screen, so without a file
    handler these records are lost. Idempotent; never raises (logging must not break startup)."""
    try:
        path = cfg.store_path.parent / "lumi.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger("lumi")
        root.setLevel(logging.INFO)
        if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root.addHandler(handler)
    except OSError:
        pass


def main() -> None:
    cfg = load_config()
    _setup_logging(cfg)
    # v0.24 send_image: supply the sink only when the Telegram bridge AND the image tool are on — the
    # TUI is the single outbox writer, so the core's send_image tool calls this closure instead of ever
    # touching the outbox. Bridge off → sink stays None (the tool reports "Telegram not connected").
    sink = make_photo_sink(cfg.outbox_path) if (cfg.bridge and cfg.image) else None
    try:
        core = build_core(config=cfg, telegram_sink=sink)
    except LLMError as exc:
        raise SystemExit(f"{exc}\nSet ANTHROPIC_API_KEY in .env (see .env.example).") from exc
    LumiApp(core).run()


if __name__ == "__main__":
    main()
