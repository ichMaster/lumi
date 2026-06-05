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
