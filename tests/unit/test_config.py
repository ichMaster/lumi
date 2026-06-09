"""Unit tests for config loading (LUMI-001)."""

from pathlib import Path

from core.config import DEFAULT_MEMORY_WINDOW, DEFAULT_MODEL, Config, load_config


def test_defaults_without_env(monkeypatch):
    for key in (
        "LUMI_MODEL",
        "LUMI_PROVIDER",
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
