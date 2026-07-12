"""The core turn — the single ``reply(...)`` contract every interface calls.

``Core.reply(user_text, session)`` ties **canon + memory + LLMClient +
Repository** into one turn: assemble the system prompt and the session's history,
call the model through the :class:`~core.llm.LLMClient` seam, persist the user
and Лілі messages, and return the reply. No interface logic lives here.

The core is **user-scoped** (v0.2): it carries an active ``user_id`` (default
``owner``) and every record it writes is keyed by it. It holds the **canon** and
builds the system prompt **per turn** (``_system_prompt``) — the seam LUMI-011
extends to fold in the user's summaries + facts. v0.1 returns ``str``; v0.3 turns
the return into a validated ``EmotionState``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from core.biorhythm import (
    Biorhythms,
    format_biorhythms,
    parse_birth_date,
)
from core.biorhythm import (
    biorhythms as biorhythm_cycles,
)
from core.chunking import chunk_text
from core.clock import Clock, format_date, format_stamp, strip_leading_stamp, system_clock
from core.closeness import (
    ClosenessTuning,
    RelationRead,
    closeness_block,
    level_name,
    load_levels,
    mood_shift,
    naive_level,
    shifted_level,
    update_closeness,
    validate_relation,
)
from core.config import (
    DEFAULT_COMPACTION_BATCH,
    DEFAULT_MEMORY_WINDOW,
    Config,
    ModelProfile,
    load_config,
)
from core.cycle import CyclePhase, format_cycle, menstrual_phase, parse_cycle_anchor
from core.deidentify import deidentify, personal_terms, topic_words
from core.embedder import Embedder
from core.emotion import DEFAULT_EMOTION, DEFAULT_INTENSITY, Emotion, EmotionState, validate
from core.images import is_image_block
from core.llm import (
    LLMClient,
    LLMError,
    Message,
    ResponseStats,
    build_llm,
    is_trusted_text,
    trusted_text,
)
from core.memory import (
    DAY_DAYS,
    MAX_DAY_ROWS,
    MAX_WEEK_ROWS,
    RECENT_SUMMARIES,
    SESSION_DAYS,
    SESSION_FORMAT_DEFAULT,
    WEEK_DAYS,
    clamp_rows,
    compaction_plan,
    core_select_request,
    day_summary_request,
    digest_request,
    facts_digest_request,
    facts_request,
    is_pinned_fact,
    parse_facts,
    parse_facts_with_core,
    parse_summary,
    session_gist,
    summary_request,
    trim_history,
    week_summary_request,
)
from core.mood import (
    MoodState,
    load_natal,
    mood_request,
    split_resolution,
    split_theme,
    strip_theme,
)
from core.moves import arbiter_dynamics, validate_move
from core.nudge import should_nudge
from core.placeholders import resolve_placeholders
from core.prompt import (
    REASONING_DIRECTIVE,
    build_system_prompt,
    load_canon,
    load_inner_voice,
    split_emotion,
    split_move,
    split_reasoning,
    split_style,
)
from core.repository import (
    Closeness,
    DaySummary,
    FactsDigest,
    LongTermFact,
    Repository,
    Session,
    SessionDigest,
    ShortSummary,
    Thought,
    VectorRecord,
    WeekSummary,
    chunk_msg_id,
    fact_vector_id,
    make_message,
    make_thought,
    now_iso,
    vector_msg_id,
)
from core.styles import load_meta_descriptions, load_meta_styles, load_styles
from core.thoughts import (
    REGISTRY,
    THOUGHT_FULL_HEADER,
    THOUGHT_FULL_HEADER_FREEFORM,
    THOUGHTS_CAP,
    THOUGHTS_INTERVAL_S,
    THOUGHTS_MAX_LINES,
    THOUGHTS_SPOKEN_RATIO,
    THOUGHTS_WINDOW_H,
    directive_mode,
    parse_directive,
    parse_thought,
    should_graduate,
    thought_full_seed,
    thought_request,
    thought_tool_hint,
    thoughts_diary_block,
)
from core.user import DEFAULT_USER_ID
from core.worldcontext import WorldContext, ambient_line

# The locked base-9 emotion set, used to validate a generated thought's emotion (v0.12).
_EMOTION_VALUES = frozenset(e.value for e in Emotion)

# The full daily mood reading is logged here (only the resolution rides in the prompt).
_mood_log = logging.getLogger("lumi.mood")

# Every recorded thought is logged here (the v0.3 logged tier) — never persisted to long-term memory.
_thoughts_log = logging.getLogger("lumi.thoughts")

# Semantic-recall indexing is best-effort; failures are logged here, never raised (v0.16).
_recall_log = logging.getLogger("lumi.recall")

# Per-session usage ledger + cost report is best-effort; failures logged here, never raised.
_usage_log = logging.getLogger("lumi.usage")
_core_log = logging.getLogger("lumi.core")  # composition-root notices (e.g. inner-voice fallback, v0.38)
_think_log = logging.getLogger("lumi.think")  # v0.38: the monologue's logged tier (never persisted)

# Cap text length before embedding: the model reads only ~512 tokens, and a huge message (e.g. a
# pasted book chapter — 100k+ chars) tokenizes pathologically slowly and can hang the embedder.
# Truncate for embedding AND for the stored display text; the content-addressed msg_id stays on the
# FULL text so has_vector / backfill idempotency is unaffected.
_MAX_EMBED_CHARS = 2000

# Per-line snippet length in the v0.17 recall block — keep each recalled moment compact (a jog of
# memory, not the whole message); the block's total is bounded by `rag_max_chars`.
_RAG_SNIPPET_CHARS = 240


def _snippet(text: str, limit: int = _RAG_SNIPPET_CHARS) -> str:
    """A compact one-line form of a message for the recall block (collapse newlines, truncate)."""
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text

# Map stored roles → the model's chat roles (Лілі speaks as the assistant).
_ROLE_TO_LLM = {"user": "user", "lili": "assistant"}

# v0.33: external thought-tools whose query/prompt arg must be de-identified before it leaves (LUMI-128).
_EXTERNAL_QUERY_ARG = {
    "wiki_search": "query", "news_search": "query", "web_lookup": "query", "generate_image": "prompt",
}


def _tool_trace_repr(result: object) -> str:
    """A short, string trace line for a tool result — an image block becomes a marker (not its base64)."""
    if is_image_block(result):
        n_bytes = len(result.get("data", "")) * 3 // 4  # base64 → ~bytes
        return f"🖼 image {result.get('media_type', '?')} (~{n_bytes} bytes)"
    if is_trusted_text(result):  # v0.31 recall: a trusted recollection — a marker, not its full text
        return f"🧠 recall ({len(result.get('text', ''))} chars)"
    return str(result)


def _monday_of(date_str: str) -> str:
    """The Monday ("YYYY-MM-DD") of the Mon–Sun week containing ``date_str`` (date-based recall weeks)."""
    d = date.fromisoformat(date_str)
    return (d - timedelta(days=d.weekday())).isoformat()


@dataclass(frozen=True)
class MemoryView:
    """A read-only snapshot of a user's relationship memory (for the TUI)."""

    summaries: list[str]
    facts: list[str]


@dataclass(frozen=True)
class DirectiveOutcome:
    """The result of routing a ``%directive`` input (v0.12).

    ``is_directive`` is ``False`` when the input was **not** a known ``%directive`` (the client
    treats it as a normal chat message). When ``True``: ``mode`` is ``"silent"``/``"open"`` and
    ``thought`` is the recorded :class:`Thought` (``None`` if the model produced nothing). The
    client shows the raw thought (``💭``) only for ``mode == "open"``.
    """

    is_directive: bool
    mode: str | None = None
    thought: Thought | None = None
    saved_to: str | None = None  # v0.33: the sandbox path the thought was ALSO saved to (notes/<date>.md, …)


@dataclass
class UsageTotals:
    """Running totals across the session (for the TUI status line).

    Counts **every** model call — user replies AND background calls (proactive thinks, summaries,
    facts, mood, compaction) — so the line shows real token consumption. ``turns`` and ``latency_ms``
    track **user turns only** (so the avg stays per-reply); the token fields include everything.
    """

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0

    @property
    def avg_latency_ms(self) -> int:
        return self.latency_ms // self.turns if self.turns else 0

    @property
    def total_tokens(self) -> int:
        """All tokens processed (fresh input + cache read + cache write + output)."""
        return (
            self.input_tokens + self.output_tokens
            + self.cache_read_tokens + self.cache_write_tokens
        )


RECALL_TOOLS: list[dict] = [
    {
        "name": "recall",
        "description": (
            "Шукає у ТВОЇЙ власній памʼяті (минулі розмови) за змістовим запитом і повертає кілька "
            "релевантних моментів (уривок + коли це було). Це ТВІЙ спогад, не зовнішнє джерело. "
            "Корисно, коли треба саме те, чого людина зараз НЕ написала прямо, або щоб уточнити пошук "
            "кількома кроками. Запит може відрізнятися від поточного повідомлення."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Змістовий запит для пошуку в памʼяті."},
                "k": {"type": "integer", "description": "Скільки моментів повернути (необовʼязково)."},
                "after": {"type": "string",
                          "description": "Лише з цієї дати й пізніше (РРРР-ММ-ДД, необовʼязково)."},
                "before": {"type": "string",
                           "description": "Лише до цієї дати, не включно (РРРР-ММ-ДД, необовʼязково)."},
                "scope": {"type": "string", "enum": ["messages", "facts", "all"],
                          "description": "Де шукати: messages (минулі розмови, типово) / facts (стійкі факти про людину) / all."},
            },
            "required": ["query"],
        },
    },
]
RECALL_TOOL_NAMES = frozenset(t["name"] for t in RECALL_TOOLS)


DATE_TOOLS: list[dict] = [
    {
        "name": "messages_on",
        "description": (
            "Повертає ТВОЇ дослівні повідомлення (твої й людини) за конкретну ДАТУ (РРРР-ММ-ДД) — "
            "сирий журнал того дня, БЕЗ змістового пошуку. Це ТВОЯ памʼять; для пошуку за змістом — recall."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "Дата (РРРР-ММ-ДД)."}},
            "required": ["date"],
        },
    },
    {
        "name": "messages_between",
        "description": (
            "Повертає ТВОЇ дослівні повідомлення за діапазон дат [start, end] включно (РРРР-ММ-ДД) — "
            "сирий журнал за кілька днів. Діапазон обмежений кількома днями."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Початкова дата (РРРР-ММ-ДД)."},
                "end": {"type": "string", "description": "Кінцева дата (РРРР-ММ-ДД, включно)."},
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "message_context",
        "description": (
            "Повертає КОНКРЕТНЕ повідомлення — за його id АБО за позначкою часу ts — РАЗОМ із K "
            "повідомленнями до і після нього в тій самій розмові, щоб побачити той момент у контексті. "
            "І id (#xxxxxxxx), і час є у результаті recall біля знайденого рядка. Це ТВОЯ памʼять."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "msg_id": {"type": "string",
                           "description": "Id повідомлення (повний або префікс, напр. #xxxxxxxx з recall)."},
                "ts": {"type": "string",
                       "description": "Або позначка часу — префікс РРРР-ММ-ДД чи РРРР-ММ-ДДTГГ:ХХ."},
                "k": {"type": "integer", "description": "Скільки повідомлень до і після (за замовч. 3)."},
            },
        },
    },
]
DATE_TOOL_NAMES = frozenset(t["name"] for t in DATE_TOOLS)


def _is_ymd(s: str) -> bool:
    """True if ``s`` is a valid ``YYYY-MM-DD`` date."""
    try:
        date.fromisoformat((s or "").strip())
        return True
    except (ValueError, TypeError):
        return False


# v0.41 LUMI-163: bare-full-id → provider inference for /model (checked after aliases + provider:id).
# Ordered; first matching prefix wins. An unknown prefix keeps the clear reject (never guesses).
_MODEL_ID_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("gemini-", "gemini"),
    ("deepseek-", "deepseek"),
)


class Core:
    """Лілі's interface-independent, user-scoped turn engine."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        repository: Repository,
        canon: str,
        model: str,
        provider: str = "",
        llm_factory: Callable[[str, str], LLMClient] | None = None,  # v0.37: (provider, model) → LLMClient
        model_aliases: dict[str, tuple[str, str]] | None = None,     # v0.37: /model aliases (from config)
        model_profiles: dict[str, ModelProfile] | None = None,        # v0.41: /model-set tier sets (from config)
        active_profile: str = "",      # v0.41 LUMI-164: the profile activated at startup ("" → env mode)
        model_think: str = "",         # v0.40: route kind="think" to this Claude tier (unset → model)
        model_mood: str = "",          # v0.40: route kind="mood" (unset → model)
        model_housekeeping: str = "",  # v0.40: route session-start/-close/compaction (unset → model)
        reasoning_directive: str = REASONING_DIRECTIVE,  # v0.38: the think-phase instruction (inner_voice → here)
        think_show: str = "debug",  # v0.38: monologue surfacing — debug / open / off (logged, never persisted)
        user_id: str = DEFAULT_USER_ID,
        memory_window: int = DEFAULT_MEMORY_WINDOW,
        compaction_batch: int = DEFAULT_COMPACTION_BATCH,
        recent_summaries: int = RECENT_SUMMARIES,
        session_days: int = SESSION_DAYS,
        session_detail_n: int | None = None,
        session_format: str = SESSION_FORMAT_DEFAULT,
        day_days: int = DAY_DAYS,
        week_days: int = WEEK_DAYS,
        max_day_rows: int = MAX_DAY_ROWS,
        max_week_rows: int = MAX_WEEK_ROWS,
        memory_index: bool = False,
        styles: dict[str, str] | None = None,
        meta_styles: dict[str, list[str]] | None = None,
        meta_descriptions: dict[str, str] | None = None,
        closeness_levels: dict[int, tuple[str, str]] | None = None,
        closeness_enabled: bool = True,
        closeness_tuning: ClosenessTuning | None = None,
        moves_enabled: bool = False,
        facts_digest_enabled: bool = False,
        facts_digest_max: int = 150,
        facts_digest_refresh: int = 20,
        facts_core_max: int = 0,
        facts_core_only: bool = False,
        recall_scope: str = "messages",
        prompt_cache: bool = False,
        embedder: Embedder | None = None,
        recall_enabled: bool = False,
        recall_k: int = 5,
        recall_tool_enabled: bool = False,
        recall_tool_k: int = 5,
        recall_tool_max_calls: int = 3,
        date_tool_enabled: bool = False,
        date_tool_max_chars: int = 4000,
        date_tool_max_days: int = 14,
        date_tool_max_calls: int = 3,
        recall_backfill_max: int = 500,
        embed_max_chars: int = _MAX_EMBED_CHARS,
        embed_model: str = "",
        rag_enabled: bool = False,
        rag_k: int = 4,
        rag_floor: float = 0.3,
        rag_max_chars: int = 1200,
        rag_w: int = 2,
        rag_snippet_chars: int = _RAG_SNIPPET_CHARS,
        facts_rag: bool = False,
        facts_rag_k: int = 4,
        rag_chunk: bool = False,
        rag_chunk_chars: int = 800,
        rag_chunk_overlap: int = 120,
        rag_chunk_threshold: int = 1200,
        rag_chunk_w: int = 1,
        clock: Clock = system_clock,
        natal: str = "",
        mood_enabled: bool = True,
        mood_log_path: Path | None = None,
        theme_descriptions: dict[str, str] | None = None,
        default_theme: str | None = None,
        biorhythms_enabled: bool = True,
        cycle_enabled: bool = True,
        face_signal: Path | None = None,
        thoughts_enabled: bool = True,
        thoughts_window_h: int = THOUGHTS_WINDOW_H,
        thoughts_max_lines: int = THOUGHTS_MAX_LINES,
        thoughts_interval_s: int = THOUGHTS_INTERVAL_S,
        thoughts_cap: int = THOUGHTS_CAP,
        thoughts_spoken_ratio: float = THOUGHTS_SPOKEN_RATIO,
        thoughts_show: str = "hidden",
        thoughts_context: str = "lean",
        thought_tools_enabled: bool = False,
        thought_journal: bool = False,
        thought_wiki: bool = False,
        thought_news: bool = False,
        thought_web: bool = False,
        thought_prompt: bool = False,
        thought_image: bool = False,
        thought_imagine_cap: int = 1,
        quiet_hours: tuple[int, int] | None = None,
        thoughts_quiet_hours: tuple[int, int] | None = None,
        usage_ledger_path: Path | None = None,
        usage_report_path: Path | None = None,
        usage_cache_ttl: str = "5m",
        file_tool_enabled: bool = False,
        files_dir: Path | None = None,
        file_read_lines: int = 200,
        file_read_max_total: int = 2000,
        file_read_max_chars: int = 8000,
        file_find_max: int = 50,
        file_write_max: int = 65536,
        file_copy_max: int = 5 * 1024 * 1024,
        file_search_max_files: int = 200,
        file_search_max_lines: int = 100,
        file_search_max_chars: int = 4000,
        file_around_max_k: int = 50,
        file_date_max_days: int = 366,
        tool_max_steps: int = 8,
        file_tool_trace: bool = False,
        wiki_enabled: bool = False,
        wiki_lang: str = "uk,en",
        wiki_base_url: str = "",
        wiki_max_chars: int = 1500,
        wiki_max_calls: int = 4,
        wiki_http_get: Callable[[str], str] | None = None,  # injected for tests; None → real urllib
        news_enabled: bool = False,
        news_api_key: str = "",
        news_api_url: str = "https://content.guardianapis.com",
        news_sections: str = "",
        news_max_results: int = 8,
        news_max_chars: int = 3000,
        news_max_calls: int = 4,
        news_days: int = 7,
        news_http_get: Callable[[str], str] | None = None,  # injected for tests; None → real urllib
        web_lookup_enabled: bool = False,
        web_lookup_model: str = "gemini-2.5-flash",
        web_lookup_max_calls: int = 2,
        web_lookup_max_chars: int = 2000,
        web_search: Callable[..., str] | None = None,  # injected GeminiSearch for tests; None → real Gemini
        journal_enabled: bool = False,
        journal_dir: str | Path = ".lumi/journal",  # the DEDICATED journal root (per-user subdirs), outside the file sandbox
        journal_max_chars: int = 4000,
        image_enabled: bool = False,
        vision_max: int = 4,
        image_max_bytes: int = 5_242_880,
        image_model: str = "gemini-2.5-flash-image",
        image_size: int = 768,
        image_max_gen: int = 2,
        image_show: str = "path,viewer,telegram",
        image_signal_path: Path | None = None,
        image_gen: Callable[..., bytes] | None = None,  # injected for tests; None → real Gemini
        telegram_sink: Callable[[str, str], None] | None = None,  # v0.24: TUI supplies it; None → not connected
        tool_log_path: Path | None = None,
        cache_log_path: Path | None = None,
        cache_report_path: Path | None = None,
        cache_monitor: bool = False,
    ) -> None:
        self._llm = llm
        self._repo = repository
        self._canon = canon
        # v0.38 Inner Voice: the think-phase instruction appended to the canon — the generic
        # REASONING_DIRECTIVE by default, or the authored core/inner_voice.md when LUMI_INNER_VOICE is on.
        self._reasoning_directive = reasoning_directive
        # v0.38: how the monologue is surfaced (debug/open/off); validated, default debug.
        self._think_show = think_show if think_show in ("debug", "open", "off") else "debug"
        self._model = model
        # v0.40 LUMI-155: per-operation tier routing — Claude ids, applied via _model_for(kind);
        # each unset ("") → that op runs on self._model.
        self._model_think = model_think.strip()
        self._model_mood = model_mood.strip()
        self._model_housekeeping = model_housekeeping.strip()
        # v0.37 LUMI-148: runtime `/model` engine toggle — the active provider, a (provider, model) →
        # LLMClient factory (rebuilds from the loaded config keys), and the configured aliases.
        self._active_provider = provider
        self._llm_factory = llm_factory
        self._model_aliases = {k.lower(): v for k, v in (model_aliases or {}).items()}
        # v0.41 LUMI-161: named per-provider tier sets + the active profile (None → raw env-var mode).
        self._model_profiles = {k.lower(): v for k, v in (model_profiles or {}).items()}
        # LUMI-164: a startup profile (already applied to provider/model/tiers by load_config) marks
        # the boot exactly like a /model-set — the status bar + the routing guard see it.
        boot = active_profile.strip().lower()
        self._active_profile: str | None = boot if boot in self._model_profiles else None
        self._user_id = user_id
        self._clock = clock  # injectable: real time by default, fixed in tests
        # Ambient "now / here" snapshot (v0.4), set by the client at startup/refresh.
        self._world: WorldContext | None = None
        # v0.6 mood of the day: the fixed natal seed + the cached daily MoodState.
        self._natal = natal.strip()
        self._mood_enabled = mood_enabled
        self._mood_log_path = mood_log_path  # the full reading is appended here (readable)
        self._mood: MoodState | None = None
        # v0.11 face themes: the mood picks one per day; the signal carries it. Off → default/None.
        self._theme_descriptions = theme_descriptions or {}
        self._default_theme = default_theme
        self._force_theme: str | None = None  # /theme override — beats the mood's pick (session)
        self._last_emotion = DEFAULT_EMOTION.value  # last face state (re-emitted on a theme change)
        self._last_intensity = DEFAULT_INTENSITY
        # v0.8 biorhythms: computed cycles merged into the daily mood + the cached state.
        self._biorhythms_enabled = biorhythms_enabled
        self._biorhythms: Biorhythms | None = None
        # v0.8 hormonal cycle: the phased body rhythm merged into the mood + the cached phase.
        self._cycle_enabled = cycle_enabled
        self._cycle: CyclePhase | None = None
        # v0.7 emotion-face signal: a one-word file the local viewer polls each turn.
        self._face_signal = face_signal
        # v0.12 thought-stream: her mind acts on its own (%think/%wonder) into the global diary;
        # the last `thoughts_window_h` hours feed back into the prompt.
        self._thoughts_enabled = thoughts_enabled
        self._thoughts_window_h = thoughts_window_h
        self._thoughts_max_lines = thoughts_max_lines
        # v0.12 proactive nudge: idle interval, a per-session cap, and the spoken fraction.
        self._thoughts_interval_s = thoughts_interval_s
        self._thoughts_cap = thoughts_cap
        self._thoughts_spoken_ratio = thoughts_spoken_ratio
        self._thoughts_show = thoughts_show  # hidden (default) / admin / off — the /thoughts policy
        self._thoughts_context = thoughts_context  # lean (seeds) / full (the whole reply backdrop)
        self._thought_tools_enabled = thought_tools_enabled  # v0.33 master gate for tool-using thoughts
        self._last_saved_to: str | None = None  # v0.33: the sink path the last think() saved to
        self._thought_journal = thought_journal  # v0.33 %journal per-family flag
        self._thought_wiki = thought_wiki  # v0.33 %lookup/%learn per-family flag
        self._thought_news = thought_news  # v0.33 %catchup/%brief per-family flag
        self._thought_web = thought_web  # v0.33 %search/%events per-family flag
        self._thought_prompt = thought_prompt  # v0.33 %prompt per-family flag (owner-only)
        self._thought_image = thought_image  # v0.33 %gaze/%imagine/%share per-family flag
        self._thought_imagine_cap = max(1, thought_imagine_cap)  # v0.33 %imagine paid sub-cap
        self._quiet_hours = quiet_hours
        # The proactive-think's quiet window is independent of the nudge's (falls back to it in config).
        self._thoughts_quiet_hours = thoughts_quiet_hours
        # Per-session usage ledger + cost report (written at session close → .lumi/). Off when paths are None.
        self._usage_ledger_path = usage_ledger_path
        self._usage_report_path = usage_report_path
        self._usage_cache_ttl = usage_cache_ttl
        self._usage_base = (0, 0, 0, 0, 0)  # totals snapshot at the last session boundary (zeros at start)
        # v0.19 local file tool (off by default) — sandboxed per-user under files_dir/<user_id>.
        self._file_tool_enabled = file_tool_enabled
        self._files_dir = files_dir
        self._file_read_lines = file_read_lines
        self._file_read_max_total = file_read_max_total
        self._file_read_max_chars = file_read_max_chars  # v0.40: per-result char cap (read_file/read_around)
        self._file_find_max = file_find_max
        self._file_write_max = file_write_max
        self._file_copy_max = file_copy_max  # v0.29 copy_file source-size cap
        self._file_search_max_files = file_search_max_files  # v0.32 search_files caps
        self._file_search_max_lines = file_search_max_lines
        self._file_search_max_chars = file_search_max_chars
        self._file_around_max_k = file_around_max_k  # v0.32 read_around K cap
        self._file_date_max_days = file_date_max_days  # v0.32 list_files date-range cap
        self._tool_max_steps = tool_max_steps
        # v0.19 tool trace: record the file tools used this turn (for the TUI trace + .lumi/tool-log.jsonl).
        self._file_tool_trace = file_tool_trace
        # v0.21 Wikipedia tool — custom wiki_search/wiki_read on the same bounded loop (off by default).
        self._wiki_enabled = wiki_enabled
        self._wiki_lang = wiki_lang
        self._wiki_base_url = wiki_base_url
        self._wiki_max_chars = wiki_max_chars
        self._wiki_max_calls = wiki_max_calls
        self._wiki_http_get = wiki_http_get
        # v0.25 Guardian news tool — custom news_search/news_read on the same bounded loop (off by default).
        self._news_enabled = news_enabled
        self._news_api_key = news_api_key
        self._news_api_url = news_api_url
        self._news_sections = news_sections
        self._news_max_results = news_max_results
        self._news_max_chars = news_max_chars
        self._news_max_calls = news_max_calls
        self._news_days = news_days
        self._news_http_get = news_http_get
        # v0.27 web lookup — custom web_lookup (Gemini grounded search) on the same bounded loop (off by default).
        self._web_lookup_enabled = web_lookup_enabled
        self._web_lookup_model = web_lookup_model
        self._web_lookup_max_calls = web_lookup_max_calls
        self._web_lookup_max_chars = web_lookup_max_chars
        self._web_search = web_search  # injected GeminiSearch (tests); None → the real Gemini caller
        # v0.28 journal tool — her day-summary diary on the same bounded loop, in her per-user sandbox (off by default).
        self._journal_enabled = journal_enabled
        self._journal_dir = journal_dir
        self._journal_max_chars = journal_max_chars
        # v0.22 vision: view_image (sandbox) + shared-image input; off by default.
        self._image_enabled = image_enabled
        self._vision_max = vision_max
        self._image_max_bytes = image_max_bytes
        # v0.23 generation: generate_image (text → PNG); image_gen injected for tests (None → real Gemini).
        self._image_model = image_model
        self._image_size = image_size
        self._image_max_gen = image_max_gen
        self._image_show = image_show
        self._image_signal_path = image_signal_path
        self._image_gen = image_gen
        # v0.24 send_image: the injected sink the TUI supplies (it is the single outbox writer). The core
        # never imports the bridge or writes the outbox — it only calls this callable. None → not connected.
        self._telegram_sink = telegram_sink
        self._tool_log_path = tool_log_path
        self.last_tool_calls: list[tuple[str, dict, str]] = []  # (name, input, result) — reset each turn
        # Per-call prompt-cache monitor (off by default): log each model call's cache behaviour by
        # channel + attribute the writes (first/expired/changed); render the report at session close.
        self._cache_log_path = cache_log_path
        self._cache_report_path = cache_report_path
        self._cache_monitor = cache_monitor
        self._cache_last_ts: dict[str, datetime] = {}  # last call time per channel (gap / expiry)
        self._active_session_id = ""  # the session each cache event is stamped with (per-session breakdown)
        self._active_cache_prefix: str | None = None  # the cached prefix of the current model call
        self._cache_prefix_sig: dict[str, dict[str, str]] = {}  # cache-group → last prefix fingerprint
        self._think_count = 0  # proactive thinks this session (reset in start_session)
        self._memory_window = memory_window
        self._compaction_batch = compaction_batch
        # date-based recall date-based short-memory windows (config/env-tunable): session/day/week spans + caps.
        self._recent_summaries = recent_summaries  # /memory quick-view count
        self._session_days = session_days  # tier 1: detailed session summaries window
        self._session_detail_n = session_detail_n  # v0.35: how many recent sessions to add (None=all, 0=none, N=last N)
        self._session_format = session_format      # v0.35: "summary" (full) or "gist" (one line) per added session
        self._day_days = day_days  # tier 2: per-day digests window
        self._week_days = week_days  # tier 3: per-week digests window
        self._max_day_rows = max_day_rows
        self._max_week_rows = max_week_rows
        self._memory_index = memory_index  # v0.34: day/week digests as a one-line dated index
        # Answer styles + meta-styles (presets → several base styles). Лілі picks her
        # own style each turn from this palette (preferring meta-styles) and declares
        # it; `/style <name>` sets a soft per-session *recommendation*, not a switch.
        self._styles = styles or {}
        self._meta = meta_styles or {}
        self._meta_desc = meta_descriptions or {}  # concise per-mega description for the palette
        # v0.10 closeness: authored level → (name, behavior) blocks, injected by active level.
        self._closeness_levels = closeness_levels or {}
        self._closeness_enabled = closeness_enabled
        self._closeness_tuning = closeness_tuning or ClosenessTuning()
        # v1.1: conversation moves — the declared `move` on set_state (off → byte-identical).
        self._moves_enabled = moves_enabled
        # v1.1 LUMI-177: this turn's dynamic arbiter lines (computed over the live window in
        # reply(); substituted into the think instruction's {move_rules} at prompt assembly).
        self._move_dynamics: str = ""
        # The level a fresh user (no record) sits at — derived from the configured baseline.
        self._default_level = naive_level(self._closeness_tuning.baseline)
        # Facts digest: a consolidated, compact view of the long-term facts injected instead of
        # all raw facts (rebuilt only when the facts grow by `refresh`; non-destructive).
        self._facts_digest_enabled = facts_digest_enabled
        self._facts_digest_max = facts_digest_max
        self._facts_digest_refresh = facts_digest_refresh
        # v0.36: the identity-core cap — re-flagged at session start (0 → the core lifecycle is off).
        self._facts_core_max = facts_core_max
        # v0.36: inject only the core facts (instead of the digest) — the tail moves to recall(scope=facts).
        self._facts_core_only = facts_core_only
        self._recall_scope = recall_scope if recall_scope in ("messages", "facts", "all") else "messages"
        self._prompt_cache = prompt_cache  # v0.15: pass the cache_prefix to the LLM on the reply turn
        # v0.16 semantic recall: embed every message into the per-user vector store (index on write
        # + lazy backfill). Best-effort — off, no embedder, or an embed error never blocks a turn.
        self._embedder = embedder
        self._recall_enabled = recall_enabled and embedder is not None
        self._recall_k = recall_k
        self._recall_tool_enabled = recall_tool_enabled and self._recall_enabled  # needs recall + embedder
        self._recall_tool_k = recall_tool_k
        self._recall_tool_max_calls = recall_tool_max_calls
        self._date_tool_enabled = date_tool_enabled  # v0.31 by-date message tool (reads the store directly)
        self._date_tool_max_chars = date_tool_max_chars
        self._date_tool_max_days = date_tool_max_days
        self._date_tool_max_calls = date_tool_max_calls
        self._turn_dedup_ids: set[str] = set()       # v0.31: window+RAG ids the recall tool dedups against
        self._turn_rag_anchor_ids: set[str] = set()  # the auto-RAG block's surfaced anchors this turn
        self._recall_backfill_max = recall_backfill_max
        self._embed_max_chars = embed_max_chars  # truncate a message to this before embedding
        self._embed_model = embed_model  # the active model — re-index if it changed (dim change)
        self._backfilled = False  # the one-time catch-up runs lazily, once
        # v0.17 automatic per-turn RAG: inject the query-relevant past into the reply. Needs the
        # recall infra (embedder + index); off → behaves like v0.16. Best-effort, never blocks a turn.
        self._rag_enabled = rag_enabled and (recall_enabled and embedder is not None)
        self._rag_k = rag_k
        self._rag_floor = rag_floor
        self._rag_max_chars = rag_max_chars
        self._rag_w = rag_w
        # v0.36: the per-turn fact-RAG push (# Релевантні факти — top-K relevant non-core facts).
        self._facts_rag = facts_rag
        self._facts_rag_k = facts_rag_k
        self._rag_snippet_chars = rag_snippet_chars  # per-line cap for recalled moments
        # v0.30 chunking: index a long message as several chunks (off → one vector per message).
        self._rag_chunk = rag_chunk
        self._rag_chunk_chars = rag_chunk_chars
        self._rag_chunk_overlap = rag_chunk_overlap
        self._rag_chunk_threshold = rag_chunk_threshold
        self._rag_chunk_w = rag_chunk_w
        # v0.17 context expansion: msg_id → (session_id, index) for this user, built lazily once
        # (no re-index needed); lets a hit be widened to its session neighbours.
        self._position_index: dict[str, tuple[str, int]] | None = None
        self._recommendation: list[str] = []  # the user's soft style suggestion (or none)
        self.last_style: str | None = None  # the style Лілі declared last turn (<style>…)
        # The validated EmotionState from the last turn (for a renderer / status line).
        self.last_emotion: EmotionState | None = None
        # The relational read of the user's last message (v0.10; internal, feeds closeness).
        self.last_relation: RelationRead = RelationRead()
        # v1.1: the declared conversation move of the last reply (internal; None when off/dropped).
        self.last_move: str | None = None
        # The model's visible thinking from the last turn (inline <think>, a public
        # structured summary, or a provider summary), for a client to render.
        self.last_thinking: str | None = None
        # Stats for the last reply + running totals, for the TUI status line.
        self.last_stats: ResponseStats | None = None
        self.totals = UsageTotals()
        # The exact prompt sent on the last turn, for inspection ({system, messages}).
        self.last_prompt: dict | None = None
        # How many messages the last turn folded into the session digest (0 if none).
        self.last_compaction: int = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        """The active provider/engine family — set at build, updated by :meth:`switch_model` (v0.37)."""
        return self._active_provider

    @property
    def think_show(self) -> str:
        """How the think monologue is surfaced — ``debug`` / ``open`` / ``off`` (v0.38). A client reads it
        to decide whether to render the Thinking box; ``off`` hides it entirely."""
        return self._think_show

    @property
    def model_aliases(self) -> dict[str, tuple[str, str]]:
        """The configured ``/model`` aliases (alias → (provider, model)); a copy (v0.37)."""
        return dict(self._model_aliases)

    def resolve_model_target(self, arg: str) -> tuple[str, str]:
        """Resolve a ``/model`` argument to ``(provider, model)``: a configured alias (case-insensitive),
        the explicit ``provider:model`` form, or (v0.41 LUMI-163) a **bare full model id** whose provider
        is inferred by prefix (``claude-*`` → anthropic, ``gpt-*``/``o1``/``o3``/``o4`` → openai,
        ``gemini-*`` → gemini, ``deepseek-*`` → deepseek). Raises :class:`ValueError` with a clear
        message on anything else."""
        token = (arg or "").strip()
        if not token:
            raise ValueError("No model given — try /model <alias>.")
        alias = self._model_aliases.get(token.lower())
        if alias is not None:
            return alias
        if ":" in token:  # explicit provider:model — a full id not in the alias list
            provider, model = token.split(":", 1)
            if provider.strip() and model.strip():
                return provider.strip().lower(), model.strip()
        for prefix, provider in _MODEL_ID_PREFIXES:  # v0.41: a bare full id → provider by prefix
            if token.lower().startswith(prefix):
                return provider, token
        known = ", ".join(sorted(self._model_aliases)) or "(none configured)"
        raise ValueError(
            f"Unknown model '{token}'. Try an alias ({known}), a full model id "
            "(claude-*/gpt-*/gemini-*/deepseek-*), or provider:model."
        )

    def switch_model(self, provider: str, model: str) -> None:
        """Swap the active engine at runtime — rebuild the :class:`LLMClient` from the (already-loaded)
        config keys and re-point the default/reply model (v0.37 LUMI-148).

        History is just messages, so the conversation continues; the new engine starts on a **cold
        cache** (one-off). The ``{reply, emotion, intensity}`` contract and per-user isolation are
        untouched (only the backend changes). Raises :class:`LLMError` if switching isn't configured or
        the provider/key is unavailable — the **old client stays in place** (the new one is assigned only
        after a successful build)."""
        if self._llm_factory is None:
            raise LLMError("Model switching isn't configured for this core.")
        new_llm = self._llm_factory(provider, model)  # may raise LLMError (unknown provider / missing key)
        self._llm = new_llm
        self._model = model
        self._active_provider = provider
        # v0.41: a reply-only swap leaves the tiers as they were, but the stack no longer matches a
        # named set — clear the profile mark (switch_profile re-sets it after this call).
        self._active_profile = None

    def switch_profile(self, name: str) -> None:
        """Swap the whole model stack to a named per-provider profile (v0.41 LUMI-161): rebuild the
        client for the profile's provider + reply model (via :meth:`switch_model`) **and** re-point the
        three v0.40 tier fields — in one step. **Atomic on failure**: ``switch_model`` builds the new
        client before assigning anything, so a raising factory leaves the old client *and* the old
        tiers untouched. Raises :class:`ValueError` on an unknown profile, :class:`LLMError` when the
        provider/key is unavailable."""
        key = (name or "").strip().lower()
        profile = self._model_profiles.get(key)
        if profile is None:
            known = ", ".join(sorted(self._model_profiles)) or "(none configured)"
            raise ValueError(f"Unknown profile '{name}'. Known: {known}.")
        self.switch_model(profile.provider, profile.reply)  # raises → nothing below runs
        self._model_think = profile.think
        self._model_mood = profile.mood
        self._model_housekeeping = profile.housekeeping
        self._active_profile = key

    @property
    def profile(self) -> str | None:
        """The active profile name (v0.41), or ``None`` when running on raw env vars / after a
        reply-only ``/model`` swap."""
        return self._active_profile

    @property
    def model_profiles(self) -> dict[str, ModelProfile]:
        """A copy of the configured profiles (for the /model-set listing)."""
        return dict(self._model_profiles)

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def clock(self) -> Clock:
        """The injected clock (so a client fetches ambient context with the same time)."""
        return self._clock

    def set_world_context(self, world: WorldContext | None) -> None:
        """Set the ambient *now / here* snapshot (the client fetches it at startup/refresh)."""
        self._world = world

    @property
    def mood(self) -> str | None:
        """Today's mood **resolution** (v0.6), or ``None`` when off / not yet computed."""
        return self._mood.resolution if self._mood else None

    @property
    def theme(self) -> str | None:
        """The active face **theme** (v0.11): a ``/theme`` override, else the mood's pick, else the
        default, else ``None``. The override wins so the daily mood can't clobber a manual choice."""
        if self._force_theme is not None:
            return self._force_theme
        if self._mood and self._mood.theme:
            return self._mood.theme
        return self._default_theme

    @property
    def themes(self) -> list[str]:
        """The known face themes (the manifest-described ones the mood / ``/theme`` choose from)."""
        return sorted(self._theme_descriptions)

    def set_theme(self, name: str | None) -> bool:
        """Manually override the face theme (``/theme``). ``None``/``"auto"`` clears it (back to the
        mood). Returns ``False`` for an unknown theme (override unchanged); on success re-emits the
        face signal so the viewer updates at once."""
        if not name or name.strip().lower() == "auto":
            self._force_theme = None
        elif name in self._theme_descriptions:
            self._force_theme = name
        else:
            return False
        self._write_face_signal(self._last_emotion, self._last_intensity)
        return True

    @property
    def closeness(self) -> Closeness | None:
        """The active user's relationship-closeness record (v0.10), or ``None`` if none yet."""
        return self._repo.get_closeness(self._user_id)

    def closeness_status(self) -> tuple[int, str | None]:
        """The active relationship **level** (1–5) + its authored **name** (for ``/closeness``).

        A fresh user (no record yet) sits at the default level. The raw value / dimension
        scores stay internal — only the level + its name are surfaced.
        """
        existing = self._repo.get_closeness(self._user_id)
        level = existing.level if existing else self._default_level
        return level, level_name(self._closeness_levels, level)

    @property
    def biorhythms(self) -> Biorhythms | None:
        """Today's computed biorhythm cycles (v0.8), cached with the mood; ``None`` when off."""
        return self._biorhythms

    @property
    def cycle(self) -> CyclePhase | None:
        """Today's hormonal-cycle phase (v0.8), cached with the mood; ``None`` when off."""
        return self._cycle

    def ensure_mood(self) -> None:
        """Compute today's mood now (idempotent / cached) — a client may call at startup."""
        self._ensure_mood()

    def regenerate_summaries(self) -> int:
        """Force-rebuild **every** day/week digest in the recall window from its session summaries — so a
        format change (e.g. ``LUMI_MEMORY_INDEX``, v0.34) applies **retroactively** (the lazy ensure_* skips
        unchanged days). **Lossless** (derives from the kept session summaries — the source is untouched),
        **idempotent**, **per-user** (only this user's digests). Returns the number of digests rebuilt."""
        return self.ensure_day_summaries(force=True) + self.ensure_week_summaries(force=True)

    def ensure_day_summaries(self, *, force: bool = False) -> int:
        """Bring each day in the recall window (last ``day_days``) up to date — lazily, at prompt
        time. A day's ≤``max_day_rows``-row digest is (re)built from that day's **session
        summaries** **only when stale** (no digest yet, or the day gained sessions — its summary
        count changed, incl. today); ``force`` rebuilds every day regardless (v0.34 regenerate).
        Best-effort; a model error on one day never blocks the turn. Returns the count rebuilt.
        """
        since = (self._clock().date() - timedelta(days=self._day_days)).isoformat()
        by_day: dict[str, list[str]] = {}
        for s in self._repo.summaries_since(self._user_id, since):
            if s.summary.strip():
                by_day.setdefault(s.ts[:10], []).append(s.summary)
        rebuilt = 0
        for day, texts in by_day.items():
            existing = self._repo.get_day_summary(self._user_id, day)
            if not force and existing is not None and existing.count == len(texts):
                continue  # count matches the day's sessions → up to date (unless forced)
            try:
                system, msgs = day_summary_request(texts, index=self._memory_index)
                rows = 1 if self._memory_index else self._max_day_rows  # v0.34: index → a single gist line
                summary = clamp_rows(self._housekeeping_reply(system, msgs, kind="session-start"), rows)
                if summary:
                    self._repo.set_day_summary(
                        DaySummary(self._user_id, day, summary, len(texts), self._clock().isoformat())
                    )
                    rebuilt += 1
            except Exception:  # noqa: BLE001 — best-effort; never block the turn
                continue
        return rebuilt

    def ensure_week_summaries(self, *, force: bool = False) -> int:
        """Bring each Mon–Sun week in the recall window (last ``week_days``) up to date — lazily.
        A week's ≤``max_week_rows``-row digest is (re)built from that week's **session summaries**
        only when its summary count changed; ``force`` rebuilds every week (v0.34 regenerate). Weeks
        are keyed by their Monday. Best-effort. Returns the count rebuilt.
        """
        since = (self._clock().date() - timedelta(days=self._week_days)).isoformat()
        by_week: dict[str, list[str]] = {}
        for s in self._repo.summaries_since(self._user_id, since):
            if s.summary.strip():
                by_week.setdefault(_monday_of(s.ts[:10]), []).append(s.summary)
        rebuilt = 0
        for week_start, texts in by_week.items():
            existing = self._repo.get_week_summary(self._user_id, week_start)
            if not force and existing is not None and existing.count == len(texts):
                continue
            try:
                system, msgs = week_summary_request(texts, index=self._memory_index)
                rows = 1 if self._memory_index else self._max_week_rows  # v0.34: index → a single gist line
                summary = clamp_rows(self._housekeeping_reply(system, msgs, kind="session-start"), rows)
                if summary:
                    self._repo.set_week_summary(
                        WeekSummary(self._user_id, week_start, summary, len(texts),
                                    self._clock().isoformat())
                    )
                    rebuilt += 1
            except Exception:  # noqa: BLE001 — best-effort; never block the turn
                continue
        return rebuilt

    @property
    def thinking(self) -> bool:
        """Whether extended thinking is enabled on the model (for a status indicator)."""
        return bool(getattr(self._llm, "_thinking", False))

    @property
    def style(self) -> str:
        """The style for the status line: Лілі's last choice + **who** picked it.

        ``"<name> (Лілі)"`` when she chose it herself; ``"<name> (ти)"`` when her
        choice matches your standing recommendation (so you can see whether she
        followed it). Before her first reply: ``"авто"`` (+ your recommendation, if any).
        """
        if self.last_style is None:
            return f"авто · радиш: {'+'.join(self._recommendation)}" if self._recommendation else "авто"
        who = "ти" if (self._recommendation and self.last_style in self._recommendation) else "Лілі"
        return f"{self.last_style} ({who})"

    @property
    def recommendation(self) -> str:
        """The active style recommendation for display ('' when none)."""
        return "+".join(self._recommendation)

    def base_names(self) -> list[str]:
        """The authored base style names."""
        return sorted(self._styles)

    def meta_names(self) -> list[str]:
        """The meta-style (preset) names."""
        return sorted(self._meta)

    def style_names(self) -> list[str]:
        """All names a recommendation may use (``auto`` + meta-styles + base styles)."""
        return ["auto", *sorted(self._meta), *sorted(self._styles)]

    def set_style(self, spec: str) -> bool:
        """Set a soft style **recommendation** (not a switch) — Лілі still chooses.

        Names are separated by spaces/commas/``+``. ``auto``/``normal``/empty clears
        the recommendation (she chooses freely). Returns ``False`` (changing nothing)
        if any name is unknown.
        """
        names = [n for n in re.split(r"[\s,+]+", spec.strip().lower()) if n]
        if not names or names == ["auto"] or names == ["normal"]:
            self._recommendation = []
            return True
        valid = {*self._styles, *self._meta}
        if any(n not in valid for n in names):
            return False
        self._recommendation = list(dict.fromkeys(names))  # dedupe, keep order
        return True

    def _style_directive(self) -> str | None:
        """The auto-style palette — just the **mega-styles** with a concise description each
        (so the prompt stays short), plus the user's soft recommendation if set. ``None`` when
        no mega-styles are authored. Base styles are no longer dumped into the prompt."""
        if not self._meta:
            return None
        lines = ["Палітра:"]  # the "pick one" instruction lives in STYLE_HEADER — no need to repeat it
        for name in sorted(self._meta):
            desc = self._meta_desc.get(name) or ", ".join(self._meta[name])
            lines.append(f"- {name}: {desc}")
        if self._recommendation:
            lines.append(
                f"(Користувач радить: {', '.join(self._recommendation)} — "
                "врахуй, якщо доречно; ти все одно вирішуєш.)"
            )
        return "\n".join(lines)

    def start_session(self) -> Session:
        """Open a fresh session for the active user (persisted).

        The style recommendation + Лілі's last choice are per-session — reset here.
        """
        self._recommendation = []
        self.last_style = None
        self._think_count = 0  # v0.12: the proactive-think cap is per session
        self._write_face_signal(DEFAULT_EMOTION.value, DEFAULT_INTENSITY)  # calm before the first turn
        self._ensure_core_flags()  # v0.36: re-rank the identity-core once per session (off when cap=0)
        session = self._repo.create_session(self._user_id)
        self._active_session_id = session.id  # stamp cache events with this session
        return session

    def _write_face_signal(self, emotion: str, intensity: float) -> None:
        """Write the viewer signal (v0.7 + v0.11): ``[<theme>] <emotion> <intensity> <stamp>``.

        The day's face **theme** (v0.11) rides in front when set; with no theme it's the bare
        v0.7 line. Best-effort: a separate viewer process polls this file; a write failure never
        affects the turn.
        """
        self._last_emotion, self._last_intensity = emotion, intensity  # for a /theme re-emit
        if self._face_signal is None:
            return
        try:
            self._face_signal.parent.mkdir(parents=True, exist_ok=True)
            stamp = self._clock().strftime("%Y-%m-%d %H:%M:%S")  # makes every turn's line unique
            prefix = f"{self.theme} " if self.theme else ""  # v0.11 theme, when set
            self._face_signal.write_text(
                f"{prefix}{emotion} {intensity:.2f} {stamp}", encoding="utf-8"
            )
        except OSError:
            pass  # best-effort; the viewer falls back to calm

    # --- Thought-stream (v0.12) — the mental-act engine ------------------
    def think(
        self,
        kind: str = "think",
        *,
        topic: str | None = None,
        session: Session | None = None,
        rng_seed: int = 0,
        spoken: bool = False,
        sink: str | None = None,
        user_topic: bool = False,
    ) -> Thought | None:
        """Run one ``%directive`` — seed → generate → validate → record — into the dated diary.

        ``sink`` (v0.33) overrides the directive's ``default_sink``: the recorded thought is **also**
        code-saved to ``notes/<date>.md`` (``"notes"``) or a file/folder path. ``None`` → use the default.
        ``user_topic`` (set by ``run_directive``) marks ``topic`` as the user's own literal words, so an
        external-tool query keeps them (the de-id whitelist) instead of redacting a place/name they typed.

        Returns the recorded :class:`Thought`, or ``None`` (off / unknown directive / malformed
        output). **Best-effort**: a model failure or an empty thought records nothing, never raises.
        The model call is **thinking-off** housekeeping (mocked in tests); the diary stamp comes
        from the injected clock; the emotion is validated to the locked base-9 set.
        """
        if not self._thoughts_enabled:
            return None
        directive = REGISTRY.get(kind)
        if directive is None:
            return None
        self._last_saved_to = None  # v0.33: the sink path this think saved to (read by run_directive)
        # the user's literal typed words — the de-id whitelist (computed BEFORE placeholder resolve, so a
        # resolved {last_thought} inner seed is NOT whitelisted, only what they actually typed).
        keep = topic_words(topic) if (user_topic and topic) else ()
        if topic:  # a topic may carry {placeholders} (e.g. %think about {last_thought})
            topic = self.resolve(topic, session=session)
        if directive.instruction_from_topic and topic:  # v0.33 %prompt: the topic IS the instruction
            directive = replace(directive, instruction=topic)
            topic = None  # consumed as the instruction — don't also inject it as a seed
        try:
            if self._thoughts_context == "full" and session is not None:
                system, msgs, seeds, cache_prefix = self._thought_call_full(
                    directive, session, topic, rng_seed
                )
            else:
                system, msgs, seeds, cache_prefix = self._thought_call_lean(
                    directive, session, topic, rng_seed
                )
            t_tools, t_exec = self._thought_tools(directive, keep=keep)  # tools (+ topic de-id whitelist)
            cap = self._thought_imagine_cap if "generate_image" in directive.tools else directive.cap
            raw = self._housekeeping_reply(
                system, msgs, cache_prefix=cache_prefix, kind="think",
                tools=t_tools, tool_executor=t_exec, max_steps=cap,
            ).strip()
        except Exception:  # noqa: BLE001 — thoughts are best-effort; never block
            return None
        _, raw = split_reasoning(raw)  # strip any <think>…</think> (the full backdrop's directive)
        parsed = parse_thought(raw)
        if parsed is None:
            return None  # empty / malformed → record nothing (never corrupt the stream)
        text, emo = parsed
        emotion = emo if emo in _EMOTION_VALUES else DEFAULT_EMOTION.value
        thought = make_thought(
            when=self._clock().strftime("%Y-%m-%dT%H:%M"),
            kind=kind, text=text, emotion=emotion, seeds=seeds,
            user_id=self._user_id, spoken=spoken,
        )
        self._repo.add_thought(thought)
        effective_sink = sink if sink is not None else directive.default_sink
        if effective_sink:  # v0.33 — code-owned save of the thought to the chosen sink (notes / a file)
            self._last_saved_to = self._save_thought(thought, effective_sink)
        _thoughts_log.info("%s [%s] %s", thought.when, thought.kind, thought.text)  # logged tier
        return thought

    def _thought_call_lean(
        self, directive, session: Session | None, topic: str | None, rng_seed: int,
    ) -> tuple[str, list[dict[str, str]], list[str], str | None]:
        """The default **lean** thought call: a dedicated prompt seeded from her live state (cheap).

        No cache prefix — the lean prompt is small and shares nothing with the reply prefix."""
        seeds: list[str] = []
        mood = self.mood
        if mood:
            seeds.append("mood")
        _, closeness = self.closeness_status()
        if closeness:
            seeds.append("closeness")
        recent = self._recent_tail(session) if session is not None else None
        if recent:
            seeds.append("recent")
        last = self._recent_thoughts_text()
        if last:
            seeds.append("last_thoughts")
        if topic:
            seeds.append("topic")
        system, msgs = thought_request(
            directive, mood=mood, closeness=closeness, recent=recent,
            last_thoughts=last, topic=topic, rng_seed=rng_seed,
        )
        return system, msgs, seeds, None

    def _thought_call_full(
        self, directive, session: Session, topic: str | None, rng_seed: int,
    ) -> tuple[str, list[dict[str, str]], list[str], str | None]:
        """The **full-context** thought call (``LUMI_THOUGHTS_CONTEXT=full``): the same backdrop a
        reply gets — canon + memory + mood + closeness + the diary block + the conversation window —
        with the reply task swapped for a thought task. Richer, but a mini-reply in tokens.

        Returns the same **cache prefix** the reply uses (canon + memory + mood) so the frequent
        proactive thinks reuse the cached stable backdrop instead of re-sending it each time —
        only her per-think tail (thoughts/ambient) + the thought header is fresh. With a 1h TTL the
        10-min thinks keep that cache warm (and only the unchanging prefix is cached, not the tail)."""
        live = trim_history(self._repo.load_messages(session.id), self._memory_window)
        messages = [
            {"role": _ROLE_TO_LLM[m.role], "content": self._history_content(m)} for m in live
        ]
        messages.append({"role": "user", "content": thought_full_seed(topic=topic, rng_seed=rng_seed)})
        full_system, cache_prefix = self._system_prompt(session)
        header = THOUGHT_FULL_HEADER_FREEFORM if directive.freeform else THOUGHT_FULL_HEADER
        system = full_system + header.format(instruction=directive.instruction)
        hint = thought_tool_hint(directive)
        if hint:  # a tool-thought: make her USE the tool, not just muse (e.g. %journal → journal_write)
            system = f"{system}\n\n{hint}"
        seeds = ["context", *(["topic"] if topic else [])]
        return system, messages, seeds, cache_prefix

    def _thought_tools(
        self, directive, *, keep: Iterable[str] = (),
    ) -> tuple[list[dict] | None, Callable[[str, dict], str | dict] | None]:
        """The (tools, executor) a directive may use in the think path (v0.33), or ``(None, None)``.

        ``(None, None)`` unless the master gate ``LUMI_THOUGHT_TOOLS`` is on **and** the directive opts in
        (``directive.tools`` non-empty). ``("*",)`` → every enabled tool; otherwise the named subset of the
        turn's tools. The per-family flags still gate each tool (via ``_turn_tools``). ``keep`` whitelists
        the user's own topic words from the external-query de-id (they explicitly asked about them)."""
        if not self._thought_tools_enabled or not directive.tools:
            return None, None
        tools, executor = self._turn_tools()
        if tools is None:
            return None, None
        if "*" in directive.tools:
            sub = tools
        else:
            allowed = set(directive.tools)
            sub = [t for t in tools if t["name"] in allowed]
            if not sub:
                return None, None
        # v0.33 LUMI-128: de-identify the thought-driven external query/prompt — unless %prompt (exempt).
        if directive.instruction_from_topic:
            return sub, executor
        return sub, self._deidentified(executor, keep)

    def _deidentified(
        self, executor: Callable[[str, dict], str | dict], keep: Iterable[str] = (),
    ) -> Callable[[str, dict], str | dict]:
        """Wrap ``executor`` so a thought-driven **external** query/prompt is de-identified before it leaves
        (v0.33 LUMI-128) — only the topical/creative part of her musing reaches the external service.
        ``keep`` preserves the user's explicitly-typed topic words (a place/name *they* asked about)."""
        keep = tuple(keep)
        def wrapped(name: str, tool_input: dict) -> str | dict:
            arg = _EXTERNAL_QUERY_ARG.get(name)
            if arg and isinstance(tool_input, dict) and isinstance(tool_input.get(arg), str):
                tool_input = {**tool_input, arg: self._deidentify_external(tool_input[arg], keep)}
            return executor(name, tool_input)
        return wrapped

    def _personal_terms(self) -> set[str]:
        """This user's proper-noun-like personal terms (from their own facts) — the de-id redaction set."""
        return personal_terms(f.fact for f in self._repo.facts(self._user_id))

    def _deidentify_external(self, query: str, keep: Iterable[str] = ()) -> str:
        """Redact this user's personal terms from an outgoing thought-driven external query (LUMI-128).

        ``keep`` whitelists the user's own typed topic words so a place/name *they* asked about survives
        (e.g. ``%events події у Львові`` must not go out as ``події у […]``)."""
        return deidentify(query, self._personal_terms(), keep=keep)

    def _family_flag(self, family: str) -> bool:
        """The per-family thought flag (``LUMI_THOUGHT_<FAMILY>``); default ``True`` → gated by the tool."""
        return bool(getattr(self, f"_thought_{family}", True))

    def _directive_enabled(self, directive, *, is_owner: bool = True) -> bool:
        """Whether a ``%directive`` may fire now (v0.33). ``%think``/``%wonder`` (no family/tools) are
        always on. A family directive needs the master gate, its per-family flag, owner-rights for
        ``%prompt``, and its underlying capability (tool/sandbox) enabled — else it is **absent** and the
        client treats the input as plain chat."""
        if not directive.family and not directive.tools:
            return True  # %think / %wonder — the v0.12 always-on directives
        if not self._thought_tools_enabled:
            return False
        if (directive.instruction_from_topic or directive.owner_only) and not is_owner:
            return False  # %prompt / %share are owner-only
        if not self._family_flag(directive.family):
            return False
        if directive.tools:  # a tool-loop directive — the tool must be enabled this turn
            return self._thought_tools(directive)[0] is not None
        return self._file_tool_enabled and self._files_dir is not None  # %note (tool-less) → file sandbox

    def _resolve_sink(self, sink: str) -> str:
        """Resolve an output sink to a sandbox-relative path (v0.33): ``notes`` → ``notes/<date>.md``; a
        trailing ``/`` → a dated file in that folder; otherwise the exact file path."""
        date = self._clock().strftime("%Y-%m-%d")
        if sink == "notes":
            return f"notes/{date}.md"
        return f"{sink}{date}.md" if sink.endswith("/") else sink

    def _save_thought(self, thought, sink: str) -> str | None:
        """Code-owned save of a directive's thought to ``sink`` (v0.33) — sandboxed, non-destructive
        (create-or-append). Returns the sandbox-relative path written, or ``None`` if off / escaping /
        failed. **Best-effort**: a bad sink never breaks the thought, so an unattended firing can't wander.
        Distinct from the v0.28 ``%journal`` diary (its own dedicated root)."""
        if not (self._file_tool_enabled and self._files_dir is not None):
            return None
        from core.files import _Denied, safe_path
        rel = self._resolve_sink(sink)
        try:
            path = safe_path(self._files_dir / self._user_id, rel)  # sandbox guard (rejects ../escapes)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"## {thought.when[11:16]} — {thought.kind}\n{thought.text}\n\n")
            return rel
        except (_Denied, OSError):
            return None  # never break the thought

    def _recent_tail(self, session: Session, n: int = 6) -> str | None:
        """The last ``n`` messages of the session, compact — a seed for a thought."""
        msgs = self._repo.load_messages(session.id)[-n:]
        if not msgs:
            return None
        speaker = {"user": "Він", "lili": "Я"}
        return "\n".join(f"{speaker.get(m.role, m.role)}: {m.text}" for m in msgs)

    def _recent_thoughts_text(self, n: int = 5) -> str | None:
        """The last ``n`` of her own (surfaceable) thoughts — continuity seed."""
        recent = self._repo.thoughts_for(self._user_id, "")[-n:]
        if not recent:
            return None
        return "\n".join(f"- {t.text}" for t in recent)

    def recent_thoughts(self, *, window_h: int | None = None) -> list[Thought]:
        """This user's surfaceable thoughts within the last ``window_h`` hours (dated, oldest first)."""
        hours = self._thoughts_window_h if window_h is None else window_h
        since = (self._clock() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")
        return self._repo.thoughts_for(self._user_id, since)

    def _thoughts_block(self) -> str | None:
        """The last-24h **dated diary** slice for the prompt (per-user, capped). ``None`` when off/empty."""
        if not self._thoughts_enabled:
            return None
        return thoughts_diary_block(self.recent_thoughts(), max_lines=self._thoughts_max_lines)

    @property
    def thoughts_show(self) -> str:
        """The ``/thoughts`` view policy: ``hidden`` (default) / ``admin`` / ``off`` (v0.12)."""
        return self._thoughts_show

    def thoughts_view(self, *, days: int = 7, max_lines: int = 20) -> str | None:
        """The recent dated diary for ``/thoughts`` — this user's surfaceable thoughts over the
        last ``days``, dated, capped. **Per-user filtered** (never the cross-user stream)."""
        return thoughts_diary_block(self.recent_thoughts(window_h=days * 24), max_lines=max_lines)

    def run_directive(
        self,
        raw: str,
        session: Session,
        *,
        is_owner: bool = True,
        rng_seed: int = 0,
    ) -> DirectiveOutcome:
        """Route a typed ``%directive`` (v0.12): parse → access-gate → fire → record.

        Returns a :class:`DirectiveOutcome`. ``is_directive=False`` means the input wasn't a known
        ``%directive`` (the client treats it as **plain chat**). Otherwise the thought is recorded
        (mode ``silent``/``open``); a **non-owner** can never fire silent (forced to ``open``). The
        topic may carry ``{placeholders}`` (resolved by ``think``).
        """
        parsed = parse_directive(raw)
        if parsed is None:
            return DirectiveOutcome(is_directive=False)
        if not self._directive_enabled(REGISTRY[parsed.name], is_owner=is_owner):
            return DirectiveOutcome(is_directive=False)  # family off / owner-gated → plain chat (absent)
        mode = directive_mode(parsed, is_owner=is_owner)
        thought = self.think(
            parsed.name, topic=parsed.topic, session=session, rng_seed=rng_seed, sink=parsed.sink,
            user_topic=True,  # the user typed this topic → its words survive the external-query de-id
        )
        saved_to = self._last_saved_to if thought is not None else None  # the path think() actually wrote
        return DirectiveOutcome(is_directive=True, mode=mode, thought=thought, saved_to=saved_to)

    def tick_think(
        self,
        session: Session,
        last_activity: datetime,
        now: datetime,
        *,
        rng_seed: int = 0,
        kind: str = "think",
        topic: str | None = None,
        ratio: float | None = None,
    ) -> Thought | None:
        """The **proactive nudge** (B + A): after the idle interval (and not in quiet hours / over
        the per-session cap), fire a directive **silently** into the diary; a configurable fraction
        **graduate** to a spoken turn (``Thought.spoken`` — the client delivers those via the hidden
        self-turn). ``kind``/``topic`` let the client **free-muse** (B, no topic) or fire a **seed**
        from a menu (A, e.g. ``%think {recent}``). Returns the Thought, or ``None`` (not due /
        capped / off / nothing made)."""
        if not self._thoughts_enabled:
            return None
        if not should_nudge(last_activity, now, self._thoughts_interval_s, self._thoughts_quiet_hours):
            return None
        if self._think_count >= self._thoughts_cap:
            return None
        spoken = should_graduate(
            rng_seed, self._thoughts_spoken_ratio if ratio is None else ratio
        )
        thought = self.think(kind, topic=topic, session=session, rng_seed=rng_seed, spoken=spoken)
        if thought is None:
            return None
        self._think_count += 1
        return thought

    # --- Prompt placeholders (v0.12) ------------------------------------
    def resolve(self, text: str, *, session: Session | None = None) -> str:
        """Expand ``{name}`` placeholders in ``text`` from live state (ARCHITECTURE §Prompt
        placeholders) — unknown tokens stay literal; ``{last_thought}``/``{thoughts}`` are
        isolation-aware (this user's surfacing read)."""
        return resolve_placeholders(text, self._placeholder_resolvers(session))

    def _placeholder_resolvers(self, session: Session | None) -> dict[str, Callable[[], str]]:
        """The fixed registry of token → live-value getters (lazy, isolation-aware)."""
        def last_thought() -> str:
            mine = self._repo.thoughts_for(self._user_id, "")
            return mine[-1].text if mine else ""

        def last_image() -> str:  # v0.33: the most recent image in THIS user's sandbox (isolation-aware)
            if self._files_dir is None:
                return ""
            root = self._files_dir / self._user_id
            if not root.is_dir():
                return ""
            imgs = [p for p in root.rglob("*")
                    if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
            if not imgs:
                return ""
            return max(imgs, key=lambda p: p.stat().st_mtime).relative_to(root).as_posix()

        def ambient_news() -> str:  # v0.33: the v0.4 startup news snapshot (topical only)
            return " | ".join(self._world.news) if (self._world and self._world.news) else ""

        def section() -> str:  # v0.33: the first configured news section (a topical seed)
            secs = [s.strip() for s in (self._news_sections or "").split(",") if s.strip()]
            return secs[0] if secs else ""

        def weekday() -> str:  # v0.33: the local weekday (Ukrainian) from the injected clock
            days = ("понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя")
            return days[self._clock().weekday()]

        return {
            "last_thought": last_thought,
            "thoughts": lambda: self._recent_thoughts_text() or "",
            "mood": lambda: self.mood or "",
            "closeness": lambda: self.closeness_status()[1] or "",
            "plan": lambda: "",  # v0.13 inner life
            "need": lambda: "",  # v0.13 needs
            "recent": lambda: (self._recent_tail(session) if session is not None else "") or "",
            "now": lambda: self._clock().strftime("%Y-%m-%d %H:%M"),
            "today": lambda: self._clock().strftime("%Y-%m-%d"),
            "user": lambda: self._user_id,
            # v0.33 thought-tool seeds (lazy, ""-on-empty, isolation-aware)
            "ambient_news": ambient_news,
            "world": lambda: ambient_line(self._world, self._clock) or "",
            "last_image": last_image,
            "interest": lambda: "",        # v1.5 inner life
            "hungriest_need": lambda: "",   # v1.5 needs
            "section": section,
            "weekday": weekday,
            "gap": lambda: "",              # v1.5 away-gap
        }

    def _system_prompt(
        self, session: Session, recall: str | None = None, fact_recall: str | None = None
    ) -> tuple[str, str]:
        """Assemble the system prompt for this turn — returns ``(system, cache_prefix)``,
        the cacheable stable head (v0.15). Rehydrated for the user.

        Composes the canon with the user's recent summaries + long-term facts and
        — if the current session has been compacted — its running digest. Loaded
        per turn, so a restart recalls prior context and new memory takes effect.
        Isolation holds — only this ``user_id``'s records are read.
        """
        # date-based recall three date-based layers (cumulative): per-WEEK digests (last week_days) →
        # per-DAY digests (last day_days) → per-SESSION detail (last session_days). Coarse → fine.
        today = self._clock().date()
        week_since = _monday_of((today - timedelta(days=self._week_days)).isoformat())
        week_summaries = []
        for ws in self._repo.week_summaries_since(self._user_id, week_since):
            body = " ".join(ln.strip() for ln in ws.summary.splitlines() if ln.strip())
            if body:
                week_summaries.append(f"[тиждень з {ws.week_start}] {body}")
        day_since = (today - timedelta(days=self._day_days)).isoformat()
        day_summaries = []
        for ds in self._repo.day_summaries_since(self._user_id, day_since):
            body = " ".join(ln.strip() for ln in ds.summary.splitlines() if ln.strip())
            if body:
                day_summaries.append(f"[{ds.date}] {body}")
        session_since = (today - timedelta(days=self._session_days)).isoformat()
        # v0.35: two orthogonal knobs. `session_detail_n` caps HOW MANY of the most-recent sessions to add
        # (None = all; 0 = none; N = last N); `session_format` picks the FORM each takes — full "summary" or
        # one-line "gist" (she pulls a gisted session's detail via messages_on / recall / auto-RAG). The
        # window is chronological (oldest-first). Default (None + "summary") = all sessions, full = unchanged.
        window = self._repo.summaries_since(self._user_id, session_since)
        if self._session_detail_n is not None:
            window = window[max(0, len(window) - self._session_detail_n):]  # last N (0 → none, >len → all)
        as_gist = self._session_format == "gist"
        summaries = [
            f"[{format_date(s.ts)}] " + (session_gist(s.gist, s.summary) if as_gist else s.summary)
            for s in window
        ]
        # Long-term facts: inject the consolidated digest + any facts added since it was built
        # (verbatim tail), instead of all raw facts. Falls back to raw when no digest exists.
        raw_facts = self._repo.facts(self._user_id)
        stale = {f.fact for f in raw_facts if f.obsolete}  # v0.36: excluded from every fact path
        core_facts = [f.fact for f in raw_facts if f.core and not f.obsolete]  # the curated identity-core
        if self._facts_core_only and core_facts:
            # v0.36: inject ONLY the identity-core; the episodic tail is reachable via recall(scope=facts).
            facts = core_facts
        else:
            digest = self._repo.get_facts_digest(self._user_id) if self._facts_digest_enabled else None
            if digest is not None:
                tail = [f.fact for f in raw_facts[digest.count:]]  # facts newer than the digest
                facts = [ln for ln in digest.summary.split("\n") if ln.strip()] + tail
            else:
                facts = [f.fact for f in raw_facts]
            if stale:  # v0.36: hide obsolete facts from the digest/raw path too
                facts = [f for f in facts if f not in stale]
        digest = self._repo.get_digest(session.id)
        # v0.10: inject the active relationship level's authored block (warmth/openness, never
        # competence). The persisted level is the prior turn's (a fresh user sits at the default).
        # Refinement: today's ephemeral mood-shift (emotional biorhythm + cycle phase) colors the
        # EFFECTIVE level for THIS prompt only — the stored value/level are untouched. Off → none.
        closeness = None
        if self._closeness_enabled:
            existing = self._repo.get_closeness(self._user_id)
            base_level = existing.level if existing else self._default_level
            emotional = self._biorhythms.emotional.value if self._biorhythms else None
            phase = self._cycle.phase if self._cycle else None
            shift = mood_shift(emotional, phase, self._closeness_tuning.mood_shift_scale)
            base_value = existing.value if existing else self._closeness_tuning.baseline
            # with a shift, re-bucket the effective value (transient, no inertia); without one,
            # keep the inertia-stabilized persisted level (no behavior change when mood is off).
            level = shifted_level(base_value, shift) if shift else base_level
            closeness = closeness_block(self._closeness_levels, level)
        # Append the reasoning directive to the canon so any pre-answer reasoning is
        # wrapped in <think>…</think> (parsed out in reply()); the style rides last.
        # v0.38: this is the generic REASONING_DIRECTIVE, or her authored inner_voice.md when on.
        # v1.1 LUMI-177: with moves on, the directive's {move_rules} token resolves to this
        # turn's dynamic arbiter lines (empty → the token disappears; failure → empty block —
        # never blocks a turn). Off, or no token → the directive rides verbatim (byte-identical).
        directive = self._reasoning_directive
        if self._moves_enabled and "{move_rules}" in directive:
            directive = resolve_placeholders(directive, {"move_rules": lambda: self._move_dynamics})
        # v0.25: when the news tool is on, add the authored "how she delivers news" line (EN→UK, cited).
        canon = f"{self._canon}\n\n{directive}"
        if self._news_enabled:
            from core.news import NEWS_DIRECTIVE

            canon = f"{canon}\n\n{NEWS_DIRECTIVE}"
        # v0.27: when the web tool is on, add the authored "how she delivers a web answer" line.
        if self._web_lookup_enabled:
            from core.weblookup import WEB_LOOKUP_DIRECTIVE

            canon = f"{canon}\n\n{WEB_LOOKUP_DIRECTIVE}"
        # v0.28: when the journal tool is on, add the authored "how she keeps her diary" line.
        if self._journal_enabled:
            from core.journal import JOURNAL_DIRECTIVE

            canon = f"{canon}\n\n{JOURNAL_DIRECTIVE}"
        return build_system_prompt(
            canon,
            summaries=summaries,
            day_summaries=day_summaries,
            week_summaries=week_summaries,
            facts=facts,
            digest=digest.summary if digest else None,
            style=self._style_directive(),
            emotion=True,
            relation=self._closeness_enabled,  # v0.10: ask for the relational read (off → skip)
            moves=self._moves_enabled,  # v1.1: ask for the declared move (off → skip, byte-identical)
            ambient=ambient_line(self._world, self._clock),
            mood=self.mood,  # only the resolution rides in the prompt (v0.6)
            closeness=closeness,  # the active relationship level's block (v0.10)
            thoughts=self._thoughts_block(),  # the last-24h dated diary (v0.12)
            recall=recall,  # v0.17: the per-turn "relevant past moments" RAG block (tail; never cached)
            fact_recall=fact_recall,  # v0.36: the per-turn fact-RAG push — top-K relevant non-core facts
        )

    def _model_for(self, kind: str) -> str:
        """The model for one internal operation (v0.40 Layer 1 routing).

        ``think`` / ``mood`` / the housekeeping kinds (``session-start`` / ``session-close`` /
        ``compaction`` / bare ``housekeeping``) route to their configured Claude tier; an unset tier —
        and the visible ``reply`` path, which never comes through here — stays on ``self._model``.
        **Provider guard:** the tier vars name Claude ids, so routing applies only while the active
        engine is Anthropic; on a foreign engine (gpt-5.5 / gemini) every call uses ``self._model``
        (a Claude id must never reach another provider's API).
        """
        if (self._active_profile is None
                and self._active_provider and self._active_provider != "anthropic"):
            # Raw env-var mode: the tier vars are Claude ids — no routing on a foreign engine. Under
            # an active profile (v0.41) the tiers are provider-homogeneous by construction, so routing
            # applies on every engine.
            return self._model
        if kind == "think":
            return self._model_think or self._model
        if kind == "mood":
            return self._model_mood or self._model
        return self._model_housekeeping or self._model

    def _housekeeping_reply(
        self, system: str, messages: list[Message], cache_prefix: str | None = None,
        kind: str = "housekeeping",
        *,
        tools: list[dict] | None = None,
        tool_executor: Callable[[str, dict], str | dict] | None = None,
        max_steps: int = 8,
    ) -> str:
        """An internal model call with extended thinking forced off.

        Used for summaries / facts / compaction — internal extraction, not
        user-facing reasoning, so it stays fast and cheap. ``cache_prefix`` (v0.17.x) lets the
        full-mode thought reuse the same cached prefix as the reply (so frequent thinks keep the
        cache warm) — passed only when prompt caching is on.
        """
        llm = self._llm
        prev_thinking = getattr(llm, "_thinking", None)
        if prev_thinking:
            llm._thinking = False
        self._active_cache_prefix = cache_prefix if self._prompt_cache else None  # fingerprinted by the monitor
        try:
            kwargs: dict = {
                "system": system, "messages": messages, "model": self._model_for(kind),
                "cache_prefix": cache_prefix if self._prompt_cache else None,
            }
            if tools is not None:  # v0.33 think-path tool-loop — omit when tool-less (call unchanged)
                kwargs.update(tools=tools, tool_executor=tool_executor, max_steps=max_steps)
            text = llm.reply(**kwargs)
            self._accumulate_stats(turn=False, kind=kind)  # count + log this background call
            return text
        finally:
            if prev_thinking:
                llm._thinking = prev_thinking

    def _accumulate_stats(self, *, turn: bool, kind: str = "reply") -> None:
        """Fold the LLM's most-recent call into the running totals + ``last_stats``.

        Called after **every** model call — user replies (``turn=True``, ``kind="reply"``) and
        background calls (thinks, summaries, facts, mood, compaction; ``turn=False``) — so the status
        line reflects real consumption. Token fields count everything; ``turns``/``latency_ms`` count
        user turns only. ``kind`` labels the channel for the per-call cache monitor."""
        stats = getattr(self._llm, "last_stats", None)
        if stats is None:
            return
        self.last_stats = stats
        self.totals.input_tokens += stats.input_tokens or 0
        self.totals.output_tokens += stats.output_tokens or 0
        self.totals.cache_read_tokens += stats.cache_read_tokens or 0
        self.totals.cache_write_tokens += stats.cache_write_tokens or 0
        if turn:
            self.totals.turns += 1
            self.totals.latency_ms += stats.latency_ms
        self._log_cache_event(kind, stats)  # per-call cache monitor (best-effort)

    def _log_cache_event(self, kind: str, stats: ResponseStats) -> None:
        """Append the call(s) of this turn to the cache log. A reply turn is split into its **per-round**
        calls (each tagged ``tool`` or ``reply`` by the loop); other channels log one event. Never raises."""
        if not self._cache_monitor or self._cache_log_path is None:
            return
        rounds = getattr(self._llm, "last_round_log", None)
        if kind == "reply" and rounds:
            for round_kind, rstats in rounds:
                if rstats is not None:
                    self._log_one_cache_event(round_kind, rstats)
        else:
            self._log_one_cache_event(kind, stats)

    def _log_one_cache_event(self, kind: str, stats: ResponseStats) -> None:
        """Append one model call's cache behaviour + classify the write. Never raises."""
        try:
            from core import cache_log

            now = self._clock()
            last = self._cache_last_ts.get(kind)
            gap_s = (now - last).total_seconds() if last is not None else None
            self._cache_last_ts[kind] = now
            cw = stats.cache_write_tokens or 0
            # Measure (don't guess) whether the cached prefix changed: fingerprint it and diff against the
            # last call of the same cache group (reply + its tool rounds share one prefix; think has its own).
            group = "think" if kind == "think" else "main"
            sections = cache_log.prefix_sections(self._active_cache_prefix or "")
            prefix_changed, changed_section = cache_log.diff_sections(self._cache_prefix_sig.get(group), sections)
            if sections:
                self._cache_prefix_sig[group] = sections
            cause = cache_log.classify(
                cw, gap_s, cache_log.ttl_seconds(self._usage_cache_ttl), prefix_changed=prefix_changed
            )
            cache_log.append_event(self._cache_log_path, cache_log.CacheEvent(
                ts=now.isoformat(timespec="seconds"), kind=kind, model=stats.model,
                cache_read=stats.cache_read_tokens or 0, cache_write=cw,
                input=stats.input_tokens or 0, output=stats.output_tokens or 0,
                gap_s=gap_s, cause=cause, session_id=self._active_session_id,
                changed_section=changed_section if cause == "moved" else "",
            ))
        except Exception:  # noqa: BLE001 — monitoring must never break a turn
            _usage_log.warning("cache event log failed", exc_info=True)

    def _render_cache_report(self) -> None:
        """Re-render the per-channel cache report from the log (at session close). Never raises."""
        if not self._cache_monitor or self._cache_log_path is None or self._cache_report_path is None:
            return
        try:
            from core import cache_log

            events = cache_log.load_events(self._cache_log_path)
            if events:
                cache_log.write_cache_report(
                    events, self._cache_report_path,
                    generated_at=self._clock().isoformat(timespec="seconds"), ttl=self._usage_cache_ttl,
                )
        except Exception:  # noqa: BLE001 — observability must never break session close
            _usage_log.warning("cache report failed", exc_info=True)

    def _maybe_compact(self, session: Session, history: list) -> SessionDigest | None:
        """Fold older-than-window messages into the session digest, in batches.

        Returns the current digest (possibly updated). Sets ``last_compaction`` to
        how many messages were folded this turn (0 if none). Best-effort — a model
        failure keeps the prior digest and never breaks the turn.
        """
        self.last_compaction = 0
        digest = self._repo.get_digest(session.id)
        compacted = digest.compacted_count if digest else 0
        new_compacted = compaction_plan(
            len(history), compacted, self._memory_window, self._compaction_batch
        )
        if new_compacted <= compacted:
            return digest
        chunk = history[compacted:new_compacted]
        try:
            system, msgs = digest_request(digest.summary if digest else None, chunk)
            summary = self._housekeeping_reply(system, msgs, kind="compaction").strip()
        except Exception:  # noqa: BLE001 — best-effort; keep the prior digest
            return digest
        if not summary:
            return digest
        updated = SessionDigest(
            session_id=session.id,
            summary=summary,
            compacted_count=new_compacted,
            ts=now_iso(),
        )
        self._repo.set_digest(updated)
        self.last_compaction = len(chunk)
        return updated

    @staticmethod
    def _history_content(m) -> str:
        """The content to replay to the model for a stored message.

        Prefixed with the message's **date-time** (so Лілі perceives the rhythm of
        the conversation and the gap since the last turn — v0.4). For Лілі's turns,
        also re-append the ``<emotion>…</emotion>`` tag reconstructed from the
        persisted ``emotion``/``intensity`` — the stored text is clean (tag-stripped),
        so without this the model only sees tag-less past replies and drifts to stop
        emitting the tag over a long conversation. Likewise (v1.1) a ``<move>…</move>``
        marker reconstructed from the persisted ``move`` — the declared type rides
        BESIDE the message as replay-only metadata (the stored text never carries it,
        no renderer ever shows it), so the retrospective can check declared-vs-done.
        A record without a ``move`` (user lines, pre-v1.1 rows, moves off) replays
        byte-identically to before.
        """
        body = m.text
        if m.role == "lili" and m.emotion:
            intensity = m.intensity if m.intensity is not None else 0.5
            body = f"{m.text} <emotion>{m.emotion} {intensity:.1f}</emotion>"
        if m.role == "lili" and getattr(m, "move", None):
            body = f"{body} <move>{m.move}</move>"
        return f"[{format_stamp(m.ts)}] {body}"

    def _ensure_mood(self) -> None:
        """Compute today's mood once per local day (cached); **log the full reading**.

        Best-effort: off, no natal, or a model failure → no mood, never blocks a turn.
        Only the **resolution** is held (``Core.mood``); the full reading goes to the log.
        Runs through the housekeeping path (extended thinking off), like summaries.
        """
        if not self._mood_enabled or not self._natal:
            return
        today = self._clock().strftime("%Y-%m-%d")
        if self._mood and self._mood.date == today:
            return  # already computed for this local day; a turn keeps its mood
        # v0.8: compute today's body rhythms (exact, deterministic) and merge them in.
        bio_line: str | None = None
        cycle_line: str | None = None
        self._biorhythms = None
        self._cycle = None
        today_date = self._clock().date()
        if self._biorhythms_enabled:
            birth = parse_birth_date(self._natal)
            if birth is not None:
                self._biorhythms = biorhythm_cycles(birth, today_date)
                bio_line = format_biorhythms(self._biorhythms)
        if self._cycle_enabled:
            anchor = parse_cycle_anchor(self._natal)
            if anchor is not None:
                self._cycle = menstrual_phase(anchor[0], today_date, anchor[1])
                cycle_line = format_cycle(self._cycle)
        try:
            system, msgs = mood_request(
                self._natal, today, biorhythms=bio_line, cycle=cycle_line,
                themes=self._theme_descriptions or None,  # v0.11: also pick a face theme
                thoughts=self._recent_thoughts_text() if self._thoughts_enabled else None,  # v0.12
            )
            reading = self._housekeeping_reply(system, msgs, kind="mood").strip()
        except Exception:  # noqa: BLE001 — mood is best-effort; never block a turn
            return
        if not reading:
            return
        # v0.11: pull the «ТЕМА: …» pick (validated against the manifest, else default/None) and
        # keep it off the resolution.
        known = {n.lower(): n for n in self._theme_descriptions}
        theme = known.get(split_theme(reading) or "")
        self._mood = MoodState(
            date=today, resolution=split_resolution(strip_theme(reading)), reading=reading,
            theme=theme,
        )
        _mood_log.info("mood %s:\n%s", today, reading, extra={"date": today})
        if self._mood_log_path is not None:  # also persist the full reading, readable
            try:
                self._mood_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._mood_log_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n\n===== {today} =====\n{reading}\n")
            except OSError:
                pass  # best-effort; never block a turn on logging

    def _ensure_facts_digest(self) -> None:
        """Consolidate the accumulated facts into a compact digest, **rebuilt only when** the raw
        facts have grown past the last digest by ``facts_digest_refresh``.

        One housekeeping call (extended thinking off, like summaries) per refresh — far cheaper
        than injecting all raw facts every turn. **Non-destructive** (the raw facts are kept) and
        best-effort: disabled, too few facts, or a model failure → no digest (the prompt falls
        back to raw facts), never blocks a turn. Deterministic ``ts`` from the injected clock.
        """
        if not self._facts_digest_enabled:
            return
        raw = self._repo.facts(self._user_id)
        if len(raw) <= self._facts_digest_max:
            return  # already small enough — inject raw, no point digesting
        existing = self._repo.get_facts_digest(self._user_id)
        if existing is not None and len(raw) - existing.count < self._facts_digest_refresh:
            return  # fresh enough — reuse (recent facts ride as a verbatim tail in the prompt)
        try:
            system, msgs = facts_digest_request([f.fact for f in raw], self._facts_digest_max)
            digest_facts = parse_facts(self._housekeeping_reply(system, msgs, kind="session-start").strip())
            if digest_facts:
                self._repo.set_facts_digest(
                    FactsDigest(self._user_id, "\n".join(digest_facts), len(raw), self._clock().isoformat())
                )
        except Exception:  # noqa: BLE001 — degrade to raw facts; never break a turn
            pass

    def _ensure_core_flags(self) -> None:
        """Session-start re-flag of the **identity-core** (v0.36).

        Re-ranks the ``core=true`` pool to ``LUMI_FACTS_CORE_MAX`` (boundaries / standing agreements
        **pinned** — kept past the cap) and writes the flag back. On the **first run** (nothing flagged
        yet) the pool is **all** facts — the one-off backfill that seeds the core. Off when the cap is 0;
        one housekeeping call over the **small core pool** (cost-neutral with the digest it replaces in
        LUMI-143). Best-effort: a model failure leaves the flags as-is, never blocks. Per-user; idempotent.
        """
        if self._facts_core_max <= 0:
            return
        all_facts = [f for f in self._repo.facts(self._user_id) if not f.obsolete]  # v0.36: skip stale
        if not all_facts:
            return
        pool = [f for f in all_facts if f.core] or all_facts  # first run → backfill over all facts
        pool_texts = {f.fact for f in pool}
        pinned = {f.fact for f in pool if is_pinned_fact(f.fact)}
        try:
            system, msgs = core_select_request([f.fact for f in pool], self._facts_core_max)
            chosen = parse_facts(self._housekeeping_reply(system, msgs, kind="session-start").strip())
        except Exception:  # noqa: BLE001 — leave the flags as-is; never break a session
            return
        ranked = [c for c in chosen if c in pool_texts]            # the model's ranking, pool-only
        keep = set(ranked[: self._facts_core_max]) | pinned       # cap the chosen; pins are extra
        for f in pool:
            want = f.fact in keep
            if f.core != want:
                self._repo.set_fact_core(self._user_id, f.fact, want)

    def _turn_tools(self) -> tuple[list[dict] | None, Callable[[str, dict], str] | None]:
        """Assemble this turn's bounded-loop tools + a **name-routing** executor — the v0.19/v0.20 file
        tools and the v0.21 wiki tools, offered together when both are on. ``(None, None)`` when neither
        is on (the turn is a single ``set_state`` call, unchanged). Resets the per-turn trace and wraps
        every call with the v0.19 trace (``LUMI_FILE_TOOL_TRACE``)."""
        self.last_tool_calls = []  # fresh per turn (the trace)
        routes: dict[str, Callable[[str, dict], str | dict]] = {}
        tools: list[dict] = []
        for tool_list, executor in (self._file_tool_args(), self._wiki_tool_args(),
                                    self._news_tool_args(), self._web_tool_args(),
                                    self._journal_tool_args(),
                                    self._image_tool_args(), self._imagegen_tool_args(),
                                    self._sendimage_tool_args(), self._recall_tool_args(),
                                    self._date_tool_args()):
            if executor is None:
                continue
            tools += tool_list
            for t in tool_list:
                routes[t["name"]] = executor
        if not routes:
            return None, None

        def dispatch(name: str, tool_input: dict) -> str | dict:
            executor = routes.get(name)
            result = executor(name, tool_input) if executor is not None else f"error: unknown tool {name!r}"
            if self._file_tool_trace:  # one trace point for every tool (file + wiki + image)
                shown = _tool_trace_repr(result)  # an image result → a short marker, not the base64 dict
                self.last_tool_calls.append((name, dict(tool_input or {}), shown))
                self._log_tool_call(name, tool_input, shown)
            return result

        return tools, dispatch

    def _image_tool_args(self) -> tuple[list[dict] | None, Callable[[str, dict], str | dict] | None]:
        """The (tools, **raw** executor) for the v0.22 vision tool ``view_image``; ``(None, None)`` off.

        Bound to **this user's** sandbox (the same per-user root as the file tool). A per-turn closure
        counter enforces ``LUMI_VISION_MAX``; the executor returns an **image block** (→ an image
        tool_result) or an error string."""
        if not self._image_enabled or self._files_dir is None:
            return None, None
        from core.imagetool import VIEW_TOOLS, ImageTools

        root = self._files_dir / self._user_id
        root.mkdir(parents=True, exist_ok=True)
        images = ImageTools(root, max_bytes=self._image_max_bytes)
        seen = {"n": 0}  # successful image loads this turn (errors don't count toward the cap)

        def capped(name: str, tool_input: dict) -> str | dict:
            if seen["n"] >= self._vision_max:
                return f"(image view limit reached: {self._vision_max} per turn)"
            result = images.execute(name, tool_input)
            if is_image_block(result):
                seen["n"] += 1
            return result

        return VIEW_TOOLS, capped

    def _imagegen_tool_args(self) -> tuple[list[dict] | None, Callable[[str, dict], str] | None]:
        """The (tools, executor) for the v0.23 ``generate_image`` tool; ``(None, None)`` off.

        Bound to **this user's** sandbox (create-only). A per-turn closure counter enforces
        ``LUMI_IMAGE_MAX_GEN`` (paid). The prompt carries **only what the model passes** — the core never
        augments it with memory/personal data. On a successful generation, the saved PNG is **shown** per
        ``LUMI_IMAGE_SHOW``. The ``ImageGen`` is injected (tests) or the real Gemini caller."""
        if not self._image_enabled or self._files_dir is None:
            return None, None
        from core.imagegen import GENERATE_TOOLS, ImageMaker, gemini_image_gen

        gen = self._image_gen if self._image_gen is not None else gemini_image_gen(model=self._image_model)
        root = self._files_dir / self._user_id
        root.mkdir(parents=True, exist_ok=True)
        maker = ImageMaker(root, image_gen=gen, size=self._image_size)
        made = {"n": 0}

        def capped(name: str, tool_input: dict) -> str:
            if made["n"] >= self._image_max_gen:
                return f"(image generation limit reached: {self._image_max_gen} per turn)"
            result = maker.execute(name, tool_input)
            if result.startswith("created "):  # a new PNG landed → count it + show it
                made["n"] += 1
                self._emit_image_display(result, root)
            return result

        return GENERATE_TOOLS, capped

    def _sendimage_tool_args(self) -> tuple[list[dict] | None, Callable[[str, dict], str] | None]:
        """The (tools, executor) for the v0.24 ``send_image`` tool; ``(None, None)`` off.

        Offered whenever the image tool is on (the same ``LUMI_IMAGE`` gate + the per-user sandbox);
        the **injected ``telegram_sink``** the TUI supplies does the actual outbox write (single writer).
        When the sink is ``None`` (the bridge isn't connected), the tool degrades to a "not connected"
        notice — it is still offered so she can try and learn the bridge is off. The core never touches
        Telegram or the outbox here."""
        if not self._image_enabled or self._files_dir is None:
            return None, None
        from core.sendimage import SEND_TOOLS, SendImageTools

        root = self._files_dir / self._user_id
        root.mkdir(parents=True, exist_ok=True)
        sender = SendImageTools(root, telegram_sink=self._telegram_sink)
        return SEND_TOOLS, sender.execute

    def _emit_image_display(self, result: str, root: Path) -> None:
        """Show a freshly-generated PNG per ``LUMI_IMAGE_SHOW`` — write its path to the display signal the
        v0.7 viewer / v0.13 Telegram daemon can pick up. **path** is always satisfied (the tool result
        names the file). Best-effort: a display failure never breaks the turn."""
        targets = {t.strip() for t in (self._image_show or "").split(",") if t.strip()}
        if self._image_signal_path is None or not ({"viewer", "telegram"} & targets):
            return  # only "path" requested (or no signal configured) → nothing to emit
        try:
            rel = result.split(" ", 2)[1]  # "created art/cat.png (123 bytes)" → "art/cat.png"
            self._image_signal_path.parent.mkdir(parents=True, exist_ok=True)
            self._image_signal_path.write_text(str(root / rel), encoding="utf-8")
        except Exception:  # noqa: BLE001 — display must never break a turn
            _usage_log.warning("image display signal failed", exc_info=True)

    def _file_tool_args(self) -> tuple[list[dict] | None, Callable[[str, dict], str] | None]:
        """The (tools, **raw** executor) for the file tool — bound to **this user's** sandbox;
        ``(None, None)`` when off. The root ``files_dir/<user_id>`` is created lazily; a fresh executor
        per turn carries the per-turn read budget (LUMI-083). Per-user keying enforces isolation. The
        v0.20 write tools ride the same executor; tracing/routing is applied by :meth:`_turn_tools`."""
        if not self._file_tool_enabled or self._files_dir is None:
            return None, None
        from core.files import READ_TOOLS, WRITE_TOOLS, FileTools

        root = self._files_dir / self._user_id
        root.mkdir(parents=True, exist_ok=True)
        tools = FileTools(
            root, read_lines=self._file_read_lines, find_max=self._file_find_max,
            read_max_total=self._file_read_max_total, read_max_chars=self._file_read_max_chars,
            write_max=self._file_write_max,
            copy_max=self._file_copy_max,
            search_max_files=self._file_search_max_files,
            search_max_lines=self._file_search_max_lines,
            search_max_chars=self._file_search_max_chars,
            around_max_k=self._file_around_max_k,
            date_max_days=self._file_date_max_days,
        )
        return READ_TOOLS + WRITE_TOOLS, tools.execute  # read + non-destructive write/filesystem (v0.29)

    def _wiki_tool_args(self) -> tuple[list[dict] | None, Callable[[str, dict], str] | None]:
        """The (tools, **raw** executor) for the v0.21 Wikipedia tool; ``(None, None)`` when off.

        The ``wiki_search`` query carries **only what the model passes** — the core never augments it
        with relationship memory, facts, or secrets (no-personal-data rule). A per-turn closure counter
        enforces ``LUMI_WIKI_MAX_CALLS`` independently of the file loop cap."""
        if not self._wiki_enabled:
            return None, None
        from core.wiki import WIKI_TOOLS, WikiTools

        kwargs = {"http_get": self._wiki_http_get} if self._wiki_http_get is not None else {}
        wiki = WikiTools(
            lang=self._wiki_lang, base_url=self._wiki_base_url, max_chars=self._wiki_max_chars, **kwargs
        )
        calls = {"n": 0}

        def capped(name: str, tool_input: dict) -> str:
            calls["n"] += 1
            if calls["n"] > self._wiki_max_calls:
                return (
                    f"(wiki call limit reached: {self._wiki_max_calls} per turn — "
                    "answer from what you already found)"
                )
            return wiki.execute(name, tool_input)

        return WIKI_TOOLS, capped

    def _recall_tool_args(self) -> tuple[list[dict] | None, Callable[[str, dict], str | dict] | None]:
        """The (tools, executor) for the v0.31 model-callable ``recall`` tool; ``(None, None)`` off.

        Exposes the shipped :meth:`recall_moments` as a tool on the v0.19 loop — the **"pull"** that
        complements the v0.17 auto-RAG **"push"**: she can issue a **targeted** query (≠ the current
        message) and search→refine mid-turn. The search is **user-scoped** (``recall_moments`` already
        runs only over this user's vectors). A per-turn closure counter enforces
        ``LUMI_RECALL_TOOL_MAX_CALLS``. Gated on recall + the embedder (folded into
        ``_recall_tool_enabled``)."""
        if not self._recall_tool_enabled:
            return None, None
        calls = {"n": 0}

        def execute(name: str, tool_input: dict) -> str | dict:
            if name != "recall":
                return f"error: unknown tool {name!r}"
            calls["n"] += 1
            if calls["n"] > self._recall_tool_max_calls:
                return (
                    f"(recall call limit reached: {self._recall_tool_max_calls} per turn — "
                    "answer from what you already recalled)"
                )
            query = ((tool_input or {}).get("query") or "").strip()
            if not query:
                return "(recall: порожній запит)"
            k = (tool_input or {}).get("k")
            try:
                k = int(k) if k is not None else self._recall_tool_k
            except (TypeError, ValueError):
                k = self._recall_tool_k
            after = ((tool_input or {}).get("after") or "").strip() or None    # YYYY-MM-DD date scope
            before = ((tool_input or {}).get("before") or "").strip() or None
            scope = ((tool_input or {}).get("scope") or self._recall_scope).strip().lower()  # v0.36
            if scope not in ("messages", "facts", "all"):
                scope = self._recall_scope
            # dedup against what's already in the prompt (the live window + auto-RAG block)
            moments = self.recall_moments(
                query, k, window_ids=self._turn_dedup_ids, before=before, after=after, scope=scope
            )
            if not moments:
                return f"(нічого не згадалося про «{query}»)"
            return trusted_text("\n\n".join(moments))  # her own memory → trusted framing in the loop

        return RECALL_TOOLS, execute

    def _messages_in_range(self, start: str, end: str) -> list[Message]:
        """This user's messages whose **date** falls in ``[start, end]`` (YYYY-MM-DD), in time order.
        Loads only the requesting user's sessions (isolation); skips empty messages."""
        out: list[Message] = []
        for session in self._repo.list_sessions(self._user_id):
            for m in self._repo.load_messages(session.id):
                if m.user_id == self._user_id and m.text.strip() and start <= m.ts[:10] <= end:
                    out.append(m)
        out.sort(key=lambda m: m.ts)
        return out

    def _format_dated_messages(self, msgs: list[Message], *, anchor: Message | None = None) -> str:
        """Render verbatim messages as a dated transcript (``— date —`` headers + ``HH:MM who: text``),
        capped at ``date_tool_max_chars`` (a truncation note if it overflows). ``anchor`` (if given,
        the same object from ``msgs``) is marked ``← (це)``."""
        lines: list[str] = []
        cur_date: str | None = None
        total = 0
        for m in msgs:
            d = m.ts[:10]
            if d != cur_date:
                hdr = f"— {d} —"
                lines.append(hdr)
                total += len(hdr) + 1
                cur_date = d
            tag = "  ← (це)" if (anchor is not None and m is anchor) else ""
            line = f"  {m.ts[11:16]} {self._who(m.role)}: {strip_leading_stamp(m.text)}{tag}"
            if total + len(line) > self._date_tool_max_chars:
                lines.append("… (обрізано — забагато тексту за цей період)")
                break
            lines.append(line)
            total += len(line) + 1
        return "\n".join(lines)

    def _message_context_window(
        self, k: int, *, msg_id: str | None = None, ts: str | None = None,
    ) -> tuple[list[Message], Message | None]:
        """Find the message matching ``msg_id`` (its ``vector_msg_id`` equals or starts-with) **or**
        ``ts`` (a timestamp prefix — date or date+time) and return its session window (the anchor ±
        ``k`` messages) + the anchor. ``([], None)`` if neither is given or nothing matches. Searches
        only **this user's** sessions (isolation); ``msg_id`` wins if both are given."""
        mid_q = (msg_id or "").strip().lstrip("#")
        ts_q = (ts or "").strip()
        if not mid_q and not ts_q:
            return [], None
        for session in self._repo.list_sessions(self._user_id):
            msgs = [m for m in self._repo.load_messages(session.id)
                    if m.user_id == self._user_id and m.text.strip()]
            for i, m in enumerate(msgs):
                if mid_q:
                    mid = vector_msg_id(m.session_id, m.ts, m.role, m.text)
                    hit = mid == mid_q or mid.startswith(mid_q)
                else:
                    hit = m.ts.startswith(ts_q)
                if hit:
                    return msgs[max(0, i - k): i + k + 1], m
        return [], None

    def _date_tool_args(self) -> tuple[list[dict] | None, Callable[[str, dict], str | dict] | None]:
        """The (tools, executor) for the v0.31 by-time message tools (``messages_on`` /
        ``messages_between`` / ``message_context``); ``(None, None)`` off. Reads **this user's** raw
        messages directly — no embedding, no meaning search. The result is her own transcript →
        **trusted** framing. Bounded by a per-turn call cap, a char budget, and a range-span cap."""
        if not self._date_tool_enabled:
            return None, None
        calls = {"n": 0}

        def execute(name: str, tool_input: dict) -> str | dict:
            calls["n"] += 1
            if calls["n"] > self._date_tool_max_calls:
                return f"(date tool limit reached: {self._date_tool_max_calls} per turn)"
            ti = tool_input or {}
            if name == "message_context":
                mid_in, ts_in = ti.get("msg_id"), ti.get("ts")
                if not ((mid_in or "").strip() or (ts_in or "").strip()):
                    return "(message_context: вкажи msg_id або ts повідомлення)"
                k = ti.get("k")
                try:
                    k = int(k) if k is not None else 3
                except (TypeError, ValueError):
                    k = 3
                window, anchor = self._message_context_window(
                    max(0, min(k, 50)), msg_id=mid_in, ts=ts_in,
                )
                if not window:
                    return f"(повідомлення не знайдено: {(mid_in or ts_in or '—').strip()})"
                return trusted_text(self._format_dated_messages(window, anchor=anchor))
            if name == "messages_on":
                day = (ti.get("date") or "").strip()
                if not _is_ymd(day):
                    return "(messages_on: вкажи дату у форматі РРРР-ММ-ДД)"
                start = end = day
            elif name == "messages_between":
                start = (ti.get("start") or "").strip()
                end = (ti.get("end") or "").strip()
                if not (_is_ymd(start) and _is_ymd(end)):
                    return "(messages_between: вкажи дати у форматі РРРР-ММ-ДД)"
                if start > end:
                    start, end = end, start
                span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
                if span > self._date_tool_max_days:
                    return f"(діапазон завеликий: максимум {self._date_tool_max_days} днів)"
            else:
                return f"error: unknown tool {name!r}"
            msgs = self._messages_in_range(start, end)
            if not msgs:
                return f"(немає повідомлень за {start if start == end else f'{start}…{end}'})"
            return trusted_text(self._format_dated_messages(msgs))

        return DATE_TOOLS, execute

    def _news_tool_args(self) -> tuple[list[dict] | None, Callable[[str, dict], str] | None]:
        """The (tools, executor) for the v0.25 Guardian news tool; ``(None, None)`` when off.

        A fresh ``NewsTools`` (its own per-turn id registry) + a per-turn call cap (``LUMI_NEWS_MAX_CALLS``,
        independent of the loop + wiki caps). The query carries **only what the model passes** — the core
        never augments it with relationship memory, facts, or secrets (no-personal-data rule)."""
        if not self._news_enabled:
            return None, None
        from core.news import NEWS_TOOLS, GuardianProvider, NewsTools

        kwargs = {"http_get": self._news_http_get} if self._news_http_get is not None else {}
        provider = GuardianProvider(
            api_key=self._news_api_key, base_url=self._news_api_url, sections=self._news_sections, **kwargs
        )
        news = NewsTools(
            provider, max_results=self._news_max_results, max_chars=self._news_max_chars, days=self._news_days
        )
        calls = {"n": 0}

        def capped(name: str, tool_input: dict) -> str:
            calls["n"] += 1
            if calls["n"] > self._news_max_calls:
                return (
                    f"(news call limit reached: {self._news_max_calls} per turn — "
                    "answer from what you already found)"
                )
            return news.execute(name, tool_input)

        return NEWS_TOOLS, capped

    def _web_tool_args(self) -> tuple[list[dict] | None, Callable[[str, dict], str] | None]:
        """The (tools, executor) for the v0.27 web lookup tool; ``(None, None)`` when off.

        A fresh ``WebLookupTools`` per turn — bound to the injected ``GeminiSearch`` (the real Gemini
        caller, or a test stub) and **today** from the v0.4 clock (so "upcoming/this week" anchors to the
        real today) — with the per-turn call + answer caps on the instance. The query carries **only what
        the model passes** — the core never augments it with relationship memory, facts, or secrets
        (no-personal-data rule). Paid (a grounded Gemini call); off by default."""
        if not self._web_lookup_enabled:
            return None, None
        from core.weblookup import WEB_LOOKUP_TOOLS, WebLookupTools, gemini_search

        search = self._web_search if self._web_search is not None else gemini_search(model=self._web_lookup_model)
        web = WebLookupTools(
            search=search, today=self._clock().strftime("%Y-%m-%d"),
            max_chars=self._web_lookup_max_chars, max_calls=self._web_lookup_max_calls,
        )
        return WEB_LOOKUP_TOOLS, web.execute

    def _journal_stamp(self) -> str:
        """Compose the **code-owned** diary header blockquote — mood (v0.6 ``resolution``) + biorhythms
        (v0.8 ``format_biorhythms``) + astrology forecast (the v0.6 ``reading``). The v0.8 "code, not model"
        merge: read from the day's cached state, never written by the model, so it matches ``/mood`` +
        ``/biorhythm``. Missing inputs (mood/biorhythms off) are simply omitted — it still writes."""
        from core.biorhythm import format_biorhythms
        from core.mood import strip_theme

        lines: list[str] = []
        if self._mood is not None and self._mood.resolution.strip():
            lines.append(f"> **Настрій:** {' '.join(self._mood.resolution.split())}")
        if self._biorhythms is not None:
            lines.append(f"> **Біоритми:** {format_biorhythms(self._biorhythms)}")
        if self._mood is not None and self._mood.reading.strip():
            forecast = " ".join(strip_theme(self._mood.reading).split())
            if len(forecast) > 300:
                forecast = forecast[:300].rstrip() + "…"
            lines.append(f"> **Прогноз:** {forecast}")
        return "\n".join(lines)

    def _journal(self, *, with_stamp: bool):
        """Build a per-turn :class:`JournalTools` bound to **this user's** dedicated journal root, or
        ``None`` when off. The journal root (``journal_dir/<user_id>``) is **outside** the file-tool
        sandbox, so the raw file tools can never reach it. ``with_stamp`` composes the code-owned
        mood/biorhythm/forecast header (write path); read/list need no stamp."""
        if not self._journal_enabled:
            return None
        from core.journal import JournalTools

        root = Path(self._journal_dir) / self._user_id  # dedicated per-user diary store (NOT files_dir)
        root.mkdir(parents=True, exist_ok=True)
        now = self._clock()
        return JournalTools(
            root, date=now.strftime("%Y-%m-%d"), time=now.strftime("%H:%M"),
            stamp=self._journal_stamp() if with_stamp else "",
            max_chars=self._journal_max_chars,
        )

    def _journal_tool_args(self) -> tuple[list[dict] | None, Callable[[str, dict], str] | None]:
        """The (tools, executor) for the v0.28 journal tools; ``(None, None)`` when off.

        A fresh ``JournalTools`` per turn, bound to **this user's** sandbox (``files_dir/<user_id>``), with
        the day's **code-composed stamp** (mood/biorhythm/forecast) + the date/time from the v0.4 clock.
        The model only supplies the prose ``text`` — the metadata is code-owned (the v0.8 merge). Gated by
        ``LUMI_JOURNAL`` alone (it reuses ``safe_path`` + ``files_dir``; **not** ``LUMI_FILE_TOOL``)."""
        journal = self._journal(with_stamp=True)
        if journal is None:
            return None, None
        from core.journal import JOURNAL_TOOLS

        return JOURNAL_TOOLS, journal.execute

    def journal_read(self, date: str | None = None) -> str:
        """Read a journal entry for the ``/journal`` command (read-only; default today/most recent)."""
        journal = self._journal(with_stamp=False)
        if journal is None:
            return "journal off"
        return journal.execute("journal_read", {"date": date} if date else {})

    def journal_list(self) -> str:
        """List the journal entry dates for the ``/journal list`` command."""
        journal = self._journal(with_stamp=False)
        if journal is None:
            return "journal off"
        return journal.execute("journal_list", {})

    def _log_tool_call(self, name: str, tool_input: dict | None, result: str) -> None:
        """Append a file-tool call to .lumi/tool-log.jsonl as it runs (for `tail -f`). Never raises."""
        if self._tool_log_path is None:
            return
        try:
            rec = {
                "ts": self._clock().isoformat(timespec="seconds"), "kind": name,
                "input": dict(tool_input or {}), "result": (result or "")[:200],
            }
            self._tool_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._tool_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 — tracing must never break a turn
            _usage_log.warning("tool log failed", exc_info=True)

    def reply(self, user_text: str, session: Session, *, images: list[dict] | None = None) -> EmotionState:
        """Run one turn and return Лілі's validated :class:`EmotionState` (v0.3).

        Compacts older-than-window messages into the session digest (in batches),
        then calls the model with the system prompt (+ digest) + the verbatim
        **live tail** (between ``memory_window`` and ``+ batch`` messages) + the
        new line, and persists both messages. The full history stays stored.

        ``images`` (v0.22) — provider-neutral image blocks (``core.images.image_block``) you shared this
        turn; attached to the user message (so she **sees** them) only when ``LUMI_IMAGE`` is on, capped
        at ``LUMI_VISION_MAX``. They are ephemeral — the turn's text is persisted, the image is not.
        """
        self._active_session_id = session.id  # stamp this turn's cache events with the session
        self._ensure_mood()  # compute today's mood once per local day (v0.6)
        self.ensure_day_summaries()  # refresh the day digests the prompt will inject (date-based recall)
        self.ensure_week_summaries()  # refresh the week digests (date-based recall)
        if not self._facts_core_only:  # v0.36: core-only replaces the digest (the session-start re-flag)
            self._ensure_facts_digest()  # consolidate facts into a compact digest (rebuild only when they grow)
        history = self._repo.load_messages(session.id)
        digest = self._maybe_compact(session, history)
        compacted = digest.compacted_count if digest else 0
        # The verbatim tail: messages not yet folded into the digest, capped for
        # safety (in case compaction repeatedly failed).
        live = trim_history(history[compacted:], self._memory_window + self._compaction_batch)
        # v1.1 LUMI-177: the arbiter's data-visible dynamics for this turn (pure; "" when off
        # or nothing applies) — consumed by the {move_rules} token in the think instruction.
        self._move_dynamics = arbiter_dynamics(live) if self._moves_enabled else ""
        turn_ts = self._clock().isoformat()  # one stamp for this turn's stored messages
        messages: list[Message] = [
            {"role": _ROLE_TO_LLM[m.role], "content": self._history_content(m)} for m in live
        ]
        stamped = f"[{format_stamp(turn_ts)}] {user_text}"
        if images and self._image_enabled:  # v0.22: she sees the shared image(s) this turn (capped)
            shared = list(images)[: max(0, self._vision_max)]
            messages.append({"role": "user", "content": [{"type": "text", "text": stamped}, *shared]})
        else:
            messages.append({"role": "user", "content": stamped})

        # v0.17: automatic per-turn RAG — the incoming message is the query; inject the relevant past
        # (deduped against the live window so it never repeats a line already in context).
        system, cache_prefix = self._system_prompt(
            session, recall=self._recall_block(user_text, live),
            fact_recall=self._fact_recall_block(user_text),  # v0.36: top-K relevant non-core facts
        )
        self.last_prompt = {"system": system, "cache_prefix": cache_prefix, "messages": list(messages)}
        self._active_cache_prefix = cache_prefix if self._prompt_cache else None  # fingerprinted for the cache monitor
        # v0.31: the recall tool dedups its moments against what's already in the prompt — the live
        # window + the moments the v0.17 auto-RAG block just surfaced (set in _recall_block above).
        self._turn_dedup_ids = {
            vector_msg_id(m.session_id, m.ts, m.role, m.text) for m in live
        } | self._turn_rag_anchor_ids
        # v0.19/v0.21: when the file tool and/or the wiki tool is on, run the turn as a bounded
        # tool-loop (file sandbox + Wikipedia), with a name-routing executor.
        tools, tool_executor = self._turn_tools()
        raw = self._llm.reply_structured(
            system=system, messages=messages, model=self._model,
            cache_prefix=cache_prefix if self._prompt_cache else None,  # v0.15 cache breakpoint
            tools=tools, tool_executor=tool_executor, max_steps=self._tool_max_steps,
        )
        # Split any <think>…</think> reasoning, then the inline <emotion> tag, out of
        # the reply field; the clean text is shown/stored. Precedence: the model's tagged
        # inline reasoning, then the provider's **native** summarized thinking (Opus extended
        # thinking / OpenAI reasoning.summary — the real thing), then the optional public
        # `thinking_summary` field as a FALLBACK. Native-before-field keeps Opus untouched:
        # its real summary always wins and is never shadowed by a self-written one-liner; the
        # field only lights the box where there is no native summary (e.g. gpt-5.5 with the
        # summary withheld, or an Anthropic model with extended thinking off).
        inline_thinking, reply_text = split_reasoning(str(raw.get("reply") or ""))
        structured_thinking = raw.get("thinking_summary")
        if not isinstance(structured_thinking, str) or not structured_thinking.strip():
            structured_thinking = None
        else:
            structured_thinking = structured_thinking.strip()
        tag_emotion, reply_text = split_emotion(reply_text)
        # v1.1: strip any inline <move> marker (it exists only in replayed history — a reply
        # imitating it must never leak the type); the captured value is the fallback channel
        # when the structured tool can't be forced (the emotion-tag precedent).
        tag_move, reply_text = split_move(reply_text)
        # Лілі's self-chosen answer style — record it (for the status "who") and strip it.
        tag_style, reply_text = split_style(reply_text)
        if tag_style:
            self.last_style = tag_style
        # Strip a leading [date-time] the model may echo from the timestamped history.
        reply_text = strip_leading_stamp(reply_text)
        self.last_thinking = inline_thinking or getattr(self._llm, "last_thinking", None) or structured_thinking
        # v0.38: the monologue's logged tier — ephemeral, never persisted to long-term memory. Off → silent.
        if self.last_thinking and self._think_show != "off":
            _think_log.info("think: %s", self.last_thinking)
        self._accumulate_stats(turn=True)  # the reply turn (+ any housekeeping above already folded in)

        # Merge emotion sources: the structured tool wins when present; otherwise the
        # inline <emotion> tag (the reliable path when the tool can't be forced —
        # extended thinking on); else the validation gate's fallback (calm).
        tag = tag_emotion or {}
        emotion = raw.get("emotion") or tag.get("emotion")
        intensity = raw.get("intensity")
        if intensity is None:
            intensity = tag.get("intensity")
        # Validate/repair into a valid EmotionState (raises EmotionError if no reply).
        state = validate(
            {"reply": reply_text, "emotion": emotion, "intensity": intensity},
            session_id=session.id,
            turn=self.totals.turns,
        )
        self.last_emotion = state
        # v0.10: the additive relational read of the user's message (internal; feeds closeness).
        self.last_relation = validate_relation(raw.get("relation"))
        # v1.1: the additive declared move of this reply — the structured field wins, the
        # stripped inline marker is the fallback (extended-thinking case); validated against
        # the closed enum (unknown/garbled → None, silently), gated so off never stores a value.
        self.last_move = (
            validate_move(raw.get("move") or tag_move) if self._moves_enabled else None
        )
        # Advance the per-user closeness: decay over silence + this turn's relational delta.
        if self._closeness_enabled:
            self._repo.set_closeness(
                update_closeness(
                    self._repo.get_closeness(self._user_id),
                    self.last_relation,
                    self._clock(),
                    self._user_id,
                    self._closeness_tuning,
                )
            )
        self._write_face_signal(state.emotion.value, state.intensity)  # update the viewer (v0.7)

        user_msg = make_message(session.id, self._user_id, "user", user_text, ts=turn_ts)
        lili_msg = make_message(
            session.id,
            self._user_id,
            "lili",
            state.reply,
            ts=turn_ts,
            emotion=state.emotion.value,
            intensity=state.intensity,
            move=self.last_move,  # v1.1: the declared move (None when off/dropped)
        )
        self._repo.append_message(user_msg)
        self._repo.append_message(lili_msg)
        # v0.16: index both new messages for semantic recall (best-effort, after the reply is built).
        self._index_messages([user_msg, lili_msg])
        return state

    def end_session(self, session: Session) -> ShortSummary | None:
        """Close a session: mark it ended, write a short summary, accumulate facts.

        Summarizes the session and extracts durable facts via the model, storing
        a per-user ``ShortSummary`` and any new ``LongTermFact``s (injected at
        startup by LUMI-011). An empty session produces none; a model failure on
        either step degrades to nothing — ending a session never raises
        (ARCHITECTURE §Error handling).
        """
        self._active_session_id = session.id  # stamp the wrap-up (summary + facts) cache events
        try:
            history = self._repo.load_messages(session.id)
            self._repo.end_session(session.id)
            if not history:
                return None
            # Summary + facts run via _housekeeping_reply (extended thinking off) so
            # this internal extraction stays fast and quitting is snappy.
            summary = self._write_summary(session, history)
            self._accumulate_facts(history)
            return summary
        finally:
            # Record this session's token usage + refresh the cost report (after summary/facts, so
            # their cost is attributed to the closing session). Runs even on an empty session.
            self._record_session_usage(session)
            self._render_cache_report()  # refresh the unified prompt-cache & cost report (best-effort)

    def _usage_snapshot(self) -> tuple[int, int, int, int, int]:
        t = self.totals
        return (t.turns, t.input_tokens, t.output_tokens, t.cache_read_tokens, t.cache_write_tokens)

    def _record_session_usage(self, session: Session) -> None:
        """Append the closing session's usage delta to the ledger and re-render the cost report.

        Best-effort: a write/render error is logged and swallowed — never blocks session close.
        """
        if self._usage_ledger_path is None:
            return
        try:
            from core import usage as usage_mod

            now = self._usage_snapshot()
            base = self._usage_base
            self._usage_base = now  # advance the baseline regardless of whether we log this one
            turns, inp, out, cr, cw = (now[i] - base[i] for i in range(5))
            if turns + inp + out + cr + cw <= 0:
                return  # nothing happened this session — don't log an empty row
            record = usage_mod.UsageRecord(
                session_id=session.id,
                user_id=self._user_id,
                model=self._model,
                started_at=session.started_at,
                ended_at=self._clock().isoformat(),
                turns=turns, input=inp, output=out, cache_read=cr, cache_write=cw,
                cache_ttl=self._usage_cache_ttl,
            )
            usage_mod.append_record(self._usage_ledger_path, record)
            if self._usage_report_path is not None:
                usage_mod.write_report(
                    usage_mod.load_records(self._usage_ledger_path),
                    self._usage_report_path,
                    generated_at=self._clock().isoformat(timespec="seconds"),
                )
        except Exception:  # noqa: BLE001 — observability must never break session close
            _usage_log.warning("usage report failed", exc_info=True)

    def _write_summary(self, session: Session, history: list) -> ShortSummary | None:
        try:
            system, msgs = summary_request(history)
            summary_text = self._housekeeping_reply(system, msgs, kind="session-close").strip()
        except Exception:  # noqa: BLE001 — never block session end on a model error
            return None
        if not summary_text:
            return None
        # v0.9: one call → both the detailed summary and a one-line gist.
        detailed, gist = parse_summary(summary_text)
        summary = ShortSummary(
            user_id=self._user_id,
            session_id=session.id,
            summary=detailed,
            gist=gist,
            ts=self._clock().isoformat(),
        )
        self._repo.add_summary(summary)
        return summary

    # --- memory commands (memory.view / memory.clear) -------------------
    def view_memory(self, user_id: str | None = None) -> MemoryView:
        """Snapshot the user's relationship memory (summaries + facts)."""
        uid = user_id or self._user_id
        return MemoryView(
            summaries=[s.summary for s in self._repo.recent_summaries(uid, self._recent_summaries)],
            facts=[f.fact for f in self._repo.facts(uid)],
        )

    def clear_memory(self, user_id: str | None = None) -> None:
        """Wipe the user's short + long-term memory (only this user)."""
        self._repo.clear_memory(user_id or self._user_id)

    # --- Semantic recall indexing (v0.16) — best-effort, never blocks a turn ----
    @property
    def recall_enabled(self) -> bool:
        """Whether semantic-recall indexing/search is active (on **and** an embedder is present)."""
        return self._recall_enabled

    def _index_messages(self, messages: list[Message]) -> None:
        """Embed + store ``messages`` in the per-user vector store (best-effort).

        Guarded by ``recall_enabled``; an embedder error is logged and swallowed — the
        messages are already persisted and get picked up by the next backfill. With chunking on
        (v0.30) a long message yields several chunk records; off → one record per message (v0.16).
        """
        if not self._recall_enabled or self._embedder is None:
            return
        to_index = [m for m in messages if m.text.strip()]
        if not to_index:
            return
        try:
            records = self._embed_and_build(to_index)
        except Exception as exc:  # noqa: BLE001 — best-effort; the messages are stored, retried by backfill
            _recall_log.warning("recall index-on-write failed (message stored; will backfill): %s", exc)
            return
        if records:
            self._repo.add_vectors(records)

    def _chunks_for(self, m: Message) -> list[str]:
        """The passages a message is indexed as: several (v0.30 chunking on, long message) or one
        (off, or a short message — the v0.16 case)."""
        if self._rag_chunk:
            cs = chunk_text(
                m.text, chunk_chars=self._rag_chunk_chars,
                overlap=self._rag_chunk_overlap, threshold=self._rag_chunk_threshold,
            )
            if cs:
                return cs
        return [m.text]

    def _chunk_record_id(self, parent: str, index: int, ctext: str, *, single: bool) -> str:
        """A one-chunk message keeps the **message id** (v0.16 back-compat); a multi-chunk message
        uses a per-chunk content-addressed id."""
        return parent if single else chunk_msg_id(parent, index, ctext)

    def _embed_and_build(self, messages: list[Message]) -> list[VectorRecord]:
        """Chunk → embed (one batch) → build one :class:`VectorRecord` per chunk. Raises on an
        embedder error (the caller logs + degrades). A one-chunk message is the v0.16 record
        (``msg_id == parent_msg_id``, ``chunk_index == 0``)."""
        plans: list[tuple[Message, str, list[str]]] = []  # (message, parent_msg_id, chunk_texts)
        flat: list[str] = []
        for m in messages:
            chunks = self._chunks_for(m)
            if not chunks:
                continue
            parent = vector_msg_id(m.session_id, m.ts, m.role, m.text)
            plans.append((m, parent, chunks))
            flat.extend(c[:self._embed_max_chars] for c in chunks)
        if not flat:
            return []
        vectors = self._embedder.embed(flat)
        records: list[VectorRecord] = []
        vi = 0
        for m, parent, chunks in plans:
            single = len(chunks) == 1
            for ci, ctext in enumerate(chunks):
                stored = ctext[:self._embed_max_chars]
                records.append(VectorRecord(
                    user_id=m.user_id,
                    msg_id=self._chunk_record_id(parent, ci, ctext, single=single),
                    vector=tuple(float(x) for x in vectors[vi]),
                    text=stored, ts=m.ts, role=m.role,
                    parent_msg_id=parent, chunk_index=ci,
                ))
                vi += 1
        return records

    def _message_indexed(self, m: Message) -> bool:
        """Whether ``m`` is already in the vector store — keyed off its **first chunk's** id, so the
        check is idempotent at message granularity whether chunking is on or off."""
        parent = vector_msg_id(m.session_id, m.ts, m.role, m.text)
        chunks = self._chunks_for(m)
        first_id = self._chunk_record_id(parent, 0, chunks[0], single=len(chunks) == 1) if chunks else parent
        return self._repo.has_vector(self._user_id, first_id)

    def backfill_vectors(self, limit: int | None = None) -> int:
        """Embed up to ``limit`` of **this user's** un-indexed messages — **one pass** (idempotent).

        ``_message_indexed`` skips the already-indexed (keyed off the first chunk's id), so repeated
        calls drain the history in batches of ``limit`` (default ``recall_backfill_max``). Returns how
        many **messages** were indexed this pass; recall off / no embedder / a model error → 0.
        :meth:`ensure_backfill` loops it to completion.
        """
        if not self._recall_enabled or self._embedder is None:
            return 0
        cap = self._recall_backfill_max if limit is None else limit
        pending: list[Message] = []
        for session in self._repo.list_sessions(self._user_id):
            for m in self._repo.load_messages(session.id):
                if m.user_id != self._user_id or not m.text.strip():
                    continue
                if not self._message_indexed(m):
                    pending.append(m)
                    if len(pending) >= cap:
                        break
            if len(pending) >= cap:
                break
        if not pending:
            return 0
        try:
            records = self._embed_and_build(pending)
        except Exception as exc:  # noqa: BLE001 — best-effort; retried on the next pass
            _recall_log.warning("recall backfill embed failed (retried next pass): %s", exc)
            return 0
        if records:
            self._repo.add_vectors(records)
        return len(pending)

    def ensure_backfill(self) -> None:
        """Index the **whole** un-indexed history once per process (drains in capped batches).

        Loops :meth:`backfill_vectors` until nothing is left — so a large existing history is fully
        covered, not just one batch. Best run off the UI thread (the TUI calls it at startup).
        """
        if not self._recall_enabled or self._backfilled:
            return
        # If the embedding model changed, the old vectors have a different dimensionality — drop
        # them so the whole history re-indexes with the current model (else /recall would mix dims).
        if self._embed_model and self._repo.vectors_model() != self._embed_model:
            self._repo.reset_vectors(self._embed_model)
        while self.backfill_vectors() > 0:
            pass
        self.backfill_facts()  # v0.36: embed any not-yet-indexed facts (idempotent)
        self._backfilled = True

    def _embed_facts(self, facts: list[LongTermFact]) -> list[VectorRecord]:
        """Embed long-term facts as `kind="fact"` vectors (v0.36) — one content-addressed record each
        (idempotent). Raises on an embedder error (the caller logs + degrades)."""
        valid = [f for f in facts if f.fact.strip()]
        if not valid:
            return []
        vectors = self._embedder.embed([f.fact[: self._embed_max_chars] for f in valid])
        out: list[VectorRecord] = []
        for f, vec in zip(valid, vectors, strict=False):
            fid = fact_vector_id(f.user_id, f.fact)
            out.append(VectorRecord(
                user_id=f.user_id, msg_id=fid, vector=tuple(float(x) for x in vec),
                text=f.fact, ts=f.ts, role="fact", parent_msg_id=fid, chunk_index=0, kind="fact",
            ))
        return out

    def _index_facts(self, facts: list[LongTermFact]) -> None:
        """Embed + store new facts as `kind="fact"` vectors (best-effort; guarded by ``recall_enabled``).
        An embedder error is logged + swallowed — the facts are stored and picked up by the backfill."""
        if not self._recall_enabled or self._embedder is None or not facts:
            return
        try:
            records = self._embed_facts(facts)
        except Exception as exc:  # noqa: BLE001 — best-effort; the facts are stored, retried by backfill
            _recall_log.warning("fact index-on-write failed (fact stored; will backfill): %s", exc)
            return
        if records:
            self._repo.add_vectors(records)

    def backfill_facts(self) -> int:
        """Embed this user's not-yet-indexed facts as `kind="fact"` vectors (v0.36). Idempotent
        (content-addressed ids; ``add_vectors`` skips ones already present). Returns the count added."""
        if not self._recall_enabled or self._embedder is None:
            return 0
        pending = [
            f for f in self._repo.facts(self._user_id)
            if f.fact.strip() and not self._repo.has_vector(self._user_id, fact_vector_id(f.user_id, f.fact))
        ]
        if not pending:
            return 0
        try:
            records = self._embed_facts(pending)
        except Exception as exc:  # noqa: BLE001 — best-effort
            _recall_log.warning("fact backfill failed: %s", exc)
            return 0
        self._repo.add_vectors(records)
        return len(records)

    def _session_vector_ids(self, session_id: str) -> set[str]:
        """The vector (message) ids of one session's messages — to drop a session's own hits from
        recall. Computed fresh from the store (no stale position cache), so messages added this turn
        are covered."""
        return {
            vector_msg_id(m.session_id, m.ts, m.role, m.text)
            for m in self._repo.load_messages(session_id)
            if m.user_id == self._user_id and m.text.strip()
        }

    def recall(
        self, query: str, k: int | None = None, *, exclude_session: str | None = None,
        before: str | None = None, after: str | None = None, scope: str = "messages",
    ) -> list[tuple[float, VectorRecord]]:
        """Explicit semantic search (the ``/recall`` command, v0.16).

        Embed ``query`` → top-``k`` over **this user's** vectors → dated matches (descending).
        Backfills a cold store first so it still answers. Off / no embedder / empty query /
        an embed error → ``[]`` — **never raises**. Scoped to the active user (isolation).

        ``exclude_session`` drops hits whose message belongs to that session — used to skip the
        **current conversation's own echoes** (already in the live window) so an older source
        surfaces past the top-``k`` cutoff. ``before`` / ``after`` (``YYYY-MM-DD`` date prefixes)
        scope the meaning search to the half-open date range ``[after, before)``. When any filter is
        set the search over-fetches, then trims to ``k`` post-filter. A chunk is matched by its
        ``parent_msg_id`` (the message it came from).
        """
        if not self._recall_enabled or self._embedder is None or not query.strip():
            return []
        self.ensure_backfill()
        k = k or self._recall_k
        filtered = bool(exclude_session or before or after)
        pool = max(k * 5, 60) if filtered else k  # over-fetch so the filter still yields ~k
        try:
            [vec] = self._embedder.embed([query[:self._embed_max_chars]], is_query=True)  # QUERY side
            # v0.36: scope to one memory layer; "messages" (default) is byte-identical to pre-v0.36.
            kind = {"messages": "message", "facts": "fact", "all": None}.get(scope, "message")
            hits = self._repo.search_vectors(self._user_id, list(vec), pool, kind=kind)
        except Exception as exc:  # noqa: BLE001 — recall must never break the UI
            _recall_log.warning("recall search failed: %s", exc)
            return []
        if exclude_session:
            own = self._session_vector_ids(exclude_session)
            hits = [(s, r) for (s, r) in hits if r.parent_msg_id not in own]
        if before:
            hits = [(s, r) for (s, r) in hits if r.ts[:10] < before]
        if after:
            hits = [(s, r) for (s, r) in hits if r.ts[:10] >= after]
        # v0.36: drop obsolete facts — excluded from every fact path (recall tool + auto fact-RAG).
        if any(r.kind == "fact" for _s, r in hits):
            stale = {f.fact for f in self._repo.facts(self._user_id) if f.obsolete}
            if stale:
                hits = [(s, r) for (s, r) in hits if not (r.kind == "fact" and r.text in stale)]
        return hits[:k]

    def recall_moments(
        self, query: str, k: int | None = None, *, exclude_session: str | None = None,
        window_ids: set[str] | None = None, before: str | None = None, after: str | None = None,
        scope: str = "messages",
    ) -> list[str]:
        """Explicit `/recall` as **dated dialogue snippets** (the v0.16 hits widened with their
        neighbours, anchor + score marked) — the same context expansion the per-turn RAG uses, so
        search results read as moments, not orphan lines. ``exclude_session`` skips the current
        conversation's own messages as matched anchors (their neighbours may still render as
        context). ``window_ids`` (v0.31) dedups the result against what's already in the prompt — the
        live window + the auto-RAG block (the recall-tool path); ``None`` → no dedup (the /recall
        command). ``before`` / ``after`` (YYYY-MM-DD) scope the meaning search to a date range.
        Empty / off → ``[]``; never raises."""
        hits = self.recall(query, k, exclude_session=exclude_session, before=before, after=after, scope=scope)
        if window_ids:
            hits = [(s, r) for s, r in hits if r.parent_msg_id not in window_ids]
        if not hits:
            return []
        return self._expand_hits(hits, window_ids or set(), show_score=True)

    @property
    def rag_enabled(self) -> bool:
        """Whether automatic per-turn RAG injection is active (v0.17)."""
        return self._rag_enabled

    def _recall_block(self, query: str, live: list[Message] | None = None) -> str | None:
        """The per-turn RAG block (v0.17): the query-relevant past as dated «relevant moments»,
        each **widened to a small window of its session neighbours** (the moment, not the line) —
        or ``None`` when off / no hit above the floor. **Best-effort, never blocks a turn.**

        Reuses :meth:`recall` (search → relevance floor), drops hits already in the live window
        (no double-context), expands each survivor to a ±``rag_w`` snippet (anchor marked, overlaps
        merged, neighbour lines in the window dropped), and caps by ``rag_max_chars``.
        """
        self._turn_rag_anchor_ids = set()  # v0.31: reset; populated below with what this block surfaces
        if not self._rag_enabled:
            return None
        hits = [(s, r) for s, r in self.recall(query, self._rag_k) if s >= self._rag_floor]
        if not hits:
            return None
        window_ids = {vector_msg_id(m.session_id, m.ts, m.role, m.text) for m in (live or [])}
        # anchor dedup (LUMI-071): a chunk's parent_msg_id is the message id, so this matches whether
        # the hit is a whole-message vector (v0.16) or a chunk (v0.30).
        hits = [(s, r) for s, r in hits if r.parent_msg_id not in window_ids]
        if not hits:
            return None
        self._turn_rag_anchor_ids = {r.parent_msg_id for _s, r in hits}  # v0.31: dedup the recall tool
        try:
            snippets = self._expand_hits(hits, window_ids)
        except Exception:  # noqa: BLE001 — expansion is best-effort; fall back to bare anchor lines
            _recall_log.warning("recall context expansion failed; using bare anchors")
            snippets = [
                f"— {r.ts[:10]} —\n  {r.ts[11:16]} {self._who(r.role)}: "
                f"{_snippet(r.text, self._rag_snippet_chars)}  ← (matched)"
                for _s, r in hits
            ]
        # Char budget across the whole block: keep whole snippets while they fit (most-relevant
        # first); if even the top snippet overflows (dense hits merged into one), truncate it.
        out, used = [], 0
        for snip in snippets:
            remaining = self._rag_max_chars - used
            if remaining <= 0:
                break
            if len(snip) > remaining:
                if not out:
                    out.append(snip[:remaining].rstrip() + " …")
                break
            out.append(snip)
            used += len(snip) + 2
        return "\n\n".join(out) if out else None

    def _fact_recall_block(self, query: str) -> str | None:
        """The per-turn **fact-RAG** block (v0.36): the top-``LUMI_FACTS_RAG_K`` facts most relevant
        to the incoming ``query``, as a `# Релевантні факти` list — or ``None`` when off / no hit above
        the floor. The *push* complement to the static core (LUMI-143) + the recall *pull* (LUMI-141).

        Excludes the **`core=true`** facts (already injected — no double-push) + duplicates; her own
        knowledge → trusted (no de-id). **Best-effort, never blocks a turn.**
        """
        if not self._facts_rag or self._embedder is None or not query.strip():
            return None
        core_texts = {f.fact for f in self._repo.facts(self._user_id) if f.core}  # already in the prompt
        try:  # over-fetch so the core/dup filter still yields ~K
            hits = [(s, r) for s, r in self.recall(query, max(self._facts_rag_k * 3, 12), scope="facts")
                    if s >= self._rag_floor]
        except Exception:  # noqa: BLE001 — best-effort; never break a turn
            _recall_log.warning("fact-RAG search failed")
            return None
        lines: list[str] = []
        seen: set[str] = set()
        for _s, r in hits:
            t = r.text.strip()
            if not t or t in core_texts or t in seen:  # skip core (already injected) + duplicates
                continue
            seen.add(t)
            lines.append(f"- {t}")
            if len(lines) >= self._facts_rag_k:
                break
        return "\n".join(lines) if lines else None

    @staticmethod
    def _who(role: str) -> str:
        return "Лілі" if role == "lili" else "ти"

    def _position_of(self, msg_id: str) -> tuple[str, int] | None:
        """Resolve a hit's ``msg_id`` to ``(session_id, index)`` via a lazily-built per-user map
        (no re-index). A miss (e.g. a message added after the map was built) → ``None`` (the caller
        degrades to a bare anchor line)."""
        if self._position_index is None:
            idx: dict[str, tuple[str, int]] = {}
            for session in self._repo.list_sessions(self._user_id):
                for i, m in enumerate(self._repo.load_messages(session.id)):
                    if m.user_id == self._user_id and m.text.strip():
                        idx[vector_msg_id(m.session_id, m.ts, m.role, m.text)] = (session.id, i)
            self._position_index = idx
        return self._position_index.get(msg_id)

    def _passage_text(self, text: str, matched: set[int]) -> str:
        """Render a long (multi-chunk) message as its **relevant passage** (v0.30): the matched
        chunk(s) ± ``chunk_w`` adjacent chunks of the same message, de-overlapped, with ``…`` for
        gaps and the trimmed ends. A short (one-chunk) message → the whole text (snippet-capped) =
        v0.16. The whole long message is never injected; the per-line size is capped by ``rag_max_chars``."""
        chunks = chunk_text(
            text, chunk_chars=self._rag_chunk_chars, overlap=self._rag_chunk_overlap,
            threshold=self._rag_chunk_threshold,
        ) or [text]
        if len(chunks) == 1:
            return _snippet(text, self._rag_snippet_chars)
        cw = self._rag_chunk_w
        keep = sorted({
            j for mi in matched
            for j in range(max(0, mi - cw), min(len(chunks) - 1, mi + cw) + 1)
        })
        out: list[str] = []
        if keep and keep[0] > 0:
            out.append("…")
        prev: int | None = None
        for j in keep:
            seg = chunks[j]
            if prev is not None:
                if j == prev + 1:
                    seg = seg[self._rag_chunk_overlap:]  # de-overlap adjacent chunks
                else:
                    out.append("…")  # a gap between kept spans
            out.append(seg)
            prev = j
        if keep and keep[-1] < len(chunks) - 1:
            out.append("…")
        return "".join(out)[:self._rag_max_chars]  # the passage, bounded by the block budget

    def _expand_hits(
        self,
        hits: list[tuple[float, VectorRecord]],
        window_ids: set[str],
        *,
        show_score: bool = False,
    ) -> list[str]:
        """Widen each hit to a ±``rag_w`` session-neighbour snippet (anchor marked); merge
        overlapping windows within a session; drop neighbour lines already in the window. Returns
        dated snippet strings, most-relevant first. A hit that doesn't resolve → a bare anchor line.

        With chunking on (v0.30) a matched **chunk** resolves to its parent message's position, and the
        anchor message renders as its **passage** (the matched chunk ± ``chunk_w`` adjacent chunks)
        rather than the whole message; neighbour messages render whole. Off → the v0.17 per-message
        behaviour, byte-for-byte. ``show_score`` annotates the anchor mark with the cosine score.
        """
        def mark(score: float | None) -> str:
            if score is None:
                return ""
            return f"  ← (matched, {score:.2f})" if show_score else "  ← (matched)"

        w = self._rag_w
        # session_id → [(parent_position, score, chunk_index)]; positions resolve via the MESSAGE id.
        by_session: dict[str, list[tuple[int, float, int]]] = {}
        bare: list[tuple[float, str]] = []
        for score, rec in hits:
            pos = self._position_of(rec.parent_msg_id)  # the message id (== msg_id for a one-chunk record)
            if pos is None:
                bid = f"  #{rec.parent_msg_id[:8]}" if show_score else ""  # an id to chain into message_context
                bare.append((score, f"— {rec.ts[:10]} —\n  {rec.ts[11:16]} {self._who(rec.role)}: "
                                    f"{_snippet(rec.text, self._rag_snippet_chars)}{mark(score)}{bid}"))
                continue
            by_session.setdefault(pos[0], []).append((pos[1], score, rec.chunk_index))

        snippets: list[tuple[float, str]] = []
        for session_id, anchors in by_session.items():
            msgs = self._repo.load_messages(session_id)
            anchor_score: dict[int, float] = {}        # position → best cosine score
            anchor_chunks: dict[int, set[int]] = {}    # position → matched chunk indices (for the passage)
            for i, sc, ci in anchors:
                anchor_score[i] = max(sc, anchor_score.get(i, sc))
                anchor_chunks.setdefault(i, set()).add(ci)
            # Merge overlapping/adjacent ±w windows into ranges, carrying the best score.
            ranges: list[list[float]] = []  # [start, end, rank]
            for i in sorted(anchor_score):
                score = anchor_score[i]
                start, end = max(0, i - w), min(len(msgs) - 1, i + w)
                if ranges and start <= ranges[-1][1] + 1:
                    ranges[-1][1] = max(ranges[-1][1], end)
                    ranges[-1][2] = max(ranges[-1][2], score)
                else:
                    ranges.append([start, end, score])
            for start, end, rank in ranges:
                lines = []
                for p in range(int(start), int(end) + 1):
                    m = msgs[p]
                    mid = vector_msg_id(m.session_id, m.ts, m.role, m.text)
                    if mid in window_ids:  # dedup the whole snippet, not just the anchor
                        continue
                    if self._rag_chunk and p in anchor_chunks:  # the long parent → its passage
                        body = self._passage_text(m.text, anchor_chunks[p])
                    else:                                        # a neighbour (or v0.16) → whole, capped
                        body = _snippet(m.text, self._rag_snippet_chars)
                    idtag = f"  #{mid[:8]}" if (show_score and p in anchor_score) else ""  # chainable id
                    lines.append(
                        f"  {m.ts[11:16]} {self._who(m.role)}: {body}{mark(anchor_score.get(p))}{idtag}"
                    )
                if lines:
                    date = msgs[int(start)].ts[:10]
                    snippets.append((rank, f"— {date} —\n" + "\n".join(lines)))

        snippets.extend(bare)
        snippets.sort(key=lambda x: x[0], reverse=True)  # most relevant first
        return [text for _, text in snippets]

    def _accumulate_facts(self, history: list) -> None:
        try:
            system, msgs = facts_request(history)
            text = self._housekeeping_reply(system, msgs, kind="session-close")
        except Exception:  # noqa: BLE001 — facts are best-effort
            return
        existing = {f.fact for f in self._repo.facts(self._user_id)}
        new_facts: list[LongTermFact] = []
        for fact, is_core in parse_facts_with_core(text):  # v0.36: the [C] marker = initial core guess
            if fact in existing:
                continue  # dedup against what's already stored
            lf = LongTermFact(user_id=self._user_id, fact=fact, meta="", confidence=0.5,
                              ts=now_iso(), core=is_core)
            self._repo.add_fact(lf)
            existing.add(fact)
            new_facts.append(lf)
        self._index_facts(new_facts)  # v0.36: embed the new facts (kind="fact"; best-effort)


def build_core(
    *,
    config: Config | None = None,
    llm: LLMClient | None = None,
    repository: Repository | None = None,
    user_id: str = DEFAULT_USER_ID,
    telegram_sink: Callable[[str, str], None] | None = None,
) -> Core:
    """Wire a :class:`Core` from config.

    Defaults to the real Anthropic backend and the local JSON store; tests inject
    a ``MockLLMClient`` and a temp-file ``JsonRepository``. The Anthropic SDK is
    only reached when ``llm`` is not supplied. ``telegram_sink`` (v0.24) is the
    callable the TUI supplies for ``send_image`` (the single outbox writer); ``None``
    → the tool reports the bridge isn't connected.
    """
    cfg = config or load_config()

    if repository is None:
        from state.local_store import JsonRepository  # imported here to keep core/ light

        repository = JsonRepository(cfg.store_path)

    if llm is None:
        llm = build_llm(cfg)  # v0.18: pick the backend from cfg.provider (anthropic by default)

    # v0.37 LUMI-148: the `/model` runtime toggle rebuilds the client for a new (provider, model) from the
    # already-loaded config keys. The closure captures cfg so Core stays config-agnostic (just a callback).
    def _llm_factory(provider: str, model: str) -> LLMClient:
        return build_llm(replace(cfg, provider=provider, model=model))

    # v0.16 semantic recall: build the embedder only when recall is on. A build failure
    # (e.g. a cloud provider with no key) degrades recall to off rather than crashing startup.
    embedder: Embedder | None = None
    if cfg.recall:
        from core.embedder import EmbedderError, build_embedder

        try:
            embedder = build_embedder(cfg.embed_provider, cfg.embed_model, api_key=cfg.embed_api_key)
        except EmbedderError:
            embedder = None

    canon = load_canon(cfg.canon_path)
    # v0.38 Inner Voice: load the authored think instruction when on; a missing/empty file degrades to
    # the generic REASONING_DIRECTIVE (logged, never a crash). Off → the directive, byte-identical.
    reasoning_directive = REASONING_DIRECTIVE
    if cfg.inner_voice:
        voice = load_inner_voice(cfg.inner_voice_path)
        if voice:
            reasoning_directive = voice
        else:
            _core_log.warning("LUMI_INNER_VOICE on but %s missing/empty — using REASONING_DIRECTIVE",
                              cfg.inner_voice_path)
    # v1.1 LUMI-178: with moves on, the v2 (moves) think instruction takes over — the
    # retrospective → typed voices → arbiter format consuming {move_rules} (LUMI-177). The v1
    # file stays untouched; a missing/empty v2 file degrades to the chain above (logged).
    if cfg.moves:
        moves_voice = load_inner_voice(cfg.inner_voice_moves_path)
        if moves_voice:
            reasoning_directive = moves_voice
        else:
            _core_log.warning("LUMI_MOVES on but %s missing/empty — keeping the v1 think instruction",
                              cfg.inner_voice_moves_path)
    # v0.11 face themes: load the manifest here (the composition root), so the Core class
    # itself stays interface-independent — it only receives the theme data.
    from viewer.themes import load_themes  # local import: keep core/ free of a viewer dependency

    _faces_themes = load_themes(cfg.faces_dir)
    return Core(
        llm=llm,
        repository=repository,
        canon=canon,
        reasoning_directive=reasoning_directive,
        think_show=cfg.think_show,
        model=cfg.model,
        provider=cfg.provider,
        llm_factory=_llm_factory,
        model_aliases=cfg.model_aliases,
        model_profiles=cfg.model_profiles,
        active_profile=cfg.model_profile,
        model_think=cfg.model_think,
        model_mood=cfg.model_mood,
        model_housekeeping=cfg.model_housekeeping,
        user_id=user_id,
        memory_window=cfg.memory_window,
        compaction_batch=cfg.compaction_batch,
        recent_summaries=cfg.recent_summaries,
        session_days=cfg.session_days,
        session_detail_n=cfg.session_detail_n,
        session_format=cfg.session_format,
        day_days=cfg.day_days,
        week_days=cfg.week_days,
        max_day_rows=cfg.max_day_rows,
        max_week_rows=cfg.max_week_rows,
        memory_index=cfg.memory_index,
        styles=load_styles(cfg.styles_path),
        meta_styles=load_meta_styles(cfg.styles_path),
        meta_descriptions=load_meta_descriptions(cfg.styles_path),
        closeness_levels=load_levels(cfg.closeness_path),
        closeness_enabled=cfg.closeness,
        closeness_tuning=cfg.closeness_tuning,
        moves_enabled=cfg.moves,
        facts_digest_enabled=cfg.facts_digest,
        prompt_cache=cfg.prompt_cache,
        embedder=embedder,
        recall_enabled=cfg.recall,
        recall_k=cfg.recall_k,
        recall_tool_enabled=cfg.recall_tool,
        recall_tool_k=cfg.recall_tool_k,
        recall_tool_max_calls=cfg.recall_tool_max_calls,
        date_tool_enabled=cfg.date_tool,
        date_tool_max_chars=cfg.date_tool_max_chars,
        date_tool_max_days=cfg.date_tool_max_days,
        date_tool_max_calls=cfg.date_tool_max_calls,
        embed_max_chars=cfg.embed_max_chars,
        # The vectors-staleness tag includes the char cap AND (v0.30) the chunk size when chunking is
        # on, so changing the model, the cap, or the chunk size — or toggling chunking — re-embeds the
        # history (ensure_backfill resets on a tag change). Off → the tag is unchanged from v0.16/0.17.
        embed_model=(
            f"{cfg.embed_model}@{cfg.embed_max_chars}"
            + (f"@chunk{cfg.rag_chunk_chars}" if cfg.rag_chunk else "")
        ),
        rag_enabled=cfg.rag,
        rag_k=cfg.rag_k,
        rag_floor=cfg.rag_floor,
        facts_rag=cfg.facts_rag,
        facts_rag_k=cfg.facts_rag_k,
        rag_max_chars=cfg.rag_max_chars,
        rag_w=cfg.rag_w,
        rag_snippet_chars=cfg.rag_snippet_chars,
        rag_chunk=cfg.rag_chunk,
        rag_chunk_chars=cfg.rag_chunk_chars,
        rag_chunk_overlap=cfg.rag_chunk_overlap,
        rag_chunk_threshold=cfg.rag_chunk_threshold,
        rag_chunk_w=cfg.rag_chunk_w,
        facts_digest_max=cfg.facts_digest_max,
        facts_core_max=cfg.facts_core_max,
        facts_core_only=cfg.facts_core_only,
        recall_scope=cfg.recall_scope,
        natal=load_natal(cfg.natal_path),
        mood_enabled=cfg.mood,
        mood_log_path=cfg.store_path.parent / "mood.log",
        theme_descriptions={n: d for n, d in _faces_themes.descriptions.items() if d},
        default_theme=_faces_themes.default,
        biorhythms_enabled=cfg.biorhythms,
        cycle_enabled=cfg.cycle,
        face_signal=cfg.face_signal or cfg.store_path.parent / "face.txt",
        thoughts_enabled=cfg.thoughts,
        thoughts_window_h=cfg.thoughts_window_h,
        thoughts_max_lines=cfg.thoughts_max_lines,
        thoughts_interval_s=cfg.thoughts_interval_s,
        thoughts_cap=cfg.thoughts_cap,
        thoughts_spoken_ratio=cfg.thoughts_spoken_ratio,
        thoughts_show=cfg.thoughts_show,
        thoughts_context=cfg.thoughts_context,
        thought_tools_enabled=cfg.thought_tools,
        thought_journal=cfg.thought_journal,
        thought_wiki=cfg.thought_wiki,
        thought_news=cfg.thought_news,
        thought_web=cfg.thought_web,
        thought_prompt=cfg.thought_prompt,
        thought_image=cfg.thought_image,
        thought_imagine_cap=cfg.thought_imagine_cap,
        quiet_hours=cfg.quiet_hours,
        thoughts_quiet_hours=cfg.thoughts_quiet_hours,
        usage_ledger_path=(cfg.store_path.parent / "usage-ledger.jsonl") if cfg.usage_report else None,
        usage_report_path=(cfg.store_path.parent / "usage-report.md") if cfg.usage_report else None,
        usage_cache_ttl=cfg.prompt_cache_ttl,
        file_tool_enabled=cfg.file_tool,
        files_dir=cfg.files_dir,
        file_read_lines=cfg.file_read_lines,
        file_read_max_total=cfg.file_read_max_total,
        file_read_max_chars=cfg.file_read_max_chars,
        file_find_max=cfg.file_find_max,
        file_write_max=cfg.file_write_max,
        file_copy_max=cfg.file_copy_max,
        file_search_max_files=cfg.file_search_max_files,
        file_search_max_lines=cfg.file_search_max_lines,
        file_search_max_chars=cfg.file_search_max_chars,
        file_around_max_k=cfg.file_around_max_k,
        file_date_max_days=cfg.file_date_max_days,
        tool_max_steps=cfg.tool_max_steps,
        file_tool_trace=cfg.file_tool_trace,
        wiki_enabled=cfg.wiki,
        wiki_lang=cfg.wiki_lang,
        wiki_base_url=cfg.wiki_base_url,
        wiki_max_chars=cfg.wiki_max_chars,
        wiki_max_calls=cfg.wiki_max_calls,
        news_enabled=cfg.news_tool,
        news_api_key=cfg.news_api_key,
        news_api_url=cfg.news_api_url,
        news_sections=cfg.news_sections,
        news_max_results=cfg.news_max_results,
        news_max_chars=cfg.news_max_chars,
        news_max_calls=cfg.news_max_calls,
        news_days=cfg.news_days,
        web_lookup_enabled=cfg.web_lookup,
        web_lookup_model=cfg.web_lookup_model,
        web_lookup_max_calls=cfg.web_lookup_max_calls,
        web_lookup_max_chars=cfg.web_lookup_max_chars,
        journal_enabled=cfg.journal,
        journal_dir=cfg.journal_dir,
        journal_max_chars=cfg.journal_max_chars,
        image_enabled=cfg.image,
        vision_max=cfg.vision_max,
        image_max_bytes=cfg.image_max_bytes,
        image_model=cfg.image_model,
        image_size=cfg.image_size,
        image_max_gen=cfg.image_max_gen,
        image_show=cfg.image_show,
        image_signal_path=(cfg.store_path.parent / "image.txt") if cfg.image else None,
        telegram_sink=telegram_sink,
        tool_log_path=(cfg.store_path.parent / "tool-log.jsonl") if cfg.file_tool_trace else None,
        cache_log_path=(cfg.store_path.parent / "cache-log.jsonl") if cfg.cache_monitor else None,
        cache_report_path=(cfg.store_path.parent / "cache-report.md") if cfg.cache_monitor else None,
        cache_monitor=cfg.cache_monitor,
    )
