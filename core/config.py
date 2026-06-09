"""Configuration and secrets.

Config is explicit and switchable (ARCHITECTURE §Configuration and secrets):
the active model id, the canon path, and the memory window live here, never
hardcoded in the core. Secrets (``ANTHROPIC_API_KEY`` from v0.1) are read from
the environment via a gitignored ``.env`` — never committed, never in code.

v0.1 has one backend — Anthropic Claude Haiku. More models become switchable
in v0.9 behind the same ``LLMClient`` seam, so ``provider`` is reserved here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from core.memory import GIST_DAYS, MAX_DAY_ROWS, RECENT_SUMMARIES
from core.worldcontext import DEFAULT_WEATHER_URL

# Repo root = the parent of this ``core/`` package.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# A current Claude Haiku model id (the only model to start; more in v0.9).
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Default canon path (config-referenced — never hardcoded in the core).
DEFAULT_CANON_PATH = _REPO_ROOT / "core" / "canon" / "lili.md"

# Answer styles (overlays); editable like the canon. Optional.
DEFAULT_STYLES_PATH = _REPO_ROOT / "core" / "styles.md"

# Idle-nudge openers (v0.4); editable. Optional.
DEFAULT_NUDGE_PATH = _REPO_ROOT / "core" / "nudges.md"

# Emotion→emoji map (v0.5); editable. Optional (built-in default in core/emoji.py).
DEFAULT_EMOJI_PATH = _REPO_ROOT / "core" / "emoji.md"

# Лілі's natal snapshot (v0.6 mood); editable. Empty file → mood off.
DEFAULT_NATAL_PATH = _REPO_ROOT / "core" / "natal.md"

# Лілі's authored closeness levels (v0.10); editable like styles.
DEFAULT_CLOSENESS_PATH = _REPO_ROOT / "core" / "closeness.md"

# Local store file (gitignored runtime data, not source). user_id-keyed in v0.2.
DEFAULT_STORE_PATH = _REPO_ROOT / ".lumi" / "store.json"

# Rolling window: how many recent messages are kept verbatim in context. Older
# messages of the current session are folded into a running digest (compaction),
# in batches of DEFAULT_COMPACTION_BATCH — so the verbatim tail floats between
# memory_window and memory_window + batch.
DEFAULT_MEMORY_WINDOW = 40
DEFAULT_COMPACTION_BATCH = 20

# Model output cap. Extended thinking (Opus 4.8 / Sonnet 4.6) is adaptive and
# off by default; `effort` tunes its depth when on (None → the API default).
DEFAULT_MAX_TOKENS = 4096
DEFAULT_THINKING = False
DEFAULT_EFFORT: str | None = None

# Valid effort levels (Anthropic adaptive thinking). xhigh/max are Opus-tier.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
_TRUTHY = {"1", "true", "on", "yes", "y"}


def _parse_bool(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration.

    ``api_key`` is read from the environment, not stored in the repo. The core
    reads it from here; only ``AnthropicClient`` ever uses it (v0.1).
    """

    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    canon_path: Path = DEFAULT_CANON_PATH
    styles_path: Path = DEFAULT_STYLES_PATH
    store_path: Path = DEFAULT_STORE_PATH
    memory_window: int = DEFAULT_MEMORY_WINDOW
    compaction_batch: int = DEFAULT_COMPACTION_BATCH
    # Cross-session short-memory recall (v0.9): N detailed convos, D-day digest window, rows/day.
    recent_summaries: int = RECENT_SUMMARIES
    gist_days: int = GIST_DAYS
    max_day_rows: int = MAX_DAY_ROWS
    max_tokens: int = DEFAULT_MAX_TOKENS
    thinking: bool = DEFAULT_THINKING
    effort: str | None = DEFAULT_EFFORT
    # v0.4 ambient context — all off unless configured (graceful degradation).
    location: str | None = None
    lat: float | None = None
    lon: float | None = None
    weather_url: str = DEFAULT_WEATHER_URL
    news_url: str | None = None
    news_cap: int = 3
    # v0.4 idle nudge — off by default.
    idle_nudge: bool = False
    idle_seconds: int = 240
    nudge_path: Path = DEFAULT_NUDGE_PATH
    quiet_hours: tuple[int, int] | None = None
    # v0.7.x TUI send/receive sound — off by default; toggled at runtime (Ctrl+S).
    sound: bool = False
    emoji_path: Path = DEFAULT_EMOJI_PATH
    # v0.6 mood of the day — on by default.
    mood: bool = True
    natal_path: Path = DEFAULT_NATAL_PATH
    closeness_path: Path = DEFAULT_CLOSENESS_PATH
    # v0.8 biorhythms — computed cycles merged into the mood. On by default (with the mood).
    biorhythms: bool = True
    # v0.8 hormonal (menstrual) cycle — a phased body rhythm merged into the mood. On by default.
    cycle: bool = True
    # v0.7 emotion-face signal — a one-word file the viewer polls (None → derive from store).
    face_signal: Path | None = None
    # v0.7 viewer: relax the face to calm after this many seconds of an unchanged signal (0 = off).
    face_idle: float = 120.0
    api_key: str | None = field(default=None, repr=False)


def load_config(*, load_env: bool = True) -> Config:
    """Build a :class:`Config` from the environment (and ``.env``).

    Environment overrides:
      - ``LUMI_MODEL`` — the model id (default :data:`DEFAULT_MODEL`).
      - ``LUMI_PROVIDER`` — the provider (default ``anthropic``; more in v0.9).
      - ``LUMI_CANON_PATH`` — the active canon file.
      - ``LUMI_MEMORY_WINDOW`` — the rolling-window size (filled in v0.2).
      - ``ANTHROPIC_API_KEY`` — the Claude Haiku key (never committed).
    """
    if load_env:
        load_dotenv()

    canon_env = os.getenv("LUMI_CANON_PATH")
    canon_path = Path(canon_env) if canon_env else DEFAULT_CANON_PATH

    styles_env = os.getenv("LUMI_STYLES_PATH")
    styles_path = Path(styles_env) if styles_env else DEFAULT_STYLES_PATH

    store_env = os.getenv("LUMI_STORE_PATH")
    store_path = Path(store_env) if store_env else DEFAULT_STORE_PATH

    window_env = os.getenv("LUMI_MEMORY_WINDOW")
    memory_window = int(window_env) if window_env else DEFAULT_MEMORY_WINDOW

    batch_env = os.getenv("LUMI_COMPACTION_BATCH")
    compaction_batch = int(batch_env) if batch_env else DEFAULT_COMPACTION_BATCH

    recent_env = os.getenv("LUMI_RECENT_SUMMARIES")
    recent_summaries = int(recent_env) if recent_env else RECENT_SUMMARIES
    gist_days_env = os.getenv("LUMI_GIST_DAYS")
    gist_days = int(gist_days_env) if gist_days_env else GIST_DAYS
    max_day_rows_env = os.getenv("LUMI_MAX_DAY_ROWS")
    max_day_rows = int(max_day_rows_env) if max_day_rows_env else MAX_DAY_ROWS

    max_tokens_env = os.getenv("LUMI_MAX_TOKENS")
    max_tokens = int(max_tokens_env) if max_tokens_env else DEFAULT_MAX_TOKENS

    thinking = _parse_bool(os.getenv("LUMI_THINKING"))

    effort_env = os.getenv("LUMI_EFFORT")
    effort = effort_env.strip().lower() if effort_env and effort_env.strip() else DEFAULT_EFFORT

    def _float(name: str) -> float | None:
        raw = os.getenv(name)
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    news_cap_env = os.getenv("LUMI_NEWS_CAP")

    idle_seconds_env = os.getenv("LUMI_IDLE_SECONDS")
    nudge_path_env = os.getenv("LUMI_NUDGE_PATH")
    quiet_env = os.getenv("LUMI_QUIET_HOURS")  # e.g. "23-7"
    quiet_hours: tuple[int, int] | None = None
    if quiet_env and "-" in quiet_env:
        try:
            a, b = (int(x) for x in quiet_env.split("-", 1))
            quiet_hours = (a, b)
        except ValueError:
            quiet_hours = None

    return Config(
        provider=os.getenv("LUMI_PROVIDER", "anthropic"),
        model=os.getenv("LUMI_MODEL", DEFAULT_MODEL),
        canon_path=canon_path,
        styles_path=styles_path,
        store_path=store_path,
        memory_window=memory_window,
        compaction_batch=compaction_batch,
        recent_summaries=recent_summaries,
        gist_days=gist_days,
        max_day_rows=max_day_rows,
        max_tokens=max_tokens,
        thinking=thinking,
        effort=effort,
        location=os.getenv("LUMI_LOCATION") or None,
        lat=_float("LUMI_LAT"),
        lon=_float("LUMI_LON"),
        weather_url=os.getenv("LUMI_WEATHER_URL") or DEFAULT_WEATHER_URL,
        news_url=os.getenv("LUMI_NEWS_URL") or None,
        news_cap=int(news_cap_env) if news_cap_env else 3,
        idle_nudge=_parse_bool(os.getenv("LUMI_IDLE_NUDGE")),
        sound=_parse_bool(os.getenv("LUMI_SOUND")),
        idle_seconds=int(idle_seconds_env) if idle_seconds_env else 240,
        nudge_path=Path(nudge_path_env) if nudge_path_env else DEFAULT_NUDGE_PATH,
        quiet_hours=quiet_hours,
        emoji_path=Path(emoji_env) if (emoji_env := os.getenv("LUMI_EMOJI_PATH")) else DEFAULT_EMOJI_PATH,
        mood=(os.getenv("LUMI_MOOD") or "on").strip().lower() in _TRUTHY,  # on by default
        biorhythms=(os.getenv("LUMI_BIORHYTHMS") or "on").strip().lower() in _TRUTHY,  # on by default
        cycle=(os.getenv("LUMI_CYCLE") or "on").strip().lower() in _TRUTHY,  # on by default
        natal_path=Path(natal_env) if (natal_env := os.getenv("LUMI_NATAL_PATH")) else DEFAULT_NATAL_PATH,
        closeness_path=(
            Path(cl_env) if (cl_env := os.getenv("LUMI_CLOSENESS_PATH")) else DEFAULT_CLOSENESS_PATH
        ),
        face_signal=Path(face_env) if (face_env := os.getenv("LUMI_FACE_SIGNAL")) else None,
        face_idle=float(idle_env) if (idle_env := os.getenv("LUMI_FACE_IDLE_SECONDS")) else 120.0,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )
