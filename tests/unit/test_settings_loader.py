"""v2.9 LUMI-244 — the settings schema + the YAML loader (``core/settings.py``, ``core.config.load_config``).

Pins the precedence (default < ``.env`` < ``settings.yaml`` / ``secrets.yaml`` < the process environment), the
validation of the files (a wrong value refuses, naming the key and the allowed values; a secret's value is never
shown), the folder discovery, the export into ``os.environ`` (never over a real variable) and the consumers that
used to read the environment directly. Fixture folders under ``tmp_path`` only — no network, no paid call.
"""

from __future__ import annotations

import io
import logging
import os
import re
import stat
from dataclasses import fields
from pathlib import Path

import pytest

from core import settings as st
from core.config import Config, load_config

_CONFIG_SRC = (Path(__file__).resolve().parents[2] / "core" / "config.py").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_exports():
    """Each test starts with no file values exported by an earlier one."""
    st._EXPORTED.clear()
    st._WARNED.clear()
    yield
    for name, value in list(st._EXPORTED.items()):
        if os.environ.get(name) == value:
            del os.environ[name]
    st._EXPORTED.clear()


def _write(folder: Path, name: str, text: str, mode: int | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)
    return path


def _load(folder: Path) -> Config:
    return load_config(load_env=False, settings_dir=folder)


# --- the schema ------------------------------------------------------------------------------------------------


def test_every_name_load_config_reads_is_in_the_schema_or_exempt():
    read = set(re.findall(r'_get\("([A-Z0-9_]+)"', _CONFIG_SRC)) | set(re.findall(r'_float\("([A-Z0-9_]+)"', _CONFIG_SRC))
    assert len(read) > 200  # the grep itself still works
    missing = sorted(n for n in read if n not in st.BY_ALIAS and n not in st.ENV_ONLY)
    assert missing == []
    unused = sorted(s.alias for s in st.SETTINGS if s.alias not in read)
    assert unused == []  # every schema setting feeds load_config


def test_the_schema_is_well_formed():
    assert len(st.BY_ALIAS) == len(st.SETTINGS) and len(st.BY_KEY) == len(st.SETTINGS)
    for s in st.SETTINGS:
        assert s.type in st.TYPES, s
        assert re.fullmatch(r"[a-z]+\.[a-z0-9_]+", s.key), s.key
        assert s.description.strip(), s
        if s.type == "enum":
            assert s.choices, s
            assert s.default is None or s.default in s.choices, s
        if s.secret:
            assert s.type == "str" and s.default is None, s
        if s.default is not None:
            assert st.to_env(s, s.default) is not None, s  # every default is valid for its own type
    assert not set(st.ENV_ONLY) & set(st.BY_ALIAS)


def test_the_exempt_names_are_pinned():
    assert set(st.ENV_ONLY) == {
        "LUMI_CONTAINER", "LUMI_BUILD", "LUMI_SETTINGS_DIR", "LUMI_IMAGE_TAG", "LUMI_INSTANCE", "LUMI_INSTANCE_DIR",
        "LUMI_PUBLISH_HOST", "LUMI_TZ", "LUMI_REALTIME_MODEL", "LUMI_REALTIME_VOICE",
    }


def test_the_secrets_are_the_keys_and_tokens():
    assert {s.alias for s in st.SETTINGS if s.secret} == {
        "LUMI_SERVER_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MINIMAX_API_KEY",
        "GEMINI_API_KEY", "VOYAGE_API_KEY", "LUMI_NEWS_API_KEY", "LUMI_TELEGRAM_TOKEN", "ELEVENLABS_API_KEY",
        "DEEPGRAM_API_KEY",
    }


# --- precedence ------------------------------------------------------------------------------------------------

# one setting of each type: (alias, the YAML key path, a .env value, a YAML value, an env value, Config field,
# the three expected Config values)
_CASES = [
    ("LUMI_RAG", "recall:\n  rag: {}", "on", "false", "on", "rag", True, False, True),
    ("LUMI_MEMORY_WINDOW", "memory:\n  window: {}", "11", "22", "33", "memory_window", 11, 22, 33),
    ("LUMI_RAG_FLOOR", "recall:\n  rag_floor: {}", "0.1", "0.2", "0.4", "rag_floor", 0.1, 0.2, 0.4),
    ("LUMI_VOICE_ID", "voice:\n  voice_id: {}", "a", "b", "c", "voice_id", "a", "b", "c"),
    ("LUMI_JOURNAL_DIR", "journal:\n  dir: {}", "/j1", "/j2", "/j3", "journal_dir", Path("/j1"), Path("/j2"),
     Path("/j3")),
    ("LUMI_THINK_SHOW", "persona:\n  think_show: {}", "open", "off", "debug", "think_show", "open", "off", "debug"),
    ("LUMI_WIKI_LANG", "wiki:\n  lang: {}", "en", "[pl, uk]", "de", "wiki_lang", "en", "pl,uk", "de"),
    ("LUMI_TELEGRAM_ALLOWLIST", "telegram:\n  allowlist: {}", "1", "[2, 3]", "4", "telegram_allowlist", (1,),
     (2, 3), (4,)),
    ("LUMI_GEMINI_CACHE_TTL", "model:\n  gemini_cache_ttl: {}", "1h", "30m", "90", "gemini_cache_ttl_s", 3600, 1800,
     90),
    ("LUMI_QUIET_HOURS", "nudge:\n  quiet_hours: {}", "1-2", "23-7", "off", "quiet_hours", (1, 2), (23, 7), None),
    ("LUMI_TELEGRAM_PHOTO", "telegram:\n  photo: {}", "0.25", "0.5", "on", "telegram_photo", 0.25, 0.5, 1.0),
]


@pytest.mark.parametrize("case", _CASES, ids=[c[0] for c in _CASES])
def test_precedence_default_then_dotenv_then_yaml_then_env(case, tmp_path, monkeypatch):
    alias, yaml_tpl, v_env_file, v_yaml, v_env, attr, want_dotenv, want_yaml, want_env = case
    monkeypatch.delenv(alias, raising=False)
    default = getattr(Config(), attr)
    assert getattr(_load(tmp_path), attr) == default  # nothing anywhere → the built-in default
    _write(tmp_path, ".env", f"{alias}={v_env_file}\n")
    assert getattr(_load(tmp_path), attr) == want_dotenv  # .env over the default
    _write(tmp_path, "settings.yaml", yaml_tpl.format(v_yaml) + "\n")
    assert getattr(_load(tmp_path), attr) == want_yaml  # settings.yaml over .env
    monkeypatch.setenv(alias, v_env)
    assert getattr(_load(tmp_path), attr) == want_env  # the process environment over everything


def test_nothing_set_is_byte_identical_to_the_defaults(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    a, b = load_config(load_env=False), _load(empty)
    assert a == b
    assert [(f.name, getattr(a, f.name)) for f in fields(a)] == [(f.name, getattr(b, f.name)) for f in fields(b)]


def test_secrets_yaml_feeds_the_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LUMI_SERVER_TOKEN", raising=False)
    _write(tmp_path, "secrets.yaml", "providers:\n  anthropic_api_key: sk-test\nserver:\n  token: tok\n", 0o600)
    cfg = _load(tmp_path)
    assert cfg.api_key == "sk-test" and cfg.server_token == "tok"


def test_a_null_value_means_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("LUMI_MEMORY_WINDOW", raising=False)
    _write(tmp_path, ".env", "LUMI_MEMORY_WINDOW=12\n")
    _write(tmp_path, "settings.yaml", "memory:\n  window:\n")
    assert _load(tmp_path).memory_window == 12  # the null drops out; the weaker layer stands


def test_the_old_gemini_name_still_works(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-old")
    assert load_config(load_env=False).gemini_api_key == "g-old"
    monkeypatch.setenv("GEMINI_API_KEY", "g-new")
    assert load_config(load_env=False).gemini_api_key == "g-new"


# --- validation ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("yaml_text, key, allowed", [
    ("persona:\n  think_show: loud\n", "persona.think_show", "one of debug, open, off"),
    ("server:\n  port: 99999\n", "server.port", "an integer 1..65535"),
    ("recall:\n  rag: maybe\n", "recall.rag", "true or false"),
    ("memory:\n  window: 0\n", "memory.window", "≥ 1"),
    ("nudge:\n  quiet_hours: evening\n", "nudge.quiet_hours", "HH-HH"),
    ("telegram:\n  allowlist: [me]\n", "telegram.allowlist", "integer ids"),
])
def test_a_wrong_value_refuses_naming_the_key_and_the_allowed_values(tmp_path, yaml_text, key, allowed):
    _write(tmp_path, "settings.yaml", yaml_text)
    with pytest.raises(st.SettingsError) as err:
        _load(tmp_path)
    msg = str(err.value)
    assert "settings.yaml" in msg and key in msg and allowed in msg
    assert isinstance(err.value, SystemExit)  # an entry point exits with the message, never a traceback


def test_every_problem_is_named_in_one_message(tmp_path):
    _write(tmp_path, "settings.yaml", "server:\n  port: 0\nrecall:\n  rag: maybe\n")
    with pytest.raises(st.SettingsError) as err:
        _load(tmp_path)
    assert "server.port" in str(err.value) and "recall.rag" in str(err.value)


def test_a_secret_value_is_never_shown_in_a_refusal(tmp_path):
    _write(tmp_path, "secrets.yaml", "server:\n  token: [s3cr3t-value]\n", 0o600)
    with pytest.raises(st.SettingsError) as err:
        _load(tmp_path)
    assert "server.token" in str(err.value) and "(hidden)" in str(err.value)
    assert "s3cr3t-value" not in str(err.value)


def test_a_secret_in_settings_yaml_is_refused(tmp_path):
    _write(tmp_path, "settings.yaml", "providers:\n  anthropic_api_key: sk-oops\n")
    with pytest.raises(st.SettingsError) as err:
        _load(tmp_path)
    assert "providers.anthropic_api_key belongs in secrets.yaml" in str(err.value)
    assert "sk-oops" not in str(err.value)


def test_a_plain_setting_in_secrets_yaml_is_refused(tmp_path):
    _write(tmp_path, "secrets.yaml", "memory:\n  window: 30\n", 0o600)
    with pytest.raises(st.SettingsError) as err:
        _load(tmp_path)
    assert "memory.window belongs in settings.yaml" in str(err.value)


def test_an_unknown_key_only_warns(tmp_path):
    _write(tmp_path, "settings.yaml", "memory:\n  window: 30\n  future_knob: 1\nnewgroup:\n  x: 2\n")
    res = st.resolve({}, folder=tmp_path)
    assert res.values["LUMI_MEMORY_WINDOW"] == "30"
    assert any("memory.future_knob" in w for w in res.warnings)
    assert any("newgroup.x" in w for w in res.warnings)


def test_invalid_yaml_refuses_readably(tmp_path):
    _write(tmp_path, "settings.yaml", "memory: [unclosed\n")
    with pytest.raises(st.SettingsError, match="not valid YAML"):
        _load(tmp_path)
    _write(tmp_path, "settings.yaml", "- a\n- b\n")
    with pytest.raises(st.SettingsError, match="must be a mapping"):
        _load(tmp_path)


def test_yaml_booleans_read_as_enum_words(tmp_path):
    # YAML 1.1 reads `off` / `on` as booleans — an enum whose choices include them still accepts the word.
    _write(tmp_path, "settings.yaml", "persona:\n  think_show: off\nmodel:\n  openai_responses: on\n")
    cfg = _load(tmp_path)
    assert cfg.think_show == "off" and cfg.openai_responses == "on"


def test_environment_values_keep_the_lenient_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMI_THINK_SHOW", "loud")  # unknown → the default, as before v2.9 (no refusal)
    assert load_config(load_env=False).think_show == "debug"


# --- the .env layer, warnings, permissions ------------------------------------------------------------------


def test_a_dotenv_warns_once_that_it_is_deprecated(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMI_SETTINGS_DIR", str(tmp_path))
    _write(tmp_path, ".env", "LUMI_MEMORY_WINDOW=12\n")
    res = st.resolve(os.environ)
    assert [w for w in res.warnings if "deprecated since v2.9" in w and "env_to_settings.py" in w]
    out = io.StringIO()
    st.warn_once(res, out)
    st.warn_once(st.resolve(os.environ), out)
    assert out.getvalue().count("deprecated since v2.9") == 1


def test_secrets_readable_by_others_get_a_chmod_warning(tmp_path):
    _write(tmp_path, "secrets.yaml", "server:\n  token: t\n", 0o644)
    res = st.resolve({}, folder=tmp_path)
    assert any("chmod 600" in w for w in res.warnings)
    (tmp_path / "secrets.yaml").chmod(0o600)
    assert not any("chmod 600" in w for w in st.resolve({}, folder=tmp_path).warnings)


def test_where_each_value_came_from(tmp_path, monkeypatch):
    monkeypatch.delenv("LUMI_RAG_K", raising=False)
    _write(tmp_path, ".env", "LUMI_MEMORY_WINDOW=12\n")
    _write(tmp_path, "settings.yaml", "recall:\n  rag: true\n")
    _write(tmp_path, "secrets.yaml", "server:\n  token: t\n", 0o600)
    res = st.resolve({"LUMI_STREAM": "on"}, folder=tmp_path)
    assert res.source("LUMI_MEMORY_WINDOW") == ".env"
    assert res.source("LUMI_RAG") == "settings.yaml"
    assert res.source("LUMI_SERVER_TOKEN") == "secrets.yaml"
    assert res.source("LUMI_STREAM") == "env"
    assert res.source("LUMI_RAG_K") == "default"


# --- discovery -------------------------------------------------------------------------------------------------


def test_the_settings_folder(tmp_path):
    repo = tmp_path / "instance" / "app"
    repo.mkdir(parents=True)
    assert st.settings_dir({}, repo=repo) == repo  # nothing anywhere → the repo root
    _write(repo.parent, "settings.yaml", "")
    assert st.settings_dir({}, repo=repo) == repo.parent  # the units' ~/lumi/<i>/app layout
    _write(repo, ".env", "")
    assert st.settings_dir({}, repo=repo) == repo  # the repo root first (the dev tree)
    assert st.settings_dir({"LUMI_SETTINGS_DIR": "/settings"}, repo=repo) == Path("/settings")  # the containers


# --- export --------------------------------------------------------------------------------------------------


def test_export_never_overrides_a_real_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMI_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setenv("LUMI_VOICE_ID", "from-env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    _write(tmp_path, "settings.yaml", "voice:\n  voice_id: from-file\n")
    _write(tmp_path, "secrets.yaml", "providers:\n  gemini_api_key: g-file\n", 0o600)
    cfg = load_config()
    assert cfg.voice_id == "from-env"
    assert os.environ["LUMI_VOICE_ID"] == "from-env"
    assert os.environ["GEMINI_API_KEY"] == "g-file"  # what load_dotenv did: direct readers see the files


def test_an_exported_value_is_not_mistaken_for_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMI_SETTINGS_DIR", str(tmp_path))
    monkeypatch.delenv("LUMI_MEMORY_WINDOW", raising=False)
    _write(tmp_path, "settings.yaml", "memory:\n  window: 30\n")
    assert load_config().memory_window == 30
    assert os.environ["LUMI_MEMORY_WINDOW"] == "30"
    _write(tmp_path, "settings.yaml", "memory:\n  window: 31\n")  # the file changes; a new load sees the file
    res = st.resolve(os.environ)
    assert res.values["LUMI_MEMORY_WINDOW"] == "31" and res.source("LUMI_MEMORY_WINDOW") == "settings.yaml"


def test_load_env_false_reads_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMI_SETTINGS_DIR", str(tmp_path))
    monkeypatch.delenv("LUMI_MEMORY_WINDOW", raising=False)
    _write(tmp_path, "settings.yaml", "memory:\n  window: 30\n")
    assert load_config(load_env=False).memory_window == Config().memory_window


# --- the consumers that used to read the environment directly ----------------------------------------------


def test_the_bind_guard_checks_a_host_set_only_in_settings_yaml(tmp_path, monkeypatch):
    import server.__main__ as sm

    monkeypatch.delenv("LUMI_SERVER_HOST", raising=False)
    _write(tmp_path, "settings.yaml", "server:\n  host: 0.0.0.0\n")
    cfg = _load(tmp_path)
    assert cfg.server_host == "0.0.0.0"
    assert sm.bind_problem(cfg.server_host) is not None


def test_the_telegram_log_level_comes_from_the_settings(tmp_path, monkeypatch):
    from telegram import get_logger

    monkeypatch.delenv("LUMI_LOG_LEVEL", raising=False)
    _write(tmp_path, "settings.yaml", "instance:\n  log_level: warning\n")
    cfg = _load(tmp_path)
    assert cfg.log_level == "WARNING"
    log = get_logger("settings-test-level", level=cfg.log_level)
    try:
        assert log.level == logging.WARNING
    finally:
        log.handlers.clear()


def test_the_env_guard_hatch_is_a_setting(tmp_path):
    from core.envguard import GitState, enforce, write_marker

    root = tmp_path / "data"
    write_marker(root, "prod")
    _write(tmp_path, "settings.yaml", "instance:\n  env: prod\n  home: " + str(root) + "\n  allow_dirty_prod: true\n")
    cfg = _load(tmp_path)
    assert cfg.allow_dirty_prod is True

    def dirty_git(cmd, **kw):  # a checkout with a modified file and no tag at HEAD
        class R:
            returncode = 0 if "status" in cmd else 128
            stdout = " M core/x.py\n" if "status" in cmd else ""
        return R()

    (tmp_path / "repo" / ".git").mkdir(parents=True)
    notes = enforce(cfg, repo=tmp_path / "repo", run=dirty_git, environ={}, dockerenv=tmp_path / "no")
    assert any("bypassed" in n for n in notes)
    assert GitState  # imported for the reader: the guard's own tests cover the git parsing


def test_the_core_passes_the_settings_gemini_key_to_image_and_web(tmp_path, monkeypatch):
    from core.agent import build_core
    from core.llm import MockLLMClient
    from state.local_store import JsonRepository

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    _write(tmp_path, "secrets.yaml", "providers:\n  gemini_api_key: g-yaml\n", 0o600)
    cfg = _load(tmp_path)
    core = build_core(config=cfg, llm=MockLLMClient(), repository=JsonRepository(tmp_path / "s.json"))
    assert core._gemini_api_key == "g-yaml"


# --- v2.9 code review ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "providers:\n  anthropic_api_key: sk-ant-LEAK123: oops\n",       # a colon in an unquoted value
    "providers:\n  anthropic_api_key: \"sk-ant-LEAK123\n",             # an unclosed quote
    "providers:\n  anthropic_api_key: [sk-ant-LEAK123\n",             # an unclosed flow list
])
def test_review_1_a_yaml_error_never_shows_the_secret_line(tmp_path, text):
    _write(tmp_path, "secrets.yaml", text, 0o600)
    with pytest.raises(st.SettingsError) as err:
        _load(tmp_path)
    assert "not valid YAML" in str(err.value) and "line " in str(err.value)
    assert "LEAK123" not in str(err.value)
    problems, _ = st.check_folder(tmp_path)  # lumi-cli config validate
    assert problems and all("LEAK123" not in p for p in problems)
    with pytest.raises(st.SettingsError) as err:  # lumi-cli config set on the broken file: no raw traceback
        st.set_value(tmp_path, "server.token", "new")
    assert "LEAK123" not in str(err.value)


@pytest.mark.parametrize("env, files, want", [
    ({"GOOGLE_API_KEY": "g-shell"}, {"secrets.yaml": "providers:\n  gemini_api_key: g-file\n"}, "g-file"),
    ({"GOOGLE_API_KEY": "g-shell"}, {".env": "GEMINI_API_KEY=g-dotenv\n"}, "g-dotenv"),
    ({"GOOGLE_API_KEY": "g-shell", "GEMINI_API_KEY": "g-env"}, {}, "g-env"),
    ({"GOOGLE_API_KEY": "g-shell"}, {}, "g-shell"),  # the old name still works when nothing else names the key
    ({}, {".env": "GOOGLE_API_KEY=g-old-dotenv\n"}, "g-old-dotenv"),
])
def test_review_3_the_old_gemini_name_never_beats_the_new_one(tmp_path, env, files, want):
    for name, text in files.items():
        _write(tmp_path, name, text, 0o600)
    assert st.resolve(env, folder=tmp_path).values["GEMINI_API_KEY"] == want


@pytest.mark.parametrize("yaml_text, key", [
    ("model:\n  gemini_cache_ttl: .inf\n", "model.gemini_cache_ttl"),
    ("model:\n  gemini_cache_ttl: inf\n", "model.gemini_cache_ttl"),
    ("model:\n  gemini_cache_ttl: -5m\n", "model.gemini_cache_ttl"),
    ("recall:\n  rag_floor: .nan\n", "recall.rag_floor"),
    ("viewer:\n  face_idle_seconds: .inf\n", "viewer.face_idle_seconds"),
    ("memory:\n  window: \"²\"\n", "memory.window"),
    ("memory:\n  window: \"--5\"\n", "memory.window"),
])
def test_review_6_values_load_config_would_crash_on_are_refused(tmp_path, yaml_text, key):
    _write(tmp_path, "settings.yaml", yaml_text)
    with pytest.raises(st.SettingsError) as err:  # a readable refusal, never an OverflowError / ValueError
        _load(tmp_path)
    assert key in str(err.value)
    assert st.check_folder(tmp_path)[0]  # and `config validate` says so


@pytest.mark.parametrize("text, line", [
    ("memory:\n  window: 12\nrecall:\n  rag: true\nmemory:\n  compaction_batch: 10\n", 5),  # a group twice
    ("memory:\n  window: 12\n  window: 30\n", 3),                                                 # a key twice
])
def test_review_7_a_duplicate_key_refuses_naming_its_line(tmp_path, text, line):
    _write(tmp_path, "settings.yaml", text)
    with pytest.raises(st.SettingsError) as err:
        _load(tmp_path)
    assert "duplicate key" in str(err.value) and f"line {line}" in str(err.value)


def test_review_7_a_duplicate_in_secrets_names_the_key_not_the_value(tmp_path):
    _write(tmp_path, "secrets.yaml", "server:\n  token: a-SECRET-1\n  token: b-SECRET-2\n", 0o600)
    with pytest.raises(st.SettingsError) as err:
        _load(tmp_path)
    assert "duplicate key 'token'" in str(err.value) and "SECRET" not in str(err.value)


def test_review_8_new_session_never_re_reads_the_settings_files(tmp_path, monkeypatch):
    """/new re-snapshots the ambient context; a settings file broken mid-session must not raise in that worker."""
    import asyncio

    import tui.app as tui_app
    from core.agent import Core
    from core.llm import MockLLMClient
    from state.local_store import JsonRepository

    core = Core(llm=MockLLMClient(states={"reply": "так", "emotion": "calm", "intensity": 0.5}),
                repository=JsonRepository(tmp_path / "s.json"), canon="Ти — Лілі.", model="claude-haiku-4-5-20251001")
    app = tui_app.LumiApp(core)
    app._app_cfg = load_config(load_env=False)  # what on_mount loaded

    def broken(**_kw):
        raise st.SettingsError("Lumi settings — refusing to start:\n  • settings.yaml: memory.window = 0 …")

    monkeypatch.setattr(tui_app, "load_config", broken)
    asyncio.run(app._refresh_world())  # no SystemExit: it used the config loaded at start



@pytest.mark.parametrize("yaml_value", ["0123", "4", "1.10"])
def test_review_10_a_number_for_a_text_setting_is_refused_with_a_quote_hint(tmp_path, yaml_value):
    _write(tmp_path, "settings.yaml", f"voice:\n  voice_id: {yaml_value}\n")
    with pytest.raises(st.SettingsError) as err:
        _load(tmp_path)
    assert "voice.voice_id" in str(err.value) and "quote" in str(err.value)
    _write(tmp_path, "settings.yaml", f'voice:\n  voice_id: "{yaml_value}"\n')
    assert _load(tmp_path).voice_id == yaml_value  # quoted, it stays exactly what was written


def test_review_10_the_cli_and_the_migration_write_numeric_text_quoted(tmp_path):
    st.set_value(tmp_path, "dictation.stt_device", "4")
    assert "stt_device: '4'" in (tmp_path / "settings.yaml").read_text(encoding="utf-8")
    assert _load(tmp_path).stt_device == "4"
    assert st.render_file({"voice.voice_id": "0123"}, "# h\n").endswith("  voice_id: '0123'\n")



def test_review_12_a_failed_write_leaves_the_old_file_whole(tmp_path, monkeypatch):
    path = _write(tmp_path, "secrets.yaml", "server:\n  token: old\n", 0o600)

    def boom(*_a, **_kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(st.os, "replace", boom)
    with pytest.raises(OSError):
        st.set_value(tmp_path, "server.token", "new")
    assert path.read_text(encoding="utf-8") == "server:\n  token: old\n"  # never truncated
    assert sorted(p.name for p in tmp_path.iterdir()) == ["secrets.yaml"]  # no temp file left behind


def test_review_12_the_modes_after_an_atomic_write(tmp_path):
    st.set_value(tmp_path, "server.token", "t")
    st.set_value(tmp_path, "memory.window", "30")
    assert stat.S_IMODE((tmp_path / "secrets.yaml").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "settings.yaml").stat().st_mode) == 0o644
    (tmp_path / "settings.yaml").chmod(0o640)
    st.set_value(tmp_path, "memory.window", "31")
    assert stat.S_IMODE((tmp_path / "settings.yaml").stat().st_mode) == 0o640  # an existing mode is kept
