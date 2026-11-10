"""v2.9 LUMI-245 — the schema contract: the settings schema ↔ the example files ↔ the loader.

1. the committed example files are exactly what the schema renders (``scripts/settings_examples.py --check``);
2. every setting has a description and a group;
3. every key in either example file is known to the schema;
4. no secret appears in ``settings.example.yaml``, no plain setting in ``secrets.example.yaml``;
5. every schema default is the loader's default (setting it explicitly changes nothing);
6. every ``Config`` field is fed by a schema setting (or is a documented derived field), and every setting feeds one.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from pathlib import Path

import pytest

from core import settings as st
from core.config import Config, load_config

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import settings_examples  # noqa: E402

# Config fields no single setting feeds on its own.
DERIVED = {
    "app_version": "the checkout's VERSION file",
    "embed_api_key": "VOYAGE_API_KEY or OPENAI_API_KEY, picked by recall.embed_provider",
}
# Settings whose change shows only together with another one (the reason is the partner).
CONDITIONAL = {
    "VOYAGE_API_KEY": "feeds embed_api_key only with recall.embed_provider = voyage",
    "LUMI_MODELS_FILE": "a missing file leaves the code defaults; the profile file's content decides",
}


def _example_keys(text: str) -> list[str]:
    """The ``group.key`` of every setting line — the commented line right after its ``· env NAME`` comment."""
    keys, group, after_comment = [], "", False
    for line in text.splitlines():
        if m := re.match(r"^([a-z][a-z0-9_]*):\s*$", line):
            group, after_comment = m.group(1), False
        elif "· env " in line:
            after_comment = True
        elif after_comment and (m := re.match(r"^\s+#\s+([a-z][a-z0-9_]*):", line)):
            keys.append(f"{group}.{m.group(1)}")
            after_comment = False
    return keys


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_1_the_example_files_are_generated_from_the_schema():
    assert settings_examples.stale(ROOT) == [], "run `uv run python scripts/settings_examples.py`"


def test_1_the_check_mode_catches_a_stale_file(tmp_path):
    settings_examples.write(tmp_path)
    assert settings_examples.stale(tmp_path) == []
    (tmp_path / "settings.example.yaml").write_text("# edited by hand\n", encoding="utf-8")
    assert settings_examples.stale(tmp_path) == ["settings.example.yaml"]


def test_2_every_setting_is_documented_and_grouped():
    for s in st.SETTINGS:
        assert s.description.strip() and s.group in st.GROUPS, s.alias


def test_3_every_example_key_is_known_and_every_setting_is_in_an_example():
    keys = _example_keys(_read("settings.example.yaml")) + _example_keys(_read("secrets.example.yaml"))
    assert [k for k in keys if k not in st.BY_KEY] == []
    assert sorted(keys) == sorted(st.BY_KEY)  # each setting exactly once across the two files


def test_4_no_secret_in_settings_and_no_plain_setting_in_secrets():
    plain = _example_keys(_read("settings.example.yaml"))
    secret = _example_keys(_read("secrets.example.yaml"))
    assert [k for k in plain if st.BY_KEY[k].secret] == []
    assert [k for k in secret if not st.BY_KEY[k].secret] == []
    for k in secret:  # no value on a secret's line, not even commented out
        assert re.search(rf"^  # {k.split('.')[1]}:$", _read("secrets.example.yaml"), re.M), k


def test_4_the_example_files_parse_and_change_nothing(tmp_path):
    # a verbatim copy of each example is a valid settings folder equal to the defaults
    (tmp_path / "settings.yaml").write_text(_read("settings.example.yaml"), encoding="utf-8")
    (tmp_path / "secrets.yaml").write_text(_read("secrets.example.yaml"), encoding="utf-8")
    (tmp_path / "secrets.yaml").chmod(0o600)
    res = st.resolve({}, folder=tmp_path)
    assert res.warnings == [] and res.from_files == {}
    assert load_config(load_env=False, settings_dir=tmp_path) == load_config(load_env=False,
                                                                              settings_dir=tmp_path / "none")


def test_5_every_schema_default_is_the_loaders_default(monkeypatch):
    for s in st.SETTINGS:
        monkeypatch.delenv(s.alias, raising=False)
    base = load_config(load_env=False)
    wrong = []
    for s in st.SETTINGS:
        if s.default is None:
            continue
        monkeypatch.setenv(s.alias, st.to_env(s, s.default))
        if load_config(load_env=False) != base:
            wrong.append(s.alias)
        monkeypatch.delenv(s.alias)
    assert wrong == []


def _sample(s: st.Setting) -> str:
    """A valid value for ``s`` that differs from its default (in env-string form)."""
    special = {
        "LUMI_MODEL_PROFILE": "openai",
        "LUMI_MODEL_ALIASES": "zz=anthropic:zz-model",
        "LUMI_MODEL_PROFILES": "zz=anthropic:a,b,c,d",
        "LUMI_EMBED_PROVIDER": "openai",
    }
    if s.alias in special:
        return special[s.alias]
    t = s.type
    if t == "bool":
        return "off" if s.default else "on"
    if t == "int":
        lo = int(s.min) if s.min is not None else 0
        return str(lo + 1 if s.default is None or s.default != lo + 1 else lo + 2)
    if t == "float":
        lo = s.min if s.min is not None else 0.0
        return repr(lo + 0.25) if s.default != lo + 0.25 else repr(lo + 0.5)
    if t == "probability":
        return "0.5" if s.default != 0.5 else "0.25"
    if t == "enum":
        return next(c for c in s.choices if c != s.default)
    if t == "path":
        return "/tmp/lumi-contract-sample"
    if t == "list":
        return "zz"
    if t == "ids":
        return "42"
    if t == "duration":
        return "7m"
    if t == "hours":
        return "1-2"
    return "zz-sample"


def test_6_every_setting_feeds_a_field_and_every_field_is_fed(monkeypatch):
    for s in st.SETTINGS:
        monkeypatch.delenv(s.alias, raising=False)
        for old in s.old:
            monkeypatch.delenv(old, raising=False)
    base = load_config(load_env=False)
    fed: set[str] = set()
    dead: list[str] = []
    for s in st.SETTINGS:
        monkeypatch.setenv(s.alias, _sample(s))
        cfg = load_config(load_env=False)
        monkeypatch.delenv(s.alias)
        changed = {f.name for f in dataclasses.fields(Config) if getattr(cfg, f.name) != getattr(base, f.name)}
        if not changed and s.alias not in CONDITIONAL:
            dead.append(s.alias)
        fed |= changed
    assert dead == [], "settings that change no Config field"
    unfed = sorted({f.name for f in dataclasses.fields(Config)} - fed - set(DERIVED))
    assert unfed == [], "Config fields no setting feeds (add a setting or document them in DERIVED)"


@pytest.mark.parametrize("alias", sorted(CONDITIONAL))
def test_6_the_conditional_settings_feed_with_their_partner(alias, monkeypatch, tmp_path):
    if alias == "VOYAGE_API_KEY":
        monkeypatch.setenv("LUMI_EMBED_PROVIDER", "voyage")
        monkeypatch.setenv(alias, "v-key")
        assert load_config(load_env=False).embed_api_key == "v-key"
    else:
        models = tmp_path / "models.toml"
        models.write_text('[aliases]\nzz = "anthropic:zz-model"\n', encoding="utf-8")
        monkeypatch.setenv(alias, str(models))
        assert load_config(load_env=False).model_aliases["zz"] == ("anthropic", "zz-model")
