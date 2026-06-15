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

from core.closeness import ClosenessTuning
from core.embedder import DEFAULT_LOCAL_MODEL
from core.memory import (
    DAY_DAYS,
    MAX_DAY_ROWS,
    MAX_WEEK_ROWS,
    RECENT_SUMMARIES,
    SESSION_DAYS,
    WEEK_DAYS,
)
from core.thoughts import (
    THOUGHTS_CAP,
    THOUGHTS_INTERVAL_S,
    THOUGHTS_MAX_LINES,
    THOUGHTS_SPOKEN_RATIO,
    THOUGHTS_WINDOW_H,
)
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
# v0.12 proactive-think seeds (%think {topic}) — separate file from the v0.4 nudge openers.
DEFAULT_THINK_SEEDS_PATH = _REPO_ROOT / "core" / "think_seeds.md"

# Emotion→emoji map (v0.5); editable. Optional (built-in default in core/emoji.py).
DEFAULT_EMOJI_PATH = _REPO_ROOT / "core" / "emoji.md"

# Лілі's natal snapshot (v0.6 mood); editable. Empty file → mood off.
DEFAULT_NATAL_PATH = _REPO_ROOT / "core" / "natal.md"

# Лілі's authored closeness levels (v0.10); editable like styles.
DEFAULT_CLOSENESS_PATH = _REPO_ROOT / "core" / "closeness.md"

# Face image packs + theme manifest (v0.7 + v0.11 themes); the viewer renders from here.
DEFAULT_FACES_DIR = _REPO_ROOT / "viewer" / "faces"
DEFAULT_FILES_DIR = _REPO_ROOT / "state" / "files"  # v0.19 file-tool sandbox root (per-user subdirs)

# Local store file (gitignored runtime data, not source). user_id-keyed in v0.2.
DEFAULT_STORE_PATH = _REPO_ROOT / ".lumi" / "store.json"

# v0.13 bridge file bus (gitignored runtime data): the TUI reads inbox, writes outbox.
DEFAULT_INBOX_PATH = _REPO_ROOT / ".lumi" / "inbox.jsonl"
DEFAULT_OUTBOX_PATH = _REPO_ROOT / ".lumi" / "outbox.jsonl"

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


def _parse_quiet_hours(raw: str | None) -> tuple[int, int] | None:
    """Parse an ``"HH-HH"`` hour range (e.g. ``"23-7"``) → ``(start, end)``; anything else → None.

    So ``off`` / ``none`` / empty / malformed all mean "no quiet hours".
    """
    if not raw or "-" not in raw:
        return None
    try:
        a, b = (int(x) for x in raw.split("-", 1))
        return (a, b)
    except ValueError:
        return None


def _parse_probability(raw: str | None) -> float:
    """Parse a 0..1 probability. ``on``/``yes``/``true``/``y``/``1`` → 1.0; a number → clamped to
    [0, 1]; ``off`` / empty / junk → 0.0 — so the old ``on``/``off`` boolean still works."""
    if not raw:
        return 0.0
    s = raw.strip().lower()
    if s in _TRUTHY:
        return 1.0
    try:
        return max(0.0, min(1.0, float(s)))
    except ValueError:
        return 0.0


def _parse_id_list(raw: str | None) -> tuple[int, ...]:
    """Parse a comma-separated list of integer ids (e.g. the Telegram allowlist); ignore junk."""
    if not raw:
        return ()
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return tuple(out)


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
    # Short-memory recall: 3 date-based windows + the /memory quick-view count + row caps.
    recent_summaries: int = RECENT_SUMMARIES
    session_days: int = SESSION_DAYS
    day_days: int = DAY_DAYS
    week_days: int = WEEK_DAYS
    max_day_rows: int = MAX_DAY_ROWS
    max_week_rows: int = MAX_WEEK_ROWS
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
    think_seeds_path: Path = DEFAULT_THINK_SEEDS_PATH  # v0.12 proactive-think seed menu (%think …)
    quiet_hours: tuple[int, int] | None = None  # the v0.4 idle nudge's quiet window
    thoughts_quiet_hours: tuple[int, int] | None = None  # the v0.12 proactive-think's (independent)
    # v0.7.x TUI send/receive sound — off by default; toggled at runtime (Ctrl+S).
    sound: bool = False
    emoji_path: Path = DEFAULT_EMOJI_PATH
    # v0.6 mood of the day — on by default.
    mood: bool = True
    natal_path: Path = DEFAULT_NATAL_PATH
    closeness_path: Path = DEFAULT_CLOSENESS_PATH
    faces_dir: Path = DEFAULT_FACES_DIR  # v0.11 face packs + themes.md
    closeness: bool = True  # v0.10 relationship level on/off
    closeness_tuning: ClosenessTuning = field(default_factory=ClosenessTuning)
    facts_digest: bool = True       # consolidate long-term facts into a compact prompt digest
    facts_digest_max: int = 150     # target lines for the consolidated facts digest
    prompt_cache: bool = True       # v0.15: mark the stable prompt prefix as a cache breakpoint
    prompt_cache_ttl: str = "5m"    # cache lifetime: 5m (default) or 1h (keeps it warm across thinks)
    usage_report: bool = True       # write per-session token usage + cost report to .lumi/ on session close
    cache_monitor: bool = False     # log each model call's cache behaviour → .lumi/cache-report.md (per channel)
    # v0.19 local file tool (read-only half) — off by default; sandboxed, untrusted, bounded.
    file_tool: bool = False         # enable the file tools at all (LUMI_FILE_TOOL)
    files_dir: Path = DEFAULT_FILES_DIR  # sandbox root (per-user subdirs under it)
    file_read_lines: int = 200      # default / max lines returned by one read_file call
    file_read_max_total: int = 2000  # max total lines one turn may read across all read_file calls
    file_find_max: int = 50         # max matches find_in_file returns
    tool_max_steps: int = 8         # max tool calls per turn (the bounded tool-loop cap)
    file_tool_trace: bool = False   # show the file tools used each turn (TUI trace + .lumi/tool-log.jsonl)
    # v0.16 semantic recall (RAG) — off by default (the whole feature: index + /recall).
    recall: bool = False
    embed_provider: str = "local"   # local (private, default) | voyage | openai
    embed_model: str = DEFAULT_LOCAL_MODEL
    embed_api_key: str = field(default="", repr=False)  # cloud embedder key — secret, never logged
    recall_k: int = 5               # /recall (v0.16) top-K results
    embed_max_chars: int = 2000     # cap per message before embedding (local model ~512 tok; Voyage ~32k → raise it)
    # v0.17 automatic per-turn RAG — off by default (off → behaves like v0.16: index + /recall only).
    rag: bool = False
    rag_k: int = 4                  # top-K relevant past moments injected per turn
    rag_floor: float = 0.3          # cosine relevance floor — weaker matches aren't injected
    rag_max_chars: int = 1200       # the recall block's total char budget (spend recall tokens carefully)
    rag_w: int = 2                  # context-expansion window: ±W neighbour messages around each hit
    rag_snippet_chars: int = 240    # per-line cap for each recalled moment (longer → more context, more tokens)
    thoughts: bool = True  # v0.12 thought-stream on/off
    thoughts_window_h: int = THOUGHTS_WINDOW_H  # v0.12 prompt feedback window (hours)
    thoughts_max_lines: int = THOUGHTS_MAX_LINES  # v0.12 max thought lines injected into the prompt
    thoughts_interval_s: int = THOUGHTS_INTERVAL_S  # v0.12 idle before a proactive %think
    thoughts_cap: int = THOUGHTS_CAP  # v0.12 proactive thinks per session
    thoughts_spoken_ratio: float = THOUGHTS_SPOKEN_RATIO  # v0.12 fraction that graduate to spoken
    thoughts_show: str = "hidden"  # v0.12 /thoughts policy: hidden / admin / off
    thoughts_context: str = "lean"  # v0.12 thought prompt: lean (seeds) / full (whole backdrop)
    # v0.13 bridge: the TUI reads inbox / writes outbox (the file bus to the Telegram daemons). Off by default.
    bridge: bool = False
    inbox_path: Path = DEFAULT_INBOX_PATH
    outbox_path: Path = DEFAULT_OUTBOX_PATH
    # v0.13 Telegram daemons (separate processes; the token is a secret — never logged/committed).
    telegram_token: str = ""
    telegram_allowlist: tuple[int, ...] = ()  # the owner's id(s); only these are served
    telegram_flush_s: int = 2  # daemon 1: inbound buffer flush cadence
    telegram_batch: int = 5  # daemon 2: max records consolidated per Telegram message (N)
    telegram_catchup_h: int = 24  # daemon 2: skip outbox records older than this on restart
    telegram_photo: float = 0.0  # daemon 2: probability 0..1 of sending the face photo (0=never, 1=always)
    telegram_voice: bool = False  # daemon 2: send replies as VOICE messages (needs the LUMI_VOICE_* key/id)
    # v0.14 local voice (the voicer reads the outbox; the key is a secret — never logged/committed).
    voice: bool = False  # the TUI writes the outbox for the voicer (like bridge); off by default
    elevenlabs_api_key: str = ""
    voice_id: str = ""  # the ElevenLabs voice to speak in
    voice_model: str = "eleven_multilingual_v2"  # ElevenLabs model (multilingual for Ukrainian)
    # On EVERY start, skip the replies missed while the voicer was off (speak only new ones).
    # Off (default) = resume — voice the backlog that piled up while it was stopped.
    voice_skip_missed: bool = False
    # v0.8 biorhythms — computed cycles merged into the mood. On by default (with the mood).
    biorhythms: bool = True
    # v0.8 hormonal (menstrual) cycle — a phased body rhythm merged into the mood. On by default.
    cycle: bool = True
    # v0.7 emotion-face signal — a one-word file the viewer polls (None → derive from store).
    face_signal: Path | None = None
    # v0.7 viewer: relax the face to calm after this many seconds of an unchanged signal (0 = off).
    face_idle: float = 120.0
    api_key: str | None = field(default=None, repr=False)
    # v0.18 more models — other providers behind the same LLMClient seam, selected by `provider`.
    # Only the ACTIVE provider's key is required; secrets are never logged.
    openai_api_key: str = field(default="", repr=False)
    deepseek_api_key: str = field(default="", repr=False)
    minimax_api_key: str = field(default="", repr=False)
    llm_base_url: str = ""  # override base_url for an OpenAI-compatible / local server (Ollama, LM Studio)


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
    session_days = int(sd) if (sd := os.getenv("LUMI_SESSION_DAYS")) else SESSION_DAYS
    day_days = int(dd) if (dd := os.getenv("LUMI_DAY_DAYS")) else DAY_DAYS
    week_days = int(wd) if (wd := os.getenv("LUMI_WEEK_DAYS")) else WEEK_DAYS
    max_day_rows = int(mdr) if (mdr := os.getenv("LUMI_MAX_DAY_ROWS")) else MAX_DAY_ROWS
    max_week_rows = int(mwr) if (mwr := os.getenv("LUMI_MAX_WEEK_ROWS")) else MAX_WEEK_ROWS

    # v0.10 closeness engine knobs (defaults from ClosenessTuning; each overridable via .env).
    _ct = ClosenessTuning()
    # The daily mood-shift strength is a 0..1 scale (on/off accepted): unset → full (1.0); set
    # parses via _parse_probability so "off"/0 disables it. (Unlike the floats above, 0.0 here is
    # a meaningful "off", so we can't use `… or default`.)
    _ms = os.getenv("LUMI_CLOSENESS_MOOD_SHIFT")
    closeness_tuning = ClosenessTuning(
        baseline=float(os.getenv("LUMI_CLOSENESS_BASELINE") or _ct.baseline),
        decay_retained=float(os.getenv("LUMI_CLOSENESS_DECAY") or _ct.decay_retained),
        w_pos=float(os.getenv("LUMI_CLOSENESS_W_POS") or _ct.w_pos),
        w_neg=float(os.getenv("LUMI_CLOSENESS_W_NEG") or _ct.w_neg),
        delta_scale=float(os.getenv("LUMI_CLOSENESS_DELTA") or _ct.delta_scale),
        inertia=float(os.getenv("LUMI_CLOSENESS_INERTIA") or _ct.inertia),
        drift_rate=float(os.getenv("LUMI_CLOSENESS_DRIFT") or _ct.drift_rate),
        mood_shift_scale=_parse_probability(_ms) if _ms is not None else _ct.mood_shift_scale,
    )

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
    # Quiet hours: the nudge's (LUMI_QUIET_HOURS) and the proactive-think's
    # (LUMI_THOUGHTS_QUIET_HOURS) are independent. The think inherits the nudge's when its own
    # var is UNSET; set it to "off" (or any non-range) to give the think no quiet hours at all.
    quiet_hours = _parse_quiet_hours(os.getenv("LUMI_QUIET_HOURS"))  # e.g. "23-7"
    thoughts_quiet_env = os.getenv("LUMI_THOUGHTS_QUIET_HOURS")
    thoughts_quiet_hours = quiet_hours if thoughts_quiet_env is None else _parse_quiet_hours(thoughts_quiet_env)

    # v0.16 semantic recall: the embedder provider + its (cloud-only) key. local → no key.
    embed_provider = (os.getenv("LUMI_EMBED_PROVIDER") or "local").strip().lower()
    embed_key = {
        "voyage": os.getenv("VOYAGE_API_KEY"),
        "openai": os.getenv("OPENAI_API_KEY"),
    }.get(embed_provider) or ""
    # The model defaults per provider (a Voyage/OpenAI model name, not the local e5 one) unless set.
    _embed_default_model = {"voyage": "voyage-3", "openai": "text-embedding-3-small"}.get(
        embed_provider, DEFAULT_LOCAL_MODEL
    )
    embed_model = (os.getenv("LUMI_EMBED_MODEL") or _embed_default_model).strip()

    return Config(
        provider=os.getenv("LUMI_PROVIDER", "anthropic"),
        model=os.getenv("LUMI_MODEL", DEFAULT_MODEL),
        canon_path=canon_path,
        styles_path=styles_path,
        store_path=store_path,
        memory_window=memory_window,
        compaction_batch=compaction_batch,
        recent_summaries=recent_summaries,
        session_days=session_days,
        day_days=day_days,
        week_days=week_days,
        max_day_rows=max_day_rows,
        max_week_rows=max_week_rows,
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
        think_seeds_path=Path(ts) if (ts := os.getenv("LUMI_THINK_SEEDS_PATH")) else DEFAULT_THINK_SEEDS_PATH,
        quiet_hours=quiet_hours,
        thoughts_quiet_hours=thoughts_quiet_hours,
        emoji_path=Path(emoji_env) if (emoji_env := os.getenv("LUMI_EMOJI_PATH")) else DEFAULT_EMOJI_PATH,
        mood=(os.getenv("LUMI_MOOD") or "on").strip().lower() in _TRUTHY,  # on by default
        biorhythms=(os.getenv("LUMI_BIORHYTHMS") or "on").strip().lower() in _TRUTHY,  # on by default
        cycle=(os.getenv("LUMI_CYCLE") or "on").strip().lower() in _TRUTHY,  # on by default
        natal_path=Path(natal_env) if (natal_env := os.getenv("LUMI_NATAL_PATH")) else DEFAULT_NATAL_PATH,
        closeness_path=(
            Path(cl_env) if (cl_env := os.getenv("LUMI_CLOSENESS_PATH")) else DEFAULT_CLOSENESS_PATH
        ),
        faces_dir=Path(fd) if (fd := os.getenv("LUMI_FACES_DIR")) else DEFAULT_FACES_DIR,
        closeness=(os.getenv("LUMI_CLOSENESS") or "on").strip().lower() in _TRUTHY,  # on by default
        facts_digest=(os.getenv("LUMI_FACTS_DIGEST") or "on").strip().lower() in _TRUTHY,  # on by default
        prompt_cache=(os.getenv("LUMI_PROMPT_CACHE") or "on").strip().lower() in _TRUTHY,  # v0.15, on by default
        prompt_cache_ttl="1h" if (os.getenv("LUMI_PROMPT_CACHE_TTL") or "5m").strip().lower() == "1h" else "5m",
        usage_report=(os.getenv("LUMI_USAGE_REPORT") or "on").strip().lower() in _TRUTHY,  # on by default
        cache_monitor=(os.getenv("LUMI_CACHE_MONITOR") or "off").strip().lower() in _TRUTHY,  # off by default
        file_tool=(os.getenv("LUMI_FILE_TOOL") or "off").strip().lower() in _TRUTHY,  # off by default
        files_dir=Path(os.getenv("LUMI_FILES_DIR")) if os.getenv("LUMI_FILES_DIR") else DEFAULT_FILES_DIR,
        file_read_lines=int(os.getenv("LUMI_FILE_READ_LINES") or 200),
        file_read_max_total=int(os.getenv("LUMI_FILE_READ_MAX_TOTAL") or 2000),
        file_find_max=int(os.getenv("LUMI_FILE_FIND_MAX") or 50),
        tool_max_steps=int(os.getenv("LUMI_TOOL_MAX_STEPS") or 8),
        file_tool_trace=(os.getenv("LUMI_FILE_TOOL_TRACE") or "off").strip().lower() in _TRUTHY,  # off by default
        recall=(os.getenv("LUMI_RECALL") or "off").strip().lower() in _TRUTHY,  # v0.16, off by default
        embed_provider=embed_provider,
        embed_model=embed_model,
        embed_api_key=embed_key,
        recall_k=int(os.getenv("LUMI_RECALL_K") or 5),
        embed_max_chars=int(os.getenv("LUMI_EMBED_MAX_CHARS") or 2000),
        rag=(os.getenv("LUMI_RAG") or "off").strip().lower() in _TRUTHY,  # v0.17, off by default
        rag_k=int(os.getenv("LUMI_RAG_K") or 4),
        rag_floor=float(os.getenv("LUMI_RAG_FLOOR") or 0.3),
        rag_max_chars=int(os.getenv("LUMI_RAG_MAX_CHARS") or 1200),
        rag_w=int(os.getenv("LUMI_RAG_W") or 2),
        rag_snippet_chars=int(os.getenv("LUMI_RAG_SNIPPET_CHARS") or 240),
        facts_digest_max=int(os.getenv("LUMI_FACTS_DIGEST_MAX") or 150),
        thoughts=(os.getenv("LUMI_THOUGHTS") or "on").strip().lower() in _TRUTHY,  # v0.12, on by default
        thoughts_window_h=int(os.getenv("LUMI_THOUGHTS_WINDOW_H") or THOUGHTS_WINDOW_H),
        thoughts_max_lines=int(os.getenv("LUMI_THOUGHTS_MAX_LINES") or THOUGHTS_MAX_LINES),
        thoughts_interval_s=int(os.getenv("LUMI_THOUGHTS_INTERVAL_S") or THOUGHTS_INTERVAL_S),
        thoughts_cap=int(os.getenv("LUMI_THOUGHTS_CAP") or THOUGHTS_CAP),
        thoughts_spoken_ratio=float(os.getenv("LUMI_THOUGHTS_SPOKEN_RATIO") or THOUGHTS_SPOKEN_RATIO),
        thoughts_show=(os.getenv("LUMI_THOUGHTS_SHOW") or "hidden").strip().lower(),
        thoughts_context=(os.getenv("LUMI_THOUGHTS_CONTEXT") or "lean").strip().lower(),
        bridge=(os.getenv("LUMI_BRIDGE") or "off").strip().lower() in _TRUTHY,  # v0.13, off by default
        inbox_path=Path(ib) if (ib := os.getenv("LUMI_INBOX_PATH")) else DEFAULT_INBOX_PATH,
        outbox_path=Path(ob) if (ob := os.getenv("LUMI_OUTBOX_PATH")) else DEFAULT_OUTBOX_PATH,
        telegram_token=(os.getenv("LUMI_TELEGRAM_TOKEN") or "").strip(),
        telegram_allowlist=_parse_id_list(os.getenv("LUMI_TELEGRAM_ALLOWLIST")),
        telegram_flush_s=int(os.getenv("LUMI_TELEGRAM_FLUSH_S") or 2),
        telegram_batch=int(os.getenv("LUMI_TELEGRAM_BATCH") or 5),
        telegram_catchup_h=int(os.getenv("LUMI_TELEGRAM_CATCHUP_H") or 24),
        telegram_photo=_parse_probability(os.getenv("LUMI_TELEGRAM_PHOTO")),
        telegram_voice=(os.getenv("LUMI_TELEGRAM_VOICE") or "off").strip().lower() in _TRUTHY,
        voice=(os.getenv("LUMI_VOICE") or "off").strip().lower() in _TRUTHY,
        elevenlabs_api_key=(os.getenv("ELEVENLABS_API_KEY") or "").strip(),
        voice_id=(os.getenv("LUMI_VOICE_ID") or "").strip(),
        voice_model=(os.getenv("LUMI_VOICE_MODEL") or "eleven_multilingual_v2").strip(),
        voice_skip_missed=(os.getenv("LUMI_VOICE_SKIP_MISSED") or "off").strip().lower() in _TRUTHY,
        closeness_tuning=closeness_tuning,
        face_signal=Path(face_env) if (face_env := os.getenv("LUMI_FACE_SIGNAL")) else None,
        face_idle=float(idle_env) if (idle_env := os.getenv("LUMI_FACE_IDLE_SECONDS")) else 120.0,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip(),
        deepseek_api_key=(os.getenv("DEEPSEEK_API_KEY") or "").strip(),
        minimax_api_key=(os.getenv("MINIMAX_API_KEY") or "").strip(),
        llm_base_url=(os.getenv("LUMI_LLM_BASE_URL") or "").strip(),
    )
