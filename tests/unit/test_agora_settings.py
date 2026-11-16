"""v3.1 LUMI-278 — the Agora space's settings (``agora.*``): off by default, the room's own model, its limits."""

from __future__ import annotations

from pathlib import Path

from core import settings as st
from core.config import Config, load_config

_AGORA = ("LUMI_AGORA", "LUMI_AGORA_TOKEN", "LUMI_AGORA_MODEL", "LUMI_AGORA_TIMEOUT_S", "LUMI_AGORA_WINDOW",
          "LUMI_AGORA_PRIVATE_NAMES", "LUMI_AGORA_MAX_TURNS_PER_HOUR", "LUMI_AGORA_TODAY_LINES",
          "LUMI_AGORA_TODAY_DIGEST_WORDS", "LUMI_AGORA_GIST_WORDS", "LUMI_AGORA_INITIATIVE")


def _clean(monkeypatch) -> None:
    for name in _AGORA:
        monkeypatch.delenv(name, raising=False)


def test_the_eleven_settings_are_in_the_schema():
    group = {s.key: s for s in st.SETTINGS if s.group == "agora"}
    assert set(group) == {"agora.enabled", "agora.token", "agora.model", "agora.timeout_s", "agora.window",
                          "agora.private_names", "agora.max_turns_per_hour", "agora.today_lines",
                          "agora.today_digest_words", "agora.gist_words", "agora.initiative"}  # v3.4: + initiative
    assert {s.alias for s in group.values()} == set(_AGORA)
    assert group["agora.token"].secret and not any(s.secret for k, s in group.items() if k != "agora.token")


def test_off_by_default_with_the_agreed_limits(monkeypatch, tmp_path):
    _clean(monkeypatch)
    cfg = load_config(load_env=False, settings_dir=tmp_path)
    assert cfg.agora is False and cfg.agora_token == ""
    assert cfg.agora_model == "gemini-2.5-flash"  # R4: still served (checked 2026-11-15)
    assert (cfg.agora_timeout_s, cfg.agora_window, cfg.agora_max_turns_per_hour) == (20, 40, 20)
    assert (cfg.agora_today_lines, cfg.agora_today_digest_words, cfg.agora_gist_words) == (20, 150, 40)
    assert cfg.agora_private_names == ()
    assert cfg.agora_initiative is False  # v3.4: her initiative is off until the owner's word (G8 on prod)
    assert cfg.agora_store_path == cfg.data_root / "spaces.sqlite"  # the room's own file, beside his store


def test_the_yaml_file_and_the_environment(monkeypatch, tmp_path):
    _clean(monkeypatch)
    (tmp_path / "settings.yaml").write_text(
        "agora:\n  enabled: true\n  model: gemini-3.5-flash\n  window: 12\n  private_names: [Віталій, Vitalii]\n",
        encoding="utf-8")
    (tmp_path / "secrets.yaml").write_text("agora:\n  token: room-secret\n", encoding="utf-8")
    (tmp_path / "secrets.yaml").chmod(0o600)
    cfg = load_config(load_env=False, settings_dir=tmp_path)
    assert cfg.agora is True and cfg.agora_token == "room-secret"
    assert cfg.agora_model == "gemini-3.5-flash" and cfg.agora_window == 12
    assert cfg.agora_private_names == ("Віталій", "Vitalii")
    monkeypatch.setenv("LUMI_AGORA_TIMEOUT_S", "7")
    assert load_config(load_env=False, settings_dir=tmp_path).agora_timeout_s == 7


def test_the_token_never_shows_in_the_config_repr():
    assert "room-secret" not in repr(Config(agora_token="room-secret"))


def test_the_store_follows_the_data_root(monkeypatch, tmp_path):
    _clean(monkeypatch)
    monkeypatch.setenv("LUMI_HOME", str(tmp_path / "home"))
    assert load_config(load_env=False).agora_store_path == Path(tmp_path / "home" / "spaces.sqlite")
