"""Unit tests for the v2.1 env name + version badge (LUMI-208)."""

from core.config import load_config, read_version
from tui.app import env_badge


def test_env_unset_is_none(monkeypatch):
    monkeypatch.delenv("LUMI_ENV", raising=False)
    assert load_config(load_env=False).env is None


def test_env_blank_is_none(monkeypatch):
    monkeypatch.setenv("LUMI_ENV", "   ")
    assert load_config(load_env=False).env is None


def test_env_is_stripped_and_lowercased(monkeypatch):
    monkeypatch.setenv("LUMI_ENV", "  PROD ")
    assert load_config(load_env=False).env == "prod"


def test_app_version_comes_from_the_version_file(monkeypatch):
    cfg = load_config(load_env=False)
    assert cfg.app_version == read_version()  # the checkout's VERSION, read at load


def test_read_version_present(tmp_path):
    f = tmp_path / "VERSION"
    f.write_text("2.1.0\n", encoding="utf-8")
    assert read_version(f) == "2.1.0"


def test_read_version_missing_or_empty_is_none(tmp_path):
    assert read_version(tmp_path / "nope") is None
    empty = tmp_path / "VERSION"
    empty.write_text("  \n", encoding="utf-8")
    assert read_version(empty) is None


def test_badge_unset_is_empty():
    assert env_badge(None, "2.1.0") == ""
    assert env_badge("", "2.1.0") == ""


def test_badge_prod_is_prominent():
    assert env_badge("prod", "2.1.0") == "[bold red]prod v2.1.0[/] · "


def test_badge_dev_is_dim():
    assert env_badge("dev", "2.1.0") == "[dim]dev v2.1.0[/] · "


def test_badge_without_version_shows_env_only():
    assert env_badge("prod", None) == "[bold red]prod[/] · "


def test_badge_escapes_markup():
    assert "\\[" in env_badge("dev[x]", None)  # an env value can't inject Rich markup
