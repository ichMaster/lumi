"""Unit tests for config loading (LUMI-001)."""

from pathlib import Path

import pytest

from core.config import DEFAULT_MEMORY_WINDOW, DEFAULT_MODEL, Config, _parse_duration_s, load_config


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5h", 18000), ("1h", 3600), ("30m", 1800), ("300s", 300),
        ("300", 300), ("0.5h", 1800), (None, None), ("", None), ("nonsense", None),
    ],
)
def test_parse_duration_s(raw, expected):
    assert _parse_duration_s(raw) == expected


def test_gemini_cache_ttl_override(monkeypatch):
    monkeypatch.setenv("LUMI_GEMINI_CACHE_TTL", "5h")
    assert load_config(load_env=False).gemini_cache_ttl_s == 18000


def test_gemini_cache_ttl_unset_is_none(monkeypatch):
    monkeypatch.delenv("LUMI_GEMINI_CACHE_TTL", raising=False)
    assert load_config(load_env=False).gemini_cache_ttl_s is None


def test_reasoning_defaults_on(monkeypatch):
    monkeypatch.delenv("LUMI_REASONING", raising=False)
    assert load_config(load_env=False).reasoning is True  # on by default → unchanged behaviour


def test_reasoning_env_off(monkeypatch):
    monkeypatch.setenv("LUMI_REASONING", "off")
    assert load_config(load_env=False).reasoning is False


def test_stream_defaults_off(monkeypatch):
    monkeypatch.delenv("LUMI_STREAM", raising=False)
    assert load_config(load_env=False).stream is False  # v1.4: off by default → blocking, byte-identical


def test_stream_env_on(monkeypatch):
    monkeypatch.setenv("LUMI_STREAM", "on")
    assert load_config(load_env=False).stream is True


def test_stt_device_default_empty(monkeypatch):
    monkeypatch.delenv("LUMI_STT_DEVICE", raising=False)
    assert load_config(load_env=False).stt_device == ""


def test_stt_device_env(monkeypatch):
    monkeypatch.setenv("LUMI_STT_DEVICE", "MacBook Pro Microphone")
    assert load_config(load_env=False).stt_device == "MacBook Pro Microphone"


def test_defaults_without_env(monkeypatch):
    for key in (
        "LUMI_MODEL",
        "LUMI_PROVIDER",
        "LUMI_MODEL_PROFILE",  # v0.41: a leaked startup profile would re-point provider/model/tiers
        "LUMI_CANON_PATH",
        "LUMI_STORE_PATH",
        "LUMI_MEMORY_WINDOW",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = load_config(load_env=False)

    assert cfg.provider == "anthropic"
    assert cfg.model == DEFAULT_MODEL
    assert cfg.model.startswith("claude-haiku")
    assert cfg.canon_path.name == "lili.md"
    assert cfg.store_path.name == "store.json"
    assert cfg.memory_window == DEFAULT_MEMORY_WINDOW


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("LUMI_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("LUMI_CANON_PATH", "/tmp/custom-canon.md")
    monkeypatch.setenv("LUMI_MEMORY_WINDOW", "5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    cfg = load_config(load_env=False)

    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.canon_path == Path("/tmp/custom-canon.md")
    assert cfg.memory_window == 5
    assert cfg.api_key == "sk-test-not-real"


def test_thoughts_max_lines_default_and_override(monkeypatch):
    # v0.12 prompt cap, now .env-tunable (LUMI_THOUGHTS_MAX_LINES).
    from core.thoughts import THOUGHTS_MAX_LINES
    monkeypatch.delenv("LUMI_THOUGHTS_MAX_LINES", raising=False)
    assert load_config(load_env=False).thoughts_max_lines == THOUGHTS_MAX_LINES  # default 12
    monkeypatch.setenv("LUMI_THOUGHTS_MAX_LINES", "20")
    assert load_config(load_env=False).thoughts_max_lines == 20  # override


def test_memory_index_default_off_and_override(monkeypatch):
    # v0.34 LUMI-136: day/week digests as a one-line dated index — off by default.
    monkeypatch.delenv("LUMI_MEMORY_INDEX", raising=False)
    assert load_config(load_env=False).memory_index is False
    monkeypatch.setenv("LUMI_MEMORY_INDEX", "on")
    assert load_config(load_env=False).memory_index is True


def test_session_detail_n_default_all_and_override(monkeypatch):
    # v0.35: how many recent sessions to add — unset → None (= all); "0" → 0 (= none); "5" → 5 (last 5).
    monkeypatch.delenv("LUMI_SESSION_DETAIL_N", raising=False)
    assert load_config(load_env=False).session_detail_n is None
    monkeypatch.setenv("LUMI_SESSION_DETAIL_N", "0")
    assert load_config(load_env=False).session_detail_n == 0
    monkeypatch.setenv("LUMI_SESSION_DETAIL_N", "5")
    assert load_config(load_env=False).session_detail_n == 5


def test_session_format_default_summary_and_override(monkeypatch):
    # v0.35: the form for added sessions — "summary" (default) or "gist"; unknown → summary.
    monkeypatch.delenv("LUMI_SESSION_FORMAT", raising=False)
    assert load_config(load_env=False).session_format == "summary"
    monkeypatch.setenv("LUMI_SESSION_FORMAT", "gist")
    assert load_config(load_env=False).session_format == "gist"
    monkeypatch.setenv("LUMI_SESSION_FORMAT", "bogus")
    assert load_config(load_env=False).session_format == "summary"  # unknown → safe default


def test_closeness_mood_shift_scale_default_and_override(monkeypatch):
    # the daily mood-shift strength (0..1; on/off accepted); unset → full (1.0).
    monkeypatch.delenv("LUMI_CLOSENESS_MOOD_SHIFT", raising=False)
    assert load_config(load_env=False).closeness_tuning.mood_shift_scale == 1.0  # unset → full
    monkeypatch.setenv("LUMI_CLOSENESS_MOOD_SHIFT", "0.5")
    assert load_config(load_env=False).closeness_tuning.mood_shift_scale == 0.5  # half
    monkeypatch.setenv("LUMI_CLOSENESS_MOOD_SHIFT", "off")
    assert load_config(load_env=False).closeness_tuning.mood_shift_scale == 0.0  # disabled


def test_recall_defaults_off_and_local_embedder(monkeypatch):
    # v0.16 semantic recall: the whole feature is off by default; the default embedder
    # is the private LOCAL one — nothing is sent anywhere unless explicitly configured.
    for key in ("LUMI_RECALL", "LUMI_EMBED_PROVIDER", "LUMI_EMBED_MODEL",
                "VOYAGE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    cfg = load_config(load_env=False)
    assert cfg.recall is False
    assert cfg.embed_provider == "local"
    assert "multilingual" in cfg.embed_model
    assert cfg.embed_api_key == ""  # local → no cloud key


def test_recall_on_and_cloud_key_resolves_by_provider(monkeypatch):
    monkeypatch.setenv("LUMI_RECALL", "on")
    monkeypatch.setenv("LUMI_EMBED_PROVIDER", "voyage")
    monkeypatch.setenv("VOYAGE_API_KEY", "vk-test-not-real")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-other")
    cfg = load_config(load_env=False)
    assert cfg.recall is True
    assert cfg.embed_provider == "voyage"
    assert cfg.embed_api_key == "vk-test-not-real"  # picked by provider, not OpenAI's
    assert cfg.embed_model == "voyage-3"  # model defaults to a Voyage model, not the local e5 one
    assert "embed_api_key" not in repr(cfg)  # the secret stays out of repr


def test_recall_k_default_and_override(monkeypatch):
    monkeypatch.delenv("LUMI_RECALL_K", raising=False)
    assert load_config(load_env=False).recall_k == 5  # default top-K
    monkeypatch.setenv("LUMI_RECALL_K", "8")
    assert load_config(load_env=False).recall_k == 8


def test_embed_max_chars_default_and_override(monkeypatch):
    monkeypatch.delenv("LUMI_EMBED_MAX_CHARS", raising=False)
    assert load_config(load_env=False).embed_max_chars == 2000  # safe for the local model
    monkeypatch.setenv("LUMI_EMBED_MAX_CHARS", "8000")
    assert load_config(load_env=False).embed_max_chars == 8000


def test_rag_defaults_off_with_k_and_floor(monkeypatch):
    # v0.17 automatic per-turn RAG is OFF by default (off → behaves like v0.16). Clear every RAG var
    # so a dev's .env (loaded via load_dotenv elsewhere) can't leak and shift a default under test.
    for key in ("LUMI_RAG", "LUMI_RAG_K", "LUMI_RAG_FLOOR", "LUMI_RAG_MAX_CHARS",
                "LUMI_RAG_W", "LUMI_RAG_SNIPPET_CHARS"):
        monkeypatch.delenv(key, raising=False)
    cfg = load_config(load_env=False)
    assert cfg.rag is False
    assert cfg.rag_k == 4
    assert cfg.rag_floor == 0.3
    assert cfg.rag_max_chars == 1200
    monkeypatch.setenv("LUMI_RAG", "on")
    monkeypatch.setenv("LUMI_RAG_K", "6")
    monkeypatch.setenv("LUMI_RAG_FLOOR", "0.5")
    monkeypatch.setenv("LUMI_RAG_MAX_CHARS", "800")
    cfg = load_config(load_env=False)
    assert cfg.rag is True and cfg.rag_k == 6 and cfg.rag_floor == 0.5 and cfg.rag_max_chars == 800


def test_rag_w_default_and_override(monkeypatch):
    monkeypatch.delenv("LUMI_RAG_W", raising=False)
    assert load_config(load_env=False).rag_w == 2  # ±2 neighbours
    monkeypatch.setenv("LUMI_RAG_W", "3")
    assert load_config(load_env=False).rag_w == 3


def test_rag_snippet_chars_default_and_override(monkeypatch):
    monkeypatch.delenv("LUMI_RAG_SNIPPET_CHARS", raising=False)
    assert load_config(load_env=False).rag_snippet_chars == 240
    monkeypatch.setenv("LUMI_RAG_SNIPPET_CHARS", "500")
    assert load_config(load_env=False).rag_snippet_chars == 500


def test_prompt_cache_ttl_default_and_1h(monkeypatch):
    monkeypatch.delenv("LUMI_PROMPT_CACHE_TTL", raising=False)
    assert load_config(load_env=False).prompt_cache_ttl == "5m"   # default
    monkeypatch.setenv("LUMI_PROMPT_CACHE_TTL", "1h")
    assert load_config(load_env=False).prompt_cache_ttl == "1h"
    monkeypatch.setenv("LUMI_PROMPT_CACHE_TTL", "junk")
    assert load_config(load_env=False).prompt_cache_ttl == "5m"   # anything but 1h → 5m


def test_prompt_cache_default_and_override(monkeypatch):
    # v0.15: the prompt-cache toggle (on by default).
    monkeypatch.delenv("LUMI_PROMPT_CACHE", raising=False)
    assert load_config(load_env=False).prompt_cache is True
    monkeypatch.setenv("LUMI_PROMPT_CACHE", "off")
    assert load_config(load_env=False).prompt_cache is False


def test_quiet_hours_independent_for_nudge_and_think(monkeypatch):
    monkeypatch.setenv("LUMI_QUIET_HOURS", "0-9")
    # unset think → inherits the nudge's window
    monkeypatch.delenv("LUMI_THOUGHTS_QUIET_HOURS", raising=False)
    cfg = load_config(load_env=False)
    assert cfg.quiet_hours == (0, 9) and cfg.thoughts_quiet_hours == (0, 9)
    # set think → independent of the nudge
    monkeypatch.setenv("LUMI_THOUGHTS_QUIET_HOURS", "23-7")
    cfg = load_config(load_env=False)
    assert cfg.quiet_hours == (0, 9) and cfg.thoughts_quiet_hours == (23, 7)
    # "off" → no quiet hours for the think, while the nudge stays quiet
    monkeypatch.setenv("LUMI_THOUGHTS_QUIET_HOURS", "off")
    cfg = load_config(load_env=False)
    assert cfg.quiet_hours == (0, 9) and cfg.thoughts_quiet_hours is None


def test_telegram_photo_is_a_probability(monkeypatch):
    monkeypatch.delenv("LUMI_TELEGRAM_PHOTO", raising=False)
    assert load_config(load_env=False).telegram_photo == 0.0          # default: never
    for raw, expect in [("0.2", 0.2), ("1", 1.0), ("on", 1.0), ("off", 0.0),
                        ("0", 0.0), ("1.5", 1.0), ("-0.3", 0.0), ("junk", 0.0)]:
        monkeypatch.setenv("LUMI_TELEGRAM_PHOTO", raw)
        assert load_config(load_env=False).telegram_photo == expect, raw  # clamped 0..1; on/off still work


def test_voice_config_default_and_override(monkeypatch):
    for k in ("LUMI_VOICE", "ELEVENLABS_API_KEY", "LUMI_VOICE_ID", "LUMI_VOICE_MODEL"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config(load_env=False)
    assert cfg.voice is False and cfg.elevenlabs_api_key == "" and cfg.voice_id == ""
    assert cfg.voice_model == "eleven_multilingual_v2"  # multilingual default for Ukrainian
    monkeypatch.setenv("LUMI_VOICE", "on")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-eleven-not-real")
    monkeypatch.setenv("LUMI_VOICE_ID", "abc123")
    cfg = load_config(load_env=False)
    assert cfg.voice is True and cfg.elevenlabs_api_key == "sk-eleven-not-real" and cfg.voice_id == "abc123"


def test_think_seeds_path_default_and_override(monkeypatch):
    from core.config import DEFAULT_THINK_SEEDS_PATH
    monkeypatch.delenv("LUMI_THINK_SEEDS_PATH", raising=False)
    assert load_config(load_env=False).think_seeds_path == DEFAULT_THINK_SEEDS_PATH  # default
    monkeypatch.setenv("LUMI_THINK_SEEDS_PATH", "/tmp/my-seeds.md")
    assert load_config(load_env=False).think_seeds_path == Path("/tmp/my-seeds.md")  # override


def test_recall_windows_default_and_override(monkeypatch):
    # date-based recall short-memory recall: 3 date-based windows, .env-tunable (defaults 2 / 7 / 14 + 4 / 6).
    for key in ("LUMI_SESSION_DAYS", "LUMI_DAY_DAYS", "LUMI_WEEK_DAYS",
                "LUMI_MAX_DAY_ROWS", "LUMI_MAX_WEEK_ROWS"):
        monkeypatch.delenv(key, raising=False)
    d = load_config(load_env=False)
    assert (d.session_days, d.day_days, d.week_days) == (2, 7, 14)
    assert (d.max_day_rows, d.max_week_rows) == (4, 6)

    monkeypatch.setenv("LUMI_SESSION_DAYS", "3")
    monkeypatch.setenv("LUMI_DAY_DAYS", "10")
    monkeypatch.setenv("LUMI_WEEK_DAYS", "28")
    monkeypatch.setenv("LUMI_MAX_WEEK_ROWS", "8")
    o = load_config(load_env=False)
    assert (o.session_days, o.day_days, o.week_days, o.max_week_rows) == (3, 10, 28, 8)


def test_closeness_toggle_and_tuning_from_env(monkeypatch):
    for key in ("LUMI_CLOSENESS", "LUMI_CLOSENESS_BASELINE", "LUMI_CLOSENESS_INERTIA"):
        monkeypatch.delenv(key, raising=False)
    d = load_config(load_env=False)
    assert d.closeness is True and d.closeness_tuning.baseline == 30.0  # defaults

    monkeypatch.setenv("LUMI_CLOSENESS", "off")
    monkeypatch.setenv("LUMI_CLOSENESS_BASELINE", "12")
    monkeypatch.setenv("LUMI_CLOSENESS_INERTIA", "7")
    o = load_config(load_env=False)
    assert o.closeness is False
    assert o.closeness_tuning.baseline == 12.0 and o.closeness_tuning.inertia == 7.0


def test_api_key_absent_is_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = load_config(load_env=False)
    assert cfg.api_key is None


def test_config_is_frozen():
    cfg = Config()
    try:
        cfg.model = "mutated"  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        assert exc.__class__.__name__ in {"FrozenInstanceError", "AttributeError"}
    else:
        raise AssertionError("Config should be immutable")
