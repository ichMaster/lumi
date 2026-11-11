"""The v2.9 settings schema and loader — every setting of an instance, one typed table, YAML files.

An instance's settings live in two files in its **settings folder**: ``settings.yaml`` (every non-secret
setting, nested by group) and ``secrets.yaml`` (``0600`` — the API keys and tokens). :data:`SETTINGS` is the one
schema the loader, the example files, the migration (``scripts/env_to_settings.py``) and ``lumi-cli config``
read: each :class:`Setting` names its YAML key, its environment alias (``LUMI_*`` / ``*_API_KEY``), its type,
default, description, the ``secret`` / ``restart`` flags and the allowed values.

**Precedence, weakest → strongest:** the built-in default < ``.env`` (deprecated since v2.9, warned) <
``settings.yaml`` / ``secrets.yaml`` < the process environment. A ``LUMI_*`` variable still wins (tests, compose's
``environment:``, an emergency hatch).

:func:`resolve` is the pure merge (plain data in, a :class:`Resolution` out). ``core.config.load_config`` reads its
``values`` (the env-string form every existing parser already understands), so the parsing and the defaults in
``load_config`` stay exactly as they were. :func:`export` copies the file values into ``os.environ`` without ever
overriding a real variable — what ``load_dotenv()`` did before, so the SDKs, the probes and any direct reader keep
working.

The settings folder: ``LUMI_SETTINGS_DIR`` when set (the containers mount the two files there), else the repo root
(the dev tree), else the repo's parent directory (the v2.6 units' ``~/lumi/<instance>/app`` layout) — the first that
holds ``settings.yaml``, ``secrets.yaml`` or ``.env``.
"""

from __future__ import annotations

import math
import os
import re
import stat
import sys
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.closeness import ClosenessTuning
from core.memory import (
    DAY_DAYS,
    MAX_DAY_ROWS,
    MAX_WEEK_ROWS,
    RECENT_SUMMARIES,
    SESSION_DAYS,
    SESSION_FORMAT_DEFAULT,
    SESSION_FORMATS,
    WEEK_DAYS,
)
from core.thoughts import (
    THOUGHT_MAX_CHARS,
    THOUGHTS_CAP,
    THOUGHTS_INTERVAL_S,
    THOUGHTS_MAX_LINES,
    THOUGHTS_SPOKEN_RATIO,
    THOUGHTS_WINDOW_H,
)
from core.worldcontext import DEFAULT_WEATHER_URL

_REPO_ROOT = Path(__file__).resolve().parent.parent

SETTINGS_FILE = "settings.yaml"
SECRETS_FILE = "secrets.yaml"
DOTENV_FILE = ".env"
SETTINGS_DIR_ENV = "LUMI_SETTINGS_DIR"
# Structured sections of settings.yaml (not flat settings): an instance's overrides of the authored files.
STRUCTURED = ("models", "schedule")

TYPES = ("bool", "int", "float", "str", "path", "enum", "list", "ids", "duration", "hours", "probability")

_TRUE_WORDS = {"1", "true", "on", "yes", "y"}
_FALSE_WORDS = {"0", "false", "off", "no", "n"}


@dataclass(frozen=True)
class Setting:
    """One setting: its YAML ``key`` (``group.name``), its environment ``alias``, type, default and rules."""

    alias: str
    key: str
    type: str
    default: Any
    description: str
    secret: bool = False
    restart: bool = True  # needs a restart to apply (every setting, until a later phase proves one live)
    choices: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    old: tuple[str, ...] = ()  # deprecated environment names still honoured (e.g. GOOGLE_API_KEY)

    @property
    def group(self) -> str:
        return self.key.split(".", 1)[0]


def _s(alias: str, key: str, type_: str, default: Any, description: str, **kw: Any) -> Setting:
    return Setting(alias, key, type_, default, description, **kw)


def _secret(alias: str, key: str, description: str, **kw: Any) -> Setting:
    return Setting(alias, key, "str", None, description, secret=True, **kw)


_CT = ClosenessTuning()
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_PROVIDERS = ("anthropic", "openai", "deepseek", "minimax", "local", "gemini")

# The schema, in group order — the order the example files are written in.
SETTINGS: tuple[Setting, ...] = (
    # --- instance -------------------------------------------------------------------------------------------
    _s("LUMI_ENV", "instance.env", "str", None,
       "The instance name (prod / dev): the status badge, the env-guard marker. Unset → no badge."),
    _s("LUMI_HOME", "instance.home", "path", None,
       "The data root — where all of her mutable state lives. Unset → the in-repo .lumi."),
    _s("LUMI_LOG_LEVEL", "instance.log_level", "enum", "INFO", "The Telegram daemons' log level.",
       choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")),
    _s("LUMI_ALLOW_DIRTY_PROD", "instance.allow_dirty_prod", "bool", False,
       "Emergency hatch for prod only: bypasses the clean/tagged check (never the marker checks); warned on every start."),
    # --- server ---------------------------------------------------------------------------------------------
    _s("LUMI_SERVER_HOST", "server.host", "str", "127.0.0.1",
       "The server's bind address (loopback, a LAN or Tailscale IP); on a client, the server it dials."),
    _s("LUMI_SERVER_PORT", "server.port", "int", 8765, "One port per instance (prod 8765, dev 8766).",
       min=1, max=65535),
    _secret("LUMI_SERVER_TOKEN", "server.token", "The client token — required to serve; never logged. Make one: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"."),
    _s("LUMI_SERVER", "server.client_mode", "bool", False,
       "The TUI runs as a client of that server (no core in-process)."),
    # --- model ----------------------------------------------------------------------------------------------
    _s("LUMI_PROVIDER", "model.provider", "enum", "anthropic", "The model provider behind the LLMClient seam.",
       choices=_PROVIDERS),
    _s("LUMI_MODEL", "model.reply", "str", "claude-haiku-4-5-20251001", "The reply model id."),
    _s("LUMI_MODEL_PROFILE", "model.profile", "str", None,
       "Boot the whole model stack from a named profile (core/models.yaml). Unset → the per-field settings."),
    _s("LUMI_MODEL_THINK", "model.think", "str", None, "The think-path model. Unset → the reply model."),
    _s("LUMI_MODEL_MOOD", "model.mood", "str", None, "The daily mood call's model. Unset → the reply model."),
    _s("LUMI_MODEL_HOUSEKEEPING", "model.housekeeping", "str", None,
       "Session start/close + compaction model. Unset → the reply model."),
    _s("LUMI_MODEL_VOICE", "model.voice", "str", None,
       "The voice-mode talking register. Unset → the profile's voice, then think tier."),
    _s("LUMI_TOOL_STEP_ROUTING", "model.tool_step_routing", "bool", False,
       "Route tool-loop continuation rounds to the step tier (Anthropic only)."),
    _s("LUMI_MODEL_TOOL_STEP", "model.tool_step", "str", None, "The tool-loop digging tier (a Claude id)."),
    _s("LUMI_MODEL_ALIASES", "model.alias_overrides", "str", None,
       "Extra /model aliases: alias=provider:model,… (over core/models.yaml and models:)."),
    _s("LUMI_MODEL_PROFILES", "model.profile_overrides", "str", None,
       "Extra profiles: name=provider:reply,think,mood,housekeeping[,voice];… (over core/models.yaml and models:)."),
    _s("LUMI_MODELS_FILE", "model.models_file", "path", None, "The model-profiles file. Unset → core/models.yaml."),
    _s("LUMI_MAX_TOKENS", "model.max_tokens", "int", 4096, "The model output cap.", min=1),
    _s("LUMI_THINKING", "model.thinking", "bool", False, "Surface the native thought summary (extended thinking)."),
    _s("LUMI_EFFORT", "model.effort", "enum", None, "Adaptive-thinking depth. Unset → the API default.",
       choices=_EFFORTS),
    _s("LUMI_REASONING", "model.reasoning", "bool", True,
       "Pre-answer reasoning; off drops the directive (and Gemini's thinking budget)."),
    _s("LUMI_STREAM", "model.stream", "bool", False, "Stream the reply's prose as it generates."),
    _s("LUMI_LLM_BASE_URL", "model.base_url", "str", None,
       "base_url for an OpenAI-compatible / local server (Ollama, LM Studio)."),
    _s("LUMI_OPENAI_RESPONSES", "model.openai_responses", "enum", "auto",
       "Use the OpenAI Responses API for reasoning models.", choices=("auto", "on", "off")),
    _s("LUMI_OPENAI_SUMMARY", "model.openai_summary", "enum", "auto",
       "The OpenAI reasoning-summary granularity.", choices=("auto", "concise", "detailed", "off")),
    _s("LUMI_PROMPT_CACHE", "model.prompt_cache", "bool", True, "Mark the stable prompt prefix as a cache breakpoint."),
    _s("LUMI_PROMPT_CACHE_TTL", "model.prompt_cache_ttl", "enum", "5m", "The prompt-cache lifetime.",
       choices=("5m", "1h")),
    _s("LUMI_GEMINI_EXPLICIT_CACHE", "model.gemini_explicit_cache", "bool", False,
       "Explicit cachedContents on Gemini."),
    _s("LUMI_GEMINI_CACHE_TTL", "model.gemini_cache_ttl", "duration", None,
       "Gemini explicit-cache lifetime (e.g. 5h). Unset → the prompt-cache TTL."),
    # --- providers (secrets) --------------------------------------------------------------------------------
    _secret("ANTHROPIC_API_KEY", "providers.anthropic_api_key", "The Anthropic (Claude) key — console.anthropic.com."),
    _secret("OPENAI_API_KEY", "providers.openai_api_key", "The OpenAI key (also the OpenAI embedder) — platform.openai.com."),
    _secret("DEEPSEEK_API_KEY", "providers.deepseek_api_key", "The DeepSeek key — platform.deepseek.com."),
    _secret("MINIMAX_API_KEY", "providers.minimax_api_key", "The MiniMax key — the MiniMax console."),
    _secret("GEMINI_API_KEY", "providers.gemini_api_key",
            "The Google Gemini key — the engine, image generation and web lookup — aistudio.google.com.", old=("GOOGLE_API_KEY",)),
    _secret("VOYAGE_API_KEY", "providers.voyage_api_key", "The Voyage key (the cloud embedder) — dash.voyageai.com."),
    # --- persona ---------------------------------------------------------------------------------------------
    _s("LUMI_CANON_PATH", "persona.canon_path", "path", None, "Her canon file. Unset → core/canon/lili.md."),
    _s("LUMI_STYLES_PATH", "persona.styles_path", "path", None, "The answer-style palette. Unset → core/styles.md."),
    _s("LUMI_INNER_VOICE", "persona.inner_voice", "bool", False,
       "Load core/inner_voice.md as the think-phase instruction."),
    _s("LUMI_INNER_VOICE_FILE", "persona.inner_voice_file", "path", None,
       "The inner-voice file. Unset → core/inner_voice.md."),
    _s("LUMI_THINK_SHOW", "persona.think_show", "enum", "debug", "How the think monologue is surfaced.",
       choices=("debug", "open", "off")),
    _s("LUMI_INTENT", "persona.intent", "bool", False, "Persist and replay the arbiter's chosen move/style."),
    _s("LUMI_EMOJI_PATH", "persona.emoji_path", "path", None, "The emotion → emoji map. Unset → core/emoji.md."),
    # --- store ----------------------------------------------------------------------------------------------
    _s("LUMI_STORE_PATH", "store.path", "path", None, "The store file. Unset → <data root>/store.json."),
    _s("LUMI_STORE_BACKEND", "store.backend", "enum", "json", "The storage backend behind the Repository.",
       choices=("json", "sqlite")),
    _s("LUMI_ASYNC_POST", "store.async_post", "bool", False,
       "Queue the post-turn persist on a background worker."),
    _s("LUMI_USAGE_REPORT", "store.usage_report", "bool", True,
       "Write the per-session token usage + cost report on session close."),
    _s("LUMI_CACHE_MONITOR", "store.cache_monitor", "bool", False,
       "Log each model call's cache behaviour to cache-report.md."),
    # --- memory ---------------------------------------------------------------------------------------------
    _s("LUMI_MEMORY_WINDOW", "memory.window", "int", 40, "Recent messages kept verbatim in context.", min=1),
    _s("LUMI_COMPACTION_BATCH", "memory.compaction_batch", "int", 20,
       "Older messages folded into the running digest per batch.", min=1),
    _s("LUMI_RECENT_SUMMARIES", "memory.recent_summaries", "int", RECENT_SUMMARIES,
       "The /memory quick-view count.", min=0),
    _s("LUMI_SESSION_DAYS", "memory.session_days", "int", SESSION_DAYS, "Days of session summaries recalled.", min=0),
    _s("LUMI_SESSION_DETAIL_N", "memory.session_detail_n", "int", None,
       "How many recent sessions to add (unset = all, 0 = none).", min=0),
    _s("LUMI_SESSION_FORMAT", "memory.session_format", "enum", SESSION_FORMAT_DEFAULT,
       "Per added session: the full summary or a one-line gist.", choices=tuple(SESSION_FORMATS)),
    _s("LUMI_DAY_DAYS", "memory.day_days", "int", DAY_DAYS, "Days of day digests recalled.", min=0),
    _s("LUMI_WEEK_DAYS", "memory.week_days", "int", WEEK_DAYS, "Days of week digests recalled.", min=0),
    _s("LUMI_MAX_DAY_ROWS", "memory.max_day_rows", "int", MAX_DAY_ROWS, "Row cap for the day digests.", min=0),
    _s("LUMI_MAX_WEEK_ROWS", "memory.max_week_rows", "int", MAX_WEEK_ROWS, "Row cap for the week digests.", min=0),
    _s("LUMI_MEMORY_INDEX", "memory.index", "bool", False, "Day/week digests as a one-line dated index."),
    _s("LUMI_FACTS", "memory.facts", "bool", True, "Inject the identity-core facts section."),
    _s("LUMI_FACTS_CORE_MAX", "memory.facts_core_max", "int", 0, "Identity-core injection cap (0 = all core facts).",
       min=0),
    # --- recall (semantic recall / RAG) ----------------------------------------------------------------------
    _s("LUMI_RECALL", "recall.enabled", "bool", False, "Semantic recall: index every message + /recall."),
    _s("LUMI_EMBED_PROVIDER", "recall.embed_provider", "enum", "local", "The embedder.",
       choices=("local", "voyage", "openai")),
    _s("LUMI_EMBED_MODEL", "recall.embed_model", "str", None, "The embedding model. Unset → the provider's default."),
    _s("LUMI_RECALL_K", "recall.k", "int", 5, "/recall top-K results.", min=1),
    _s("LUMI_EMBED_MAX_CHARS", "recall.embed_max_chars", "int", 2000, "Cap per message before embedding.", min=1),
    _s("LUMI_RAG", "recall.rag", "bool", False, "Automatic per-turn RAG injection."),
    _s("LUMI_RAG_K", "recall.rag_k", "int", 4, "Top-K past moments injected per turn.", min=0),
    _s("LUMI_RAG_FLOOR", "recall.rag_floor", "float", 0.3, "Cosine relevance floor.", min=0.0, max=1.0),
    _s("LUMI_FACTS_RAG", "recall.facts_rag", "bool", False, "Per-turn fact-RAG push (non-core facts)."),
    _s("LUMI_FACTS_RAG_K", "recall.facts_rag_k", "int", 4, "Top-K non-core facts pushed per turn.", min=0),
    _s("LUMI_RAG_MAX_CHARS", "recall.rag_max_chars", "int", 1200, "The recall block's total char budget.", min=0),
    _s("LUMI_RAG_W", "recall.rag_w", "int", 2, "± neighbour messages around each hit.", min=0),
    _s("LUMI_RAG_SNIPPET_CHARS", "recall.rag_snippet_chars", "int", 240, "Per-line cap for each recalled moment.",
       min=1),
    _s("LUMI_RAG_CHUNK", "recall.chunk", "bool", False, "Index a long message as several chunks."),
    _s("LUMI_RAG_CHUNK_CHARS", "recall.chunk_chars", "int", 800, "Target chunk size in chars.", min=1),
    _s("LUMI_RAG_CHUNK_OVERLAP", "recall.chunk_overlap", "int", 120, "Overlap between adjacent chunks.", min=0),
    _s("LUMI_RAG_CHUNK_THRESHOLD", "recall.chunk_threshold", "int", 1200, "Only chunk messages longer than this.",
       min=1),
    _s("LUMI_RAG_CHUNK_W", "recall.chunk_w", "int", 1, "± adjacent chunks injected around a hit.", min=0),
    _s("LUMI_RECALL_TOOL", "recall.tool", "bool", False, "The model-callable recall tool (needs recall on)."),
    _s("LUMI_RECALL_TOOL_K", "recall.tool_k", "int", 5, "Past moments the recall tool returns.", min=1),
    _s("LUMI_RECALL_TOOL_MAX_CALLS", "recall.tool_max_calls", "int", 3, "Per-turn recall-tool call cap.", min=0),
    _s("LUMI_RECALL_SCOPE", "recall.scope", "enum", "messages", "The recall tool's default scope.",
       choices=("messages", "facts", "all")),
    _s("LUMI_DATE_TOOL", "recall.date_tool", "bool", False, "Fetch raw verbatim messages by date."),
    _s("LUMI_DATE_TOOL_MAX_CHARS", "recall.date_tool_max_chars", "int", 4000, "The date tool's char budget.", min=1),
    _s("LUMI_DATE_TOOL_MAX_DAYS", "recall.date_tool_max_days", "int", 14, "Max days a date range may span.", min=1),
    _s("LUMI_DATE_TOOL_MAX_CALLS", "recall.date_tool_max_calls", "int", 3, "Per-turn date-tool call cap.", min=0),
    # --- mood -----------------------------------------------------------------------------------------------
    _s("LUMI_MOOD", "mood.enabled", "bool", True, "The daily mood of the day."),
    _s("LUMI_NATAL_PATH", "mood.natal_path", "path", None, "Her natal snapshot. Unset → core/natal.md."),
    _s("LUMI_BIORHYTHMS", "mood.biorhythms", "bool", True, "Merge the computed biorhythms into the mood."),
    _s("LUMI_CYCLE", "mood.cycle", "bool", True, "Merge the hormonal cycle into the mood."),
    # --- world (ambient context) -----------------------------------------------------------------------------
    _s("LUMI_LOCATION", "world.location", "str", None, "Her location name for the ambient weather."),
    _s("LUMI_LAT", "world.lat", "float", None, "Latitude for the ambient weather.", min=-90.0, max=90.0),
    _s("LUMI_LON", "world.lon", "float", None, "Longitude for the ambient weather.", min=-180.0, max=180.0),
    _s("LUMI_WEATHER_URL", "world.weather_url", "str", DEFAULT_WEATHER_URL, "The weather API URL."),
    _s("LUMI_NEWS_URL", "world.news_url", "str", None, "The ambient news feed URL. Unset → no ambient news."),
    _s("LUMI_NEWS_CAP", "world.news_cap", "int", 3, "Ambient headlines kept.", min=0),
    # --- closeness ------------------------------------------------------------------------------------------
    _s("LUMI_CLOSENESS", "closeness.enabled", "bool", True, "The per-user relationship level."),
    _s("LUMI_CLOSENESS_PATH", "closeness.path", "path", None, "The authored levels. Unset → core/closeness.md."),
    _s("LUMI_CLOSENESS_BASELINE", "closeness.baseline", "float", _CT.baseline, "The value decay drifts toward."),
    _s("LUMI_CLOSENESS_DECAY", "closeness.decay", "float", _CT.decay_retained, "Fraction retained per day of silence."),
    _s("LUMI_CLOSENESS_W_POS", "closeness.w_pos", "float", _CT.w_pos, "Weight of the warm signals."),
    _s("LUMI_CLOSENESS_W_NEG", "closeness.w_neg", "float", _CT.w_neg, "Weight of the harm signals."),
    _s("LUMI_CLOSENESS_DELTA", "closeness.delta", "float", _CT.delta_scale, "Per-turn nudge scale."),
    _s("LUMI_CLOSENESS_INERTIA", "closeness.inertia", "float", _CT.inertia, "Level re-bucket inertia."),
    _s("LUMI_CLOSENESS_DRIFT", "closeness.drift", "float", _CT.drift_rate, "Drift rate toward the baseline."),
    _s("LUMI_CLOSENESS_MOOD_SHIFT", "closeness.mood_shift", "probability", _CT.mood_shift_scale,
       "Daily mood-shift strength 0..1 (off = 0)."),
    # --- nudge ----------------------------------------------------------------------------------------------
    _s("LUMI_IDLE_NUDGE", "nudge.enabled", "bool", False, "The v0.4 idle nudge."),
    _s("LUMI_IDLE_SECONDS", "nudge.idle_seconds", "int", 240, "Idle seconds before a nudge.", min=1),
    _s("LUMI_NUDGE_PATH", "nudge.path", "path", None, "The nudge openers. Unset → core/nudges.md."),
    _s("LUMI_QUIET_HOURS", "nudge.quiet_hours", "hours", None, "The nudge's quiet window, HH-HH (e.g. 23-7)."),
    # --- thoughts -------------------------------------------------------------------------------------------
    _s("LUMI_THOUGHTS", "thoughts.enabled", "bool", True, "The thought-stream."),
    _s("LUMI_THOUGHTS_QUIET_HOURS", "thoughts.quiet_hours", "hours", None,
       "The proactive think's quiet window (HH-HH, or off). Unset → the nudge's."),
    _s("LUMI_THOUGHTS_WINDOW_H", "thoughts.window_h", "int", THOUGHTS_WINDOW_H, "Prompt feedback window (hours).",
       min=0),
    _s("LUMI_THOUGHTS_MAX_LINES", "thoughts.max_lines", "int", THOUGHTS_MAX_LINES,
       "Max thought lines injected into the prompt.", min=0),
    _s("LUMI_THOUGHT_MAX_CHARS", "thoughts.max_chars", "int", THOUGHT_MAX_CHARS,
       "Hard per-thought length cap (0 = off).", min=0),
    _s("LUMI_THOUGHTS_INTERVAL_S", "thoughts.interval_s", "int", THOUGHTS_INTERVAL_S,
       "Idle seconds before a proactive %think.", min=1),
    _s("LUMI_THOUGHTS_CAP", "thoughts.cap", "int", THOUGHTS_CAP, "Proactive thinks per session.", min=0),
    _s("LUMI_THOUGHTS_SPOKEN_RATIO", "thoughts.spoken_ratio", "float", THOUGHTS_SPOKEN_RATIO,
       "Fraction of thoughts that graduate to a spoken turn.", min=0.0, max=1.0),
    _s("LUMI_THOUGHTS_SHOW", "thoughts.show", "enum", "hidden", "The /thoughts policy.",
       choices=("hidden", "admin", "off")),
    _s("LUMI_THOUGHTS_CONTEXT", "thoughts.context", "enum", "lean", "The thought prompt: seeds only, or the backdrop.",
       choices=("lean", "full")),
    _s("LUMI_THINK_SEEDS_PATH", "thoughts.seeds_path", "path", None, "The %think seed menu. Unset → core/think_seeds.md."),
    _s("LUMI_THOUGHT_TOOLS", "thoughts.tools", "bool", False, "Master gate: tool-using thoughts."),
    _s("LUMI_THOUGHT_JOURNAL", "thoughts.journal", "bool", False, "The %journal directive."),
    _s("LUMI_THOUGHT_WIKI", "thoughts.wiki", "bool", False, "The %lookup / %learn directives."),
    _s("LUMI_THOUGHT_NEWS", "thoughts.news", "bool", False, "The %catchup / %brief directives."),
    _s("LUMI_THOUGHT_WEB", "thoughts.web", "bool", False, "The %search / %events directives."),
    _s("LUMI_THOUGHT_PROMPT", "thoughts.prompt", "bool", False, "The owner-only %prompt directive."),
    _s("LUMI_THOUGHT_IMAGE", "thoughts.image", "bool", False, "The %gaze / %imagine / %share directives."),
    _s("LUMI_THOUGHT_IMAGINE_CAP", "thoughts.imagine_cap", "int", 1, "The paid %imagine sub-cap.", min=0),
    _s("LUMI_THOUGHT_SURFACE", "thoughts.surface", "bool", False, "Surface a running directive as a chat-log line."),
    # --- scheduler ------------------------------------------------------------------------------------------
    _s("LUMI_SCHEDULER", "scheduler.enabled", "bool", False, "The in-process thought scheduler."),
    _s("LUMI_SCHEDULE_PATH", "scheduler.path", "path", None, "The thought schedule. Unset → core/schedule.yaml."),
    _s("LUMI_SCHED_TICK_MS", "scheduler.tick_ms", "int", 30000, "Scheduler evaluation interval (ms).", min=1),
    _s("LUMI_SCHED_TICK_FAST_MS", "scheduler.tick_fast_ms", "int", 60000, "The ephemeral fast tick (ms).", min=1),
    _s("LUMI_SCHED_CATCHUP_H", "scheduler.catchup_h", "int", 6, "Startup catch-up window (hours).", min=0),
    _s("LUMI_SCHED_DAY_CAP", "scheduler.day_cap", "int", 24, "Global scheduled thoughts per day.", min=0),
    _s("LUMI_SCHED_SHOW_TO_OUTBOX", "scheduler.show_to_outbox", "bool", False,
       "Shown scheduled thoughts also go to Telegram / the voicer."),
    # --- tools (the bounded tool-loop) -----------------------------------------------------------------------
    _s("LUMI_TOOL_MAX_STEPS", "tools.max_steps", "int", 8, "Max tool calls per turn.", min=1),
    # --- files (the file tool) -------------------------------------------------------------------------------
    _s("LUMI_FILE_TOOL", "files.enabled", "bool", False, "The sandboxed file tools."),
    _s("LUMI_FILES_DIR", "files.dir", "path", None, "The sandbox root. Unset → <data root>/files."),
    _s("LUMI_FILE_READ_LINES", "files.read_lines", "int", 200, "Default / max lines per read_file.", min=1),
    _s("LUMI_FILE_READ_MAX_TOTAL", "files.read_max_total", "int", 2000, "Max lines one turn may read.", min=1),
    _s("LUMI_FILE_READ_MAX_CHARS", "files.read_max_chars", "int", 8000, "Per-result char cap on a read.", min=1),
    _s("LUMI_FILE_FIND_MAX", "files.find_max", "int", 50, "Max matches find_in_file returns.", min=1),
    _s("LUMI_FILE_WRITE_MAX", "files.write_max", "int", 65536, "Max bytes of one create/append.", min=1),
    _s("LUMI_FILE_COPY_MAX", "files.copy_max", "int", 5 * 1024 * 1024, "Max source bytes for one copy_file.", min=1),
    _s("LUMI_FILE_SEARCH_MAX_FILES", "files.search_max_files", "int", 200, "Max files search_files scans.", min=1),
    _s("LUMI_FILE_SEARCH_MAX_LINES", "files.search_max_lines", "int", 100, "Max matching lines search_files returns.", min=1),
    _s("LUMI_FILE_SEARCH_MAX_CHARS", "files.search_max_chars", "int", 4000, "The output char budget of search_files.", min=1),
    _s("LUMI_FILE_AROUND_MAX_K", "files.around_max_k", "int", 50, "Max K lines each side of a read_around anchor.", min=0),
    _s("LUMI_FILE_DATE_MAX_DAYS", "files.date_max_days", "int", 366, "list_files date-range span cap (days).", min=1),
    _s("LUMI_FILE_TOOL_TRACE", "files.trace", "bool", False, "Show the file tools used each turn."),
    # --- wiki -----------------------------------------------------------------------------------------------
    _s("LUMI_WIKI", "wiki.enabled", "bool", False, "The Wikipedia tool."),
    _s("LUMI_WIKI_LANG", "wiki.lang", "list", ["uk", "en"], "Language editions; the first with a hit wins."),
    _s("LUMI_WIKI_BASE_URL", "wiki.base_url", "str", None, "Override the host. Unset → https://{lang}.wikipedia.org."),
    _s("LUMI_WIKI_MAX_CHARS", "wiki.max_chars", "int", 1500, "Extract cap for one wiki_read.", min=1),
    _s("LUMI_WIKI_MAX_CALLS", "wiki.max_calls", "int", 4, "Per-turn wiki call cap.", min=0),
    # --- news (the Guardian tool) ----------------------------------------------------------------------------
    _s("LUMI_NEWS_TOOL", "news.enabled", "bool", False, "The Guardian news tool."),
    _secret("LUMI_NEWS_API_KEY", "news.api_key", "The Guardian developer key — open-platform.theguardian.com."),
    _s("LUMI_NEWS_API_URL", "news.api_url", "str", "https://content.guardianapis.com", "The Guardian API base."),
    _s("LUMI_NEWS_SECTIONS", "news.sections", "list",
       ["world", "politics", "business", "technology", "science", "environment", "culture", "sport"],
       "The topic allowlist."),
    _s("LUMI_NEWS_MAX_RESULTS", "news.max_results", "int", 8, "Candidates per news_search.", min=1),
    _s("LUMI_NEWS_MAX_CHARS", "news.max_chars", "int", 3000, "Body cap for one news_read.", min=1),
    _s("LUMI_NEWS_MAX_CALLS", "news.max_calls", "int", 4, "Per-turn news call cap.", min=0),
    _s("LUMI_NEWS_DAYS", "news.days", "int", 7, "Default recency window for a search (days).", min=1),
    # --- web (web lookup) ------------------------------------------------------------------------------------
    _s("LUMI_WEB_LOOKUP", "web.enabled", "bool", False, "The web_lookup tool + /web (paid, Gemini grounding)."),
    _s("LUMI_WEB_LOOKUP_MODEL", "web.model", "str", "gemini-2.5-flash", "The Gemini grounding model."),
    _s("LUMI_WEB_LOOKUP_MAX_CALLS", "web.max_calls", "int", 2, "Per-turn grounded-call cap.", min=0),
    _s("LUMI_WEB_LOOKUP_MAX_CHARS", "web.max_chars", "int", 2000, "Cap on the answer folded into the reply.", min=1),
    # --- journal --------------------------------------------------------------------------------------------
    _s("LUMI_JOURNAL", "journal.enabled", "bool", False, "The journal tools + /journal."),
    _s("LUMI_JOURNAL_DIR", "journal.dir", "path", None, "The journal root. Unset → <data root>/journal."),
    _s("LUMI_JOURNAL_MAX_CHARS", "journal.max_chars", "int", 4000, "Cap on one journal_write body.", min=1),
    # --- image ----------------------------------------------------------------------------------------------
    _s("LUMI_IMAGE", "image.enabled", "bool", False, "Vision (view_image + shared images) and generation."),
    _s("LUMI_VISION_MAX", "image.vision_max", "int", 4, "Max images viewed per turn.", min=0),
    _s("LUMI_IMAGE_MAX_BYTES", "image.max_bytes", "int", 5_242_880, "Max size of one viewed image.", min=1),
    _s("LUMI_IMAGE_PROVIDER", "image.provider", "str", "gemini", "The generation backend."),
    _s("LUMI_IMAGE_MODEL", "image.model", "str", "gemini-2.5-flash-image", "The generation model."),
    _s("LUMI_IMAGE_SIZE", "image.size", "int", 768, "Generated PNG size hint.", min=1),
    _s("LUMI_IMAGE_MAX_GEN", "image.max_gen", "int", 2, "Max generations per turn (paid).", min=0),
    _s("LUMI_IMAGE_SHOW", "image.show", "list", ["path", "viewer", "telegram"], "Display targets for a generated PNG."),
    # --- bridge (the file bus) -------------------------------------------------------------------------------
    _s("LUMI_BRIDGE", "bridge.enabled", "bool", False, "The TUI reads the inbox / writes the outbox."),
    _s("LUMI_INBOX_PATH", "bridge.inbox_path", "path", None, "The inbox. Unset → <data root>/inbox.jsonl."),
    _s("LUMI_OUTBOX_PATH", "bridge.outbox_path", "path", None, "The outbox. Unset → <data root>/outbox.jsonl."),
    # --- telegram -------------------------------------------------------------------------------------------
    _secret("LUMI_TELEGRAM_TOKEN", "telegram.token", "The bot token from @BotFather — never logged."),
    _s("LUMI_TELEGRAM_ALLOWLIST", "telegram.allowlist", "ids", None, "The Telegram user ids served (the owner's)."),
    _s("LUMI_TELEGRAM_FLUSH_S", "telegram.flush_s", "int", 2, "Inbound buffer flush cadence (s).", min=1),
    _s("LUMI_TELEGRAM_BATCH", "telegram.batch", "int", 5, "Max outbox records per Telegram message.", min=1),
    _s("LUMI_TELEGRAM_CATCHUP_H", "telegram.catchup_h", "int", 24, "Skip outbox records older than this (hours).",
       min=0),
    _s("LUMI_TELEGRAM_PHOTO", "telegram.photo", "probability", 0.0, "Probability 0..1 of sending the face photo."),
    _s("LUMI_TELEGRAM_VOICE", "telegram.voice", "bool", False, "Send replies as voice messages."),
    _s("LUMI_TELEGRAM_STT", "telegram.stt", "bool", False, "Transcribe inbound voice messages."),
    # --- voice ----------------------------------------------------------------------------------------------
    _s("LUMI_VOICE", "voice.enabled", "bool", False, "The TUI writes the outbox for the voicer."),
    _secret("ELEVENLABS_API_KEY", "voice.elevenlabs_api_key", "The ElevenLabs key (TTS, Scribe STT) — elevenlabs.io."),
    _s("LUMI_VOICE_ID", "voice.voice_id", "str", None, "The ElevenLabs voice to speak in."),
    _s("LUMI_VOICE_MODEL", "voice.model", "str", "eleven_multilingual_v2", "The ElevenLabs model."),
    _s("LUMI_VOICE_SKIP_MISSED", "voice.skip_missed", "bool", False,
       "On start, skip the replies missed while the voicer was off."),
    _s("LUMI_VOICE_SENTENCES", "voice.sentences", "bool", False, "Speak each reply sentence by sentence."),
    _s("LUMI_VOICE_ENDPOINT_MS", "voice.endpoint_ms", "int", 500, "Streaming-STT end-of-turn silence (ms).", min=1),
    _s("LUMI_VOICE_OUT_DEVICE", "voice.out_device", "str", None, "Voice-mode speaker: an index or a name substring."),
    _s("LUMI_VOICE_FIRST_CLAUSE_WORDS", "voice.first_clause_words", "int", 8,
       "First spoken chunk may cut after this many words (0 = off).", min=0),
    _s("LUMI_VOICE_RECONNECT", "voice.reconnect", "int", 3, "STT reconnect attempts before falling back to text.",
       min=0),
    _s("LUMI_MODE_SET", "voice.mode", "enum", "text", "The interaction mode at startup.", choices=("text", "voice")),
    # --- dictation ------------------------------------------------------------------------------------------
    _s("LUMI_DICTATION", "dictation.enabled", "bool", False, "Local dictation (mic → inbox)."),
    _s("LUMI_STT_PROVIDER", "dictation.stt_provider", "enum", "deepgram", "The STT provider.",
       choices=("deepgram", "elevenlabs", "whisper")),
    _s("LUMI_STT_MODEL", "dictation.stt_model", "str", None, "The STT model. Unset → the provider's default."),
    _s("LUMI_STT_LANG", "dictation.stt_lang", "str", "uk", "The recognition language."),
    _s("LUMI_STT_DEVICE", "dictation.stt_device", "str", None, "The mic: an index or a name substring."),
    _s("LUMI_LISTEN_FLAG", "dictation.listen_flag", "path", None, "The listen toggle. Unset → <data root>/listen.flag."),
    _secret("DEEPGRAM_API_KEY", "dictation.deepgram_api_key", "The Deepgram key (cloud STT) — console.deepgram.com."),
    # --- tui ------------------------------------------------------------------------------------------------
    _s("LUMI_SOUND", "tui.sound", "bool", False, "The send/receive sound."),
    _s("LUMI_INPUT_BUFFER", "tui.input_buffer", "bool", False, "Keep the input editable during a turn (queued)."),
    # --- viewer ---------------------------------------------------------------------------------------------
    _s("LUMI_FACES_DIR", "viewer.faces_dir", "path", None, "The face packs. Unset → viewer/faces."),
    _s("LUMI_FACE_SIGNAL", "viewer.face_signal", "path", None, "The emotion-face signal file the viewer polls."),
    _s("LUMI_FACE_IDLE_SECONDS", "viewer.face_idle_seconds", "float", 120.0,
       "Relax the face to calm after this long (0 = off).", min=0.0),
)

BY_ALIAS: dict[str, Setting] = {s.alias: s for s in SETTINGS}
BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}
_BY_OLD: dict[str, Setting] = {old: s for s in SETTINGS for old in s.old}
GROUPS: tuple[str, ...] = tuple(dict.fromkeys(s.group for s in SETTINGS))

# Environment-only names — process facts and compose parameters, never settings (pinned by a test).
ENV_ONLY: dict[str, str] = {
    "LUMI_CONTAINER": "baked into the image (the container mode)",
    "LUMI_BUILD": "baked into the image (its build ref)",
    "LUMI_SETTINGS_DIR": "where the settings folder is — read before any file",
    "LUMI_IMAGE_TAG": "a compose parameter (the deploy agent sets it)",
    "LUMI_INSTANCE": "a compose parameter (deploy/compose/<instance>.env)",
    "LUMI_INSTANCE_DIR": "a compose parameter",
    "LUMI_PUBLISH_HOST": "a compose parameter",
    "LUMI_TZ": "a compose parameter (the container's TZ)",
    "LUMI_REALTIME_MODEL": "the manual realtime probes only (scripts/*_probe.py)",
    "LUMI_REALTIME_VOICE": "the manual realtime probes only (scripts/*_probe.py)",
}


class SettingsError(SystemExit):
    """A settings file the process must not start on; ``str(exc)`` is the readable, multi-line reason.

    A ``SystemExit`` so an entry point that doesn't catch it still exits with the message, not a traceback."""

    def __str__(self) -> str:  # SystemExit keeps the message in .code
        return str(self.code)


# --- values ---------------------------------------------------------------------------------------------------


def _allowed(s: Setting) -> str:
    if s.choices:
        return "one of " + ", ".join(s.choices)
    if s.type == "bool":
        return "true or false (on / off)"
    if s.type == "probability":
        return "a number 0..1 (or on / off)"
    if s.type == "hours":
        return "an hour range HH-HH (e.g. 23-7), or off"
    if s.type == "duration":
        return "a duration like 30m, 5h, 300s or a number of seconds"
    if s.type == "ids":
        return "a list of integer ids"
    if s.type == "list":
        return "a list of strings (or one comma-separated string)"
    if s.type in ("str", "path"):
        return "text — quote a value that looks like a number (\"0123\"); YAML reads 0123 as 83"
    kind = {"int": "an integer", "float": "a number"}.get(s.type, "text")
    if s.min is not None and s.max is not None:
        return f"{kind} {s.min:g}..{s.max:g}"
    if s.min is not None:
        return f"{kind} ≥ {s.min:g}"
    return kind


def _shown(s: Setting, value: Any) -> str:
    return "(hidden)" if s.secret else repr(value)


def _range_problem(s: Setting, number: float) -> bool:
    return (s.min is not None and number < s.min) or (s.max is not None and number > s.max)


def to_env(s: Setting, value: Any) -> str | None:
    """A YAML value → the env-string form ``load_config`` parses, or ``None`` when it isn't valid for ``s``.

    ``None`` (YAML null) is never passed in — a null means "unset" and is dropped before."""
    t = s.type
    if t == "bool":
        if isinstance(value, bool):
            return "on" if value else "off"
        if isinstance(value, str) and value.strip().lower() in _TRUE_WORDS | _FALSE_WORDS:
            return "on" if value.strip().lower() in _TRUE_WORDS else "off"
        return None
    if t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            if not isinstance(value, str) or not re.fullmatch(r"-?[0-9]+", value.strip()):
                return None  # ASCII digits only: "²".isdigit() is True, but int("²") raises (code review #6)
            value = int(value.strip())
        return None if _range_problem(s, value) else str(value)
    if t in ("float", "probability"):
        if t == "probability" and isinstance(value, bool):
            return "1.0" if value else "0.0"
        if isinstance(value, bool):
            return None
        if isinstance(value, str):
            word = value.strip().lower()
            if t == "probability" and word in _TRUE_WORDS | _FALSE_WORDS:
                return "1.0" if word in _TRUE_WORDS else "0.0"
            try:
                value = float(word)
            except ValueError:
                return None
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return None  # .inf / nan pass a range check and crash load_config (code review #6)
        lo, hi = (0.0, 1.0) if t == "probability" else (s.min, s.max)
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            return None
        return repr(float(value)) if isinstance(value, float) else str(value)
    if t == "enum":
        if isinstance(value, bool):  # YAML 1.1 reads on/off/yes/no as booleans
            value = "on" if value else "off"
        if not isinstance(value, (str, int, float)):
            return None
        text = str(value).strip()
        for choice in s.choices:
            if text.lower() == choice.lower():
                return choice
        return None
    if t in ("str", "path"):
        # text only: YAML 1.1 reads an unquoted 0123 as the number 83 and 1.10 as 1.1 — the original is lost, so a
        # number is refused with a hint to quote it (code review #10)
        return value if isinstance(value, str) else None
    if t in ("list", "ids"):
        items = value if isinstance(value, list) else (value.split(",") if isinstance(value, str) else None)
        if items is None:
            return None
        out: list[str] = []
        for item in items:
            if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                return None
            text = str(item).strip()
            if t == "ids" and not text.lstrip("-").isdigit():
                return None
            if text:
                out.append(text)
        return ",".join(out)
    if t == "duration":
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return str(value) if math.isfinite(value) and value >= 0 else None
        if isinstance(value, str):
            text = value.strip().lower()
            num = text[:-1] if text[-1:] in ("h", "m", "s") else text
            try:
                number = float(num)
            except ValueError:
                return None
            return text if math.isfinite(number) and number >= 0 else None
        return None
    if t == "hours":
        if value is False or (isinstance(value, str) and value.strip().lower() in ("off", "none", "")):
            return "off"
        if isinstance(value, str) and "-" in value:
            a, _, b = value.strip().partition("-")
            if a.strip().isdigit() and b.strip().isdigit() and int(a) <= 23 and int(b) <= 23:
                return f"{int(a)}-{int(b)}"
        return None
    raise ValueError(f"unknown setting type {t!r}")  # pragma: no cover - the schema test pins the types


def problem(s: Setting, value: Any, *, where: str) -> str | None:
    """Why ``value`` is not valid for ``s`` (one readable line naming where, the key, the value, the allowed values)."""
    if value is None or to_env(s, value) is not None:
        return None
    return f"{where}: {s.key} = {_shown(s, value)} is not valid — allowed: {_allowed(s)}"


# --- files ----------------------------------------------------------------------------------------------------


def settings_dir(environ: Mapping[str, str] | None = None, *, repo: Path = _REPO_ROOT) -> Path:
    """The instance's settings folder: ``LUMI_SETTINGS_DIR``, else the repo root, else the repo's parent — the first
    that holds a settings file or a ``.env`` (the repo root when none does)."""
    environ = os.environ if environ is None else environ
    explicit = (environ.get(SETTINGS_DIR_ENV) or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    for folder in (repo, repo.parent):
        if any((folder / name).exists() for name in (SETTINGS_FILE, SECRETS_FILE, DOTENV_FILE)):
            return folder
    return repo


_STRICT_LOADER: Any = None


def _strict_loader() -> Any:
    """PyYAML's SafeLoader, refusing a duplicate key — ``safe_load`` keeps the last one silently (code review #7)."""
    global _STRICT_LOADER
    if _STRICT_LOADER is None:
        import yaml

        class StrictLoader(yaml.SafeLoader):
            pass

        def mapping(loader: Any, node: Any, deep: bool = False) -> Any:
            seen: set = set()
            for key_node, _value in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if key in seen:
                    raise yaml.constructor.ConstructorError(
                        None, None, f"duplicate key {key!r} (only the last would count)", key_node.start_mark)
                seen.add(key)
            return yaml.SafeLoader.construct_mapping(loader, node, deep)

        StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        _STRICT_LOADER = StrictLoader
    return _STRICT_LOADER


def parse_yaml(text: str, name: str) -> Any:
    """Parse a settings file's text. A syntax error refuses with the problem and its line/column only — never
    PyYAML's snippet of the line, which in ``secrets.yaml`` is the secret itself (code review #1); a duplicate key
    refuses too (code review #7)."""
    import yaml

    try:
        return yaml.load(text, Loader=_strict_loader())  # noqa: S506 — a SafeLoader subclass
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        what = exc.problem or exc.context or "a syntax error"
        raise SettingsError(f"Lumi settings — {name} is not valid YAML: {what}{where}") from None
    except yaml.YAMLError:
        raise SettingsError(f"Lumi settings — {name} is not valid YAML") from None


def parse_mapping(text: str, name: str) -> dict:
    """A YAML mapping from a settings file's ``text`` (``{}`` when empty); a parse error or a non-mapping refuses."""
    data = parse_yaml(text, name)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SettingsError(f"Lumi settings — {name} must be a mapping of groups (e.g. `memory:`), not a "
                            f"{type(data).__name__}")
    return data


def read_yaml(path: Path) -> dict:
    """A YAML mapping from ``path`` (``{}`` when missing or empty); a parse error or a non-mapping refuses."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return parse_mapping(text, str(path))


def flatten(data: Mapping, prefix: str = "") -> dict[str, Any]:
    """Nested groups → dotted keys (``{"memory": {"window": 40}}`` → ``{"memory.window": 40}``)."""
    out: dict[str, Any] = {}
    for name, value in data.items():
        key = f"{prefix}{name}"
        if value is None and not prefix and key in GROUPS:
            continue  # a group header whose lines are all commented out (the example files' shape)
        if isinstance(value, dict) and key not in BY_KEY:
            out.update(flatten(value, key + "."))
        else:
            out[key] = value
    return out


def nest(flat: Mapping[str, Any]) -> dict[str, Any]:
    """Dotted keys → nested groups (the inverse of :func:`flatten`), in the order given."""
    out: dict[str, Any] = {}
    for key, value in flat.items():
        node = out
        *parents, leaf = key.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def read_dotenv(path: Path) -> dict[str, str]:
    """The keys a ``.env`` sets (commented and value-less lines dropped; quotes stripped like ``load_dotenv``)."""
    if not path.is_file():
        return {}
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


# --- the merge ------------------------------------------------------------------------------------------------


@dataclass
class Resolution:
    """The merged settings: ``values`` (alias → env string) + where each came from + the non-blocking warnings."""

    folder: Path | None
    values: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # alias → default/.env/settings.yaml/secrets.yaml/env
    warnings: list[str] = field(default_factory=list)
    from_files: dict[str, str] = field(default_factory=dict)  # alias → value, the file layers only (for export)
    sections: dict[str, Any] = field(default_factory=dict)  # the validated structured sections (models, schedule)

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.values.get(name, default)

    def source(self, alias: str) -> str:
        return self.sources.get(alias, "default")


# Values this process copied into os.environ from the files — so a later resolve doesn't mistake them for real env.
_EXPORTED: dict[str, str] = {}
_WARNED: set[str] = set()


def _real_env(environ: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in environ.items() if not (k in _EXPORTED and _EXPORTED[k] == v)}


def _apply_old_names(res: Resolution, layers: list[tuple[dict[str, str], str]]) -> None:
    """A deprecated name (``GOOGLE_API_KEY``) fills its setting only when the current name is absent from **every**
    layer — so a shell's old name never beats the file's new one (code review #3); among the layers that carry the
    old name, the strongest wins."""
    for old, s in _BY_OLD.items():
        if s.alias in res.values:
            continue
        for layer, label in reversed(layers):
            if old in layer:
                res.values[s.alias] = layer[old]
                res.sources[s.alias] = label
                if label != "env":
                    res.from_files[s.alias] = layer[old]  # exported under the current name too
                break


_PROFILE_FIELDS = ("provider", "reply", "think", "mood", "housekeeping", "voice")
_ROW_KEYS = ("name", "directive", "seeds", "topic", "enabled", "show", "cron", "between", "at", "every", "idle",
             "days")
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _check_models(data: Any, where: str) -> tuple[list[str], list[str]]:
    """The ``models:`` section (aliases + profiles merged over core/models.yaml) — its shape, never the model ids."""
    problems: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return [f"{where}: models must be a mapping with aliases: and/or profiles:"], []
    for key in data:
        if key not in ("aliases", "profiles"):
            warnings.append(f"unknown key 'models.{key}' in {where} — ignored")
    aliases = data.get("aliases") or {}
    if not isinstance(aliases, dict):
        problems.append(f"{where}: models.aliases must be a mapping (alias: \"provider:model\")")
    else:
        for alias, target in aliases.items():
            if not isinstance(target, str) or ":" not in target:
                problems.append(f"{where}: models.aliases.{alias} = {target!r} is not valid — allowed: "
                                "\"provider:model-id\"")
    profiles = data.get("profiles") or {}
    if not isinstance(profiles, dict):
        problems.append(f"{where}: models.profiles must be a mapping (name: {{provider, reply, …}})")
    else:
        for name, block in profiles.items():
            if not isinstance(block, dict):
                problems.append(f"{where}: models.profiles.{name} must be a mapping of "
                                + ", ".join(_PROFILE_FIELDS))
                continue
            for k, v in block.items():
                if k not in _PROFILE_FIELDS:
                    warnings.append(f"unknown key 'models.profiles.{name}.{k}' in {where} — ignored")
                elif v is not None and not isinstance(v, str):
                    problems.append(f"{where}: models.profiles.{name}.{k} = {v!r} is not valid — allowed: a model id")
    return problems, warnings


def _check_schedule(rows: Any, where: str) -> tuple[list[str], list[str]]:
    """The ``schedule:`` section — rows merged over core/schedule.yaml by ``name``; each needs one."""
    problems: list[str] = []
    warnings: list[str] = []
    if not isinstance(rows, list):
        return [f"{where}: schedule must be a list of rows (- name: …)"], []
    for i, row in enumerate(rows, 1):
        at = f"{where}: schedule row {i}"
        if not isinstance(row, dict):
            problems.append(f"{at} must be a mapping (name: …, directive: …, at: …)")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{at} needs a name: (the authored row it changes, or a new one)")
            continue
        label = f"{where}: schedule[{name}]"
        for k, v in row.items():
            if k not in _ROW_KEYS:
                warnings.append(f"unknown key '{k}' in schedule row '{name}' ({where}) — ignored")
            elif v is None:
                continue
            elif k in ("enabled", "show") and not isinstance(v, bool):
                problems.append(f"{label}.{k} = {v!r} is not valid — allowed: true or false")
            elif k in ("at", "between") and not isinstance(v, str):
                problems.append(f"{label}.{k} = {v!r} is not valid — allowed: a quoted time like \"08:00\" "
                                f"(YAML reads an unquoted 08:00 as a number)")
            elif k == "days" and not (isinstance(v, list) and all(str(d).lower() in _DAYS for d in v)):
                problems.append(f"{label}.days = {v!r} is not valid — allowed: a list of " + ", ".join(_DAYS))
            elif k in ("directive", "seeds", "topic", "cron", "name") and not isinstance(v, str):
                problems.append(f"{label}.{k} = {v!r} is not valid — allowed: text")
            elif k in ("every", "idle") and (isinstance(v, bool) or not isinstance(v, (str, int, float))):
                problems.append(f"{label}.{k} = {v!r} is not valid — allowed: a duration like 15m or 3h")
    return problems, warnings


def _file_layer(path: Path, *, secret: bool, sections: dict | None = None, text: str | None = None
                ) -> tuple[dict[str, str], list[str], list[str]]:
    """``(alias → env string, problems, warnings)`` for one YAML file (or its ``text``, not yet written); its
    structured sections go into ``sections`` (settings.yaml only)."""
    values: dict[str, str] = {}
    problems: list[str] = []
    warnings: list[str] = []
    name = path.name
    data = read_yaml(path) if text is None else parse_mapping(text, name)
    for section in STRUCTURED:
        if section not in data:
            continue
        raw = data.pop(section)
        if secret:
            problems.append(f"{name}: the {section}: section belongs in {SETTINGS_FILE}")
            continue
        if raw is None:
            continue
        p, w = (_check_models if section == "models" else _check_schedule)(raw, name)
        problems += p
        warnings += w
        if not p and sections is not None:
            sections[section] = raw
    for key, value in flatten(data).items():
        s = BY_KEY.get(key)
        if s is None:
            warnings.append(f"unknown setting '{key}' in {name} — ignored")
            continue
        if s.secret != secret:
            where = SECRETS_FILE if s.secret else SETTINGS_FILE
            problems.append(f"{name}: {key} belongs in {where}" + (" (it is a secret)" if s.secret else ""))
            continue
        if value is None:
            continue  # null = unset
        if (p := problem(s, value, where=name)) is not None:
            problems.append(p)
            continue
        values[s.alias] = to_env(s, value)  # type: ignore[assignment]
    return values, problems, warnings


def resolve(environ: Mapping[str, str] | None = None, *, folder: Path | None = None,
            load_files: bool = True) -> Resolution:
    """Merge default < ``.env`` < ``settings.yaml`` / ``secrets.yaml`` < the process environment.

    Raises :class:`SettingsError` when a settings file holds a wrong value (every problem named in one message)."""
    environ = os.environ if environ is None else environ
    env = _real_env(environ)
    if not load_files:
        res = Resolution(folder=None)
        res.values = dict(env)
        res.sources = {k: "env" for k in env if k in BY_ALIAS}
        _apply_old_names(res, [(env, "env")])
        return res
    explicit = folder is None and bool((environ.get(SETTINGS_DIR_ENV) or "").strip())
    folder = settings_dir(environ) if folder is None else Path(folder)
    res = Resolution(folder=folder)
    if explicit and not folder.is_dir():  # a typo'd folder would otherwise give the defaults silently (review #14)
        res.warnings.append(f"{SETTINGS_DIR_ENV}={folder} does not exist — no settings file is read")
    dotenv = read_dotenv(folder / DOTENV_FILE)
    if dotenv:
        res.warnings.append(
            f"{folder / DOTENV_FILE} is deprecated since v2.9 — migrate it with "
            f"`python scripts/env_to_settings.py {folder / DOTENV_FILE}` (settings.yaml + secrets.yaml)"
        )
    plain, p1, w1 = _file_layer(folder / SETTINGS_FILE, secret=False, sections=res.sections)
    secrets_path = folder / SECRETS_FILE
    secret, p2, w2 = _file_layer(secrets_path, secret=True)
    problems = p1 + p2
    res.warnings += w1 + w2
    if secret and secrets_path.exists():
        try:
            if stat.S_IMODE(secrets_path.stat().st_mode) & 0o077:
                res.warnings.append(f"{secrets_path} is readable by others — run `chmod 600 {secrets_path}`")
        except OSError:
            pass
    if problems:
        raise SettingsError("Lumi settings — refusing to start:\n" + "\n".join(f"  • {p}" for p in problems))
    layers = [(dotenv, ".env"), (plain, SETTINGS_FILE), (secret, SECRETS_FILE), (env, "env")]
    for layer, label in layers:
        for name, value in layer.items():
            res.values[name] = value
            if name in BY_ALIAS:
                res.sources[name] = label
            if label != "env":
                res.from_files[name] = value
    _apply_old_names(res, layers)
    return res


def export(res: Resolution, environ: MutableMapping[str, str] | None = None) -> None:
    """Copy the file values into the process environment, never overriding a real variable (``load_dotenv``'s rule),
    so the SDKs, the probes and any direct reader see what the files set."""
    environ = os.environ if environ is None else environ
    for name in res.from_files:
        if res.sources.get(name, ".env") == "env":
            continue
        if name not in environ or _EXPORTED.get(name) == environ[name]:
            environ[name] = res.values[name]
            _EXPORTED[name] = res.values[name]


def warn_once(res: Resolution, stream=None) -> None:
    """Print each warning once per process (``WARNING (settings): …`` on stderr)."""
    warn_messages(res.warnings, stream)


def warn_messages(messages: list[str], stream=None) -> None:
    """Print each message once per process, as a ``WARNING (settings): …`` line on stderr."""
    stream = sys.stderr if stream is None else stream
    for w in messages:
        if w not in _WARNED:
            _WARNED.add(w)
            print(f"WARNING (settings): {w}", file=stream)


# --- the example files (scripts/settings_examples.py writes them; the schema contract test compares) -----------

_HEADERS = {
    False: (
        "# Lumi settings — every non-secret setting of an instance (v2.9). GENERATED from core/settings.py by\n"
        "# `python scripts/settings_examples.py` — do not edit by hand.\n"
        "#\n"
        "# Copy the lines you change into settings.yaml (gitignored, next to secrets.yaml). Every line here is\n"
        "# commented out at its built-in default, so a copy of this file changes nothing until you uncomment one.\n"
        "# Precedence: the default < .env (deprecated) < settings.yaml / secrets.yaml < the process environment\n"
        "# (the `env` name on each line). `lumi-cli config set <group.key> <value>` validates and writes a change;\n"
        "# every setting takes effect after a restart.\n"
    ),
    True: (
        "# Lumi secrets — the API keys and tokens of an instance (v2.9). GENERATED from core/settings.py by\n"
        "# `python scripts/settings_examples.py` — do not edit by hand.\n"
        "#\n"
        "# Copy to secrets.yaml (gitignored) and `chmod 600 secrets.yaml`; fill in only the keys you use (the active\n"
        "# provider's, the bot token, the server token). Never commit a real key. `lumi-cli config` masks every\n"
        "# secret on every output; `lumi-cli config set <group.key> <value>` writes one here.\n"
    ),
}


def _yaml_value(key: str, value: Any) -> str:
    import yaml

    leaf = key.rsplit(".", 1)[1]
    if value is None:
        return f"{leaf}:"
    text = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10**6)
    return f"{leaf}: {text.removesuffix(chr(10) + '...' + chr(10)).strip()}"


def _comment(s: Setting) -> str:
    parts = [s.description]
    if s.choices or s.min is not None or s.max is not None or s.type in (
            "hours", "duration", "probability", "ids", "list"):
        parts.append(_allowed(s))
    parts.append(f"env {s.alias}")
    if s.old:
        parts.append("formerly " + ", ".join(s.old))
    return " · ".join(parts)


def render_example(*, secret: bool) -> str:
    """``settings.example.yaml`` (``secret=False``) or ``secrets.example.yaml`` — every setting of that kind, by
    group in schema order, commented out at its default with its description, allowed values and env name."""
    out = [_HEADERS[secret].rstrip("\n")]
    for group in GROUPS:
        members = [s for s in SETTINGS if s.group == group and s.secret == secret]
        if not members:
            continue
        out.append(f"\n{group}:")
        for s in members:
            out.append(f"  # {_comment(s)}")
            out.append(f"  # {_yaml_value(s.key, None if secret else s.default)}")
    if not secret:
        out.append(_OVERRIDES_EXAMPLE)
    return "\n".join(out) + "\n"


_OVERRIDES_EXAMPLE = """
# --- an instance's own changes to the authored files (optional) ---------------------------------------------
# models:                 # merged over core/models.yaml by name — a profile's fields over the authored profile's
#   aliases:
#     fast: "anthropic:claude-haiku-4-5-20251001"
#   profiles:
#     anthropic:
#       reply: claude-sonnet-5
# schedule:               # merged over core/schedule.yaml by name — a new name adds a row
#   - name: morning-brief
#     enabled: false
#   - name: evening-walk
#     directive: think
#     at: "19:00"         # quote every time: YAML reads an unquoted 19:00 as a number
#     topic: "прогулянка"
#     enabled: true"""


# --- the migration's and the CLI's file form ------------------------------------------------------------------


def from_env(s: Setting, raw: str) -> Any:
    """An env-string value (a ``.env`` line) → its typed YAML value, mirroring how ``load_config`` reads it.

    The result still goes through :func:`problem`; a value ``load_config`` would have crashed on (``int("x")``)
    comes back as the raw string, so the validator names it."""
    text = raw.strip()
    t = s.type
    if t == "bool":
        return text.lower() in _TRUE_WORDS
    if t == "int":
        try:
            return int(text)
        except ValueError:
            return text
    if t == "float":
        try:
            return float(text)
        except ValueError:
            return text
    if t == "probability":  # load_config's _parse_probability: on → 1, a number clamped, off/junk → 0
        word = text.lower()
        if word in _TRUE_WORDS:
            return 1.0
        if word in _FALSE_WORDS or not word:
            return 0.0
        try:
            return max(0.0, min(1.0, float(word)))
        except ValueError:
            return text
    if t == "enum":
        for choice in s.choices:
            if text.lower() == choice.lower():
                return choice
        return text
    if t == "list":
        return [part.strip() for part in text.split(",") if part.strip()]
    if t == "ids":
        parts = [part.strip() for part in text.split(",") if part.strip()]
        return [int(p) for p in parts] if all(p.lstrip("-").isdigit() for p in parts) else text
    if t == "hours":
        return "off" if text.lower() in ("", "off", "none") else text
    return text  # str / path / duration


def yaml_scalar(value: Any) -> str:
    """One value as a single-line YAML scalar or flow list (``true``, ``40``, ``[uk, en]``, ``'23-7'``)."""
    import yaml

    text = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10**6)
    return text.removesuffix("\n...\n").strip()


def render_file(values: Mapping[str, Any], header: str) -> str:
    """A settings file from ``group.key → value``: the header, then each group in schema order, its keys in
    schema order (a key the schema doesn't know goes last in its group)."""
    order = {s.key: i for i, s in enumerate(SETTINGS)}
    lines = [header.rstrip("\n")]
    groups: dict[str, list[str]] = {}
    for key in sorted(values, key=lambda k: (order.get(k, len(order)), k)):
        group, _, leaf = key.partition(".")
        groups.setdefault(group, []).append(f"  {leaf}: {yaml_scalar(values[key])}")
    for group in sorted(groups, key=lambda g: GROUPS.index(g) if g in GROUPS else len(GROUPS)):
        lines.append(f"\n{group}:")
        lines.extend(groups[group])
    return "\n".join(lines) + "\n"


def write_atomic(path: Path, text: str, *, private: bool = False) -> None:
    """Write ``path`` whole or not at all (code review #12): a temp file beside it — ``0600`` from the first byte —
    fsynced, then ``os.replace``. A secret file ends ``0600``; another keeps its mode (``0644`` when new)."""
    path = Path(path)
    mode = 0o600 if private else (stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_private(path: Path, text: str) -> None:
    """A secret file: written atomically, ``0600`` from the first byte (no window where it is world-readable)."""
    write_atomic(path, text, private=True)


# --- lumi-cli config: one setting at a time --------------------------------------------------------------------


def lookup(name: str) -> Setting | None:
    """A setting by its YAML key (``memory.window``) or its env name (``LUMI_MEMORY_WINDOW``)."""
    return BY_KEY.get(name) or BY_ALIAS.get(name) or _BY_OLD.get(name)


def parse_cli_value(s: Setting, text: str) -> Any:
    """A value typed on the command line → its YAML value: numbers and booleans parsed as YAML (``on`` → true),
    text kept verbatim (a voice id ``007`` stays ``007``), a list as ``a,b`` or ``[a, b]``."""
    import yaml

    raw = text.strip()
    if s.type in ("bool", "int", "float", "probability"):
        try:
            return yaml.safe_load(raw)
        except yaml.YAMLError:
            return raw
    if s.type in ("list", "ids"):
        if raw.startswith("["):
            try:
                return yaml.safe_load(raw)
            except yaml.YAMLError:
                return raw
        return from_env(s, raw)
    return raw


def _group_block(lines: list[str], group: str) -> tuple[int, int, int] | None:
    """``(header, end, last member)`` line indexes of ``group:``'s block, or ``None`` when there is none."""
    header = next((i for i, line in enumerate(lines) if re.match(rf"^{re.escape(group)}:\s*(#.*)?$", line)), None)
    if header is None:
        return None
    end, last = header + 1, header
    while end < len(lines):
        line = lines[end]
        if line.startswith((" ", "\t")) and line.strip():
            last = end
        elif line.strip() and not line.startswith("#"):
            break  # the next top-level key
        end += 1
    return header, end, last


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def set_in_text(text: str, key: str, value: Any) -> str:
    """``text`` with ``key`` set to ``value`` — only that key's line changes (its old block value, e.g. a ``- item``
    list, replaced), or one line is added to its group (at the group's own indentation), or the group is appended;
    every other line and comment stays. Verified by re-parsing: unless ``key`` now holds ``value`` and every other
    setting and section is exactly as before, it refuses (a :class:`SettingsError`) — it never rewrites a file
    (code review #2)."""
    group, leaf = key.split(".", 1)
    scalar = yaml_scalar(value)
    lines = text.splitlines()
    block = _group_block(lines, group)
    if block is None and not any(re.match(rf"^{re.escape(group)}:", line) for line in lines):
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", f"{group}:", f"  {leaf}: {scalar}"]
    elif block is not None:
        header, end, last = block
        members = [i for i in range(header + 1, end) if lines[i].strip() and not lines[i].lstrip().startswith("#")]
        indent = " " * (_indent(lines[members[0]]) if members else 2)
        for i in range(header + 1, end):
            if m := re.match(rf"^(\s+){re.escape(leaf)}:(\s|$)", lines[i]):
                j = i + 1  # the old value's own lines (a block list / mapping under the key) go with it
                while j < end and (not lines[j].strip() or _indent(lines[j]) > len(m.group(1))):
                    j += 1
                while j > i + 1 and not lines[j - 1].strip():
                    j -= 1  # keep the blank lines that separate it from what follows
                lines[i:j] = [f"{m.group(1)}{leaf}: {scalar}"]
                break
        else:
            lines.insert(last + 1, f"{indent}{leaf}: {scalar}")
    new = "\n".join(lines) + "\n"
    before = parse_yaml(text, "the file") or {}
    try:
        after = parse_yaml(new, "the edited file") or {}
    except SettingsError:
        after = None
    if isinstance(before, dict) and isinstance(after, dict):
        want = flatten({k: v for k, v in before.items() if k not in STRUCTURED}) | {key: value}
        got = flatten({k: v for k, v in after.items() if k not in STRUCTURED})
        if got == want and all(before.get(sec) == after.get(sec) for sec in STRUCTURED):
            return new
    raise SettingsError(f"lumi-cli config set can't change {key} in this file line by line (its layout is unusual — "
                        "an inline group, a duplicate key, mixed indentation); nothing was written — edit it by hand")


def set_value(folder: Path, name: str, text: str) -> tuple[Setting, Path]:
    """Validate ``text`` for setting ``name`` and write it into the right file of ``folder`` (``secrets.yaml`` —
    kept ``0600`` — for a secret). Raises :class:`SettingsError` naming the key and the allowed values; on a refusal
    nothing is written."""
    s = lookup(name)
    if s is None:
        raise SettingsError(f"unknown setting '{name}' — see settings.example.yaml / secrets.example.yaml")
    value = parse_cli_value(s, text)
    if value is None or to_env(s, value) is None:
        raise SettingsError(f"{s.key} = {_shown(s, text)} is not valid — allowed: {_allowed(s)}")
    path = Path(folder) / (SECRETS_FILE if s.secret else SETTINGS_FILE)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if not old:
        old = (f"# Lumi {'secrets' if s.secret else 'settings'} — written by `lumi-cli config set`. Every setting is "
               "described in settings.example.yaml / secrets.example.yaml.\n")
    new = set_in_text(old, s.key, value)
    write_atomic(path, new, private=s.secret)
    return s, path


def check_texts(settings_text: str, secrets_text: str) -> tuple[list[str], list[str]]:
    """``(problems, warnings)`` for a ``settings.yaml`` + ``secrets.yaml`` pair given as text, before it is written —
    the same check an instance runs at start (v2.11: the admin panel refuses what would stop it). A problem names
    a secret's key, never its value."""
    problems: list[str] = []
    warnings: list[str] = []
    for text, name, secret in ((settings_text, SETTINGS_FILE, False), (secrets_text, SECRETS_FILE, True)):
        try:
            _, p, w = _file_layer(Path(name), secret=secret, sections=None if secret else {}, text=text)
        except SettingsError as exc:
            p, w = [str(exc).removeprefix("Lumi settings — ")], []
        problems += p
        warnings += w
    return problems, warnings


def last4(value: str | None) -> str:
    """What a write-only secret shows of itself: its last four characters — only when the value is long enough
    (12+) that four don't give most of it away; else nothing."""
    return value[-4:] if isinstance(value, str) and len(value) >= 12 else ""


def unset_in_text(text: str, key: str) -> str:
    """``text`` without ``key`` (back to its default): its line — with a block value's own lines — is removed, and
    its group header too when nothing (not even a comment) is left under it; every other line and comment stays.
    A key the file doesn't set returns ``text`` unchanged. Verified by re-parsing like :func:`set_in_text`: unless
    ``key`` is gone and everything else is exactly as before, it refuses (a :class:`SettingsError`)."""
    group, leaf = key.split(".", 1)
    lines = text.splitlines()
    block = _group_block(lines, group)
    if block is not None:
        header, end, _ = block
        for i in range(header + 1, end):
            if m := re.match(rf"^(\s+){re.escape(leaf)}:(\s|$)", lines[i]):
                j = i + 1
                while j < end and (not lines[j].strip() or _indent(lines[j]) > len(m.group(1))):
                    j += 1
                while j > i + 1 and not lines[j - 1].strip():
                    j -= 1
                del lines[i:j]
                header, end, _ = _group_block(lines, group) or (header, header + 1, header)
                if all(not lines[k].strip() for k in range(header + 1, end)):
                    del lines[header:end]
                    while lines and not lines[-1].strip():
                        lines.pop()
                break
    new = "\n".join(lines) + "\n" if lines else ""
    before = parse_yaml(text, "the file") or {}
    try:
        after = parse_yaml(new, "the edited file") or {}
    except SettingsError:
        after = None
    if isinstance(before, dict) and isinstance(after, dict):
        want = flatten({k: v for k, v in before.items() if k not in STRUCTURED})
        want.pop(key, None)
        got = flatten({k: v for k, v in after.items() if k not in STRUCTURED})
        if got == want and all(before.get(sec) == after.get(sec) for sec in STRUCTURED):
            return new if want != flatten({k: v for k, v in before.items() if k not in STRUCTURED}) else text
    raise SettingsError(f"can't remove {key} from this file line by line (its layout is unusual — an inline group, "
                        "a duplicate key, mixed indentation); nothing was written — edit it by hand")


def check_folder(folder: Path) -> tuple[list[str], list[str]]:
    """``(problems, warnings)`` for a settings folder — what ``lumi-cli config validate`` reports."""
    try:
        res = resolve({}, folder=Path(folder))
    except SettingsError as exc:
        lines = str(exc).splitlines()
        return [line.strip().removeprefix("• ") for line in lines[1:]] or lines, []
    return [], res.warnings
